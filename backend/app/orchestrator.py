"""오케스트레이터.

사용자의 자연어 질의를 받아 어떤 에이전트를 어떤 순서로 돌릴지 판단하고,
결과를 종합해 답변한다. 판단은 Claude 가, 실행은 도구가 한다.

  prediagnose      사전진단 에이전트 호출 (공간정보 + 법령 규제 검토)
  render_on_map    지도제어 에이전트 호출 (진단 결과를 지도에 반영)
  restudy_massing  좌표·규제 재조회 없이 건축 가능 규모만 다시 산출 (후속 질의용)

이벤트를 async generator 로 흘려보내 프론트가 진행 상황을 실시간으로 본다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from typing import AsyncIterator

from .agents.map_control import build_map_commands
from .agents.prediagnosis import (
    compact,
    detect_use_restriction,
    format_diagnosis_answer,
    _has_building_feasibility_intent,
    run_prediagnosis,
)
from .llm import append_tool_results
from .tools import building_register, massing, site_constraints, vworld

logger = logging.getLogger("uvicorn.error")

ANSWER_STYLE_RULES = """답변 표현 원칙:
- 법령·조례·진단 데이터에 없는 분류명이나 개념을 임의로 만들지 마라.
- '일반 건축', '원칙적으로', '가능할 수 있습니다', '건축을 검토할 수 있습니다'처럼
  범위와 결론이 모호한 표현을 쓰지 마라.
- 판정은 데이터의 verdict에 따라 '건축 가능합니다', '조건부 가능합니다',
  '건축이 불가합니다', '추가 확인이 필요합니다' 중 의미가 맞는 말로 명확히 표현하라.
- 조건·예외·제한은 실제 진단 데이터에 있을 때만 판정 다음 문장에서 설명하라.
- 필지명·용도·규제명·수치는 제공된 데이터에서 읽고, 고정 예문을 복사하지 말고
  사용자의 질문 의도에 맞는 자연스러운 한국어 문장으로 구성하라.
"""


def _answer_system(instructions: str) -> str:
    """모든 사용자 답변용 Gemini 호출에 동일한 표현 원칙을 적용한다."""
    return f"{ANSWER_STYLE_RULES}\n{instructions}"


def _is_building_restore_request(query: str) -> bool:
    """사용자가 직접 건물 표시 복원을 요청한 경우만 참이다.

    프런트가 새 필지 진단용으로 덧붙이는 '이전 진단 필지와 다르면 … 건물'
    같은 내부 문장은 복원 명령이 아니다. 반드시 복원 동작어까지 있어야 한다.
    """
    compact = re.sub(r"\s+", "", query)
    return bool(
        re.search(
            r"(?:(?:3d|입체)(?:건물|모델)?|건물모델|상세모델|모델)"
            r".*(?:다시켜|다시보여|복원|복구|원래대로|이전으로|켜줘|보여줘|표시해)"
            r"|(?:다시|이전|원래).*(?:3d|입체|건물|모델)"
            r".*(?:보여|켜|표시|복원|복구|돌려)",
            compact,
            re.IGNORECASE,
        )
        or re.search(
            r"(?:다시)?(?:이전|원래)(?:상태|모습)?(?:으로|대로)?"
            r"(?:돌려|돌아|보여|복원)"
            r"|(?:다시)?원상(?:태)?(?:으로)?(?:복구|복원|돌려)"
            r"|^(?:다시)?(?:복원|복구)(?:해|해줘|해주세요|시켜줘)?$"
            r"|^다시(?:켜|켜줘|켜죠|보여|보여줘|표시해|표시해줘)$",
            compact,
            re.IGNORECASE,
        )
    )


SYSTEM = _answer_system("""당신은 공간정보 기반 건축 인허가 상담 시스템의 오케스트레이터입니다.
사용자의 자연어 질의를 해석해 필요한 에이전트를 호출하고, 결과를 종합해 답변합니다.

판단 기준:
- 특정 주소/필지 또는 위도·경도로 지정한 위치의 건축 가능 여부를 묻는 질의 -> prediagnose 를 먼저 호출한다.
- prediagnose 는 결과를 지도에 자동 반영한다. 따로 render_on_map 을 부를 필요 없다.
- 직전 진단이 있는 상태에서 "용적률 250%면?", "10층으로 올리면?" 같은 조건 변경
  질의는 restudy_massing 을 쓴다. prediagnose 를 다시 돌리지 않는다.
- 주소가 없는 일반 법령 질문은 도구 없이 바로 답한다.
- 주소가 모호하면(예: "강남에 건물") 되묻는다. 임의로 특정 주소를 지어내지 않는다.
- 지도 표시를 켜고 끄라는 요청("지적도 꺼줘", "용도지역 보여줘", "경사도 켜줘",
  "치수선 숨겨", "주제도 다 꺼줘")은 set_map_layers 를 쓴다. 진단을 다시 돌리지 않는다.
  '다 꺼/켜'는 해당하는 항목을 모두 지정한다. **레이어 켜고 끄기는 화면에 즉시
  반영되는 조용한 동작이다 — 답변 텍스트에 '경사도와 지적도를 켜고 용도지역을 껐습니다'
  처럼 무엇을 켰/껐는지 서술하지 마라.** 사용자가 레이어 제어와 함께 다른 질문(현황·
  이격·판정·필지분할 등)을 물었으면, 레이어 변경은 일절 언급하지 말고 그 질문에만 답하라.
  레이어 제어만 단독으로 요청했을 때만 '○○ 표시를 변경했습니다'처럼 아주 짧게 알린다.
- "팝업(창) 닫아/접어/열어/펼쳐" 처럼 가능여부 판정 팝업을 여닫는 요청도 set_map_layers
  의 panel 로 처리한다(닫기=false, 열기=true). 임의로 '닫았다'고만 말하지 말고 반드시
  도구를 호출한다.
- 지도 도구 실행 요청("거리 재줘", "면적 그려줘", "높이 재줘", "측정 지워/초기화",
  "내 위치로 가줘")은 run_map_tool 을 쓴다. 측정은 실행 후 사용자가 지도를 클릭해
  잰다고 한 줄로 안내한다.
- 현재 건물의 토공 전·후 모습을 바꾸라는 요청은 set_earthwork_mode 를 쓴다.
  "토공/절토/평탄화 하기 전, 원지형 모습"은 original, "평탄화 작업해줘,
  절토·성토 적용해줘, 토공 후 모습"은 graded다. 건물을 다시 진단하거나 새로 고르지 않는다.
- 사용자가 문의한 특정 시설·구조물이, 시스템 용도 목록에 없어 다른 용도로 판정됐더라도
  개별 법령상 불가·크게 제한되면(예: 농지에 '움막'은 농지법상 불가, 농막은 신고 후 20㎡
  이하), flag_verdict_restriction 으로 팝업에 빨간 경고를 함께 띄운다. 답변 텍스트에서
  "안 된다"고만 하지 말고 이 도구로 팝업에도 표시한다.
- 인허가 절차·민원 신청 방법을 묻거나("어떻게 신청해?", "무슨 서류 필요해?", "어디에
  접수해?", "얼마나 걸려?") 진단 후 절차 설명이 필요하면, 직전 진단의
  permit_requirements 를 근거로 각 단계의 담당부서(department)·필요서류(documents)·
  법정 처리기간(processing_days)·근거법령(basis)을 순서대로 안내한다. 데이터에 없는
  값은 지어내지 말고 '관할 행정청 확인 필요'로 둔다.

답변 작성 — 기본은 '대화'다. 고정 카드는 최초 진단 1회만:
- **특정 필지에 대한 자연어 답변은 첫 문장에 진단 데이터의 전체 지번 주소를 한 번
  명시한다.** 주소 없이 "해당 필지는", "이 필지는", "선택한 필지는"으로 시작하지 마라.
  예: "충청남도 아산시 음봉면 신수리 100은 계획관리지역이며…" 주소를 추측하거나
  사용자가 입력한 다른 주소로 바꾸지 말고 parcel.jibun을 우선 사용한다.
- **최초로 "여기 건물/○○ 지을 수 있어?"를 물었을 때만** 시스템이 종합 판정 카드(용도지역·
  건폐율/용적률·규모·부담금·인허가 단계 등)를 확정 형식으로 한 번 표시한다. 이때 너는 카드
  항목을 되풀이하지 말고, 사용자 의도에 맞춘 1~3문장만 자연어로 덧붙인다.
- **그 이후의 모든 질문(역질문·후속·다른 용도·조건·절차·일반 법령)에는 카드를 다시 찍지
  말고, 수집된 데이터를 근거로 사람에게 말하듯 자연스럽게 답하라.** 매번 같은 표를 뱉으면
  사용자가 짜증낸다. 표·번호목록으로 도배하지 말고 대화체로.
  · "공장도 되나?" → recheck_use 로 다시 판정하되, 답은 "여기는 제3종일반주거라 공장은
    불가예요. 주거지역이라 제조시설은 원칙적으로 못 들어갑니다" 식의 짧은 자연어.
  · "상가는?" → 용도지역 기준으로 되는지/안 되는지 + 이유를 대화체로. (일반 상가가 불가면
    그 사실을 분명히.)
  · "여기 뭐가 문제야?" → 판정을 좌우한 핵심 제약만 골라 설명.
- **근거는 이 시스템이 수집한 데이터(직전 진단 diagnosis·regulation·site_constraints·
  conversion_charge·permit_requirements 등)를 반드시 1순위로 쓴다.** 물어본 항목의 값이
  이 데이터에 있으면 **그 수치·근거를 먼저 명시해 답한다.** 이격·건폐율·용적률·규모·부담금·
  도로 접함처럼 값이 이미 계산돼 있는 항목을 두고 "관할 행정청에 문의하라"는 일반 회피로
  빠지지 마라 — 값이 있는데 일반론으로만 답하는 것은 오답이다. 계산값이 0이거나 미수집이면
  그 사실(예: 조례 미수집으로 0m)을 근거와 함께 밝힌다.
- **정말 데이터에 없는 항목만**: 네가 아는 일반 법령·인허가 상식으로 답하되 "정확한 건 관할
  행정청·최신 조례 확인이 필요하다"는 캐비앗을 붙인다. 그것도 불확실하면 **모른다고 솔직히
  말하고**, 사용자가 대신 물어볼 만한 것(예: 구체 지번, 특정 시설·용도, 조례명)을 제안한다.
  없는 걸 지어내지 마라.
- 인허가 절차·서류·처리기간·민원 접수 방법은 대화하듯 자연스럽게 설명한다(permit_requirements
  가 있으면 근거로, 없으면 일반 절차 + 캐비앗).
- 요청 시설이 개별 법령상 불가하면(예: 농지의 움막) flag_verdict_restriction 도 호출한다.
- "이 필지에 뭘 지을 수 있어?" 열거 질의는 regulation.zone_use_overview 로 가능/조건부/불가
  용도를 자연스럽게 정리하되, 판정표 9개 대분류 개요일 뿐 별표1 전체가 아님을 밝힌다.
- 산출 규모는 '건축 가능 규모'라 부르고 '매스'는 쓰지 않는다.

도구 선택:
- 사용자가 입력한 행정구역명은 철자까지 그대로 보존한다. 예를 들어 "만송동"을
  "삼숭동"처럼 비슷한 지명으로 추측·교정하거나 바꾸지 마라. 검색 결과가 없으면
  다른 동의 결과를 대신 보여주지 말고 해당 지역에서 후보를 찾지 못했다고 답한다.
- 학교·관공서·역·상호 등 장소명 근처로 이동해 달라는 요청은 move_to_place를 쓴다.
  정확한 지번을 다시 묻기 전에 장소 검색을 먼저 실행한다.
- "초평동 157-2 필지로 이동해줘"처럼 지번으로 이동만 요청하면 prediagnose가 아니라
  move_to_parcel을 쓴다. 사용자가 말한 동·리와 번지를 임의의 시·군이나 다른 주소로
  확장하지 말고 원문 그대로 query에 전달한다.
- **인허가·건축 가능 여부와 무관한 '필지 사실' 질문**(공시지가 얼마야, 대지면적/지목/
  용도지역이 뭐야 등)은 lookup_parcel_facts 로 값을 얻어 **자연어 한두 문장**으로 답하라.
  이런 질문에 종합 판정 카드(1·2·3·4·유의사항)를 절대 만들지 마라. 직전에 진단했거나
  지도에서 선택한 필지면 좌표 없이 호출해도 된다(직전 필지를 쓴다).
- 주소가 특정된 질의("○○동 12-3에 창고 지을 수 있어?")면 prediagnose.
- **직전에 진단한 그 필지에 '다른 용도도 되냐'(예: 주택 진단 후 "공장도 지을 수 있나",
  "그럼 상가는?")를 물으면 recheck_use 를 그 용도로 호출한다.** 용도가 바뀌면 판정이
  달라지므로 반드시 새 판정 카드를 낸다. restudy_massing(규모만)이나 답변 텍스트로 때우지 마라.
- 주소 없이 지역+조건으로 후보를 찾아달라는 탐색형("양평 비도시 지역에서 농막 지을 데
  찾아줘", "○○시에 공장 지을 만한 곳 추천")이면 recommend_areas 를 쓴다. 리스트는
  시스템이 클릭 가능하게 표시하므로, 그 뒤 후보를 텍스트로 다시 나열하지 마라."""
)

TOOLS: list[dict] = [
    {
        "name": "prediagnose",
        "description": (
            "사전진단 에이전트를 호출한다. 주소 또는 위도·경도에서 필지와 용도지역을 조회한 뒤, "
            "해당 용도의 건축 허용 여부와 건폐율·용적률을 검토하고 건축 가능 규모를 산출한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "사전진단 에이전트에게 전달할 지시. 주소와 검토할 건축물 용도를 "
                        "명확히 포함시킨다. 예: '서울시 강남구 테헤란로 152에 업무시설을 "
                        "지을 수 있는지 검토'"
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "move_to_parcel",
        "description": (
            "사용자가 말한 지번 주소를 VWorld 주소 후보에서 직접 찾아 해당 필지로 이동하고 "
            "경계를 표시한다. 건축 진단은 실행하지 않는다. 예: '초평동 157-2 필지로 이동해줘'. "
            "동·리와 번지는 사용자 원문 그대로 query에 넣고 다른 지역을 추측하지 않는다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "원문 지번 주소. 예: 초평동 157-2",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "move_to_place",
        "description": (
            "학교·관공서·역·상호 등 장소명을 VWorld에서 검색해 해당 위치의 필지로 "
            "지도를 이동하고 경계를 표시한다. 예: '만송동 덕현고등학교 근처 필지로 이동해줘'. "
            "주소가 없어도 장소명과 지역명을 query에 사용한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "사용자가 말한 지역명과 장소명. 예: 만송동 덕현고등학교",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "render_on_map",
        "description": (
            "직전 사전진단 결과를 VWorld 3D 지도에 반영한다. 해당 위치로 이동하고, "
            "필지를 강조하고, 산출된 건축 가능 규모를 3D 로 세운다."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "show_map_lines",
        "description": (
            "직전 진단 필지의 특정 선을 지도에 다시 그려 보여준다. 카메라·3D 매스는 그대로 두고 "
            "선만 얹는다. 사용자가 도로 접촉·진입로·접도를 묻거나('도로 접촉 있어?', '진입로 있어?', "
            "'근접 도로와 접촉해?', '길 낼 수 있어?') 보여달라고 하면 kinds=[\"road\"] 로, "
            "'건축선 그려줘/보여줘'는 [\"building_line\"], '우수·오수 배수로/방류 어디로'는 [\"drainage\"] "
            "로 호출한다. 호출한 뒤에는 표시된 선의 값을 근거로 자연어 한두 문장 답도 함께 한다. "
            "건축선·이격선은 이격 값이 있고 건축 가능(가능/조건부) 판정일 때만 그려진다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["road", "building_line", "drainage"],
                    },
                    "description": (
                        "road=도로 접촉선(진입로·접도), building_line=건축선·이격선, "
                        "drainage=우수 배수로"
                    ),
                },
            },
            "required": ["kinds"],
        },
    },
    {
        "name": "restudy_massing",
        "description": (
            "직전 진단의 대지면적과 규제값을 그대로 쓰고 밀도만 바꿔 건축 가능 규모를 다시 산출한다. "
            "'용적률 200%면 몇 층?', '지하주차로 바꿔줘' 같은 후속 질의에 사용한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "far_target_pct": {"type": "number", "description": "적용할 용적률(%)"},
                "bcr_target_pct": {
                    "type": "number",
                    "description": "적용할 건폐율(%). 생략하면 직전 값을 유지한다.",
                },
                "parking_strategy": {
                    "type": "string",
                    "enum": ["surface", "underground", "mechanical", "mixed", "unspecified"],
                    "description": "변경할 주차방식",
                },
            },
        },
    },
    {
        "name": "set_map_layers",
        "description": (
            "지도 표시 레이어를 켜거나 끈다. 사용자가 '지적도 꺼줘', '용도지역 보여줘', "
            "'경사도 켜줘', '치수선 숨겨줘', '다 꺼줘' 처럼 지도 표시를 조절해 달라고 할 때 쓴다. "
            "지정한 항목만 바꾸고 나머지는 그대로 둔다. 진단 없이도 동작한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cadastre": {"type": "boolean", "description": "연속지적도(지적선) 표시"},
                "zoning": {"type": "boolean", "description": "용도지역 주제도(색) 표시"},
                "slope": {"type": "boolean", "description": "경사도 격자(색) 표시"},
                "dimensions": {"type": "boolean", "description": "치수선·면적 라벨 표시"},
                "panel": {
                    "type": "boolean",
                    "description": "가능여부(판정) 팝업창. true=펼치기/열기, false=접기/닫기. 접으면 치수선도 함께 숨는다.",
                },
            },
        },
    },
    {
        "name": "set_earthwork_mode",
        "description": (
            "현재 선택된 동일 건물 모델을 토공 전 원지형 상태 또는 평탄화 후 상태로 전환한다. "
            "'토공/절토 하기 전 모습', '원지형으로 보여줘'는 original, "
            "'평탄화 작업해줘', '절토·성토 적용해줘', '토공 후 모습'은 graded."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["original", "graded"],
                    "description": "original=토공 전 원지형, graded=평탄화·절성토 적용",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "run_map_tool",
        "description": (
            "지도 도구를 실행한다. '거리 재줘/거리측정'→measure_line, "
            "'면적 그려줘/면적측정'→measure_area, '높이 재줘'→measure_height, "
            "'측정 지워/초기화'→erase, '내 위치로 가줘/내 위치'→my_location. "
            "측정 도구는 실행 후 사용자가 지도를 클릭해 직접 잰다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "measure_line",
                        "measure_area",
                        "measure_height",
                        "erase",
                        "my_location",
                    ],
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "flag_verdict_restriction",
        "description": (
            "종합 판정은 가능/조건부여도, 사용자가 요청한 특정 시설·구조물이 개별 법령상 "
            "불가하거나 크게 제한될 때 팝업(판정 옆)에 빨간 경고를 표시한다. "
            "예: 농지에 '움막'은 농지법상 불가(농막은 신고 후 연면적 20㎡ 이하). "
            "시스템 용도 목록에 없어 다른 용도로 판정됐지만 실제 요청 대상은 제한되는 경우에 쓴다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "짧은 경고. 예: \"'움막' 건축불가\"",
                },
                "reason": {
                    "type": "string",
                    "description": "한 줄 근거. 예: 농지법상 허용 안 됨 · 농막은 신고 후 20㎡ 이하",
                },
            },
            "required": ["label"],
        },
    },
    {
        "name": "lookup_parcel_facts",
        "description": (
            "인허가·건축 가능 여부와 무관한 '필지 사실'만 묻는 질문에 쓴다. "
            "예: 공시지가 얼마야, 대지면적/지목/용도지역이 뭐야. 좌표(없으면 직전 진단 필지)의 "
            "기본 정보를 값으로 돌려준다. **종합 판정 카드를 만들지 않는다** — 너가 그 값으로 "
            "자연어 한두 문장으로 답하면 된다. 건축 가능 여부·규모·부담금·인허가 절차를 물으면 "
            "이게 아니라 prediagnose 를 써라."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lon": {"type": "number", "description": "필지 경도(선택). 없으면 직전 진단 필지."},
                "lat": {"type": "number", "description": "필지 위도(선택)."},
            },
        },
    },
    {
        "name": "recheck_use",
        "description": (
            "직전에 진단한 그 필지에 '다른 용도(공장·상가·주택 등)도 지을 수 있냐'를 물을 때 쓴다. "
            "예: 방금 단독주택으로 진단한 뒤 '공장도 되나?'. 같은 좌표를 그대로 재사용해 그 용도 "
            "기준으로 종합 판정·건폐율/용적률·규모를 다시 산출하고 카드를 새로 표시한다. "
            "restudy_massing(규모만 재계산)이나 flag_verdict_restriction 으로 대신하지 마라 — "
            "용도가 바뀌면 판정 자체가 달라지므로 반드시 이 도구로 새 판정을 낸다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "building_use": {
                    "type": "string",
                    "description": "새로 검토할 용도. 예: \"공장\", \"창고\", \"상가\", \"단독주택\".",
                },
            },
            "required": ["building_use"],
        },
    },
    {
        "name": "recommend_areas",
        "description": (
            "특정 주소 하나가 아니라 '○○(시·군) 비도시 지역에서 농막 지을 데 찾아줘'처럼 "
            "지역+조건으로 후보지를 찾아달라는 탐색형 질의에 쓴다. 해당 시·군 범위의 "
            "비도시(관리·농림·자연환경보전·녹지) 용도지역을 스캔해 대표 지점 몇 곳을 "
            "지번·지목과 함께 리스트로 돌려준다. 사용자는 리스트 항목을 눌러 그 지점으로 "
            "이동해 개별 진단을 실행한다. 이 도구는 리스트만 만들며, 각 지점의 최종 판정은 "
            "클릭 후 prediagnose 가 한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "탐색 대상 시·군·구. 예: \"경기도 양평군\", \"양평\"",
                },
                "building_use": {
                    "type": "string",
                    "description": "지으려는 시설. 예: \"농막\", \"단독주택\", \"창고\". 없으면 빈 문자열.",
                },
            },
            "required": ["region"],
        },
    },
]


def _normalize_numbered_headings(text: str) -> str:
    """번호 제목의 순번과 Markdown 문단 간격을 바로잡는다.

    모델이 ``앞 문장. **1. 제목**``처럼 한 줄에 이어 쓰더라도 각 절이 독립된
    문단으로 렌더링되게 한다.
    """
    index = 0

    def replace(match: re.Match) -> str:
        nonlocal index
        index += 1
        title = match.group(1).strip()
        return f"\n\n**{index}. {title}**\n\n"

    normalized = re.sub(
        r"\s*\*\*\s*\d+\.\s*([^*\n]+?)\s*\*\*",
        replace,
        str(text or ""),
    )
    # 절 내부의 고정 항목도 앞 문장에 붙지 않게 한 줄씩 분리한다.
    # Gemini가 ``- **담당부서:**``처럼 불릿으로 쓰더라도 하이픈만 빈 줄에
    # 남기지 않고 동일한 일반 항목 형식으로 통일한다.
    normalized = re.sub(
        r"(?:[ \t]*[-*][ \t]*)?\s*\*{0,2}"
        r"(담당부서|제출서류|근거법령|내용|설명):\*{0,2}\s*",
        r"\n\n**\1:** ",
        normalized,
    )
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def _strip_internal_field_names(text: str) -> str:
    """LLM이 답변에 흘린 내부 데이터 필드명(영어 snake_case)을 제거한다.

    front_setback_m·zone_use_overview 같은 필드명이 사용자에게 노출되면 안 된다.
    한국어 문장에는 밑줄이 들어간 영어 토큰이 없으므로, 밑줄 포함 영어 토큰과
    그 앞의 괄호 병기를 안전하게 걷어낸다.
    """
    if not text:
        return text
    # "…이격(front_setback_m)" / "(regulation.zone_use_overview)" 괄호 병기 → 괄호째 제거
    text = re.sub(
        r"\s*[\(（]\s*[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*\s*[\)）]",
        "",
        text,
    )
    # 점으로 이어붙인 필드 경로(regulation.zone_use_overview, site_constraints.front_setback_m)
    text = re.sub(r"\b[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", "", text)
    # 본문에 그대로 노출된 snake_case 필드명 제거
    text = re.sub(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+", "", text)
    # 제거하면서 생긴 이중 공백을 정리하되 Markdown 줄바꿈은 보존한다.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+([,.·)\]）])", r"\1", text)
    text = re.sub(r"^[\s,.·)\]）]+", "", text)
    return text.strip()


def _collected_forest_evidence(diagnosis: dict | None) -> str:
    """임야 진단에서 실제로 수집된 지형·임상도 참고값을 사용자 문장으로 만든다."""
    diagnosis = diagnosis or {}
    parcel = diagnosis.get("parcel") or {}
    if parcel.get("jimok") not in {"임", "임야"}:
        return ""
    conversion = diagnosis.get("land_conversion") or {}
    terrain = conversion.get("terrain") or {}
    inventory = conversion.get("forest_inventory") or []
    parts: list[str] = []
    if terrain.get("status") == "REFERENCE_AVAILABLE":
        parts.append(
            f"{terrain.get('source') or '30m DEM'} 자료에서 평균경사도 "
            f"{float(terrain.get('slope_mean_deg') or 0):.1f}°, 최대경사도 "
            f"{float(terrain.get('slope_max_deg') or 0):.1f}°, 표고 "
            f"{float(terrain.get('elevation_min_m') or 0):.1f}~"
            f"{float(terrain.get('elevation_max_m') or 0):.1f}m"
            f"(평균 {float(terrain.get('elevation_mean_m') or 0):.1f}m)가 확인됩니다."
        )
    if inventory:
        top = inventory[0]
        attributes = [
            top.get("forest_type"),
            top.get("species"),
            top.get("age_class"),
            top.get("diameter_class"),
            f"수관밀도 {top['density']}" if top.get("density") else "",
            top.get("stand_height"),
        ]
        description = " · ".join(str(value) for value in attributes if value)
        share = (
            f"필지의 {float(top.get('share_pct') or 0):.1f}%"
            if top.get("share_pct") is not None
            else "필지 내"
        )
        year = f", {top.get('updated_year')}년 갱신" if top.get("updated_year") else ""
        parts.append(f"1:5,000 임상도에서는 {share}가 {description}로 확인됩니다{year}.")
    if parts:
        parts.append(
            "다만 이 값은 사전검토 참고자료이므로 허가 심사용 확정값은 "
            "경사도·표고조사서와 산림조사서로 확인해야 합니다."
        )
    return " ".join(parts)


def _ensure_collected_forest_evidence(
    text: str, diagnosis: dict | None, *, relevant: bool = True
) -> str:
    """Gemini가 수집값을 일반론으로 뭉갠 경우 실제 근거 문장을 보충한다."""
    if not relevant:
        return text
    evidence = _collected_forest_evidence(diagnosis)
    if not evidence:
        return text
    conversion = (diagnosis or {}).get("land_conversion") or {}
    terrain = conversion.get("terrain") or {}
    inventory = conversion.get("forest_inventory") or []
    has_terrain = not terrain or (
        str(terrain.get("slope_mean_deg")) in text
        and str(terrain.get("slope_max_deg")) in text
    )
    species = str((inventory[0] if inventory else {}).get("species") or "")
    has_inventory = not species or species in text
    return text if has_terrain and has_inventory else f"{text.rstrip()} {evidence}".strip()


def _query_evidence(diagnosis: dict | None, query: str) -> str:
    """질문 주제에 대응하는 진단의 실제 값을 짧은 근거 문장으로 변환한다."""
    diagnosis = diagnosis or {}
    if not diagnosis:
        return ""
    parcel = diagnosis.get("parcel") or {}
    regulation = diagnosis.get("regulation") or {}
    mass = diagnosis.get("massing") or {}
    site = diagnosis.get("site_constraints") or {}
    road = diagnosis.get("road_access") or {}
    parts: list[str] = []

    if re.search(r"면적|넓이|몇\s*평", query) and parcel.get("area_m2") is not None:
        area = float(parcel["area_m2"])
        parts.append(f"필지면적은 약 {area / 3.3058:,.0f}평({area:,.0f}㎡)입니다.")
    if re.search(r"공시지가|땅값|지가", query) and parcel.get("jiga_won_per_m2") is not None:
        unit = float(parcel["jiga_won_per_m2"])
        parts.append(f"개별공시지가는 평당 약 {unit * 3.3058:,.0f}원(㎡당 {unit:,.0f}원)입니다.")
    if re.search(r"용도지역|지역지구|용도지구|토지이용", query):
        zone = regulation.get("zone")
        districts = regulation.get("districts") or (diagnosis.get("land_use") or {}).get("districts") or []
        if zone:
            parts.append(f"용도지역은 {zone}입니다.")
        if districts:
            parts.append(f"확인된 용도지구·규제는 {', '.join(map(str, districts))}입니다.")
    if re.search(r"건폐율|용적률", query):
        bcr = regulation.get("bcr_max_pct")
        far = regulation.get("far_max_pct")
        if bcr is not None or far is not None:
            parts.append(f"적용 상한은 건폐율 {bcr}%·용적률 {far}%입니다.")
    if re.search(r"건축면적|연면적|몇\s*층|층수|규모", query) and mass:
        values = []
        if mass.get("building_area_m2") is not None:
            values.append(f"건축면적 {float(mass['building_area_m2']):,.0f}㎡")
        if mass.get("gross_floor_area_m2") is not None:
            values.append(f"연면적 {float(mass['gross_floor_area_m2']):,.0f}㎡")
        if mass.get("floors") is not None:
            values.append(f"신축 추정 {mass['floors']}층")
        if values:
            parts.append("진단상 " + "·".join(values) + "입니다.")
    if re.search(r"도로|접도|맹지|진입로|도로폭|접촉", query) and road:
        summary = road.get("summary") or road.get("reason")
        if summary:
            parts.append(str(summary).rstrip(" .") + ".")
        elif road.get("status"):
            parts.append(f"지적도 도로 접촉 판정은 {road.get('status')}입니다.")
    if re.search(r"이격|후퇴|건축선|정북|일조", query) and site:
        values = []
        for label, key in (
            ("전면 건축선 이격", "front_setback_m"),
            ("인접 대지경계 이격", "adjacent_setback_m"),
            ("정북 일조 이격", "north_setback_m"),
        ):
            if site.get(key) is not None:
                values.append(f"{label} {site[key]}m")
        if values:
            parts.append("계산값은 " + "·".join(values) + "입니다.")
    if re.search(r"주차|주차대수|주차면적", query):
        parking = site.get("parking") or {}
        if parking.get("estimated") and parking.get("spaces") is not None:
            parts.append(f"현재 용도 기준 주차 필요량은 {parking['spaces']}대로 산정됐습니다.")
        else:
            parts.append("주차 방식과 세부 용도가 확정되지 않아 주차대수는 아직 산정되지 않았습니다.")
    if re.search(r"농지보전부담금|대체산림(?:자원)?조성비", query):
        charge = diagnosis.get("conversion_charge") or {}
        if charge.get("estimated_won") is not None:
            parts.append(
                f"현재 전용예상면적 {float(charge.get('area_m2') or 0):,.0f}㎡ 기준 "
                f"참고액은 약 {int(charge['estimated_won']):,}원입니다."
            )
    if re.search(r"개발부담금", query):
        charge = diagnosis.get("development_charge") or {}
        if charge.get("reason"):
            parts.append(str(charge["reason"]).rstrip(" .") + ".")
    if re.search(r"인허가|허가\s*요건|허가\s*절차|심의|서류|접수|처리기간|어디.*문의", query):
        permit = diagnosis.get("permit_requirements") or {}
        items = permit.get("items") or []
        if items:
            names = [str(item.get("name")) for item in items if item.get("name")]
            parts.append(f"이 필지의 예상 인허가 순서는 {' → '.join(names)}입니다.")

    forest_relevant = bool(
        re.search(r"산지|산림|임야|경사도|표고|입목|임상도", query)
        or (
            parcel.get("jimok") in {"임", "임야"}
            and re.search(r"심의|인허가|허가.*(?:요건|절차|조건)", query)
        )
    )
    if forest_relevant:
        forest = _collected_forest_evidence(diagnosis)
        if forest:
            parts.append(forest)
    return " ".join(dict.fromkeys(part for part in parts if part))


def _ensure_query_evidence(text: str, diagnosis: dict | None, query: str) -> str:
    """관련 실제 값이 하나도 없는 일반론 응답에 결정적 근거를 보충한다."""
    evidence = _query_evidence(diagnosis, query)
    if not evidence:
        return text
    diagnosis = diagnosis or {}
    required_tokens: list[str] = re.findall(r"\d+(?:\.\d+)?", evidence)
    if re.search(r"용도지역|용도지구|토지이용", query):
        zone = ((diagnosis.get("regulation") or {}).get("zone") or "")
        if zone:
            required_tokens.append(str(zone))
    if re.search(r"도로|접도|맹지|진입로", query):
        road = diagnosis.get("road_access") or {}
        if road.get("summary"):
            required_tokens.append(str(road["summary"]).split(":")[0])
    if re.search(r"인허가|허가\s*요건|허가\s*절차|심의|서류|접수|처리기간", query):
        items = ((diagnosis.get("permit_requirements") or {}).get("items") or [])
        permit_names = [str(item["name"]) for item in items if item.get("name")]
        if permit_names and not all(name in text for name in permit_names):
            return f"{text.rstrip()} {evidence}".strip()
        required_tokens.extend(permit_names)
    if any(token and token in text for token in required_tokens):
        return text
    return f"{text.rstrip()} {evidence}".strip()


def _requested_map_lines(query: str) -> list[str]:
    """자연어에서 지도에 그려 보여줄 선 종류를 뽑는다.
    도로접촉(진입로·접도·길 낼 때 필요한 접도), 건축선·이격선, 우수·오수 배수로.
    사용자가 '도로 접촉 있어?/진입로 있어?/건축선 그려줘'처럼 물으면 해당 선을 지도에 얹는다.
    """
    kinds: list[str] = []
    if re.search(r"도로\s*접|접도|진입로|맹지|근접\s*도로|길\s*(?:낼|내|있|만들)", query):
        kinds.append("road")
    if re.search(r"건축선|이격|후퇴선|대지\s*안의?\s*공지", query):
        kinds.append("building_line")
    if re.search(r"배수로|방류|우수|오수", query):
        kinds.append("drainage")
    return kinds


def _is_line_only_query(query: str) -> bool:
    """'선만' 묻는 질문인지. 도로접촉·건축선·이격·배수로 선을 청하되, 건축 가능여부·규모를
    함께 묻지 않는 질문은 종합판정 카드·3D 매스·팝업 없이 선만 그린다.
    """
    if not _requested_map_lines(query):
        return False
    # 가능여부·규모를 함께 물으면 정식 진단(카드)으로 보낸다.
    if re.search(
        r"지을\s*수|지어|신축|건축\s*(?:가능|불가|할)|가능(?:해|한|합니|하나|할까|여부|성|한지)|"
        r"규모|건폐율|용적률|층수|얼마나|몇\s*층|평수|지을까|되나요?|될까",
        query,
    ):
        return False
    return True


def _deterministic_verdict_judgment(diagnosis: dict | None) -> str:
    """단일 용도 검토 의견의 LLM 지연·실패 시 실제 진단값으로 만드는 요약."""
    diagnosis = diagnosis or {}
    parcel = diagnosis.get("parcel") or {}
    regulation = diagnosis.get("regulation") or {}
    request = diagnosis.get("request") or {}
    address = (
        parcel.get("jibun")
        or (diagnosis.get("location") or {}).get("matched_address")
        or "선택한 필지"
    )
    use = request.get("building_use") or regulation.get("building_use") or "요청 용도"
    verdict = diagnosis.get("verdict") or regulation.get("verdict") or "unknown"
    conclusion = {
        "allowed": "현재 건축 가능합니다",
        "conditional": "현재 조건부 가능합니다",
        "not_allowed": "현재 건축이 불가합니다",
        "unknown": "현재 추가 확인이 필요합니다",
    }.get(verdict, "현재 추가 확인이 필요합니다").rstrip(".")

    evidence: list[str] = []
    zone = regulation.get("zone")
    if zone:
        evidence.append(f"용도지역은 {zone}입니다.")
    conversion = diagnosis.get("land_conversion") or {}
    if conversion.get("summary"):
        evidence.append(_as_sentence(
            _natural_conversion_summary(conversion["summary"], parcel)
        ))
    road = diagnosis.get("road_access") or {}
    if road.get("summary"):
        evidence.append(_as_sentence(str(road["summary"])))
    constraints = regulation.get("constraints") or []
    evidence.extend(
        _as_sentence(_human_constraint(item)) for item in constraints[:2] if item
    )
    evidence = list(dict.fromkeys(item for item in evidence if item))

    first = f"{address} 필지의 {use}은 {conclusion}."

    if verdict == "conditional":
        permit_names = [
            str(item.get("name"))
            for item in ((diagnosis.get("permit_requirements") or {}).get("items") or [])
            if item.get("name")
        ]
        procedures = "·".join(permit_names[:3])
        zone_basis = (
            f"{zone}에서 {use}이 허용되는 범위에 해당하지만"
            if zone
            else f"{use}의 용도 허용 범위에는 들어가지만"
        )
        reasons = " ".join(evidence[1:4] if zone and evidence else evidence[:3])
        second = (
            f"{zone_basis}, {reasons} 관련 법령에 따른 허가·협의 요건의 "
            "충족 여부를 확인해야 하므로 현재 조건부 가능합니다."
            if reasons
            else f"{zone_basis}, 세부 허용기준과 개별 규제에 따른 허가·협의 "
            "요건의 충족 여부를 확인해야 하므로 현재 조건부 가능합니다."
        )
        third = (
            f"건축하려면 {procedures} 절차를 거쳐야 하며, 관계기관 협의와 필요한 "
            "심의를 포함한 허가 과정에서 용도·전용·접도·이격 등 적용 조건을 확인합니다. "
            "서로 다른 법령의 요건이 함께 적용되는 경우에는 각 법령의 허가·협의 조건을 "
            "모두 충족해야 최종 허가가 가능합니다."
            if procedures
            else (
                "건축하려면 관계기관 협의와 필요한 심의를 포함한 허가 절차를 거쳐야 하며, "
                "서로 다른 법령의 요건이 함께 적용되는 경우에는 각 법령의 허가·협의 조건을 "
                "모두 충족해야 최종 허가가 가능합니다."
            )
        )
    elif verdict == "not_allowed":
        second = (
            "확인된 필지 자료를 보면 " + " ".join(evidence[:4])
            if evidence
            else "현재 연결된 용도 허용 기준에서 제한 사유가 확인됐습니다."
        )
        third = "현재 확인된 제한 사유가 해소되거나 법령상 예외 허용시설에 해당하지 않으면 진행하기 어렵습니다."
    elif verdict == "unknown":
        second = (
            "확인된 필지 자료를 보면 " + " ".join(evidence[:4])
            if evidence
            else "현재 연결된 자료만으로는 판정에 필요한 조건을 모두 확인하지 못했습니다."
        )
        third = "미수집 규제와 관할 조례를 확인하기 전에는 허가 가능으로 확정할 수 없습니다."
    else:
        second = (
            "확인된 필지 자료를 보면 " + " ".join(evidence[:4])
            if evidence
            else "현재 연결된 필지 자료와 용도 허용 기준을 적용한 결과입니다."
        )
        third = "다만 표시된 규모는 법정 상한을 이용한 개념값이므로 실제 설계·허가 과정에서 줄어들 수 있습니다."
    return f"{first} {second} {third}"


def _as_sentence(text: str) -> str:
    """구조화 값에서 온 설명을 다른 문장과 안전하게 이어 붙인다."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "!", "?")) else cleaned + "."


def _limit_review_length(text: str, *, max_sentences: int = 4) -> str:
    """검토 의견이 상세 보고서를 반복하지 않도록 문장 수를 제한한다."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    # 토큰 한도에서 잘린 미완성 꼬리 문장은 사용자에게 노출하지 않는다.
    if cleaned and not cleaned.endswith((".", "!", "?")):
        last_end = max(cleaned.rfind("."), cleaned.rfind("!"), cleaned.rfind("?"))
        if last_end >= 0:
            cleaned = cleaned[: last_end + 1]
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned)
        if sentence.strip()
    ]
    if len(sentences) <= max_sentences:
        return cleaned
    # 결론·최종 허가 조건은 대개 마지막 문장에 있으므로 앞의 핵심 3문장과
    # 마지막 문장을 보존한다.
    return " ".join(sentences[: max_sentences - 1] + [sentences[-1]])


def _human_constraint(item) -> str:
    """구조화 규제 객체를 내부 필드명 없이 자연스러운 한국어로 바꾼다."""
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("label") or "").strip()
        note = str(item.get("note") or item.get("reason") or "").strip()
        if name and note:
            if "가축분뇨법상 축사 제한" in note:
                return (
                    f"{name}에서는 가축분뇨법에 따라 축사가 제한되므로, "
                    "축산 관련 시설인 경우 별도 확인이 필요합니다"
                )
            note = note.replace(" — ", ". ")
            note = re.sub(
                r"(.+?)이면\s*확인\s*필요$",
                r"\1인 경우 확인이 필요합니다",
                note,
            )
            return f"{name}: {note}".rstrip(" .")
        return (name or note).rstrip(" .")
    return str(item or "").strip().rstrip(" .")


def _natural_conversion_summary(summary: str, parcel: dict) -> str:
    """전용 판정 요약의 논리관계를 사용자 문장으로 명확히 표현한다."""
    summary = str(summary or "").strip().rstrip(" .")
    summary = summary.replace(
        "산지전용허가와 대체산림자원조성비 검토가 필요합니다",
        "산지전용허가가 필요하고 대체산림자원조성비가 부과될 수 있으나, "
        "실제 전용면적과 사업 유형에 따라 감면 또는 면제될 수 있습니다",
    ).replace(
        "농지전용허가·협의와 농지보전부담금 검토가 필요합니다",
        "농지전용허가·협의가 필요하고 농지보전부담금이 부과될 수 있으나, "
        "실제 전용면적과 사업 유형에 따라 감면 또는 면제될 수 있습니다",
    )
    if summary.startswith("농업진흥지역 중첩은 확인되지 않았지만"):
        jimok = parcel.get("jimok")
        land_label = "농지이므로" if jimok in {"전", "답", "과"} else "해당 토지의 전용을 위해"
        remainder = summary.split("않았지만", 1)[1].strip()
        return f"농업진흥지역과 중첩되지는 않지만, {land_label} {remainder}"
    forest_overlap = re.match(
        r"(.+?산지)\s+(\d+(?:\.\d+)?)%\s+중첩입니다\.\s*(.*)",
        summary,
    )
    if forest_overlap:
        forest_name, share_text, remainder = forest_overlap.groups()
        share = float(share_text)
        subject = (
            f"필지 전체가 {forest_name}와 중첩되어"
            if share >= 99.95
            else f"필지의 {share:g}%가 {forest_name}와 중첩되어"
        )
        return f"{subject} {remainder}".strip()
    return summary


def _all_uses_verdict_judgment(diagnosis: dict | None) -> str:
    """모든 용도 검토는 현황·조건부 사유·필요 절차만 요약한다."""
    diagnosis = diagnosis or {}
    parcel = diagnosis.get("parcel") or {}
    regulation = diagnosis.get("regulation") or {}
    address = (
        parcel.get("jibun")
        or (diagnosis.get("location") or {}).get("matched_address")
        or "선택한 필지"
    )
    zone = regulation.get("zone")
    overview = regulation.get("zone_use_overview") or {}
    allowed_count = len(overview.get("allowed") or [])
    conditional_count = len(overview.get("conditional") or [])
    has_possible_use = allowed_count + conditional_count > 0
    possible_uses = list(dict.fromkeys(
        list(overview.get("allowed") or [])
        + list(overview.get("conditional") or [])
    ))
    possible_examples = "·".join(possible_uses[:4])
    if len(possible_uses) > 4:
        possible_examples += " 등"
    conditions: list[str] = []
    conversion = diagnosis.get("land_conversion") or {}
    if conversion.get("summary"):
        conditions.append(_as_sentence(
            _natural_conversion_summary(conversion["summary"], parcel)
        ))
    road = diagnosis.get("road_access") or {}
    if (
        road.get("summary")
        and road.get("status") not in {"CADASTRAL_CONTACT", "CONFIRMED"}
    ):
        conditions.append(_as_sentence(str(road["summary"])))
    conditions.extend(
        _as_sentence(_human_constraint(item))
        for item in (regulation.get("constraints") or [])[:2]
        if item and "가축사육제한" not in str(item)
    )
    conditions = list(dict.fromkeys(item for item in conditions if item))

    permit_names = [
        str(item.get("name"))
        for item in ((diagnosis.get("permit_requirements") or {}).get("items") or [])
        if item.get("name")
    ]
    procedures = "·".join(permit_names[:4])

    # 협소·기존건물 등 실질 배치 불가면 '조건부 가능' 대신 배치 불가 결론을 낸다.
    if diagnosis.get("placement_restricted") or (
        (regulation.get("map_presentation") or {}).get("verdict") == "not_allowed"
    ):
        existing = diagnosis.get("existing_buildings") or {}
        cause = (
            f"기존 건축물 {existing.get('count')}건이 있어 멸실·해체를 선행하지 않으면"
            if existing.get("has_buildings")
            else "법정 최소 대지면적에 못 미치는 협소 대지라"
        )
        return (
            f"{address} 필지는 {zone or '해당 용도지역'}으로 용도지역상으로는 "
            f"{possible_examples or '일부 용도'}가 조건부이나, {cause} 실질적으로 신축 "
            "배치가 불가합니다. 신축하려면 기존 건축물의 소유권·임대차를 확인해 해체·멸실 "
            "정리 또는 합필 등으로 배치 요건을 먼저 갖춘 뒤에야 개별 용도의 인허가 절차를 "
            "검토할 수 있습니다."
        )
    if zone and has_possible_use:
        first = (
            f"{address} 필지는 {zone}입니다. 건축물 용도 중 {possible_examples}은 "
            "건축 가능하거나 조건부로 검토할 수 있습니다. "
            f"다만 {' '.join(conditions[:2]) if conditions else '개별 규제를 확인해야 합니다.'} "
            "따라서 현재 조건부 가능합니다."
        )
    else:
        first = (
            f"{address} 필지는 현재 확인된 토지이용과 개별 규제를 종합하면 "
            f"{' '.join(conditions[:2]) if conditions else '추가 확인이 필요합니다.'}"
        )
    second = (
        f"건축하려면 먼저 건축물 용도를 정한 뒤 {procedures} 절차를 진행해야 합니다."
        if procedures
        else "건축하려면 먼저 건축물 용도를 정한 뒤 해당 용도의 인허가 절차를 진행해야 합니다."
    )
    third = (
        "관계기관 협의와 필요한 심의를 거쳐 서로 다른 법령의 허가·협의 조건을 "
        "모두 충족해야 최종 허가가 가능합니다."
    )
    return f"{first} {second} {third}"


def _same_parcel_address(address_in_query: str, diagnosis: dict | None) -> bool:
    """질의의 전체 지번이 직전 진단 필지와 같은지 공백 차이를 무시해 비교한다."""
    if not address_in_query or not diagnosis:
        return False
    query_key = re.sub(r"\s+", "", address_in_query)
    candidates = [
        (diagnosis.get("request") or {}).get("address"),
        (diagnosis.get("parcel") or {}).get("jibun"),
        (diagnosis.get("location") or {}).get("matched_address"),
    ]
    return bool(
        query_key
        and any(
            query_key == re.sub(r"\s+", "", str(candidate or ""))
            for candidate in candidates
        )
    )


def _target_in_query(target: str, query: str) -> bool:
    """LLM이 낸 이동 대상 주소(target_address)가 실제 질문에 근거하는지 확인한다.

    의도 판단은 제미나이가 하지만, 주소 없는 질문에서 엉뚱한 주소를 지어내는(환각)
    경우가 있어 결정적 가드를 둔다: 지번/번지면 '장소 토큰 + 숫자'가, 번지 없는
    주소(도로명·POI)면 마지막 장소 토큰이 질문 원문(공백 무시)에 실제로 있어야
    이동을 허용한다. 없으면 환각으로 보고 이동하지 않는다.
    """
    q = re.sub(r"\s+", "", query or "")
    t = (target or "").strip()
    if not q or not t:
        return False
    m = re.search(r"([가-힣]{2,})\s*(?:산\s*)?(\d+(?:-\d+)?)\s*$", t)
    if m:
        place, num = m.group(1), re.sub(r"\s", "", m.group(2))
        return place in q and num in q
    toks = [re.sub(r"\s+", "", w) for w in t.split() if len(w) >= 2]
    return bool(toks) and toks[-1] in q


def _same_parcel_coordinate(
    coordinate_match: re.Match | None, diagnosis: dict | None
) -> bool:
    """지도 선택 좌표가 직전 진단 위치와 같은 필지로 볼 수 있는지 확인한다."""
    if not coordinate_match or not diagnosis:
        return False
    location = diagnosis.get("location") or {}
    if location.get("lon") is None or location.get("lat") is None:
        return False
    lon, lat = map(float, coordinate_match.groups())
    return (
        abs(float(location["lon"]) - lon) < 0.001
        and abs(float(location["lat"]) - lat) < 0.001
    )


def _asks_possible_use_list(user_query: str) -> bool:
    """가능 용도/모델의 *목록*을 명시적으로 요구한 질문만 식별한다.

    규제 적용 여부, 뜻, 이유, 절차 같은 질문은 단어에 ``가능``이나 ``시설``이
    섞여 있어도 목록 요청이 아니다. 그 의미 해석은 최신 진단 데이터를 받은
    언어모델에 맡긴다.
    """
    query = re.sub(r"\s+", "", user_query or "").lower()
    if not query:
        return False
    if re.search(
        r"건축물대장|대장조회|건축선|이격거리|공시지가|건폐율|용적률|"
        r"뜻|의미|정의|개념|왜|이유|절차|기준|근거|확인|필요|조치|서류|부서|"
        r"제한|규제|구역|지구|지역|중첩|해당|적용",
        query,
    ):
        return False
    if re.fullmatch(r"(?:3d)?모델(?:이란|은|이)?(?:뭐야|무엇이야)\??", query):
        return False
    # '모델'이라는 낱말 자체는 버튼 의도가 아니다. 모델의 표시/숨김은 앞선
    # 지도 제어 규칙이 처리하고, 여기서는 대체 모델의 조회·선택 요청만 받는다.
    if re.search(
        r"(?:다른|가능|허용|대체|추천).*(?:모델|3d건물|건축물|건물|용도)"
        r"|(?:모델|3d건물|건축물|건물|용도).*(?:목록|종류|선택|추천|가능|허용)",
        query,
    ):
        return True
    return bool(
        re.search(
            r"(?:가능|허용(?:되는)?|지을수있는|건축할수있는)(?:한)?"
            r"(?:건물|건축물|시설|용도).*(?:뭐|무엇|어떤|종류|목록|알려|보여|추천)",
            query,
        )
        or re.search(
            r"(?:뭐|무엇|어떤|종류|목록).*(?:건물|건축물|시설|용도).*"
            r"(?:가능|허용|지을|건축)",
            query,
        )
        or re.search(
            r"(?:건축할수있는거|지을거|지을수있는거|뭘지을수|무엇을지을수|"
            r"어떤용도가(?:돼|되|가능)|허용(?:되는)?건축물|건물있어|건축있어|"
            r"건축가능(?:한)?(?:게|거|뭐|무엇)|다른건물도?가능)",
            query,
        )
    )


def _model_options_for_diagnosis(
    diagnosis: dict | None,
    *,
    include_alternatives: bool = False,
    allow_alternative_verdict: bool = False,
) -> list[dict]:
    """구조화 판정에서 실제로 준비된 3D 모델 버튼만 만든다.

    구조화 용도 판정표에서 허용·조건부인 용도 가운데 실제로 준비된
    3D 모델만 낸다. 주소나 PNU별 예외는 두지 않는다.
    """
    diagnosis = diagnosis or {}
    regulation = diagnosis.get("regulation") or {}
    presentation = regulation.get("map_presentation") or {}
    verdict = presentation.get("verdict") or regulation.get("verdict") or diagnosis.get("verdict")
    massing = diagnosis.get("massing") or {}
    if (
        (verdict == "not_allowed" or not massing)
        and not allow_alternative_verdict
    ):
        return []
    if massing and (
        massing.get("exceeds_far_limit")
        or massing.get("layout_feasible") is False
    ):
        return []
    if diagnosis.get("placement_restricted"):  # 협소·기존건물 → 배치 제한, 모델 숨김
        return []

    overview = regulation.get("zone_use_overview") or {}
    possible = set(overview.get("allowed") or []) | set(overview.get("conditional") or [])
    requested_use = str((diagnosis.get("request") or {}).get("building_use") or "시설물")
    prepared = [
        ("단독주택", "단독주택형", "detached", "법정 가능 층수 반영 · 주택 비례"),
        ("공동주택", "공동주택형", "lowrise", "건축 가능 영역 최대 활용"),
        ("공장", "공장 모델", "factory", "산업 외피 · 롤러셔터"),
        ("제1종근린생활시설", "상가 모델", "commercial", "쇼윈도 · 유리 출입구"),
        ("제2종근린생활시설", "상가 모델", "commercial", "쇼윈도 · 유리 출입구"),
        ("판매시설", "상가 모델", "commercial", "쇼윈도 · 유리 출입구"),
        ("창고시설", "창고 모델", "warehouse", "산업 외피 · 롤러셔터"),
    ]
    floors = massing.get("floors")
    options: list[dict] = []
    used_actions: set[str] = set()
    for use, label, model, detail in prepared:
        if use not in possible:
            continue
        if (
            not include_alternatives
            and requested_use != "시설물"
            and requested_use != use
        ):
            continue
        action = f"housing:{model}"
        if action in used_actions:
            continue
        used_actions.add(action)
        options.append({
            "label": f"{floors}층 {label}" if floors else label,
            "detail": detail,
            "action": action,
        })
    return options


class Orchestrator:
    """대화 1건(=세션)의 상태를 들고 있는 오케스트레이터."""

    def __init__(self, client) -> None:
        self.client = client
        self.messages: list[dict] = []
        self.diagnosis: dict | None = None   # 직전 사전진단 결과
        self.recommendations: dict | None = None  # 직전 지역 추천 결과
        self._diag_shown = False             # 이번 턴에 코드가 진단 카드를 냈는지
        self._last_query = ""                # 직전 사용자 질의(추천 농막류 감지용)
        self.selected_parcel: dict | None = None
        self._selection_changed = False
        # 필지별 상태를 독립 보관한다. A→B→A로 이동하면 A의 진단·대화 맥락을
        # 복원해 후속 질문으로 이어가고, 처음 보는 PNU만 새 상담으로 시작한다.
        self._diagnosis_by_pnu: dict[str, dict] = {}
        self._messages_by_pnu: dict[str, list[dict]] = {}
        self._diag_shown_by_pnu: dict[str, bool] = {}
        # 자연어 후속 질문이 짧아도 제미나이가 무엇을 이어 묻는지 알 수 있도록
        # 필지별 UI·대화 상태를 구조화해 보관한다.
        self._context_by_pnu: dict[str, dict] = {}

    def snapshot_state(self) -> dict:
        """타임아웃 재시도 전의 필지별 상담 상태를 복제한다."""
        return copy.deepcopy({
            "messages": self.messages,
            "diagnosis": self.diagnosis,
            "recommendations": self.recommendations,
            "_diag_shown": self._diag_shown,
            "_last_query": self._last_query,
            "selected_parcel": self.selected_parcel,
            "_selection_changed": self._selection_changed,
            "_diagnosis_by_pnu": self._diagnosis_by_pnu,
            "_messages_by_pnu": self._messages_by_pnu,
            "_diag_shown_by_pnu": self._diag_shown_by_pnu,
            "_context_by_pnu": self._context_by_pnu,
        })

    def restore_state(self, snapshot: dict) -> None:
        """실패한 실행에서 변경된 상태를 버리고 질문 직전 상태로 되돌린다."""
        restored = copy.deepcopy(snapshot)
        for key, value in restored.items():
            setattr(self, key, value)
        # 이전 버전 세션 파일도 안전하게 복원한다.
        if not hasattr(self, "_context_by_pnu"):
            self._context_by_pnu = {}

    def _active_pnu(self) -> str:
        return (
            ((self.diagnosis or {}).get("parcel") or {}).get("pnu")
            or (self.selected_parcel or {}).get("pnu")
            or ""
        )

    def conversation_context(self) -> dict:
        pnu = self._active_pnu()
        return self._context_by_pnu.setdefault(pnu, {}) if pnu else {}

    def update_conversation_context(self, **values) -> None:
        context = self.conversation_context()
        for key, value in values.items():
            if value is not None:
                context[key] = value

    def set_selected_parcel(
        self,
        *,
        lon: float,
        lat: float,
        address: str = "",
        pnu: str = "",
        from_mouse: bool = False,
    ) -> None:
        diagnosed_pnu = ((self.diagnosis or {}).get("parcel") or {}).get("pnu") or ""
        selected_pnu = (self.selected_parcel or {}).get("pnu") or ""
        if from_mouse:
            parcel_changed = bool(
                pnu and pnu != (diagnosed_pnu or selected_pnu)
            )
            if parcel_changed:
                # 떠나는 필지의 대화 상태를 먼저 저장한다.
                if diagnosed_pnu:
                    self._diagnosis_by_pnu[diagnosed_pnu] = self.diagnosis
                    self._messages_by_pnu[diagnosed_pnu] = list(self.messages)
                    self._diag_shown_by_pnu[diagnosed_pnu] = self._diag_shown

                cached = self._diagnosis_by_pnu.get(pnu)
                if cached is not None:
                    # 이미 진단했던 필지로 돌아온 경우: 해당 필지 상태를 복원해
                    # 다음 질문을 후속 질문으로 처리한다.
                    self.diagnosis = cached
                    self.messages = list(self._messages_by_pnu.get(pnu, []))
                    self._diag_shown = self._diag_shown_by_pnu.get(pnu, True)
                    self._selection_changed = False
                else:
                    # 처음 보는 필지만 새 진단으로 시작한다.
                    self._selection_changed = True
                    self.diagnosis = None
                    self.messages = []
                    self._diag_shown = False
                self.recommendations = None
            else:
                self._selection_changed = False
        elif pnu and diagnosed_pnu == pnu:
            self._selection_changed = False
        self.selected_parcel = {
            "lon": float(lon),
            "lat": float(lat),
            "address": address,
            "pnu": pnu,
        }
        logger.info(
            "parcel_state from_mouse=%s diagnosed_pnu=%s selected_pnu=%s "
            "changed=%s restored=%s",
            from_mouse,
            diagnosed_pnu,
            pnu,
            self._selection_changed,
            bool(pnu and pnu in self._diagnosis_by_pnu and self.diagnosis is not None),
        )

    async def ask(
        self,
        user_query: str,
        max_turns: int = 8,
        *,
        continuation: bool = False,
    ) -> AsyncIterator[dict]:
        original_query = user_query
        has_explicit_address = bool(
            re.search(
                r"(?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)+"
                r"(?:산\s*)?\d+(?:-\d+)?",
                user_query,
            )
        )
        has_coordinates = bool(re.search(r"경도\s*-?\d", user_query))
        # 현재 필지 후속 질문에서 모델이 새 주소를 지어내 이동 도구를 호출하지
        # 못하게, 사용자가 이번 턴에 실제로 위치 변경을 요구했는지 보관한다.
        self._turn_has_explicit_address = has_explicit_address
        self._turn_requests_location_change = bool(
            re.search(r"(이동|가\s*줘|찾아\s*줘|찾아줘)", original_query)
        )
        parcel_question = bool(
            re.search(
                r"(지을|짓|건축|신축|건물|가능|규모|건폐율|용적률|층수|용도|"
                r"허용|제한|한정|상가|근린생활|창고|공장|업무시설|판매시설|"
                r"숙박시설|원룸|다가구|다세대|주인|임대|공시지가|지목|"
                r"건축물\s*대장|부담금|농지전용|산지전용)",
                user_query,
            )
        )
        if (
            self.selected_parcel
            and (parcel_question or self._selection_changed or continuation)
            and not has_explicit_address
            and not has_coordinates
        ):
            selected = self.selected_parcel
            user_query = (
                f"지도에서 선택한 위치(경도 {selected['lon']:.7f}, "
                f"위도 {selected['lat']:.7f})의 필지에 대한 질문이다: "
                + (
                    "새로 선택한 필지의 건축 가능 여부를 처음부터 진단해줘. "
                    if self._selection_changed
                    else ""
                )
                + f"사용자 질문: {original_query}"
            )

        self.messages.append({"role": "user", "content": original_query})
        self.update_conversation_context(
            last_user_query=original_query,
            active_address=(
                ((self.diagnosis or {}).get("parcel") or {}).get("jibun")
                or (self.selected_parcel or {}).get("address")
            ),
        )
        self._last_query = user_query
        # _diag_shown은 세션 동안 유지한다. 매 질문마다 초기화하면 같은 필지의
        # 후속 질문도 다시 '최초 진단'으로 오인해 종합 보고서를 반복하게 된다.

        # 화면 상태 전환은 자연어 표현이 조금 달라도 반드시 실행돼야 하므로 LLM 판단
        # 전에 결정적으로 처리한다. 인허가 진단이나 건물 유형은 바꾸지 않는다.
        compact_query = re.sub(r"\s+", "", user_query)
        display_verdict = (
            ((((self.diagnosis or {}).get("regulation") or {}).get("map_presentation") or {})
             .get("verdict"))
            or ((self.diagnosis or {}).get("regulation") or {}).get("verdict")
            or (self.diagnosis or {}).get("verdict")
        )
        building_display_blocked = display_verdict == "not_allowed"

        # 2D 필지 선택 화면과 3D 규모 화면을 버튼과 동일하게 전환한다.
        view_mode: str | None = None
        if re.search(
            r"(?:2d|이차원|평면지도|지적도화면).*(?:전환|바꿔|변경|보여|켜|가줘|모드)"
            r"|(?:전환|바꿔|변경|보여|켜).*(?:2d|이차원|평면지도)",
            compact_query,
            re.IGNORECASE,
        ):
            view_mode = "2d"
        elif re.search(
            r"(?:3d|삼차원|입체지도).*(?:전환|바꿔|변경|보여|켜|가줘|모드)"
            r"|(?:전환|바꿔|변경|보여|켜).*(?:3d|삼차원|입체지도)",
            compact_query,
            re.IGNORECASE,
        ) and not re.search(r"(?:건물|모델|매스|윤곽|형상)", compact_query):
            view_mode = "3d"
        if view_mode:
            yield {
                "event": "map_commands",
                "data": {"commands": [{"type": "set_view_mode", "mode": view_mode}]},
            }
            yield {
                "event": "message",
                "data": {"text": f"{view_mode.upper()} 지도 화면으로 전환했습니다."},
            }
            return

        # LOD1·건물 윤곽을 끄는 표현은 아래의 'LOD1 보여줘' 분기보다 먼저 처리한다.
        # "꺼죠" 같은 구어·오타도 받아서 입체 매스와 평면 윤곽을 모두 숨긴다.
        if re.search(
            r"(?:lod1|3d(?:건물|모델)?|모델|건물(?:매스|윤곽|형상|모델)?|"
            r"건축(?:매스|윤곽|형상)|입체(?:건물|매스|모델)?)."
            r"*(?:꺼|끄|숨|치워|지워|없애|안보이|안보여)",
            compact_query,
            re.IGNORECASE,
        ):
            yield {
                "event": "map_commands",
                "data": {"commands": [{"type": "hide_building_shape"}]},
            }
            yield {
                "event": "message",
                "data": {"text": "LOD1과 건물 윤곽을 숨겼습니다."},
            }
            return

        # 숨김 또는 건축면적 평면으로 전환하기 전의 상세 3D 모델을 복원한다.
        # 상세 모델을 선택한 적이 없으면 같은 진단의 LOD1 매스를 복원한다.
        if _is_building_restore_request(original_query):
            if building_display_blocked:
                yield {
                    "event": "message",
                    "data": {"text": "현재 건축 불가 판정이므로 3D 건축 모델을 표시하지 않습니다."},
                }
                return
            yield {
                "event": "map_commands",
                "data": {"commands": [{"type": "show_building_shape"}]},
            }
            yield {
                "event": "message",
                "data": {"text": "3D 건축 모델을 다시 표시했습니다."},
            }
            return

        # 바닥면적·평면·윤곽 등 일상적인 표현을 정확한 '건축면적 윤곽' 표시로
        # 통일한다. 단독으로 말한 "면적만 보여줘"도 지도 표시 명령으로 인식한다.
        if re.search(
            r"(?:건축면적|건물(?:바닥|면적|평면)|바닥면적|바닥윤곽|"
            r"평면윤곽|평면만|^면적만).*(?:켜|보여|표시|남겨|만|해줘|해죠)",
            compact_query,
            re.IGNORECASE,
        ):
            if building_display_blocked:
                yield {
                    "event": "message",
                    "data": {"text": "현재 건축 불가 판정이므로 건축면적 윤곽을 표시하지 않습니다."},
                }
                return
            yield {
                "event": "map_commands",
                "data": {"commands": [{"type": "show_building_footprint"}]},
            }
            yield {
                "event": "message",
                "data": {"text": "입체 건물을 숨기고 건축면적 윤곽만 표시했습니다."},
            }
            return

        # 상세 창문·외벽 모델을 세우기 전의 단순 형상은 진단 시 계산한 LOD1
        # 매스를 그대로 복원한다. 후속 자연어 질문을 종합 진단으로 오인하지 않는다.
        if re.search(
            r"(?:lod1(?:단계)?(?:만)?|건물(?:매스|윤곽|형상).*(?:켜|보여|표시)|"
            r"건물모델(?:세우기|올리기)전(?:모습)?|"
            r"상세(?:건물)?모델(?:숨기|끄|치우)|기본매스(?:만)?)",
            compact_query,
            re.IGNORECASE,
        ):
            if building_display_blocked:
                yield {
                    "event": "message",
                    "data": {"text": "현재 건축 불가 판정이므로 건축 매스를 표시하지 않습니다."},
                }
                return
            yield {
                "event": "map_commands",
                "data": {
                    "commands": [
                        {"type": "set_layers", "zoning": False},
                        {"type": "show_lod1"},
                    ]
                },
            }
            yield {
                "event": "message",
                "data": {
                    "text": "상세 건물 모델을 숨기고, 건물을 세우기 전 단계의 LOD1 매스만 표시했습니다."
                },
            }
            return

        # 화면 하단 지도 도구 메뉴 자체를 자연어로 여닫는다.
        if re.search(r"(?:하단)?(?:도구)?메뉴", compact_query):
            close_menu = bool(
                re.search(r"(닫|접|숨|꺼|끄|치워|없애|안보이|내려)", compact_query)
            )
            open_menu = bool(
                re.search(r"(열|펼|보여|켜|띄워|나타내|꺼내)", compact_query)
            )
            if close_menu or open_menu:
                opened = not close_menu
                yield {
                    "event": "map_commands",
                    "data": {
                        "commands": [{"type": "set_tool_menu", "open": opened}]
                    },
                }
                yield {
                    "event": "message",
                    "data": {
                        "text": f"하단 지도 도구 메뉴를 {'열었습니다' if opened else '닫았습니다'}."
                    },
                }
                return

        # 하단 메뉴의 각 버튼도 자연어로 즉시 실행한다. LLM 도구 선택에 맡기면
        # "넓이 재줘" 같은 표현을 일반 질의로 답하고 끝낼 수 있다.
        map_tool_action: str | None = None
        if re.search(
            r"(?:내|현재|현)\s*위치.*(?:가|이동|보여|찾아|맞춰)"
            r"|(?:내|현재|현)\s*위치(?:로|에)?$",
            user_query,
        ):
            map_tool_action = "my_location"
        elif re.search(r"(거리|길이).*(재|측정)|측정.*(거리|길이)", user_query):
            map_tool_action = "measure_line"
        elif re.search(r"(면적|넓이).*(재|측정)|측정.*(면적|넓이)", user_query):
            map_tool_action = "measure_area"
        elif re.search(r"높이.*(재|측정)|측정.*높이", user_query):
            map_tool_action = "measure_height"
        elif re.search(r"(측정|도형|그리기).*(지워|삭제|초기화)|초기화.*(측정|도형|그리기)", user_query):
            map_tool_action = "erase"
        if map_tool_action:
            labels = {
                "measure_line": "거리 측정",
                "measure_area": "면적 측정",
                "measure_height": "높이 측정",
                "erase": "측정 초기화",
                "my_location": "현재 위치 이동",
            }
            yield {
                "event": "map_commands",
                "data": {
                    "commands": [{"type": "run_tool", "action": map_tool_action}]
                },
            }
            message = (
                "측정 결과를 초기화했습니다."
                if map_tool_action == "erase"
                else "현재 위치로 지도를 이동합니다."
                if map_tool_action == "my_location"
                else f"{labels[map_tool_action]}을 시작했습니다. "
                "지도에서 점을 찍어 측정해 주세요."
            )
            yield {
                "event": "message",
                "data": {"text": message},
            }
            return

        # 지도 표시 제어는 필지 진단보다 먼저 처리한다. "용도지역 꺼줘"의
        # '용도'를 건축물 용도 질문으로 오인해 사전진단을 실행하면 안 된다.
        layer_terms = {
            "cadastre": r"(지적도|연속지적도|지적선|필지경계)",
            "zoning": r"(용도지역|용도지역색|용도지역주제도)",
            "slope": r"(경사도|경사격자|지형격자|표고경사)",
            "dimensions": r"(치수선|치수값|면적라벨|거리라벨)",
            "panel": r"(팝업|결과창|진단창|판정창)",
        }
        layer_command: dict[str, bool] = {}
        all_layer_request = re.search(
            r"(?:지도표시|지도레이어|레이어|주제도)(?:를|는|도)?"
            r"(?:전부|전체|모두|다).*(?:꺼|끄|숨|없애|켜|보여|표시)"
            r"|(?:전부|전체|모두|다).*(?:지도표시|지도레이어|레이어|주제도)"
            r".*(?:꺼|끄|숨|없애|켜|보여|표시)",
            compact_query,
            re.IGNORECASE,
        )
        if all_layer_request:
            turn_all_off = bool(
                re.search(r"(꺼|끄|숨|없애|off)", compact_query, re.IGNORECASE)
            )
            enabled = not turn_all_off
            # 결과 팝업과 건축 모델은 별도 기능이므로 '지도 레이어 전부'에
            # 포함하지 않는다.
            layer_command.update({
                "cadastre": enabled,
                "zoning": enabled,
                "slope": enabled,
                "dimensions": enabled,
            })
        for key, term in layer_terms.items():
            if not re.search(term, user_query, re.I):
                continue
            turn_off = bool(
                re.search(
                    rf"(?:{term}).*(꺼|끄|숨|닫|접|치워|없애|안\s*보이|off)|"
                    rf"(꺼|끄|숨|닫|접|치워|없애|안\s*보이).*(?:{term})",
                    user_query,
                    re.I,
                )
            )
            turn_on = bool(
                re.search(
                    rf"(?:{term}).*(켜|보여|표시|열|펼|띄워|나타내|on)|"
                    rf"(켜|보여|표시|열|펼|띄워|나타내).*(?:{term})",
                    user_query,
                    re.I,
                )
            )
            if turn_off or turn_on:
                layer_command[key] = not turn_off
        if layer_command:
            yield {
                "event": "map_commands",
                "data": {"commands": [{"type": "set_layers", **layer_command}]},
            }
            changed = ", ".join(
                f"{'팝업' if key == 'panel' else '치수선' if key == 'dimensions' else '용도지역' if key == 'zoning' else '지적도' if key == 'cadastre' else '경사도'} "
                f"{'켜기' if enabled else '끄기'}"
                for key, enabled in layer_command.items()
            )
            yield {
                "event": "message",
                "data": {"text": f"{changed}를 적용했습니다."},
            }
            return

        # 군부대 목적지가 공개 POI 검색에 없을 때 문장 속 학교·역 같은 다른
        # 장소로 대신 이동하면 안 된다. 공개 데이터에서 군시설 위치가 누락·제한될
        # 수 있으므로, 요청한 군시설 자체가 검색되는지 먼저 확정한다.
        military_target = re.search(
            r"((?:육군\s*)?제?\s*\d+\s*(?:보병)?사단(?:\s*예하\s*부대)?|군부대)",
            user_query,
        )
        if military_target and re.search(r"(이동|가\s*줘|찾아\s*줘|보여\s*줘)", user_query):
            target_name = re.sub(r"\s+", " ", military_target.group(1)).strip()
            military_places = await vworld.search_places(target_name)
            if not military_places:
                # 제66보병사단은 VWorld PLACE에는 없지만 가평군청이 방문 접수처를
                # '가평읍 가화로 269'로 공개한다. VWorld에서 그 도로명에 대응하는
                # 공개 지번(승안리 50-6)을 기준점으로 사용할 수 있다.
                if "66" in target_name and "가평" in user_query:
                    public_candidates = await vworld.search_addresses(
                        "경기도 가평군 가평읍 승안리 50-6"
                    )
                    exact_public = next(
                        (
                            candidate
                            for candidate in public_candidates
                            if (candidate.get("parcel") or "").endswith("승안리 50-6")
                        ),
                        None,
                    )
                    if exact_public:
                        parcel = await vworld.get_parcel(
                            exact_public["lon"], exact_public["lat"]
                        )
                        commands = [
                            {"type": "clear_mass"},
                            {
                                "type": "fly_to",
                                "lon": exact_public["lon"],
                                "lat": exact_public["lat"],
                                "altitude": 900,
                                "tilt": 45,
                                "heading": 0,
                            },
                        ]
                        if parcel.get("geometry"):
                            commands.append(
                                {
                                    "type": "highlight_parcel",
                                    "geometry": parcel["geometry"],
                                    "pnu": parcel.get("pnu", ""),
                                    "label": (
                                        parcel.get("jibun")
                                        or "제66보병사단 공개 방문 주소 기준점"
                                    ),
                                    "color": "#00E5FF",
                                }
                            )
                        yield {
                            "event": "map_commands",
                            "data": {"commands": commands},
                        }
                        yield {
                            "event": "message",
                            "data": {
                                "text": (
                                    "가평군청에 공개된 제66보병사단 방문 주소 "
                                    "**경기도 가평군 가평읍 가화로 269**에 대응하는 공개 "
                                    "지번 **승안리 50-6** 기준으로 이동했습니다. "
                                    "가평중학교 위치로 대신 이동한 것이 아니라 공개된 부대 "
                                    "방문 지점을 사용했으며, 내부 예하부대의 정확한 위치를 "
                                    "의미하지는 않습니다."
                                )
                            },
                        }
                        return
                yield {
                    "event": "message",
                    "data": {
                        "text": (
                            f"VWorld 공개 장소검색에서는 **{target_name}**의 위치가 나오지 "
                            "않아 정확한 좌표를 확정할 수 없습니다. 군사시설은 공개 POI에서 "
                            "명칭이나 위치가 누락·제한될 수 있어 보안 또는 공개데이터 제한 "
                            "가능성이 있지만, 검색 결과만으로 보안 사유라고 단정할 수는 없습니다. "
                            "문장에 함께 나온 가평중학교를 군부대 위치로 대신 사용하지 않겠습니다. "
                            "이동하려면 공개된 도로명·지번 주소나 좌표를 알려주세요."
                        )
                    },
                }
                return

        earthwork_mode: str | None = None
        if (
            re.search(r"(토공|절토|성토|평탄화).*(하기전|적용전|작업전|이전|전모습)", compact_query)
            or re.search(r"(원지형|원래지형).*(보여|모습|상태)", compact_query)
        ):
            earthwork_mode = "original"
        elif (
            re.search(r"(평탄화).*(해줘|작업|적용|보여|실행)", compact_query)
            or re.search(r"(토공|절토|성토).*(해줘|적용|작업|실행|후모습)", compact_query)
        ):
            earthwork_mode = "graded"
        requests_specific_model = bool(
            re.search(r"(창고|공장|상가|주택|주거|근린생활|판매시설).*(모델|건물|층)", compact_query)
            or re.search(r"\d+층.*(창고|공장|상가|주택|건물)", compact_query)
        )
        has_new_location = "지도에서선택한위치" in compact_query or bool(
            re.search(r"(경도|위도)\d", compact_query)
        )
        if earthwork_mode and not requests_specific_model and not has_new_location:
            label = "토공·절토 전 원지형 모습" if earthwork_mode == "original" else "평탄화·절성토 적용 모습"
            yield {
                "event": "map_commands",
                "data": {
                    "commands": [
                        {"type": "set_earthwork_mode", "mode": earthwork_mode}
                    ]
                },
            }
            yield {"event": "message", "data": {"text": f"같은 건물 모델을 {label}으로 전환했습니다."}}
            return

        # 지도에서 새 필지를 클릭한 뒤 건축 가능 여부를 묻는 요청은 이전 대화
        # 맥락보다 새 좌표가 절대 우선이다. LLM이 이를 후속 용도 질문으로 오인해
        # 직전 주소의 recheck_use를 호출하지 못하도록 결정적으로 새 진단을 실행한다.
        coordinate_match = re.search(
            r"경도\s*(-?\d+(?:\.\d+)?)[^\d-]+위도\s*(-?\d+(?:\.\d+)?)",
            user_query,
        )

        # "선택 필지에 단독주택 건물 보여줘"처럼 용도 모델 '표시'를 요청하는데 새 좌표·
        # 주소가 없으면, 이미 선택·진단된 필지 위에 세우려는 것이다. 이를 주소검색
        # (move_to_parcel)이나 LLM 도구선택에 맡기면 '선택 필지'를 주소로 오해해
        # "주소가 여러 곳…" 으로 새므로, 여기서 결정적으로 현재 필지에 모델을 올린다.
        if (
            requests_specific_model
            and not has_new_location
            and not coordinate_match
            and re.search(r"(보여|올려|세워|배치|표시)", compact_query)
            and (self.diagnosis or self.selected_parcel)
        ):
            _model_map = {
                "창고": "warehouse", "공장": "factory", "상가": "commercial",
                "단독주택": "detached", "주택": "detached",
                "공동주택": "lowrise", "주거": "lowrise",
            }
            requested_model = next(
                (m for k, m in _model_map.items() if k in compact_query), "detached"
            )
            floor_match = re.search(r"(\d+)\s*층", compact_query)
            floors = int(floor_match.group(1)) if floor_match else None
            before = bool(
                re.search(r"(토공|절토|성토|평탄화)(하기)?(전|이전)", compact_query)
                or re.search(r"(원지형|원래지형)", compact_query)
            )
            yield {
                "event": "map_commands",
                "data": {"commands": [{
                    "type": "show_housing_model",
                    "model": requested_model,
                    "floors": floors,
                    "earthwork_mode": "original" if before else "graded",
                    "hide_envelope": True,
                }]},
            }
            _addr = (
                ((self.diagnosis or {}).get("parcel") or {}).get("jibun")
                or ((self.diagnosis or {}).get("location") or {}).get("matched_address")
                or (self.selected_parcel or {}).get("address")
                or "선택한 필지"
            )
            _label = {
                "warehouse": "창고", "factory": "공장", "commercial": "상가",
                "detached": "단독주택", "lowrise": "공동주택",
            }.get(requested_model, "건물")
            yield {
                "event": "message",
                "data": {"text": f"**{_addr}**에 {_label} 모델을 지도에 올렸습니다."},
            }
            return

        # 지번 이동은 모델의 도구 선택에 맡기지 않는다. 프론트 주소 선확인은
        # 카메라만 옮기므로, 모델이 move_to_parcel 호출을 생략하면 필지 경계가
        # 없는 산 화면만 남는다. 주소+이동 의도가 명확하면 여기서 경계까지 그린다.
        parcel_move_match = re.search(
            r"((?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)*"
            r"(?:산\s*)?\d+(?:-\d+)?)",
            user_query,
        )
        if (
            parcel_move_match
            and re.search(r"(이동|가\s*줘|찾아\s*줘|보여\s*줘)", user_query)
            and not re.search(r"(건축|지을|짓|가능|진단|검토)", user_query)
            # '단독주택 건물 보여줘'처럼 특정 모델/건물 표시 요청은 주소 이동이 아니다.
            and not requests_specific_model
            and not re.search(r"(건물|모델)", user_query)
            # 좌표가 붙은 건 이미 선택된 필지 맥락 — 주소 재검색으로 빠지면 안 된다.
            and not has_new_location
            and not coordinate_match
        ):
            yield {"event": "tool_start", "data": {"tool": "move_to_parcel"}}
            try:
                out, events = await self._run_tool(
                    "move_to_parcel", {"query": parcel_move_match.group(1).strip()}
                )
                for event in events:
                    yield event
                if out.get("found"):
                    yield {
                        "event": "message",
                        "data": {
                            "text": (
                                f"**{out['address']}** 필지로 이동해 경계를 표시했습니다."
                            )
                        },
                    }
                else:
                    yield {
                        "event": "message",
                        "data": {
                            "text": (
                                "주소가 여러 곳과 일치합니다. 표시된 후보에서 정확한 "
                                "필지를 선택해 주세요."
                            ),
                            "options": [
                                {"label": address, "value": f"{address}로 이동해줘"}
                                for address in out.get("candidates", [])
                            ],
                        },
                    }
            except Exception as exc:
                yield {
                    "event": "error",
                    "data": {"tool": "move_to_parcel", "message": str(exc)},
                }
            return

        # 시·군·읍·면·리와 번지가 모두 있는 건축 가능성 질문은 LLM의 주소
        # 추측이나 도구 선택을 거치지 않는다. 같은 이름의 '신수리'가 전국에
        # 있어도 사용자가 적은 전체 주소 그대로 사전진단한다.
        explicit_build_address = re.search(
            r"((?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)+"
            r"(?:산\s*)?\d+(?:-\d+)?)",
            user_query,
        )
        explicit_build_question = bool(
            explicit_build_address
            and not coordinate_match
            and _has_building_feasibility_intent(user_query)
            and not re.search(
                r"(농지전용|산지전용|건축물\s*대장|개발부담금|농지보전부담금|"
                r"대체산림(?:자원)?조성비)",
                user_query,
            )
        )
        if explicit_build_question:
            yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
            try:
                same_parcel = _same_parcel_address(
                    explicit_build_address.group(1).strip(), self.diagnosis
                )
                _out, events = await self._diagnose_and_emit(
                    user_query, emit_card=not same_parcel
                )
                for event in events:
                    yield event
                if same_parcel:
                    yield {
                        "event": "message",
                        "data": {
                            "text": await self._natural_followup_answer(user_query)
                        },
                    }
            except Exception as exc:
                yield {
                    "event": "error",
                    "data": {"tool": "prediagnose", "message": str(exc)},
                }
            return

        # 전체 지번을 다시 말한 용도·허용·제한 후속 질문은 현재 지도 상태보다
        # 그 주소가 우선이다. "상가 용도는 무엇으로 한정돼?"처럼 '지을 수
        # 있어'가 없는 문장을 일반 대화로 넘기면 직전의 엉뚱한 필지 데이터를
        # 답하는 문제가 생긴다. 필지는 다시 확정하되 종합 카드는 반복하지 않는다.
        explicit_address_followup = bool(
            explicit_build_address
            and not coordinate_match
            and self.diagnosis
            and re.search(
                r"(용도|허용|제한|한정|조건|상가|근린생활|주택|원룸|다가구|"
                r"다세대|창고|공장|업무시설|판매시설|숙박시설|가능)",
                user_query,
            )
        )
        if explicit_address_followup:
            yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
            try:
                _out, events = await self._diagnose_and_emit(
                    user_query, emit_card=False
                )
                for event in events:
                    yield event
                yield {
                    "event": "message",
                    "data": {"text": await self._natural_followup_answer(user_query)},
                }
            except Exception as exc:
                yield {
                    "event": "error",
                    "data": {"tool": "prediagnose", "message": str(exc)},
                }
            return

        # '건축물대장'의 '건축'을 건축 가능성 질문으로 오인하지 않는다.
        # 선택 좌표의 실제 필지를 다시 확정한 뒤 건축HUB 결과만 자연어로 답한다.
        if re.search(r"(건축물\s*대장|대장\s*(확인|조회))", user_query):
            try:
                if coordinate_match:
                    lon, lat = map(float, coordinate_match.groups())
                else:
                    address_match = re.search(
                        r"([가-힣0-9]+(?:읍|면|동|리)\s+(?:산\s*)?\d+(?:-\d+)?)",
                        user_query,
                    )
                    if not address_match:
                        yield {
                            "event": "message",
                            "data": {
                                "text": (
                                    "건축물대장을 확인할 필지를 알 수 없습니다. "
                                    "지도에서 필지를 선택하거나 지번 주소를 말씀해 주세요."
                                )
                            },
                        }
                        return
                    address_query = address_match.group(1)
                    candidates = await vworld.search_addresses(address_query)
                    locality = re.search(
                        r"([가-힣0-9]+(?:읍|면|동|리))", address_query
                    )
                    lot = re.search(r"((?:산\s*)?\d+(?:-\d+)?)$", address_query)

                    def candidate_lot(candidate: dict) -> str:
                        value = candidate.get("parcel") or candidate.get("address") or ""
                        found = re.search(r"((?:산\s*)?\d+(?:-\d+)?)\s*$", value)
                        return found.group(1).replace(" ", "") if found else ""

                    exact = [
                        candidate
                        for candidate in candidates
                        if (
                            not locality
                            or locality.group(1)
                            in (
                                candidate.get("parcel")
                                or candidate.get("address")
                                or ""
                            )
                        )
                        and (
                            not lot
                            or candidate_lot(candidate)
                            == lot.group(1).replace(" ", "")
                        )
                    ]
                    if len(exact) != 1:
                        yield {
                            "event": "message",
                            "data": {
                                "text": (
                                    f"**{address_query}**와 정확히 일치하는 필지를 하나로 "
                                    "확정하지 못했습니다. 시·군·구를 포함한 지번 주소를 말씀해 주세요."
                                )
                            },
                        }
                        return
                    lon, lat = float(exact[0]["lon"]), float(exact[0]["lat"])
                parcel = await vworld.get_parcel(lon, lat)
                ledger = await building_register.lookup(parcel.get("pnu", ""))
                address = parcel.get("jibun") or "선택한 필지"
                status = ledger.get("status")
                if status == "FOUND":
                    buildings = ledger.get("buildings") or []
                    descriptions = []
                    for building in buildings[:5]:
                        parts = [
                            building.get("name"),
                            building.get("main_use"),
                            (
                                f"지상 {building.get('ground_floors')}층"
                                if building.get("ground_floors") not in (None, "")
                                else None
                            ),
                            (
                                f"연면적 {building.get('total_area_m2')}㎡"
                                if building.get("total_area_m2") not in (None, "")
                                else None
                            ),
                        ]
                        descriptions.append(" · ".join(str(p) for p in parts if p))
                    detail = "; ".join(descriptions)
                    asked_floor_match = re.search(r"(\d+)\s*층", user_query)
                    asks_comparison = bool(
                        asked_floor_match
                        or re.search(
                            r"(왜|다르|틀리|차이|가능\s*여부|가능해|가능한가)",
                            user_query,
                        )
                    )
                    diagnosed_parcel = (self.diagnosis or {}).get("parcel") or {}
                    diagnosed_mass = (self.diagnosis or {}).get("massing") or {}
                    diagnosed_floor = (
                        diagnosed_mass.get("floors")
                        if diagnosed_parcel.get("jibun") == address
                        else None
                    )
                    existing_floors = [
                        int(building["ground_floors"])
                        for building in buildings
                        if str(building.get("ground_floors") or "").isdigit()
                    ]
                    existing_floor = max(existing_floors) if existing_floors else None
                    planned_floor = (
                        diagnosed_floor
                        or (
                            int(asked_floor_match.group(1))
                            if asked_floor_match
                            else None
                        )
                    )
                    if asks_comparison and existing_floor and planned_floor:
                        facts = {
                            "address": address,
                            "registered_existing_floors": existing_floor,
                            "registered_building_count": ledger.get(
                                "count", len(buildings)
                            ),
                            "registered_detail": detail,
                            "new_building_estimated_floors": planned_floor,
                            "estimate_basis": "건폐율·용적률과 이격·주차 조건",
                            "important_caveat": (
                                "신축 추정 층수는 허가 확정값이 아니며 지구단위계획의 "
                                "높이·층수 제한과 실제 설계 검토가 필요함"
                            ),
                        }
                        natural = await self.client.complete(
                            system=_answer_system(
                                "사용자의 말투와 질문 의도에 맞춰 한국어로 자연스럽게 답하라. "
                                "제공된 사실만 사용하고, 기존 건축물대장의 실제 층수와 신축 "
                                "규모 추정 층수를 반드시 구분하라. 사용자가 차이를 물으면 이유를 "
                                "먼저 설명하되 정해진 문구나 고정 서식을 반복하지 마라. "
                                "신축 추정치를 허가 확정값처럼 말하지 마라."
                            ),
                            messages=[
                                {
                                    "role": "user",
                                    "content": (
                                        f"사용자 질문: {user_query}\n"
                                        f"확인된 사실: {json.dumps(facts, ensure_ascii=False)}"
                                    ),
                                }
                            ],
                            tools=[],
                            max_tokens=600,
                        )
                        text = _strip_internal_field_names(" ".join(natural.texts).strip())
                        if not text:
                            text = (
                                f"{address}의 대장상 기존 건물은 {existing_floor}층이고, "
                                f"지도에 나온 {planned_floor}층은 신축 규모 추정치라 기준이 "
                                "다릅니다. 신축 층수는 아직 허가 확정값이 아닙니다."
                            )
                    else:
                        text = (
                            f"선택하신 **{address}**의 건축물대장 표제부는 "
                            f"총 **{ledger.get('count', len(buildings))}건** 확인됩니다."
                            + (f" {detail}" if detail else "")
                        )
                elif status == "CLEAR":
                    text = (
                        f"선택하신 **{address}**는 건축물대장 표제부가 조회되지 않았습니다. "
                        "다만 무허가·미등재 건축물이나 현장 현황은 별도로 확인해야 합니다."
                    )
                else:
                    text = (
                        f"선택하신 **{address}**의 건축물대장을 확인하지 못했습니다. "
                        f"{ledger.get('message') or '공공데이터 조회 상태를 확인해 주세요.'}"
                    )
                yield {"event": "message", "data": {"text": text}}
            except Exception as exc:
                yield {
                    "event": "error",
                    "data": {"tool": "building_register", "message": str(exc)},
                }
            return

        asks_conditional_requirements = bool(
            coordinate_match
            and (
                re.search(
                    r"(조건부\s*가능|가능\s*조건).*(어떻게|하려면|되려면|뭐|무엇|절차|해결)",
                    user_query,
                )
                or re.search(r"어떻게.*조건부\s*가능", user_query)
                or re.search(
                    r"(?:개별\s*)?(?:심의|인허가|허가).*(?:요건|절차|단계|서류|"
                    r"거쳐|필요|어떻게|어디|기간)|"
                    r"(?:요건|절차|단계|서류).*(?:심의|인허가|허가)",
                    user_query,
                )
            )
        )
        if asks_conditional_requirements:
            lon, lat = map(float, coordinate_match.groups())
            current_location = (self.diagnosis or {}).get("location") or {}
            same_location = (
                current_location.get("lon") is not None
                and current_location.get("lat") is not None
                and abs(float(current_location["lon"]) - lon) < 0.001
                and abs(float(current_location["lat"]) - lat) < 0.001
            )
            if not same_location:
                self.diagnosis = await run_prediagnosis(
                    self.client,
                    (
                        f"지도에서 선택한 위치(경도 {lon}, 위도 {lat})의 현재 필지에 "
                        "일반적인 건축을 검토한다"
                    ),
                )

            diagnosis = self.diagnosis or {}
            model_diagnosis = json.loads(compact(diagnosis))
            facts = {
                "address": (
                    (diagnosis.get("parcel") or {}).get("jibun")
                    or (diagnosis.get("location") or {}).get("matched_address")
                ),
                "verdict": diagnosis.get("verdict"),
                "jimok": (diagnosis.get("parcel") or {}).get("jimok"),
                "land_conversion": model_diagnosis.get("land_conversion"),
                "road_access": model_diagnosis.get("road_access"),
                "regulatory_screen": model_diagnosis.get("regulatory_screen"),
                "regulation_constraints": (
                    diagnosis.get("regulation") or {}
                ).get("constraints"),
                "site_constraints": model_diagnosis.get("site_constraints"),
                "permit_requirements": model_diagnosis.get("permit_requirements"),
            }
            try:
                natural = await self.client.complete(
                    system=_answer_system(
                        "사용자가 선택 필지의 조건부 사항 또는 개별 심의·인허가 요건을 묻는다. "
                        "제공된 진단 사실만 근거로 한국어 자연어 답변을 작성하라. 주소를 먼저 "
                        "명시하고, 실제로 필요한 조치를 우선순위대로 설명하라. 지목이 전이면 "
                        "농지전용 협의·허가와 개발행위허가 후 준공 단계의 지목변경(전→대)을 "
                        "구분해 설명하라. 접도, 주차, 일조·이격거리, 지구단위계획 등 데이터에 "
                        "있는 조건을 빠뜨리지 마라. '건축 가능하다'만 반복하거나 건축 모델을 "
                        "추천하지 말고, 확인되지 않은 사항은 확정적으로 말하지 마라. 고정된 "
                        "permit_requirements에 단계가 있으면 이름·담당부서·서류·처리기간·"
                        "근거법령을 실제 데이터대로 설명하고 일부 단계만 임의로 생략하지 마라. "
                        "지형·임상도 참고값이 있으면 실제 수치를 읽고 현장조사 확정값과 구분하라. "
                        "regulatory_screen에 생태·자연도 1·2등급 또는 별도관리지역 "
                        "중첩이 있으면 실제 등급·중첩률·면적과 별도관리 유형을 읽고, "
                        "왜 환경성 검토나 관계기관 협의가 필요한지 해당 permit_requirements "
                        "단계와 연결해 설명하라. 3등급은 참고정보를 허가 제한이나 불가로 "
                        "확대하지 마라. 생태·자연도 등급만으로 건축 불가를 단정하지 마라. "
                        "문구를 반복하지 마라. 독립적인 조치가 3개 이상이면 짧은 도입문 뒤에 "
                        "각 제목을 **1. 조치명**, **2. 조치명**, **3. 조치명**처럼 서로 다른 "
                        "번호로 명시해 정리하고 같은 번호를 반복하지 마라. "
                        "조치가 1~2개뿐이면 자연스러운 문장으로 답하라."
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"사용자 질문: {user_query}\n"
                                f"필지 진단 사실: {json.dumps(facts, ensure_ascii=False)}"
                            ),
                        }
                    ],
                    tools=[],
                    max_tokens=900,
                )
                text = _strip_internal_field_names(" ".join(natural.texts).strip())
            except Exception:
                logger.debug("자연어 답변 생성 실패, 결정적 폴백", exc_info=True)
                text = ""
            if not text:
                address = facts.get("address") or "선택한 필지"
                text = (
                    f"{address}의 조건부 사항을 해소하려면 지목·전용, 개발행위허가, "
                    "접도와 주차·이격 조건을 순서대로 확인해야 합니다. 지목이 전이라면 "
                    "농지전용 절차와 개발행위허가를 먼저 진행하고, 공사 준공 후 실제 이용 "
                    "현황에 맞춰 전에서 대로 지목변경하는 절차를 검토해야 합니다."
                )
            text = _ensure_query_evidence(text, diagnosis, user_query)
            text = _normalize_numbered_headings(text)
            yield {"event": "message", "data": {"text": text}}
            return

        asks_forest_charge = bool(
            re.search(
                r"(대체산림(?:자원)?조성비|대체산림.*비|농지보전부담금|개발부담금)",
                user_query,
            )
        )
        if asks_forest_charge:
            diagnosis = self.diagnosis or {}
            current_address = (
                (diagnosis.get("parcel") or {}).get("jibun")
                or (diagnosis.get("location") or {}).get("matched_address")
                or ""
            )
            stated_lot = re.search(
                r"([가-힣0-9]+(?:읍|면|동|리)\s+(?:산\s*)?\d+(?:-\d+)?)",
                user_query,
            )
            if not diagnosis or (
                stated_lot and stated_lot.group(1) not in current_address
            ):
                try:
                    _out, events = await self._diagnose_and_emit(
                        user_query, emit_card=False
                    )
                    for event in events:
                        yield event
                    diagnosis = self.diagnosis or {}
                except Exception as exc:
                    yield {
                        "event": "error",
                        "data": {"tool": "prediagnose", "message": str(exc)},
                    }
                    return
            parcel = diagnosis.get("parcel") or {}
            address = (
                parcel.get("jibun")
                or (diagnosis.get("location") or {}).get("matched_address")
                or "선택한 필지"
            )
            charge = diagnosis.get("conversion_charge") or {}
            development = diagnosis.get("development_charge") or {}
            if "개발부담금" in user_query:
                if development:
                    avg = int(development.get("region_avg_per_case_won") or 0)
                    avg_text = (
                        f" 참고로 2025년 {development.get('region') or '전국'} 전체 부과 "
                        f"실적의 건당 평균은 약 {avg:,.0f}원이지만, 이 필지의 계산액은 "
                        "아닙니다."
                        if avg
                        else ""
                    )
                    formula = development.get("calculation_formula") or (
                        "개발이익은 종료시점지가에서 개시시점지가·정상지가상승분·"
                        "인정 개발비용을 뺀 금액입니다."
                    )
                    yield {
                        "event": "message",
                        "data": {
                            "text": (
                                f"**{address}**은 {development.get('reason', '개발부담금 대상 여부 확인이 필요합니다.')} "
                                f"{development.get('rate_note', '')} 필지별 산식은 "
                                f"`{formula}`입니다. 따라서 정확한 금액은 종료시점 지가와 "
                                f"인정 개발비용이 확정돼야 계산됩니다.{avg_text}"
                            ).strip()
                        },
                    }
                else:
                    yield {
                        "event": "message",
                        "data": {
                            "text": (
                                f"**{address}**은 현재 진단상 개발부담금 대상 자료가 "
                                "산출되지 않았습니다. 지목변경을 수반하는 개발면적과 사업 "
                                "유형이 정해져야 대상 여부와 부과율을 계산할 수 있습니다."
                            )
                        },
                    }
                return
            if charge.get("type") == "farmland_conservation" and charge.get("estimated_won"):
                area = float(charge.get("area_m2") or 0)
                unit = int(charge.get("unit_won_m2") or 0)
                total = int(charge["estimated_won"])
                yield {
                    "event": "message",
                    "data": {
                        "text": (
                            f"**{address}**의 농지보전부담금은 현재 계획상 전용예상면적 "
                            f"**{area:,.0f}㎡**를 기준으로 약 **{total:,.0f}원**입니다. "
                            f"적용 참고단가는 ㎡당 **{unit:,}원**입니다. 실제 농지전용 "
                            "허가면적과 농업진흥지역 여부, 감면 대상에 따라 최종 금액은 "
                            "달라질 수 있습니다."
                        )
                    },
                }
            elif charge.get("type") == "forest_replacement" and charge.get("estimated_won"):
                area = float(charge.get("area_m2") or 0)
                unit = int(charge.get("unit_won_m2") or 0)
                total = int(charge["estimated_won"])
                base = int(charge.get("base_won_m2") or 0)
                land_component = int(charge.get("land_component_won_m2") or 0)
                area_basis = (
                    "전체 필지를 전용한다고 가정한 참고 상한"
                    if charge.get("area_basis") == "full_parcel_reference"
                    else "현재 계획상 전용예상면적"
                )
                yield {
                    "event": "message",
                    "data": {
                        "text": (
                            f"**{address}**의 대체산림자원조성비는 {area_basis} "
                            f"**{area:,.0f}㎡**를 기준으로 약 **{total:,.0f}원**입니다. "
                            f"적용 참고단가는 ㎡당 **{unit:,}원**(기본단가 {base:,}원 + "
                            f"공시지가 반영액 {land_component:,}원)입니다. 이는 참고액이며 "
                            "허가 가능 판정이 아닙니다. 실제 "
                            "허가면적·산지 구분·연도별 산림청 고시단가와 감면 여부에 따라 "
                            "최종 금액은 달라질 수 있습니다."
                        )
                    },
                }
            else:
                missing = []
                if not parcel.get("area_m2"):
                    missing.append("필지면적")
                if not parcel.get("jiga_won_per_m2"):
                    missing.append("개별공시지가")
                if not diagnosis.get("land_conversion"):
                    missing.append("산지 구분")
                reason = (
                    f"{', '.join(missing)} 자료가 없어"
                    if missing
                    else "현재 건축계획의 전용예상면적이 확정되지 않아"
                )
                yield {
                    "event": "message",
                    "data": {
                        "text": (
                            f"**{address}**은 {reason} 대체산림자원조성비를 계산하지 "
                            "못했습니다. 산지전용 예정면적을 말씀해 주시면 그 면적 기준으로 "
                            "다시 산정할 수 있습니다."
                        )
                    },
                }
            return

        asks_land_conversion = bool(
            ("산지전용" in user_query or "농지전용" in user_query)
            and re.search(r"(지을|짓|건축|신축|가능|되나|돼|할\s*수)", user_query)
        )

        # 주소를 직접 말한 농지·산지전용 질문도 일반 도구 선택에 맡기지 않는다.
        # 진단 카드가 나온 뒤 답변 없이 끝나는 일을 막고, 같은 진단 사실로 질문에
        # 대한 자연어 결론을 반드시 덧붙인다.
        explicit_land_conversion_diagnosis = bool(
            asks_land_conversion
            and not coordinate_match
            and re.search(r"\d+(?:-\d+)?", user_query)
        )
        if (
            asks_land_conversion
            and not coordinate_match
            and not explicit_land_conversion_diagnosis
            and self.diagnosis
        ):
            yield {
                "event": "message",
                "data": {"text": await self._natural_land_conversion_answer(user_query)},
            }
            return
        if explicit_land_conversion_diagnosis:
            yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
            try:
                # 농지·산지전용 허가만 물은 질문에는 건축 가능 규모 종합 카드를
                # 출력하지 않는다. 필지 규제는 조회하되 답은 자연어로만 낸다.
                _out, events = await self._diagnose_and_emit(
                    user_query, emit_card=False
                )
                for event in events:
                    yield event
                yield {
                    "event": "message",
                    "data": {"text": await self._natural_land_conversion_answer(user_query)},
                }
            except Exception as exc:
                yield {"event": "error", "data": {"tool": "prediagnose", "message": str(exc)}}
            return

        # 개념·정의·해석 질문("건축선 후퇴가 이격거리야?", "필지 분할해야 해?",
        # "용적률이 뭐야?", "협의 필요해?")은 '건축(선)·필지' 같은 단어가 들어가도
        # 재진단·카드 대상이 아니다. 이미 진단된 같은 필지면 자연어로 해석해 답한다.
        _concept_q = re.search(
            r"(뭐(?:야|예요|니|냐)|무엇(?:이|인가|인지)|무슨\s*(?:뜻|의미|말)|의미(?:가|는|야|니)|"
            r"뜻(?:이|은|을|인|이야)?|정의|차이(?:가|는|야|점)?|란\s*(?:뭐|무엇)|이란|"
            r"이격거리(?:야|냐|인가|인지)|후퇴(?:가|는|해야|하면|한다는)|"
            r"분할(?:해야|하면|이\s*필요|필요한|되나|하는\s*거|하는거)|"
            r"협의(?:가\s*필요|해야|필요한|가\s*있)|절차(?:가|는)?\s*(?:어떻게|뭐))",
            user_query,
        )
        _build_intent = re.search(
            r"(지을\s*수|지어도\s*(?:되|돼)|신축|몇\s*층|\d+\s*층|모델|배치|올려|세워|"
            r"건물\s*(?:지|올|세))",
            user_query,
        )
        if coordinate_match and _concept_q and not _build_intent:
            if self.diagnosis and _same_parcel_coordinate(coordinate_match, self.diagnosis):
                # 이미 진단된 같은 필지 → 재진단 없이 바로 자연어로 해석해 답한다.
                yield {
                    "event": "message",
                    "data": {"text": await self._natural_followup_answer(user_query)},
                }
            else:
                # 진단 데이터가 없거나(세션 초기화 등) 다른 필지면 데이터는 갱신하되,
                # 개념 질문이므로 종합 판정 카드는 찍지 않고(emit_card=False) 개념
                # 답변만 준다.
                yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
                try:
                    _out, events = await self._diagnose_and_emit(user_query, emit_card=False)
                    for event in events:
                        yield event
                    yield {
                        "event": "message",
                        "data": {"text": await self._natural_followup_answer(user_query)},
                    }
                except Exception as exc:
                    yield {
                        "event": "error",
                        "data": {"tool": "prediagnose", "message": str(exc)},
                    }
            self._selection_changed = False
            return

        # 새 프로토콜의 같은 필지 후속은 질문 문장에 좌표·판정 지시문을 섞지 않는다.
        # 진단이 살아 있으면 최신 구조화 데이터로 바로 답하고, 서버 재시작 등으로
        # 진단이 사라진 경우에만 아래 좌표 진단으로 조용히 복구한다.
        if continuation and self.diagnosis:
            interpreted = await self._interpret_followup(original_query)
            intent = interpreted.get("intent") or "followup_explanation"
            # 제미나이가 '다른 주소로 가서 건축 가능한지 보라'고 판단하면(target_address),
            # 그 주소로 이동해 새로 진단한다(정규식 아닌 LLM 판단, 도로명도 처리).
            _target = str(interpreted.get("target_address") or "").strip()
            # 환각 방지: 제미나이가 낸 주소가 질문 원문에 실제로 있어야 이동한다.
            if (
                _target
                and _target_in_query(_target, original_query)
                and not _same_parcel_address(_target, self.diagnosis)
            ):
                yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
                try:
                    _out, _ev = await self._diagnose_and_emit(
                        f"{_target}에 건축 가능 여부를 진단해줘. 사용자 질문: {original_query}",
                        emit_card=True,
                    )
                    for _e in _ev:
                        yield _e
                except Exception as exc:
                    yield {"event": "error", "data": {"tool": "prediagnose", "message": str(exc)}}
                self._selection_changed = False
                return
            # 특정 건축물의 신규 가능 여부 검토만 아래 결정식 진단 경로로 보낸다.
            # 그 밖의 같은-PNU 질문은 제미나이가 현재 상태를 읽고 해석한 답으로
            # 끝내므로 종합진단이나 모델 카드가 임의로 다시 붙지 않는다.
            if intent != "specific_use_feasibility":
                answer = str(interpreted.get("answer") or "").strip()
                if not answer:
                    answer = await self._natural_followup_answer(original_query)
                # 도로접촉·건축선·이격·배수로를 보고 싶어 하면, 그 선만 지도에 다시
                # 얹는다(카메라·3D 매스는 그대로). 어떤 선인지는 제미나이 해석(map_lines)이
                # 정한다 — 키워드 매칭이 아니라 의도로 판단한다. 가능 판정일 때만 건축선·이격.
                _line_kinds = [
                    k for k in (interpreted.get("map_lines") or [])
                    if k in {"road", "building_line", "drainage"}
                ]
                if _line_kinds:
                    from .agents.map_control import overlay_command

                    _line_cmd = overlay_command(self.diagnosis, _line_kinds)
                    if _line_cmd:
                        yield {
                            "event": "map_commands",
                            "data": {"commands": [_line_cmd]},
                        }
                data: dict = {"text": answer}
                if intent == "possible_models":
                    self.update_conversation_context(
                        last_intent=intent,
                        last_subject="현재 필지에서 허용되는 다른 건축물·모델",
                        active_building_use="가능한 건축물 전체",
                    )
                    # 판매시설 등 직전 단일용도 판정은 진단 원본에 보존하되,
                    # 팝업의 현재 검토 범위는 후속 질문의 의미에 맞게 전환한다.
                    # 검토 범위가 '가능한 건축물 전체'로 바뀌면 배지도 그 범위 판정으로 바꾼다.
                    # 직전 단일용도가 불가여도(판매시설 등) 지을 수 있는 다른 용도가 있으면
                    # 전체 관점은 조건부 가능이므로, 배지가 '불가'로 남지 않게 갱신한다.
                    _zuo = (
                        (self.diagnosis.get("regulation") or {}).get("zone_use_overview") or {}
                    )
                    _context_cmd = {
                        "type": "set_panel_context",
                        "building_use": "가능한 건축물 전체",
                    }
                    if (
                        (_zuo.get("allowed") or _zuo.get("conditional"))
                        and not self.diagnosis.get("placement_restricted")  # 배치 제한이면 배지 유지
                    ):
                        _context_cmd.update({
                            "verdict": "conditional",
                            "verdict_label": "조건부 가능",
                            "verdict_color": "#F9A825",
                        })
                    yield {
                        "event": "map_commands",
                        "data": {"commands": [_context_cmd]},
                    }
                    data["text"] = (
                        f"{answer}\n\n"
                        "**가능 모델**\n"
                        "허용되는 용도 중 준비된 모델만 보여드립니다."
                    )
                    data["options"] = _model_options_for_diagnosis(
                        self.diagnosis,
                        include_alternatives=True,
                        # 원래 요청 용도가 불가여도 사용자가 명시적으로 다른
                        # 가능 모델을 물으면 허용·조건부 용도 모델은 제시한다.
                        allow_alternative_verdict=True,
                    )
                else:
                    self.update_conversation_context(
                        last_intent=intent,
                        last_subject=interpreted.get("subject") or original_query,
                    )
                # 필지별 상태에는 질문뿐 아니라 실제로 사용자에게 보낸 답도 남긴다.
                # A→B→A로 돌아왔을 때 제미나이가 앞선 답변을 이어 해석할 수 있다.
                self.messages.append({
                    "role": "assistant",
                    "content": data["text"],
                })
                yield {"event": "message", "data": data}
                self._selection_changed = False
                return

        # 선만 묻는 질문('건축선 그려줘'·'도로 접촉 있어?')은 종합판정 카드·3D 매스·
        # 가능여부 팝업 없이 요청한 선만 그린다. 좌표 주입 전 '원문' 기준으로 판정한다
        # (주입된 문장엔 '건축 가능 여부 진단' 이 들어가 오탐될 수 있다).
        _line_only = (
            _requested_map_lines(original_query)
            if _is_line_only_query(original_query) else None
        )
        if _line_only:
            same_diag = bool(
                self.diagnosis
                and not self._selection_changed
                and (
                    not coordinate_match
                    or _same_parcel_coordinate(coordinate_match, self.diagnosis)
                )
            )
            if same_diag:
                from .agents.map_control import build_lines_only_commands

                yield {
                    "event": "map_commands",
                    "data": {
                        "commands": build_lines_only_commands(self.diagnosis, _line_only)
                    },
                }
                yield {
                    "event": "message",
                    "data": {"text": await self._natural_followup_answer(original_query)},
                }
                self._selection_changed = False
                return
            if coordinate_match:
                yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
                try:
                    _out, events = await self._diagnose_and_emit(
                        user_query, lines_only=_line_only
                    )
                    for event in events:
                        yield event
                    yield {
                        "event": "message",
                        "data": {
                            "text": await self._natural_followup_answer(original_query)
                        },
                    }
                    self._selection_changed = False
                except Exception as exc:
                    yield {
                        "event": "error",
                        "data": {"tool": "prediagnose", "message": str(exc)},
                    }
                return

        coordinate_diagnosis = bool(
            coordinate_match
            and (
                continuation
                or
                self._selection_changed
                or re.search(
                r"(지을|짓|건축|신축|가능|규모|건폐율|용적률|층수|건축물\s*용도|"
                r"용도(?:는|가|를|에|로)?|허용|제한|한정|상가|근린생활|창고|공장|"
                r"업무시설|판매시설|숙박시설|만들|원룸|다가구|다세대|주인.*살|임대)",
                user_query,
                )
            )
        )
        if coordinate_diagnosis:
            yield {"event": "tool_start", "data": {"tool": "prediagnose"}}
            try:
                lon, lat = map(float, coordinate_match.groups())
                # /selection에서 다른 PNU 클릭이 이미 확인됐다면 좌표 재조회 결과보다
                # 그 명시적 선택을 우선한다. 경계 가까운 클릭 좌표가 이웃 필지로
                # 재해석돼 새 필지를 후속 질문으로 오인하는 것을 막는다.
                starts_new_parcel = self._selection_changed
                current_pnu = (
                    ((self.diagnosis or {}).get("parcel") or {}).get("pnu")
                )
                try:
                    selected_parcel = await vworld.get_parcel(lon, lat)
                    selected_pnu = selected_parcel.get("pnu")
                    same_parcel = (
                        False
                        if starts_new_parcel
                        else bool(
                            current_pnu
                            and selected_pnu
                            and current_pnu == selected_pnu
                        )
                    )
                except Exception:
                    logger.debug("같은 필지 판정 비교 실패", exc_info=True)
                    same_parcel = (
                        False
                        if starts_new_parcel
                        else _same_parcel_coordinate(
                            coordinate_match, self.diagnosis
                        )
                    )
                _out, events = await self._diagnose_and_emit(
                    user_query,
                    emit_card=(
                        not asks_land_conversion
                        and not same_parcel
                        and not continuation
                    ),
                )
                logger.info(
                    "coordinate_diagnosis current_pnu=%s selected_pnu=%s "
                    "starts_new=%s same_parcel=%s emit_card=%s",
                    current_pnu,
                    locals().get("selected_pnu", ""),
                    starts_new_parcel,
                    same_parcel,
                    not asks_land_conversion and not same_parcel,
                )
                for event in events:
                    yield event
                if asks_land_conversion:
                    yield {
                        "event": "message",
                        "data": {
                            "text": await self._natural_land_conversion_answer(user_query)
                        },
                    }
                elif "원룸" in user_query:
                    # 원룸·복합용도(1층 임대+2층 자가 등) 질문 — 용도지역을 하드코딩하지
                    # 않고, 실제 진단 데이터(용도지역·zone_use_overview 등)를 읽어 답한다.
                    yield {
                        "event": "message",
                        "data": {
                            "text": await self._natural_followup_answer(user_query)
                        },
                    }
                elif same_parcel or continuation:
                    yield {
                        "event": "message",
                        "data": {
                            "text": await self._natural_followup_answer(user_query)
                        },
                    }
                # 새 필지 클릭 플래그는 '첫 진단' 한 번만 소비한다. 이후 같은 필지
                # 후속 질문은 새 진단(카드)이 아니라 자연어 답변으로 가야 하므로,
                # 진단을 한 번 돌렸으면 여기서 반드시 해제한다.
                self._selection_changed = False
            except Exception as exc:
                yield {"event": "error", "data": {"tool": "prediagnose", "message": str(exc)}}
            return

        if (
            "선택한 필지" in user_query
            and not coordinate_match
            and re.search(r"(지을|짓|건축|신축|가능|원룸|주택|건물)", user_query)
        ):
            yield {
                "event": "message",
                "data": {
                    "text": (
                        "새로 선택한 필지의 좌표가 전달되지 않았습니다. 이전 주소로 판단하지 "
                        "않겠습니다. 지도에서 필지를 다시 클릭한 뒤 질문해 주세요."
                    )
                },
            }
            return

        # 일반 도구 경로에서 최초 진단 카드와 검토 의견까지 표시한 뒤에는
        # 다음 LLM 턴이 같은 결론을 다시 한 문장으로 말하지 못하게 한다.
        answer_card_complete = False
        for _ in range(max_turns):
            response = await self.client.complete(
                system=SYSTEM, messages=self.messages, tools=TOOLS, max_tokens=8000
            )
            self.messages.append(response.raw_assistant)

            for text in ([] if answer_card_complete else response.texts):
                cleaned = _strip_internal_field_names(text)
                if not response.tool_calls and self.diagnosis:
                    cleaned = _ensure_query_evidence(
                        cleaned, self.diagnosis, original_query
                    )
                yield {
                    "event": "message",
                    "data": {"text": cleaned},
                }

            if not response.tool_calls:
                return

            results = []
            for call in response.tool_calls:
                yield {"event": "tool_start", "data": {"tool": call.name}}
                try:
                    out, extra_events = await self._run_tool(call.name, call.input)
                    if (
                        isinstance(out, dict)
                        and out.get("answer_card_already_shown")
                    ):
                        answer_card_complete = True
                    for ev in extra_events:
                        yield ev
                    content = json.dumps(out, ensure_ascii=False)
                    is_error = False
                except Exception as exc:
                    content = f"오류: {exc}"
                    is_error = True
                    yield {"event": "error", "data": {"tool": call.name, "message": str(exc)}}

                results.append({"id": call.id, "content": content, "is_error": is_error})

            append_tool_results(self.messages, self.client, results)
            if answer_card_complete:
                # 카드 안의 검토 의견이 이 질문의 최종 자연어 답변이다.
                # 같은 진단을 다시 요약하는 추가 LLM 턴은 중복이자 지연이다.
                return

    # ------------------------------------------------------------------

    async def _natural_land_conversion_answer(self, user_query: str) -> str:
        """산지전용 건축 질문에 직전 필지 진단을 근거로 직접 답한다."""
        diagnosis = self.diagnosis or {}
        parcel = diagnosis.get("parcel") or {}
        location = diagnosis.get("location") or {}
        address = (
            parcel.get("jibun")
            or location.get("matched_address")
            or "선택한 필지"
        )
        model_diagnosis = json.loads(compact(diagnosis))
        facts = {
            "address": address,
            "verdict": diagnosis.get("verdict"),
            "jimok": parcel.get("jimok"),
            "zone": (diagnosis.get("regulation") or {}).get("zone"),
            "districts": (diagnosis.get("regulation") or {}).get("districts"),
            "constraints": (diagnosis.get("regulation") or {}).get("constraints"),
            "land_conversion": model_diagnosis.get("land_conversion"),
            "conversion_charge": model_diagnosis.get("conversion_charge"),
            "development_charge": model_diagnosis.get("development_charge"),
            "regulatory_screen": model_diagnosis.get("regulatory_screen"),
            "road_access": model_diagnosis.get("road_access"),
            "summary": diagnosis.get("summary"),
        }
        try:
            natural = await self.client.complete(
                system=_answer_system(
                    "사용자의 농지전용·산지전용 허가 가능 여부 질문에 한국어 대화체로 직접 답하라. "
                    "제공된 해당 필지 진단 사실만 사용하고 주소와 결론을 첫 문장에 분명히 "
                    "밝혀라. 지목과 질문이 어긋나면(예: 임야에 농지전용을 질문) 그 필지는 "
                    "농지전용이 아니라 산지전용 대상이라고 바로잡아라. 사용자가 전용허가 "
                    "자체의 가능성을 물으면 종합 건축 가능 "
                    "판정을 반복하지 말고 land_conversion의 status·summary·overlaps를 "
                    "우선해 전용허가 가능성, 제한 사유와 추가 심사항목만 설명하라. "
                    "산지전용허가는 산지를 다른 용도로 쓰기 위한 선행 절차일 뿐 "
                    "건축허가를 자동으로 보장하지 않는다는 점을 설명하라. 준보전산지는 "
                    "조건부 검토가 가능할 수 있지만, 보전산지·공익용산지·자연환경보전지역·"
                    "공원구역 등은 요청 시설이 법령상 허용행위에 해당하는지에 따라 건축이 제한되거나 불가할 "
                    "수 있으므로 실제 진단의 판정을 우선하라. 대체산림자원조성비는 진단 "
                    "자료상 관련성이 있을 때만 언급하고, conversion_charge에 계산값이 있으면 "
                    "전용예상면적·단가·예상액을 자연스럽게 포함하라. development_charge가 "
                    "있으면 대상 가능성과 부과율을 설명하되 정확액 산정에 종료시점 지가와 "
                    "개발비용이 필요하다는 점을 구분하라. 납부하면 허가된다는 식으로 말하지 "
                    "마라. 지형·임상도 참고값이 있으면 평균·최대 경사도, 표고 범위와 대표 "
                    "수종·영급·중첩률을 실제 숫자와 속성으로 설명하고, 참고자료와 허가 심사용 "
                    "현장조사 확정값을 구분하라. 수집값이 있는데도 자료가 없다고 말하거나 "
                    "일반적인 조사 필요 문구로만 대체하지 마라. 건축 모델을 추천하거나 종합 진단 카드를 반복하지 말고, 질문의 "
                    "표현에 맞춘 자연스러운 2~5문장으로 이유와 필요한 확인만 설명하라."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"사용자 질문: {user_query}\n"
                            f"해당 필지 진단 사실: {json.dumps(facts, ensure_ascii=False)}"
                        ),
                    }
                ],
                tools=[],
                max_tokens=700,
            )
            text = _strip_internal_field_names(" ".join(natural.texts).strip())
            if text:
                text = _ensure_query_evidence(text, diagnosis, user_query)
                return _ensure_collected_forest_evidence(text, diagnosis)
        except Exception:
            logger.debug("단일 용도 검토의견 LLM 실패, 결정적 폴백", exc_info=True)

        conversion = diagnosis.get("land_conversion") or {}
        conversion_status = conversion.get("status")
        conversion_summary = conversion.get("summary")
        if "농지전용" in user_query and parcel.get("jimok") in {"임", "임야"}:
            charge = diagnosis.get("conversion_charge") or {}
            charge_text = (
                f" 현재 계획의 산지전용 예상면적 {float(charge.get('area_m2') or 0):,.0f}㎡ "
                f"기준 대체산림자원조성비 참고액은 약 "
                f"{int(charge.get('estimated_won') or 0):,.0f}원입니다."
                if charge.get("type") == "forest_replacement"
                and charge.get("estimated_won")
                else ""
            )
            return _ensure_collected_forest_evidence((
                f"{address}은 지목이 임야이므로 농지전용 대상이 아니라 산지전용허가 "
                f"대상입니다. {conversion_summary or '산지 구분과 경사도·표고·입목축적을 확인해야 합니다.'}"
                f"{charge_text}"
            ), diagnosis)
        if conversion_status == "PERMIT_REQUIRED":
            return _ensure_collected_forest_evidence((
                f"{address}은 산지전용허가를 받아 전용을 검토할 수 있습니다. "
                f"{conversion_summary or '다만 경사도·표고·입목축적과 복구계획 등을 심사받아야 합니다.'} "
                "허가 여부는 이 심사 결과로 결정되며 대체산림자원조성비 납부만으로 "
                "허가되는 것은 아닙니다."
            ), diagnosis)
        if conversion_status == "RESTRICTED_REVIEW":
            return (
                f"{address}은 보전산지·공익용산지 등 제한 여부 때문에 산지전용허가가 "
                "가능하다고 바로 답할 수 없습니다. 허용행위에 해당하는 시설인지와 "
                "경사도·표고·입목축적을 관할 산림부서에서 먼저 확인해야 합니다."
            )
        if conversion_status in {"MANUAL_REVIEW", "UNKNOWN"}:
            return (
                f"{address}은 현재 자료만으로 산지전용허가 가능 여부를 확정할 수 없습니다. "
                f"{conversion_summary or '현황 산지와 산지 구분을 추가 확인해야 합니다.'}"
            )

        verdict = diagnosis.get("verdict")
        if verdict == "not_allowed":
            return (
                f"{address}은 산지전용 절차만 밟는다고 건축할 수 있는 필지가 아닙니다. "
                "산지전용은 건축허가를 자동으로 보장하지 않으며, 이 필지는 현재 확인된 "
                "용도지역·보전 규제로 건축이 불가합니다. 해당 시설이 법령상 "
                "예외적인 허용행위에 해당하는지는 관할 행정청에서 별도로 확인해야 합니다."
            )
        if verdict == "conditional":
            return (
                f"{address}은 산지전용허가 심사를 먼저 통과해야 하는 조건부 사항이 있습니다. "
                "산지전용은 선행 절차이지 건축허가 자체가 아니므로 개발행위, "
                "접도, 용도지역과 개별 보전 규제도 함께 충족해야 합니다."
            )
        return (
            f"{address}은 산지전용만으로 건축 가능하다고 확정할 수 없습니다. 산지 구분과 "
            "보전 규제상 허용행위 해당 여부를 먼저 확인한 뒤 개발행위·건축허가 요건을 "
            "별도로 검토해야 합니다."
        )

    async def _interpret_followup(self, user_query: str) -> dict:
        """제미나이가 필지별 상태를 읽고 후속 의도와 답변을 한 번에 결정한다."""
        diagnosis = self.diagnosis or {}
        address = (
            (diagnosis.get("parcel") or {}).get("jibun")
            or (self.selected_parcel or {}).get("address")
            or "선택한 필지"
        )
        previous_questions = [
            str(message.get("content") or "")
            for message in self.messages
            if message.get("role") == "user" and message.get("content")
        ]
        if previous_questions and previous_questions[-1] == user_query:
            previous_questions = previous_questions[:-1]
        recent_turns: list[dict] = []
        skipped_current = False
        for message in reversed(self.messages):
            role = str(message.get("role") or "")
            content = message.get("content")
            if (
                not skipped_current
                and role == "user"
                and str(content or "") == user_query
            ):
                skipped_current = True
                continue
            if role in {"user", "assistant"} and isinstance(content, str):
                recent_turns.append({"role": role, "content": content})
            if len(recent_turns) >= 8:
                break
        recent_turns.reverse()
        tool = {
            "name": "return_followup_interpretation",
            "description": "현재 필지 상태를 바탕으로 후속 질문의 의도와 답변을 반환한다.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": [
                            "possible_models",
                            "specific_use_feasibility",
                            "term_definition",
                            "parcel_fact",
                            "permit_procedure",
                            "followup_explanation",
                        ],
                    },
                    "subject": {"type": "string"},
                    "answer": {"type": "string"},
                    "map_lines": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["road", "building_line", "drainage"],
                        },
                        "description": (
                            "사용자가 지도에서 '보고 싶어 하는 선'이 있으면 그 종류를 담는다. "
                            "접한 도로·진입로·접도·맹지 여부를 묻거나 그 선을 보고자 하면 road, "
                            "건축선·이격·대지 안의 공지·후퇴선을 보고자 하면 building_line, "
                            "우수·오수 배수 방향·방류처·배수로를 보고자 하면 drainage. "
                            "단어가 아니라 의도로 판단하고, 선을 보려는 뜻이 없으면 빈 배열."
                        ),
                    },
                    "target_address": {
                        "type": "string",
                        "description": (
                            "사용자가 '현재 필지가 아닌 다른 주소'의 건축 가능 여부를 묻거나 "
                            "그 주소로 이동을 원하면 그 주소를 최대한 완전하게 담는다"
                            "(지번·도로명 모두. 예: '서울특별시 금천구 한내로 62'). "
                            "현재 필지를 설명하다 인접 지번을 '언급'만 한 경우(예: 접한 도로 "
                            "지번이 뭐냐)엔 담지 않는다. 이동·재진단 의도일 때만. 아니면 빈 문자열."
                        ),
                    },
                },
                "required": ["intent", "subject", "answer"],
            },
        }
        try:
            response = await self.client.complete(
                system=_answer_system(
                    "너는 같은 PNU의 후속 질문을 해석한다. 반드시 "
                    "return_followup_interpretation 도구를 한 번 호출하라. 단어 하나로 "
                    "판정하지 말고 현재 필지별 대화 상태와 이전 질문을 함께 읽어라. "
                    "다른 건축물·다른 용도·대체 모델의 조회나 선택을 원하면 possible_models, "
                    "새로운 특정 건축물 용도의 건축 가능 여부를 다시 계산해야 하면 "
                    "specific_use_feasibility, 용어의 뜻·개념을 묻는다면 term_definition, "
                    "현재 필지의 수치·규제·중첩 사실을 묻는다면 parcel_fact, 인허가 절차를 "
                    "묻는다면 permit_procedure, 나머지는 followup_explanation으로 분류하라. "
                    "표시·숨김 같은 지도 제어는 이 단계 전에 이미 처리된다. "
                    "answer에는 최신 진단 데이터에 근거해 질문에 직접 답하라. 사전적 의미 "
                    "질문은 뜻을 먼저 설명한 뒤 이 필지에서의 의미를 연결하라. possible_models는 "
                    "허용·조건부 용도를 간결히 검토하되 모델 버튼 문구를 직접 만들지 마라. "
                    "사용자가 지도에서 특정 선(접한 도로·진입로, 건축선·이격, 배수 방향)을 "
                    "보고 싶어 하는 뜻이면 map_lines 에 해당 종류를 담아라. 정해진 단어가 "
                    "아니라 의도로 판단하고, 선을 보려는 뜻이 아니면 빈 배열로 둔다. "
                    "사용자가 현재 필지가 아닌 '다른 주소'의 건축 가능 여부를 묻거나 그리로 "
                    "이동을 원하면, 그 주소를 target_address 에 완전하게 담아라(그러면 "
                    "answer 는 비워도 된다. 시스템이 그 주소로 이동해 새로 진단한다). "
                    "확인되지 않은 법적 사실이나 수치를 만들지 마라."
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"현재 질문: {user_query}\n"
                        f"현재 PNU/주소: {self._active_pnu()} / {address}\n"
                        f"필지별 대화 상태: {compact(self.conversation_context())}\n"
                        f"이전 질문·답변: "
                        f"{json.dumps(recent_turns, ensure_ascii=False)}\n"
                        f"최신 구조화 진단: {compact(diagnosis)}"
                    ),
                }],
                tools=[tool],
                max_tokens=500,
            )
            for call in response.tool_calls:
                if call.name == "return_followup_interpretation":
                    interpreted = dict(call.input or {})
                    logger.info(
                        "followup_llm pnu=%s intent=%s subject=%s",
                        self._active_pnu(),
                        interpreted.get("intent"),
                        interpreted.get("subject"),
                    )
                    return interpreted
        except Exception:
            logger.exception("followup intent interpretation failed")

        # LLM 장애 시에도 같은 필지 상태를 잃거나 종합진단을 다시 띄우지 않는다.
        # 기존 판별기는 가용성 fallback으로만 사용한다.
        intent = (
            "possible_models"
            if _asks_possible_use_list(user_query)
            else "specific_use_feasibility"
            if _has_building_feasibility_intent(user_query)
            else "followup_explanation"
        )
        return {"intent": intent, "subject": user_query, "answer": ""}

    async def _natural_followup_answer(self, user_query: str) -> str:
        """같은 필지 후속 질문에는 전체 보고서 없이 질문한 내용만 답한다."""
        diagnosis = self.diagnosis or {}
        address = (
            (diagnosis.get("parcel") or {}).get("jibun")
            or (diagnosis.get("location") or {}).get("matched_address")
            or "선택한 필지"
        )
        recent_user_questions = [
            str(message.get("content") or "")
            for message in self.messages
            if message.get("role") == "user" and message.get("content")
        ]
        # ask()가 현재 질문을 먼저 이력에 넣으므로, 제미나이에 전달하는 '이전'
        # 질문에서는 현재 질문 한 건을 제외한다.
        if recent_user_questions and recent_user_questions[-1] == user_query:
            recent_user_questions = recent_user_questions[:-1]
        recent_user_questions = recent_user_questions[-4:]
        recent_context = "\n".join(
            f"- {question}" for question in recent_user_questions
        )
        # "가능한 건물 뭐야?"는 LLM이 직전 용도를 임의로 단독주택으로 정해
        # "단독주택 외에"라고 답하지 않도록 판정표 목록에서 결정적으로 조립한다.
        # 특정 제외 용도를 사용자가 직접 말한 경우에만 그 용도를 제외한다.
        asks_possible_uses = _asks_possible_use_list(user_query)
        if asks_possible_uses:
            regulation = diagnosis.get("regulation") or {}
            overview = regulation.get("zone_use_overview") or {}
            allowed = list(overview.get("allowed") or [])
            conditional = list(overview.get("conditional") or [])
            explicit_exclusion = re.search(
                r"([가-힣0-9·]+(?:주택|시설))\s*(?:말고|외에|제외)",
                user_query,
            )
            if explicit_exclusion:
                excluded = explicit_exclusion.group(1)
                allowed = [use for use in allowed if use != excluded]
                conditional = [use for use in conditional if use != excluded]

            parts: list[str] = []
            if allowed:
                parts.append(f"가능한 용도는 {', '.join(allowed)}입니다.")
            if conditional:
                parts.append(
                    f"세부 규모와 관할 조례 확인이 필요한 조건부 용도는 "
                    f"{', '.join(conditional)}입니다."
                )
            if not parts:
                parts.append("현재 수집된 용도 판정표에는 가능 또는 조건부 용도가 없습니다.")
            return f"{address} 필지에서 " + " ".join(parts)

        try:
            response = await self.client.complete(
                system=_answer_system(
                    "같은 필지에 대한 후속 질문이다. 제공된 최신 진단 데이터만 근거로 "
                    "사용자가 방금 물은 내용에만 한국어 자연어로 직접 답하라. 종합 판정 "
                    "보고서, 섹션 제목, 번호 목록, 건축 모델 추천을 다시 출력하지 마라. "
                    "주소는 혼동 방지를 위해 첫 문장에 한 번만 자연스럽게 언급하라. "
                    # 단순 사실 질문은 짧게, 가능여부·절차 질문은 육하원칙 요소가 다 드러나게.
                    "단순 사실 하나를 묻는 질문(예: 이격이 얼마야, 공시지가 얼마야)이면 결론과 "
                    "핵심 이유를 1~3문장으로 간결하게 답하라. 다만 '○○ 지을 수 있냐'처럼 가능 "
                    "여부·절차를 묻는 질문이면 육하원칙 요소가 빠짐없이 드러나게 구조적으로 답하라 "
                    "— ①무엇이 가능한지(판정)와 이 필지 현황(무엇/어디), ②왜 그런지(조건부·제한 "
                    "사유), ③무엇이 필요하고 어떻게 진행하는지(필요 절차·요건·언제), ④어디에·누구에게 "
                    "문의하는지(토목/건축 설계사무소·관할청)를 각각 한두 문장으로 순서대로 짚어라. "
                    "단 '1.무엇 2.왜' 같은 번호·라벨은 붙이지 말고 자연스러운 문단으로 이어 써라. "
                    "질문하지 않은 수치를 무작정 전부 나열하지는 말고, 확인되지 않은 내용은 "
                    "확정하지 마라. "
                    "다만 질문이 산지·산지전용·경사도·표고·입목축적·임상도·개별 심의·"
                    "허가 요건에 관한 것이면, 최신 진단에 있는 실제 지형 통계와 임상도 "
                    "대표 속성을 반드시 읽어 조건 판단에 연결하라. 이미 수집된 참고값과 "
                    "현장조사로 확정해야 할 값을 분명히 구분하라. "
                    "질문이 생태·자연도·환경성 검토·별도관리지역 또는 조건부 판정 "
                    "사유에 관한 것이면 regulatory_screen의 실제 등급·중첩률·면적·"
                    "보전유형과 permit_requirements를 읽어 답하라. 1·2등급은 환경성 "
                    "검토와 관계기관 협의 필요성에 연결하고, 3등급은 참고정보로만 "
                    "설명하며 등급 하나만으로 건축 불가를 단정하지 마라. "
                    # 이격거리는 반드시 진단 데이터의 계산값을 그대로 읽어 답한다.
                    "이격거리를 물으면 진단 데이터의 전면 건축선 이격, 인접 대지경계 이격, "
                    "정북 일조 이격 계산값을 그대로 근거로 제시하라. 값이 0이면 '0m로 "
                    "확인됩니다'처럼 명확히 답하되, '시스템 계산상' 같은 표현은 쓰지 마라. "
                    "조례 별표를 아직 수집하지 못해 0m인 경우에는 '관할 건축조례 대지 안의 공지 "
                    "별표가 아직 수집되지 않아 0m로 확인됩니다'라고 사유를 밝혀라. 일반적인 "
                    "건축법 시행령 수치(1m, 50cm 등)를 진단값 대신 임의로 지어내지 마라. "
                    # 용어의 뜻·개념을 물으면 사전적 의미 + 현황 + 실질적 함의까지 해석한다.
                    "용어의 뜻·개념을 물으면(예: '건축선 후퇴가 이격거리야?', '이격거리가 뭐야', "
                    "'용적률이 뭐야') ①먼저 그 용어의 사전적 의미를 한두 문장으로 쉽게 설명하고, "
                    "②이어서 이 필지의 현황(진단 데이터의 해당 계산값)을 짚고, ③거기서 그치지 "
                    "말고 그 개념이 이 필지에서 갖는 실질적 의미까지 해석해 답하라. 예를 들어 "
                    "'이대로 지으려면 필지 분할이나 도로 편입이 필요한지, 어떤 절차·관계기관 협의가 "
                    "따르는지, 토목 설계사무소·건축 설계사무소·관할 행정청 중 어디에 문의해야 하는지'를 "
                    "자연스럽게 안내하라. (참고: 건축선 후퇴는 대체로 같은 대지 안에서 건축물을 "
                    "띄우는 것이라 필지 분할과는 다르며, 도로 폭이 부족하면 도로 중심선 후퇴선 안쪽이 "
                    "대지면적에서 제외될 수 있다.) 단, 확인되지 않은 구체 수치·절차·요건은 지어내지 "
                    "말고 방향과 문의처를 정성적으로 안내하라. "
                    # '다른 용도/건물'을 물으면 전체 용도 판정표를 기준으로 답한다.
                    "사용자가 '다른 건물/다른 용도/이 밖에 무엇을 지을 수 있나'를 명시적으로 물을 "
                    "때만 진단 데이터 "
                    "regulation.zone_use_overview 의 allowed(건축 가능)와 conditional(조건부 가능) "
                    "목록 전체를 읽어 건축물 종류를 알려줘라. 사용자가 특정 용도를 직접 "
                    "'말고·외에·제외'라고 하지 않았다면 단독주택 등 임의의 기준 용도를 만들거나 "
                    "'단독주택 외에'라고 표현하지 마라. "
                    "목록이 비어 있으면 그 사유를 설명하라. 용어 정의·이격·현황 등 '무엇을 지을 수 "
                    "있나'가 아닌 질문에는 이 '다른 건축물 목록'을 절대 덧붙이지 마라. "
                    # 특정 규제의 적용 여부는 자연어 의미를 해석하되 구조화 지정정보로 답한다.
                    "사용자가 특정 지역·지구·구역·규제가 이 필지에 적용되는지 묻는다면, 표현이 "
                    "정확한 법정 명칭이 아니어도 질문의 의미를 해석한 뒤 land_use.districts와 "
                    "land_use.designation_lookup의 포함·저촉 기록에서 대응하는 규제를 찾아라. "
                    "대응 규제가 있으면 포함 여부와 그 규제의 실질적 의미를 설명하고, 없으면서 "
                    "지정정보 조회가 완료된 경우에는 현재 지정정보상 확인되지 않았다고 답하라. "
                    "조회가 실패했으면 없다고 단정하지 마라. 이 질문에는 allowed·conditional "
                    "건축용도 목록이나 건축 모델을 덧붙이지 마라. 규제 명칭을 단순 반복하지 말고 "
                    "사용자가 물은 말의 사전적 의미와 이 필지에서의 적용 결과를 함께 설명하라. "
                    "진단 데이터가 '제한' 또는 '별도 확인 필요'라고만 하면 전면 금지로 확대해 "
                    "해석하지 마라. 제한 방식·축종·거리·예외가 수집되지 않았다면 '제한이 적용되며 "
                    "세부 기준은 해당 조례 확인이 필요하다'고 답하고, '설치할 수 없다'처럼 절대적으로 "
                    "단정하지 마라. "
                    # '또/그럼/그것'은 필지별 최근 사용자 질문을 이어받는다.
                    "'또', '그럼', '그것', '더 확인할 것'처럼 주어가 생략된 후속 질문은 아래의 "
                    "최근 사용자 질문에서 직전 주제를 복원해 답하라. 이를 새 종합진단 질문으로 "
                    "확대하지 말고 직전 주제와 직접 관련된 추가 사항만 설명하라. 사용자가 명시적으로 "
                    "가능 건축물의 종류·목록·모델을 묻지 않았다면 가능·조건부·불가 용도 목록을 "
                    "절대 나열하지 마라. "
                    # 말투: 딱딱한 수치 나열이 아니라 상담하듯 친절하게.
                    "말투는 고객 상담하듯 친절하게: 가능해 보이면 '수치적(사전검토)으로 볼 때 "
                    "…가 가능할 것으로 보입니다'처럼 안내하라. "
                    # 문의처는 '이 필지 현황상 실제로 무엇이 필요한지'를 보고 케이스로 나눠 안내한다.
                    "그리고 이 필지의 현황(진단 데이터의 지목·전용 필요 여부, 개발행위허가 대상 "
                    "여부, 도로 접함·경사도·토지형질변경 필요성 등)을 보고, 실제로 어떤 절차가 "
                    "필요한지 구체적으로 짚어 준 뒤 문의처를 케이스로 나눠 안내하라 — 즉 부지 조성· "
                    "절토/성토·옹벽·배수·경사도·개발행위허가·산지/농지 전용·도로 개설·현황측량 같은 "
                    "'땅을 만드는' 작업이 필요한 경우에는 '토목 설계사무소'에도 함께 문의하도록 "
                    "안내하고, 그런 토목 작업 없이 건축물만 올리면 되는 단순한 경우에는 '건축허가만 "
                    "필요하니 건축 설계사무소만으로 충분하다'고 명확히 구분해 알려줘라. 건축물 배치· "
                    "구조·용도·이격·건축허가는 '건축 설계사무소' 소관이다. 최종 인허가 가능 여부는 "
                    "관할 행정청(시·군·구청)에 확인하도록 정성적으로 안내하라. "
                    # 필요한 것을 다 쏟아내기보다, 의도가 갈리면 되물어 방향을 좁힌다.
                    "핵심만 답한 뒤, 사용자의 질문 의도가 여러 갈래로 해석될 수 있거나 이어서 "
                    "궁금해할 만한 방향이 있으면, 답변 끝에 '혹시 …가 궁금하신 게 맞을까요? 아니면 "
                    "…도 함께 확인해 드릴까요?'처럼 한 문장으로 되물어 방향을 좁혀라. 단, 매 답변마다 "
                    "억지로 붙이지 말고 되물음이 자연스러울 때만 짧게 덧붙여라. "
                    # 내부 데이터 필드명(영어)은 사용자에게 절대 노출하지 않는다.
                    "답변에는 front_setback_m, adjacent_setback_m, north_setback_m, "
                    "zone_use_overview, site_constraints 같은 영어 필드명·변수명을 절대 쓰지 "
                    "마라. 괄호로도 병기하지 마라. 반드시 '전면 건축선 이격', '인접 대지경계 이격', "
                    "'정북 일조 이격'처럼 한국어 용어로만 표현하라."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"후속 질문: {user_query}\n"
                            f"같은 필지의 최근 사용자 질문:\n{recent_context or '- 없음'}\n"
                            f"현재 필지별 대화 상태: "
                            f"{compact(self.conversation_context())}\n"
                            f"현재 필지: {address}\n"
                            f"최신 진단: {compact(diagnosis)}"
                        ),
                    }
                ],
                tools=[],
                max_tokens=400,
            )
            text = _strip_internal_field_names(" ".join(response.texts).strip())
            if text:
                forest_relevant = bool(
                    re.search(
                        r"산지|산림|임야|경사도|표고|입목|임상도|"
                        r"개별.*(?:심의|허가)|(?:심의|허가).*요건|"
                        r"거쳐야.*(?:심의|허가)|조건부.*(?:조건|이유|절차)",
                        user_query,
                    )
                )
                return _ensure_collected_forest_evidence(
                    _ensure_query_evidence(text, diagnosis, user_query),
                    diagnosis,
                    relevant=forest_relevant,
                )
        except Exception:
            logger.debug("검토의견 LLM 실패, 결정적 폴백", exc_info=True)
        verdict = {
            "allowed": "건축 가능한 판정입니다",
            "conditional": "현재 조건부 가능합니다",
            "not_allowed": "현재 진단 기준으로는 건축할 수 없습니다",
            "unknown": "추가 규제 확인 전에는 가능 여부를 확정할 수 없습니다",
        }.get(diagnosis.get("verdict"), "추가 확인이 필요합니다")
        reason = (diagnosis.get("regulation") or {}).get("reason") or ""
        return f"**{address}**은 {verdict}. {reason}".strip()

    async def _verdict_judgment(self, user_query: str) -> str:
        """단일·복합 용도 질문에 대해 진단 데이터를 읽어 유의사항 아래 붙일 판단 문단.

        사용자가 특정 용도(단독주택·업무시설·창고 등)나 층별 복합 용도(1층 임대
        원룸+2층 자가 등)를 물었을 때, 종합 판정 카드 아래에 제미나이가 진단
        데이터를 읽고 해석한 결론 문단을 붙인다. 값을 지어내지 않는다.
        """
        diagnosis = self.diagnosis or {}
        address = (
            (diagnosis.get("parcel") or {}).get("jibun")
            or (diagnosis.get("location") or {}).get("matched_address")
            or "선택한 필지"
        )
        use = (
            (diagnosis.get("request") or {}).get("building_use")
            or (diagnosis.get("regulation") or {}).get("building_use")
            or "요청 용도"
        )
        try:
            response = await self.client.complete(
                system=_answer_system(
                    "너는 건축 인허가 사전검토 상담원이다. 아래 진단 데이터만 근거로, "
                    "사용자가 물은 용도(단일 용도 또는 층별 복합 용도)를 이 필지에 지을 수 "
                    "있는지 판단해 한 문단(2~4문장, 약 450자 이내)으로 답하라. 화면에서 "
                    "최대 8줄을 넘기지 말라. 첫 문장에 전체 지번 주소와 "
                    "검토 용도를 자연스럽게 포함하라. 결론은 진단 verdict의 의미를 바꾸지 "
                    "말고 명확하게 표현하라(allowed=건축 가능, conditional=조건부 가능이며 "
                    "선행조건 명시, not_allowed=건축 불가이며 사유 명시, "
                    "unknown=추가 확인 필요). "
                    "판단 근거는 데이터의 용도지역, regulation.zone_use_overview 의 "
                    "allowed(건축 가능)·conditional(조건부 가능), use_restriction(개별 제한), "
                    "건폐율·용적률, 도로 접함, site_constraints 이격과 "
                    "land_conversion의 산지구분·지형 통계·임상도에서 가져오고 "
                    "수치나 근거를 지어내지 마라. 층별 복합 용도를 물었으면 각 층 용도가 "
                    "이 용도지역에서 허용되는지 함께 짚어라. 표·목록·섹션 제목 없이 "
                    "임야이고 지형·임상도 참고값이 있으면 평균·최대 경사도와 표고 범위, "
                    "대표 수종·영급·중첩률을 실제 값으로 해석하고, 허가 심사용 현장조사 "
                    "확정값과 구분하라. 데이터가 있는데 조사자료가 없다는 일반론만 쓰지 마라. "
                    "regulatory_screen의 생태·자연도 1·2등급 또는 별도관리지역이 "
                    "조건부 판정에 포함되면 실제 등급·중첩률·면적·보전유형을 읽어 "
                    "사업계획의 환경성 검토와 관계기관 협의에 어떤 영향을 주는지 설명하라. "
                    "3등급은 참고자료로만 해석하고, 등급만으로 건축 불가라고 확대하지 마라. "
                    "상세자료를 다시 나열하는 데 그치지 말고 육하원칙에 따라 왜 이 판정인지, "
                    "동시에 적용되는 법적 요건은 무엇인지, 사업자가 언제 무엇을 보완해야 하는지, "
                    "관할 부서와 관계기관이 어떤 협의·심의·허가로 확인하는지, 각 법령의 조건을 "
                    "어떻게 모두 충족해야 최종 허가가 가능한지를 인과관계로 설명하라. 한 법의 "
                    "허가가 다른 법의 허가를 대신한다고 쓰지 마라. 데이터가 법적 충돌을 명시하지 "
                    "않으면 충돌한다고 지어내지 말고 여러 법령의 요건이 함께 적용된다고 표현하라. "
                    "상담하듯 자연스러운 한국어 문단 하나로만 답하라. "
                    "front_setback_m, adjacent_setback_m, north_setback_m, zone_use_overview "
                    "같은 영어 필드명·변수명을 답변에 절대 쓰지 말고(괄호 병기도 금지), "
                    "'전면 건축선 이격', '인접 대지경계 이격'처럼 한국어 용어로만 표현하라."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"질문: {user_query}\n"
                            f"필지 주소: {address}\n"
                            f"검토 용도: {use}\n"
                            f"진단 데이터: {compact(diagnosis)}"
                        ),
                    }
                ],
                tools=[],
                max_tokens=280,
            )
            text = _strip_internal_field_names(" ".join(response.texts).strip())
            text = _ensure_query_evidence(text, diagnosis, user_query)
            text = _ensure_collected_forest_evidence(text, diagnosis)
            return _limit_review_length(text)
        except Exception:
            logger.debug("단일용도 검토의견 LLM 생성 실패", exc_info=True)
            return ""

    async def _all_uses_verdict_judgment_with_llm(self, user_query: str) -> str:
        """모든 용도 검토 의견은 Gemini가 구조화 진단을 읽어 작성한다."""
        diagnosis = self.diagnosis or {}
        try:
            response = await self.client.complete(
                system=_answer_system(
                    "너는 건축 인허가 사전검토 상담원이다. 제공된 구조화 진단 데이터만 "
                    "읽고 모든 건축물 용도에 대한 '검토 의견'을 자연스러운 한국어 한 문단 "
                    "정확히 3문장, 약 350자 이내로 작성하라. 화면에서 최대 8줄을 넘기지 않는 "
                    "분량이어야 한다. 이 문단은 위 상세자료를 다시 나열하는 요약이 아니라 "
                    "자료 사이의 인과관계와 해결 경로를 설명하는 종합 의견이다. 계산·판정·법적 "
                    "조건을 새로 만들지 말고 데이터의 verdict와 실제 필지 값을 해석만 하라. "
                    "육하원칙의 핵심만 자연스럽게 압축하라: ①어느 필지에서 무엇이 가능한지, "
                    "②왜 조건부·불가·추가확인인지, ③어떤 법령상 요건들이 동시에 적용되는지, "
                    "④그 요건을 누가(관할 부서·관계기관) 어떤 협의·심의·허가로 확인하는지, "
                    "⑤사업계획 확정 전후 어느 단계에서 무엇을 보완해야 하는지, ⑥어떻게 모든 "
                    "조건을 충족해야 최종 허가가 가능한지를 짧게 이어서 설명하라. 첫 문장은 반드시 "
                    "'[전체 지번]은 [용도지역]의 [지목]로, [대표 용도 3개] 등은 건축 가능 또는 "
                    "조건부 허용 대상입니다.' 정도의 짧은 구조로 써라. 가능한 용도와 조건부 용도에서 "
                    "대표 건축물만 합계 3개 제시하라. 단, placement_restricted가 true이거나 "
                    "map_presentation.verdict가 not_allowed이면 이 필지는 실질적으로 신축 배치가 "
                    "불가하므로, 첫 문장을 '가능/조건부 허용' 대신 '[전체 지번]은 용도지역상으로는 "
                    "여러 용도가 조건부이나, [기존 건축물 규모 또는 협소 대지 사유]로 실질적으로 신축 "
                    "배치가 불가합니다.'처럼 배치 불가 결론으로 시작하고, existing_buildings의 실제 "
                    "동수·용도(공동주택 등)나 최소 대지면적 미달을 사유로 읽어 멸실·해체 선행 또는 "
                    "합필 필요를 설명하라. 둘째 문장에는 조건부 판정에 직접 영향을 준 "
                    "핵심 규제와 서로 다른 "
                    "법률의 요건은 하나가 다른 하나를 대신하지 않고 각각 충족해야 한다는 점과 "
                    "permit_requirements에 있는 관계기관 협의·필요한 심의·허가를 통해 조정·확인하는 "
                    "경로만 설명하라. 셋째 문장에는 최종 허가를 위해 모두 충족할 조건만 요약하라. "
                    "부서명·제출서류·조사서 목록은 쓰지 마라. 실제 데이터가 법적 충돌을 명시하지 않으면 '충돌한다'고 "
                    "지어내지 말고 '요건이 함께 적용된다'고 표현하라. 상세 보고서에 이미 있는 "
                    "경사도·표고·임상도·접도·부담금 수치를 전부 반복하지 말고 결론을 좌우하는 "
                    "값만 인과관계에 사용하라. 부담금이 있으면 부과 가능성과 감면·면제 가능성을 "
                    "한 문장 안에서 함께 설명하라. 조사서·제출서류·부서별 세부 업무는 사용자가 "
                    "후속 질문할 때 답할 내용이므로 여기서는 나열하지 마라. '확인된 현황상', '등처럼', "
                    "regulatory_screen에 생태·자연도 1·2등급 또는 별도관리지역 "
                    "중첩이 있으면 조건부 판정의 원인으로 실제 등급과 중첩률을 읽고 "
                    "환경성 검토·관계기관 협의와 연결하라. 3등급은 참고정보로만 다루고 "
                    "생태·자연도 등급만으로 건축 불가를 만들지 마라. "
                    "'판정에는 ~이(가) 반영', '검토할 수 있는 용도가 있습니다' 같은 기계적인 "
                    "표현을 쓰지 마라. '다만'을 연달아 반복하지 말고 문장 사이의 원인과 결과가 "
                    "자연스럽게 이어지게 하라. 모든 용도의 결과가 같다고 말하지 말고 용도를 "
                    "확정하면 개별 판정이 필요하다는 점을 짧게 밝혀라. 영어 필드명, JSON 표현, "
                    "표, 목록, 제목은 출력하지 마라."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"사용자 질문: {user_query}\n"
                            f"구조화 진단 데이터: {compact(diagnosis)}"
                        ),
                    }
                ],
                tools=[],
                max_tokens=450,
            )
            text = _strip_internal_field_names(" ".join(response.texts).strip())
            return _limit_review_length(text, max_sentences=3)
        except Exception:
            logger.debug("전체용도 검토의견 LLM 생성 실패", exc_info=True)
            return ""

    def _render_event(self) -> dict:
        """현재 진단을 지도 명령으로 바꿔 프론트로 보낼 이벤트."""
        return {
            "event": "map_commands",
            "data": {"commands": build_map_commands(self.diagnosis or {})},
        }

    async def render_pending_judgment(self, query: str) -> dict | None:
        """최초 진단 카드의 '검토 의견'(판단 문단)을 뒤늦게 계산해 message 이벤트로 만든다.
        무거운 LLM(≈5s)이라 카드·지도를 먼저 보낸 뒤 소비 지점이 이걸 호출해 이어붙인다.
        단일 용도면 _verdict_judgment, 그 외(용도 미지정·시설물)면 all-uses 판단을 쓴다.
        """
        diagnosis = self.diagnosis or {}
        req = diagnosis.get("request") or {}
        names_specific_use = bool(
            not req.get("inferred", True) and req.get("building_use") != "시설물"
        )
        judgment = ""
        if names_specific_use:
            try:
                judgment = await asyncio.wait_for(
                    self._verdict_judgment(query), timeout=8.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "single-use verdict judgment timeout; using deterministic fallback"
                )
            if not judgment:
                judgment = _deterministic_verdict_judgment(diagnosis)
        else:
            try:
                judgment = await asyncio.wait_for(
                    self._all_uses_verdict_judgment_with_llm(query), timeout=8.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "all-uses verdict judgment timeout; using deterministic fallback"
                )
            if not judgment:
                judgment = _all_uses_verdict_judgment(diagnosis)
        # 실질 배치 불가(협소·기존건물)면 그 사유를 검토 의견 맨 앞에 결정적으로 둔다.
        # LLM 문단이 '조건부'로 시작해도 배지(실질 배치 불가)와 어긋나지 않게 보장하는
        # 안전장치다(제미나이가 프롬프트 지시를 안 따를 때 대비). 이미 배치 불가로
        # 시작하면 중복해 붙이지 않는다.
        if diagnosis.get("placement_restricted") and "배치" not in judgment[:80]:
            existing = diagnosis.get("existing_buildings") or {}
            lot = diagnosis.get("min_lot_area") or {}
            if existing.get("has_buildings"):
                lead = (
                    f"이 필지에는 기존 건축물 {existing.get('count')}건이 확인되어, "
                    "멸실·해체를 선행하지 않으면 신축을 배치할 수 없습니다(실질 배치 불가)."
                )
            else:
                lead = lot.get("note") or "이 필지는 실질적으로 신축 배치가 불가합니다."
            judgment = f"{lead}\n\n{judgment}".strip()
        if not judgment:
            return None
        return {"event": "message", "data": {"text": f"## 검토 의견\n{judgment}"}}

    async def _diagnose_and_emit(
        self, query: str, emit_card: bool = True, lines_only: list | None = None
    ) -> tuple[dict, list[dict]]:
        """진단을 돌리고 지도 반영·경고를 이벤트로 내보낸다.

        emit_card=True (처음 '지을 수 있어?' 진단): 종합 판정 카드를 확정 형식으로
          한 번 표시한다(사실이 사라지지 않게).
        emit_card=False (recheck_use 등 후속 용도 검토): 지도만 갱신하고 카드는
          다시 찍지 않는다. 모델이 데이터를 근거로 자연어로 답하게 한다.
        lines_only=[...] ('건축선 그려줘'·'도로 접촉 있어?' 등 선만 묻는 질문): 진단은
          돌려 선 기하를 얻되, 종합판정 카드·3D 매스·가능여부 팝업은 내지 않고 요청한
          선만 지도에 얹는다.
        """
        events: list[dict] = []
        steps: list[dict] = []
        previous_diagnosis = self.diagnosis
        previous_pnu = (
            ((previous_diagnosis or {}).get("parcel") or {}).get("pnu") or ""
        )
        if previous_pnu:
            self._diagnosis_by_pnu[previous_pnu] = previous_diagnosis
            self._messages_by_pnu[previous_pnu] = list(self.messages)
            self._diag_shown_by_pnu[previous_pnu] = self._diag_shown
        self.diagnosis = await run_prediagnosis(
            self.client,
            query,
            on_progress=lambda step, payload: steps.append(
                {"event": "diagnosis_step", "data": {"step": step, "input": payload}}
            ),
        )

        # 요청한 단일 용도가 용도지역상 확정적으로 불가한지는 지도 명령을 만들기
        # 전에 반영한다. 예전에는 패널·매스 렌더링 뒤에 경고만 추가해, 빨간
        # '건축 불가' 팝업 아래에 건물 모델과 치수선이 남는 모순이 생겼다.
        restriction = detect_use_restriction(
            getattr(self, "_last_query", ""), self.diagnosis
        )
        if restriction and restriction.get("kind") != "verification_required":
            self.diagnosis["use_restriction"] = restriction
            regulation = self.diagnosis.setdefault("regulation", {})
            regulation["map_presentation"] = {
                "verdict": "not_allowed",
                "label": "건축 불가",
                "color": "#C62828",
                "show_building_mass": False,
                "show_building_dimensions": False,
                "reason": restriction.get("reason", ""),
            }

        location = self.diagnosis.get("location") or {}
        parcel = self.diagnosis.get("parcel") or {}
        if location.get("lon") is not None and location.get("lat") is not None:
            diagnosed_pnu = parcel.get("pnu") or ""
            active_pnu = (self.selected_parcel or {}).get("pnu") or ""
            explicit_new_location = bool(
                getattr(self, "_turn_has_explicit_address", False)
                or getattr(self, "_turn_requests_location_change", False)
            )
            if (
                explicit_new_location
                or not active_pnu
                or not diagnosed_pnu
                or active_pnu == diagnosed_pnu
            ):
                self.set_selected_parcel(
                    lon=location["lon"],
                    lat=location["lat"],
                    address=parcel.get("jibun") or location.get("matched_address") or "",
                    pnu=diagnosed_pnu,
                )
                self._selection_changed = False
        # 필지별 진단 기억 — 이전 필지 재클릭 시 복원해 후속질문으로 잇기 위함.
        _pnu = (self.diagnosis.get("parcel") or {}).get("pnu")
        if _pnu:
            self._diagnosis_by_pnu[_pnu] = self.diagnosis
            self.update_conversation_context(
                active_address=parcel.get("jibun")
                or location.get("matched_address"),
                active_pnu=_pnu,
                active_building_use=(
                    (self.diagnosis.get("request") or {}).get("building_use")
                ),
            )
        events.extend(steps)
        events.append({"event": "diagnosis", "data": self.diagnosis})

        # 선만 묻는 질문: 진단 데이터로 요청한 선만 얹고, 카드·3D 매스·팝업은 내지 않는다.
        if lines_only:
            from .agents.map_control import build_lines_only_commands

            cmds = build_lines_only_commands(self.diagnosis, lines_only)
            events.append({"event": "map_commands", "data": {"commands": cmds}})
            return self.diagnosis, events

        # 진단이 끝나면 지도 반영은 확정 절차다. 여기서 바로 실행한다.
        events.append(self._render_event())

        # 한 문장에 필지·건물 종류·층수·토공 전후 상태를 함께 요청하면 진단 뒤
        # 정확히 그 모델 하나를 지도에 올린다. 규제 한계 매스는 숨겨 다른 건물로
        # 오인되지 않게 한다. 불가 판정에는 모델을 그리지 않는다.
        request_text = getattr(self, "_last_query", "")
        model_map = {
            "창고": "warehouse",
            "공장": "factory",
            "상가": "commercial",
            "단독주택": "detached",
            "공동주택": "lowrise",
            "주택": "detached",
        }
        requested_model = next(
            (model for keyword, model in model_map.items() if keyword in request_text),
            None,
        )
        wants_model = requested_model and re.search(r"(모델|건물|보여|올려|배치)", request_text)
        display_verdict = (
            ((self.diagnosis.get("regulation") or {}).get("map_presentation") or {})
            .get("verdict")
            or (self.diagnosis.get("regulation") or {}).get("verdict")
            or self.diagnosis.get("verdict")
        )
        if wants_model and display_verdict != "not_allowed":
            floor_match = re.search(r"(\d+)\s*층", request_text)
            floors = int(floor_match.group(1)) if floor_match else None
            before = bool(
                re.search(r"(토공|절토|성토|평탄화)\s*(하기\s*)?(전|이전)", request_text)
                or re.search(r"(원지형|원래\s*지형)", request_text)
            )
            events.append(
                {
                    "event": "map_commands",
                    "data": {
                        "commands": [
                            {
                                "type": "show_housing_model",
                                "model": requested_model,
                                "floors": floors,
                                "earthwork_mode": "original" if before else "graded",
                                "hide_envelope": True,
                            }
                        ]
                    },
                }
            )

        # 사용자가 말한 용도의 제한은 진단 상태에는 보존한다. 다만 최초 진단은
        # show_panel의 종합 판정 배지와 본문에 이미 같은 불가 사유를 표시하므로
        # verdict_warning을 또 보내지 않는다. 이중 경고는 모든 지역에서 같은
        # 판정을 두 번 보여 주고 팝업 높이만 늘리는 전역 UI 오류였다.
        if restriction and restriction.get("kind") != "verification_required":
            self.diagnosis["use_restriction"] = restriction

        if emit_card:
            # 최초 진단만 확정 형식 카드를 한 번 표시한다. '검토 의견'(판단 문단)은
            # 무거운 LLM(≈5s)이라 지금 계산하지 않는다 — 종합판정 카드·가능 모델을 먼저
            # 흘려보내고, 그 아래 붙일 검토 의견은 pending_judgment 마커로 미뤄, 소비 지점
            # (main.py)이 이어서 계산·방출한다. 지도·카드가 판정 LLM을 기다리지 않게 하는
            # 스트리밍 최적화다(총 시간은 같아도 체감이 빠르다).
            card_text = format_diagnosis_answer(self.diagnosis)
            events.append(
                {"event": "message", "data": {"text": card_text}}
            )
            # 요청 용도의 전용 모델이 아직 없어도, 같은 필지에서 허용되는
            # 용도 가운데 준비된 모델은 최초 진단 하단에 함께 제시한다.
            model_options = _model_options_for_diagnosis(
                self.diagnosis,
                include_alternatives=True,
            )
            if model_options:
                events.append({
                    "event": "message",
                    "data": {
                        "text": "**가능 모델**\n허용되는 용도 중 준비된 모델만 보여드립니다.",
                        "options": model_options,
                    },
                })
            # 검토 의견은 뒤이어 방출 — 마커로 남기고 소비 지점이 render_pending_judgment 호출.
            events.append(
                {"event": "pending_judgment", "data": {"query": query}}
            )
            self._diag_shown = True
            if _pnu:
                self._diag_shown_by_pnu[_pnu] = True
            # 판단 문단까지 시스템이 (곧) 표시하므로 모델이 결론을 되풀이하지 않게 한다.
            note = (
                "종합 판정 카드와 그 아래 '검토 의견'(가능/불가 판단 문단)까지 시스템이 "
                "이미/곧 화면에 표시한다. 같은 판정을 다시 서술하거나 표·번호목록으로 "
                "나열하지 마라. 필요하면 사용자가 이어서 확인하면 좋을 다음 단계 한 문장만 "
                "덧붙이거나, 덧붙일 것이 없으면 생략하라."
            )
        else:
            # 후속 용도 검토 — 카드를 다시 찍지 않는다. 모델이 자연어로 답한다.
            note = (
                "이 필지에 대해 새 용도로 다시 판정한 데이터다(지도는 갱신됨). "
                "첫 문장에는 진단 데이터의 전체 지번 주소를 한 번 명시하고, 주소 없이 "
                "'해당 필지'라고만 쓰지 마라. "
                "카드를 다시 출력하지 마라. 바뀐 핵심만 — 이 용도가 되는지/안 되는지, "
                "그 이유, 건폐율·용적률·규모가 달라졌으면 그 요지 — 를 2~4문장 자연어로 "
                "대화하듯 답하라. 표·번호목록 금지."
            )

        return {
            "diagnosis": compact(self.diagnosis),
            "rendered_on_map": True,
            "answer_card_already_shown": emit_card,
            "note": note,
        }, events

    async def _run_tool(self, name: str, args: dict) -> tuple[dict, list[dict]]:
        """도구를 실행하고 (모델에게 돌려줄 결과, 프론트로 흘릴 추가 이벤트) 반환."""
        events: list[dict] = []

        if name == "prediagnose":
            query = args["query"]
            if (
                self.selected_parcel
                and not getattr(self, "_turn_has_explicit_address", False)
                and not getattr(self, "_turn_requests_location_change", False)
            ):
                selected = self.selected_parcel
                query = (
                    f"지도에서 현재 선택된 위치(경도 {selected['lon']:.7f}, "
                    f"위도 {selected['lat']:.7f})의 필지에 대한 후속 질문이다. "
                    f"다른 주소를 추측하거나 이동하지 마라. 사용자 질문: {self.messages[-1]['content']}"
                )
            # 세션에 이미 필지 진단이 있으면 일반 LLM 도구 경로에서도 종합 카드를
            # 반복하지 않는다. 새 주소·좌표의 최초 진단은 위 결정적 분기가 담당한다.
            return await self._diagnose_and_emit(
                query, emit_card=not bool(self.diagnosis)
            )

        if name == "move_to_parcel":
            if (
                self.selected_parcel
                and not getattr(self, "_turn_has_explicit_address", False)
                and not getattr(self, "_turn_requests_location_change", False)
            ):
                return {
                    "found": False,
                    "blocked": True,
                    "note": (
                        "사용자가 새 주소나 이동을 요청하지 않았다. 현재 선택 필지를 "
                        "유지하고 질문에만 답하라."
                    ),
                }, events
            # 도구 인자보다 사용자 원문을 우선한다. 모델이 '초평동 157-2'를
            # 강원도 등 다른 지역으로 확장해도 원문의 동/리+번지를 복원한다.
            original = getattr(self, "_last_query", "")
            full_match = re.search(
                r"((?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)*"
                r"(?:산\s*)?\d+(?:-\d+)?)",
                original,
            )
            short_match = re.search(
                r"([가-힣0-9]+(?:읍|면|동|리)\s+(?:산\s*)?\d+(?:-\d+)?)",
                original,
            )
            query = (
                full_match.group(1)
                if full_match
                else short_match.group(1)
                if short_match
                else args.get("query") or ""
            ).strip()
            candidates = await vworld.search_addresses(query)
            locality = re.search(r"([가-힣0-9]+(?:읍|면|동|리))", query)
            lot = re.search(r"((?:산\s*)?\d+(?:-\d+)?)$", query)

            def _candidate_lot(candidate: dict) -> str:
                address = candidate.get("parcel") or candidate.get("address") or ""
                found = re.search(r"((?:산\s*)?\d+(?:-\d+)?)\s*$", address)
                return found.group(1).replace(" ", "") if found else ""

            exact = [
                c for c in candidates
                if (not locality or locality.group(1) in (c.get("parcel") or c.get("address") or ""))
                and (
                    not lot
                    or _candidate_lot(c) == lot.group(1).replace(" ", "")
                )
            ]
            # 프론트에서 사용자가 전국 후보 중 하나를 선택해 전체 주소를 보냈다면
            # 백엔드도 그 전체 주소를 우선한다. 다시 '현리 435-8'만 보고 전국
            # 동명이번지 후보로 되돌아가면 카메라만 움직이고 필지 경계가 사라진다.
            full_address_matches = [
                c
                for c in exact
                if (
                    (c.get("parcel") and c["parcel"] in original)
                    or (c.get("address") and c["address"] in original)
                )
            ]
            if len(full_address_matches) == 1:
                exact = full_address_matches
            if len(exact) != 1:
                options = exact or candidates
                return {
                    "found": False,
                    "candidates": [c.get("parcel") or c.get("address") for c in options],
                    "note": (
                        "정확히 일치하는 주소가 하나가 아니다. 다른 지역을 임의 선택하지 말고 "
                        "시·군을 포함한 주소를 요청하라."
                    ),
                }, events

            selected = exact[0]
            parcel = await vworld.get_parcel(selected["lon"], selected["lat"])
            commands = [
                {"type": "clear_mass"},
                {
                    "type": "fly_to",
                    "lon": selected["lon"],
                    "lat": selected["lat"],
                    "altitude": 900,
                    "tilt": 45,
                    "heading": 0,
                },
                {
                    "type": "highlight_parcel",
                    "geometry": parcel["geometry"],
                    "pnu": parcel.get("pnu", ""),
                    "label": parcel.get("jibun") or selected["address"],
                    "color": "#00E5FF",
                },
            ]
            events.append({"event": "map_commands", "data": {"commands": commands}})
            return {
                "found": True,
                "address": parcel.get("jibun") or selected["address"],
                "note": "요청한 지번 필지로 이동하고 경계를 표시했다. 한 문장으로 알려라.",
            }, events

        if name == "move_to_place":
            if (
                self.selected_parcel
                and not getattr(self, "_turn_requests_location_change", False)
            ):
                return {
                    "found": False,
                    "blocked": True,
                    "note": (
                        "사용자가 장소 이동을 요청하지 않았다. 현재 선택 필지를 유지하라."
                    ),
                }, events
            query = (args.get("query") or "").strip()
            places = await vworld.search_places(query)
            if not places:
                # 지역명을 함께 넣었을 때 결과가 없는 일부 POI를 위해 장소명만 재시도한다.
                stripped = re.sub(
                    r"(?:[가-힣]+(?:특별시|광역시|특별자치시|도|특별자치도|시|군|구|읍|면|동|리))\s*",
                    "",
                    query,
                ).strip()
                if stripped and stripped != query:
                    places = await vworld.search_places(stripped)
            if not places:
                return {
                    "found": False,
                    "note": f"'{query}' 장소를 찾지 못했다. 정확한 장소명을 요청하라.",
                }, events

            # 사용자 문장에 들어 있는 행정동을 주소에 포함한 결과를 우선한다.
            locality_terms = re.findall(r"[가-힣0-9]+(?:읍|면|동|리)", query)
            place = next(
                (
                    p for p in places
                    if all(term in f"{p.get('road', '')} {p.get('parcel', '')}" for term in locality_terms)
                ),
                places[0],
            )
            parcel = await vworld.get_parcel(place["lon"], place["lat"])
            commands = [
                {"type": "clear_mass"},
                {
                    "type": "fly_to",
                    "lon": place["lon"],
                    "lat": place["lat"],
                    "altitude": 900,
                    "tilt": 45,
                    "heading": 0,
                }
            ]
            if parcel.get("geometry"):
                commands.append({
                    "type": "highlight_parcel",
                    "geometry": parcel["geometry"],
                    "pnu": parcel.get("pnu", ""),
                    "label": parcel.get("jibun") or place["title"],
                    "color": "#00E5FF",
                })
            events.append({"event": "map_commands", "data": {"commands": commands}})
            return {
                "found": True,
                "place": place["title"],
                "address": place.get("road") or place.get("parcel"),
                "parcel": parcel.get("jibun"),
                "note": "지도 이동과 필지 경계 표시를 완료했다. 한 문장으로 알려라.",
            }, events

        if name == "lookup_parcel_facts":
            # 인허가와 무관한 필지 사실 질문 — 카드 없이 값만 돌려주고,
            # 모델이 자연어로 답한다. 좌표가 없으면 직전 진단 필지를 쓴다.
            lon = args.get("lon")
            lat = args.get("lat")
            if lon is None or lat is None:
                loc = (self.diagnosis or {}).get("location") or {}
                lon, lat = loc.get("lon"), loc.get("lat")
            if lon is None or lat is None:
                return {
                    "error": "어느 필지인지 알 수 없습니다. 지도에서 필지를 먼저 선택하거나 주소를 알려주세요.",
                }, events
            parcel, land_use = await asyncio.gather(
                vworld.get_parcel(float(lon), float(lat)),
                vworld.get_land_use(float(lon), float(lat)),
            )
            # 용도지구(보전산지·준보전산지·지구단위계획·교육환경보호구역 등)는 용도지역
            # 레이어엔 거의 안 잡히고 getLandUseAttr(PNU)에서 온다. 직전 진단이 같은
            # 필지면 그 병합본을 쓰고, 아니면 여기서 직접 조회해 채운다.
            from .tools import landuse as landuse_tool

            districts = list(land_use.get("districts") or [])
            diag_lu = (self.diagnosis or {}).get("land_use") or {}
            diag_parcel = (self.diagnosis or {}).get("parcel") or {}
            if (
                parcel.get("pnu")
                and diag_parcel.get("pnu") == parcel.get("pnu")
                and diag_lu.get("districts")
            ):
                districts = list(diag_lu["districts"])
            else:
                extra = await landuse_tool.get_landuse_districts(parcel.get("pnu", ""))
                districts = list(dict.fromkeys(districts + (extra or [])))
            jiga = parcel.get("jiga_won_per_m2")
            area = parcel.get("area_m2")
            _P = 3.3058  # 1평 = 3.3058㎡
            from .agents.prediagnosis import jimok_label

            # 도로 접촉·이격은 직전 진단이 같은 필지면 그 계산값을 그대로 실어
            # LLM이 '확인 필요'로 뭉개지 않고 수치를 읽어 답하게 한다(기하는 제외).
            same_parcel_diag = bool(
                parcel.get("pnu") and diag_parcel.get("pnu") == parcel.get("pnu")
            )
            road_access = (self.diagnosis or {}).get("road_access") if same_parcel_diag else None
            site = (self.diagnosis or {}).get("site_constraints") if same_parcel_diag else None
            road_compact = (
                {k: v for k, v in road_access.items() if k != "road_contact_geometry"}
                if isinstance(road_access, dict) else None
            )
            setback_compact = (
                {
                    "front_setback_m": site.get("front_setback_m"),
                    "adjacent_setback_m": site.get("adjacent_setback_m"),
                    "north_setback_m": site.get("north_setback_m"),
                    "setback_rule_status": (site.get("setback_rule") or {}).get("status"),
                }
                if isinstance(site, dict) else None
            )

            return {
                "address": parcel.get("jibun"),
                "jimok": jimok_label(parcel.get("jimok")),
                "area_m2": area,
                "area_pyeong": round(area / _P) if area else None,
                "jiga_won_per_m2": jiga,
                "jiga_won_per_pyeong": round(jiga * _P) if jiga else None,
                "jiga_total_won": (round(jiga * area) if jiga and area else None),
                "zone": (land_use.get("zones") or [None])[0],
                "districts": districts,
                "road_access": road_compact,
                "setback": setback_compact,
                # 같은 필지면 전체 진단 데이터를 실어, 어떤 사실 질문이든 수집·계산값으로
                # 답하게 한다(농지/산지 전용, 기존 건축물, 부담금, 인허가 단계, 조례 근거 등).
                "diagnosis": (
                    compact(self.diagnosis) if same_parcel_diag and self.diagnosis else None
                ),
                "note": (
                    "이 필지에 대해 시스템이 수집·계산한 값은 위 데이터(특히 diagnosis)에 모두 "
                    "들어 있다. 사용자가 물은 것(도로 접촉·이격거리·농지/산지 전용·기존 건축물·"
                    "부담금·인허가 단계·용도지구·조례 근거 등)에 해당하는 값이 있으면 그 값을 반드시 "
                    "읽어 근거로 제시하라(있는 계산값을 숨기고 일반론만 말하지 마라). 다만 딱딱한 수치 "
                    "나열이 아니라, 고객에게 상담하듯 친절하고 자연스러운 문장으로 설명하라. 정량 "
                    "수치가 없거나 사전검토만으로 확정할 수 없는 부분은 '데이터상 …로 보이며, 최종 "
                    "판단·확정은 관할 행정청(시·군·구청) 확인이 필요합니다'처럼 정성적으로 안내해도 "
                    "된다. 말투 예시: 가능해 보이면 '수치적(사전검토)으로 볼 때 …가 가능할 것으로 "
                    "보입니다'처럼 안내하라. 상세 검토·설계가 필요하면 주제에 맞는 전문가로 나눠 "
                    "안내하라 — 부지 조성·절토/성토·옹벽·배수·경사도·개발행위허가·도로·현황측량은 "
                    "'토목 설계사무소', 건축물 배치·구조·용도·이격·건축허가는 '건축 설계사무소'로 "
                    "문의하도록 하고, 최종 인허가 가능 여부는 관할 행정청(시·군·구청)에 확인하도록 "
                    "안내하라. 요지: 있는 값은 반드시 읽어 답하고, 확정이 어려운 부분은 정성적 안내와 "
                    "(주제에 맞는 토목/건축 설계사무소·관할청) 문의 권고를 자연스럽게 덧붙인다. "
                    "'다른 건물/다른 용도/이 밖에 무엇을 지을 수 있나'를 물으면 방금 물은 용도를 "
                    "반복하지 말고 diagnosis 의 regulation.zone_use_overview 의 allowed·conditional "
                    "목록으로 '이 지역에서 지을 수 있는 다른 건축물'을 알려줘라. "
                    "용도지구를 물으면 districts 목록을 그대로 "
                    "알려줘라(있으면 '없다'고 하지 마라). 자연어 두세 문장으로 답하고, 종합 판정 카드나 "
                    "1·2·3·4 섹션, 유의사항은 절대 쓰지 마라. 실무 관례대로 면적은 평, 공시지가는 "
                    "평당을 우선 말하고 ㎡는 괄호로 덧붙여라(예: 1,757평(5,808㎡), 평당 약 …원)."
                ),
            }, events

        if name == "recheck_use":
            # 같은 필지에 '다른 용도(공장/상가 등)도 되냐'는 새 진단이다.
            # 직전 진단의 좌표를 그대로 재사용해 그 용도로 판정 카드를 다시 만든다.
            loc = (self.diagnosis or {}).get("location") or {}
            lon, lat = loc.get("lon"), loc.get("lat")
            use = (args.get("building_use") or "").strip()
            if lon is None or lat is None:
                raise RuntimeError(
                    "먼저 특정 필지를 진단해야 그 필지에 다른 용도를 검토할 수 있습니다."
                )
            query = (
                f'지도에서 선택한 위치(경도 {lon}, 위도 {lat})에서 '
                f'사용자가 원하는 건축물 용도는 "{use}"이다. 건축 가능 여부를 검토해줘'
            )
            # 후속 용도 검토는 카드를 다시 찍지 않고 모델이 자연어로 답하게 한다.
            return await self._diagnose_and_emit(query, emit_card=False)

        if name == "render_on_map":
            if not self.diagnosis:
                raise RuntimeError("먼저 prediagnose 를 실행해야 지도에 반영할 수 있습니다.")
            ev = self._render_event()
            events.append(ev)
            return {"rendered": True, "command_count": len(ev["data"]["commands"])}, events

        if name == "show_map_lines":
            if not self.diagnosis:
                raise RuntimeError("먼저 특정 필지를 진단해야 그 선을 지도에 그릴 수 있습니다.")
            from .agents.map_control import overlay_command

            cmd = overlay_command(self.diagnosis, args.get("kinds") or [])
            if cmd:
                events.append({"event": "map_commands", "data": {"commands": [cmd]}})
                return {
                    "shown": True,
                    "lines": [s.get("label", "") for s in cmd["segments"]],
                    "note": (
                        "요청한 선을 지도에 다시 그렸다(카메라·3D 매스는 그대로 유지). 표시한 선의 "
                        "라벨 값을 근거로 자연어 한두 문장으로 답하라. 도로 접촉선은 지적도 기준 참고 "
                        "판정이라 건축법상 지정도로 여부·유효폭은 도로대장·현황측량으로 확인해야 한다고 "
                        "덧붙여라. 종합 판정 카드나 유의사항 목록은 쓰지 마라."
                    ),
                }, events
            return {
                "shown": False,
                "note": (
                    "요청한 선을 그릴 데이터가 없다. 건축선·이격선은 이격 수치가 수집된 지자체이면서 "
                    "건축 가능(가능/조건부) 판정일 때만 그려지고, 불가 판정 필지에는 건물 치수선을 "
                    "표시하지 않는다. 도로 접촉선은 지적도상 접한 도로가 있을 때만 그려진다. 이 사정을 "
                    "자연어 한두 문장으로 설명하라."
                ),
            }, events

        if name == "restudy_massing":
            if not self.diagnosis or not self.diagnosis.get("regulation"):
                raise RuntimeError("직전 진단이 없습니다. 먼저 prediagnose 를 실행하세요.")

            reg = self.diagnosis["regulation"]
            parcel = self.diagnosis.get("parcel", {})
            new_mass = massing.calc_massing(
                area_m2=parcel.get("area_m2", 0),
                bcr_max_pct=args.get("bcr_target_pct", reg["bcr_max_pct"]),
                far_max_pct=reg["far_max_pct"],
                far_target_pct=args.get(
                    "far_target_pct",
                    self.diagnosis.get("massing", {}).get("far_applied_pct", reg["far_max_pct"]),
                ),
            )
            previous_site = self.diagnosis.get("site_constraints", {})
            constrained = site_constraints.apply(
                parcel_geometry=parcel["geometry"],
                massing=new_mass,
                building_use=self.diagnosis.get("request", {}).get("building_use", ""),
                zone=reg.get("zone", ""),
                jurisdiction=self.diagnosis.get("jurisdiction", ""),
                road_access=self.diagnosis.get("road_access"),
                parking_strategy=args.get(
                    "parking_strategy",
                    previous_site.get("parking", {}).get("strategy", "unspecified"),
                ),
            )
            new_mass.update({
                "density_building_area_m2": new_mass["building_area_m2"],
                "building_area_m2": constrained["adjusted_building_area_m2"],
                "floors": constrained["floors"],
                "full_floors": constrained["full_floors"],
                "top_floor_ratio": constrained["top_floor_ratio"],
                "mass_height_m": constrained["mass_height_m"],
                "note": constrained["caveat"],
            })
            self.diagnosis["massing"] = new_mass
            self.diagnosis["site_constraints"] = constrained
            events.append({"event": "diagnosis", "data": self.diagnosis})
            events.append(self._render_event())   # 규모가 바뀌면 지도도 함께 갱신
            return new_mass, events

        if name == "set_map_layers":
            # 지정된 레이어만 담아 프론트로 보낸다. 프론트가 토글 상태를 바꾼다.
            cmd = {"type": "set_layers"}
            for key in ("cadastre", "zoning", "slope", "dimensions", "panel"):
                if key in args and args[key] is not None:
                    cmd[key] = bool(args[key])
            events.append({"event": "map_commands", "data": {"commands": [cmd]}})
            applied = {k: v for k, v in cmd.items() if k != "type"}
            return {"applied": applied}, events

        if name == "set_earthwork_mode":
            mode = args.get("mode")
            if mode not in {"original", "graded"}:
                raise ValueError("토공 표시 모드는 original 또는 graded여야 합니다.")
            events.append(
                {
                    "event": "map_commands",
                    "data": {
                        "commands": [
                            {"type": "set_earthwork_mode", "mode": mode}
                        ]
                    },
                }
            )
            return {
                "mode": mode,
                "applied": True,
                "note": "같은 건물 모델의 토공 표시 상태만 전환했다. 짧게 완료를 알려라.",
            }, events

        if name == "run_map_tool":
            action = args.get("action")
            events.append(
                {"event": "map_commands", "data": {"commands": [{"type": "run_tool", "action": action}]}}
            )
            return {"ran": action}, events

        if name == "recommend_areas":
            from .agents.area_recommender import recommend_areas

            region = args.get("region", "")
            use = (args.get("building_use") or "").strip()
            rec = await recommend_areas(
                region, use, query=getattr(self, "_last_query", "")
            )
            self.recommendations = rec

            items = rec.get("items") or []
            options = []
            for it in items:
                use_label = use or "건물"
                detail = f"{it['zone']} · 지목 {it['jimok'] or '—'}"
                if it.get("area_m2"):
                    detail += f" · {it['area_m2']:,.0f}㎡"
                options.append(
                    {
                        "label": it["address"],
                        "detail": detail,
                        # 클릭 시 그 지번으로 개별 진단을 실행하도록 프론트가 해석한다.
                        "action": f"diagnose:{use_label}::{it['address']}",
                    }
                )

            header = (
                f"**{rec.get('matched') or region}** 주변 비도시 지역 후보 {len(items)}곳입니다. "
                f"항목을 누르면 그 지점으로 이동해 진단합니다.\n\n> {rec.get('note', '')}"
            )
            events.append(
                {"event": "message", "data": {"text": header, "options": options}}
            )

            # 지도를 대상 지역 상공으로 이동해 후보 위치 감을 잡게 한다.
            center = rec.get("center")
            if center:
                events.append(
                    {
                        "event": "map_commands",
                        "data": {
                            "commands": [
                                {
                                    "type": "fly_to",
                                    "lon": center["lon"],
                                    "lat": center["lat"],
                                    "altitude": 12000,
                                    "tilt": 35,
                                    "heading": 0,
                                }
                            ]
                        },
                    }
                )

            return {
                "region": region,
                "count": len(items),
                "recommendation_shown": True,
                "note": (
                    (
                        "조건과 행정구역이 일치하는 후보를 찾지 못했다. 후보를 찾았다고 말하지 말고, "
                        "검색 범위를 넓히거나 인접 동을 지정해 달라고 한 줄로 안내하라."
                    )
                    if not items else
                    (
                        "후보 리스트는 시스템이 이미 클릭 가능한 형태로 표시했다. "
                        "리스트를 텍스트로 다시 나열하지 마라. 필요하면 한 줄 안내만 덧붙여라."
                    )
                ),
            }, events

        if name == "flag_verdict_restriction":
            cmd = {
                "type": "verdict_warning",
                "label": args.get("label", ""),
                "reason": args.get("reason", ""),
            }
            events.append({"event": "map_commands", "data": {"commands": [cmd]}})
            return {"flagged": cmd["label"]}, events

        raise ValueError(f"알 수 없는 도구: {name}")
