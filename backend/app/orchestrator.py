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
import json
import logging
import re
from typing import AsyncIterator

from .agents.map_control import build_map_commands
from .agents.prediagnosis import (
    compact,
    detect_use_restriction,
    format_diagnosis_answer,
    run_prediagnosis,
)
from .llm import append_tool_results
from .tools import building_register, massing, site_constraints, vworld

logger = logging.getLogger("uvicorn.error")

SYSTEM = """당신은 공간정보 기반 건축 인허가 상담 시스템의 오케스트레이터입니다.
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
  '다 꺼/켜'는 해당하는 항목을 모두 지정한다. 답변은 무엇을 켰/껐는지 한 줄로 짧게.
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
- **근거는 이 시스템이 수집한 데이터(직전 진단 diagnosis·regulation·permit_requirements 등)를
  1순위로 쓴다.** 데이터에 있으면 그걸로 답한다.
- **데이터에 없으면**: 네가 아는 일반 법령·인허가 상식으로 답하되 "정확한 건 관할 행정청·
  최신 조례 확인이 필요하다"는 캐비앗을 붙인다. 그것도 불확실하면 **모른다고 솔직히 말하고**,
  사용자가 대신 물어볼 만한 것(예: 구체 지번, 특정 시설·용도, 조례명)을 제안한다.
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
            "일반 건축은 가능/조건부여도, 사용자가 요청한 특정 시설·구조물이 개별 법령상 "
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
    """LLM이 반복한 ``**1. 제목**`` 번호를 실제 순번으로 바로잡는다."""
    index = 0

    def replace(match: re.Match) -> str:
        nonlocal index
        index += 1
        spacing = match.group(1) or " "
        return f"**{index}.{spacing}"

    return re.sub(r"(?m)^\*\*\d+\.(\s*)", replace, text)


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
        if from_mouse:
            # 진단 전 최초 클릭 또는 현재 진단과 다른 PNU 클릭은 다음 질문에서
            # 반드시 새 종합 진단을 시작해야 한다.
            parcel_changed = bool(
                pnu and (not diagnosed_pnu or pnu != diagnosed_pnu)
            )
            self._selection_changed = parcel_changed
            if parcel_changed:
                # 필지 전환은 새 상담의 시작이다. 이전 필지의 진단·추천·LLM
                # 대화문맥을 남겨 두면 주소 없는 후속 질문에 섞일 수 있으므로
                # 활성 필지 문맥을 완전히 분리한다.
                self.diagnosis = None
                self.recommendations = None
                self.messages = []
                self._diag_shown = False
        elif pnu and diagnosed_pnu == pnu:
            self._selection_changed = False
        self.selected_parcel = {
            "lon": float(lon),
            "lat": float(lat),
            "address": address,
            "pnu": pnu,
        }
        logger.info(
            "parcel_state from_mouse=%s diagnosed_pnu=%s selected_pnu=%s changed=%s",
            from_mouse,
            diagnosed_pnu,
            pnu,
            self._selection_changed,
        )

    async def ask(self, user_query: str, max_turns: int = 8) -> AsyncIterator[dict]:
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
            and (parcel_question or self._selection_changed)
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
        self._last_query = user_query
        # _diag_shown은 세션 동안 유지한다. 매 질문마다 초기화하면 같은 필지의
        # 후속 질문도 다시 '최초 진단'으로 오인해 종합 보고서를 반복하게 된다.

        # 화면 상태 전환은 자연어 표현이 조금 달라도 반드시 실행돼야 하므로 LLM 판단
        # 전에 결정적으로 처리한다. 인허가 진단이나 건물 유형은 바꾸지 않는다.
        compact_query = re.sub(r"\s+", "", user_query)

        # 상세 창문·외벽 모델을 세우기 전의 단순 형상은 진단 시 계산한 LOD1
        # 매스를 그대로 복원한다. 후속 자연어 질문을 종합 진단으로 오인하지 않는다.
        if re.search(
            r"(?:lod1(?:단계)?(?:만)?|건물모델(?:세우기|올리기)전(?:모습)?|"
            r"상세(?:건물)?모델(?:숨기|끄|치우)|기본매스(?:만)?)",
            compact_query,
            re.IGNORECASE,
        ):
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
        if re.search(r"(거리|길이).*(재|측정)|측정.*(거리|길이)", user_query):
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
            "cadastre": r"(지적도|지적선)",
            "zoning": r"용도지역",
            "slope": r"경사도",
            "dimensions": r"(치수선|치수|라벨)",
            "panel": r"(팝업|결과창|진단창)",
        }
        layer_command: dict[str, bool] = {}
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
            and re.search(
                r"(지을|짓|건축|신축|건물).*(가능|되나|돼|할\s*수|수\s*있)",
                user_query,
            )
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
                            system=(
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
                        text = " ".join(natural.texts).strip()
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
            facts = {
                "address": (
                    (diagnosis.get("parcel") or {}).get("jibun")
                    or (diagnosis.get("location") or {}).get("matched_address")
                ),
                "verdict": diagnosis.get("verdict"),
                "jimok": (diagnosis.get("parcel") or {}).get("jimok"),
                "land_conversion": diagnosis.get("land_conversion"),
                "road_access": diagnosis.get("road_access"),
                "regulatory_screen": diagnosis.get("regulatory_screen"),
                "regulation_constraints": (
                    diagnosis.get("regulation") or {}
                ).get("constraints"),
                "site_constraints": diagnosis.get("site_constraints"),
                "permit_requirements": diagnosis.get("permit_requirements"),
            }
            try:
                natural = await self.client.complete(
                    system=(
                        "사용자가 선택 필지의 '조건부 가능' 조건을 어떻게 해소하는지 묻는다. "
                        "제공된 진단 사실만 근거로 한국어 자연어 답변을 작성하라. 주소를 먼저 "
                        "명시하고, 실제로 필요한 조치를 우선순위대로 설명하라. 지목이 전이면 "
                        "농지전용 협의·허가와 개발행위허가 후 준공 단계의 지목변경(전→대)을 "
                        "구분해 설명하라. 접도, 주차, 일조·이격거리, 지구단위계획 등 데이터에 "
                        "있는 조건을 빠뜨리지 마라. '건축 가능하다'만 반복하거나 건축 모델을 "
                        "추천하지 말고, 확인되지 않은 사항은 확정적으로 말하지 마라. 고정된 "
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
                text = " ".join(natural.texts).strip()
            except Exception:
                text = ""
            if not text:
                address = facts.get("address") or "선택한 필지"
                text = (
                    f"{address}의 조건부 사항을 해소하려면 지목·전용, 개발행위허가, "
                    "접도와 주차·이격 조건을 순서대로 확인해야 합니다. 지목이 전이라면 "
                    "농지전용 절차와 개발행위허가를 먼저 진행하고, 공사 준공 후 실제 이용 "
                    "현황에 맞춰 전에서 대로 지목변경하는 절차를 검토해야 합니다."
                )
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

        coordinate_diagnosis = bool(
            coordinate_match
            and (
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
                    same_parcel = (
                        False
                        if starts_new_parcel
                        else _same_parcel_coordinate(
                            coordinate_match, self.diagnosis
                        )
                    )
                _out, events = await self._diagnose_and_emit(
                    user_query,
                    emit_card=not asks_land_conversion and not same_parcel,
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
                    verdict = (self.diagnosis or {}).get("verdict")
                    address = (
                        ((self.diagnosis or {}).get("parcel") or {}).get("jibun")
                        or ((self.diagnosis or {}).get("location") or {}).get("matched_address")
                        or "선택한 위치"
                    )
                    verdict_text = {
                        "allowed": "가능",
                        "conditional": "조건부 가능",
                        "not_allowed": "불가",
                        "unknown": "추가 확인 필요",
                    }.get(verdict, "추가 확인 필요")
                    yield {
                        "event": "message",
                        "data": {
                            "text": (
                                f"선택하신 필지는 **{address}**이며, 이 계획은 **{verdict_text}**입니다. "
                                "2층에 주인이 거주하고 1층을 원룸으로 임대하는 계획은 보통 "
                                "다가구주택(단독주택)으로 검토합니다. 제1종전용주거지역에서는 "
                                "지구단위계획·조례의 다가구 제한과 주차 기준을 확인하기 전에는 "
                                "이 임대형 계획을 확정할 수 없습니다."
                            )
                        },
                    }
                elif same_parcel:
                    yield {
                        "event": "message",
                        "data": {
                            "text": await self._natural_followup_answer(user_query)
                        },
                    }
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

        for _ in range(max_turns):
            response = await self.client.complete(
                system=SYSTEM, messages=self.messages, tools=TOOLS, max_tokens=8000
            )
            self.messages.append(response.raw_assistant)

            for text in response.texts:
                yield {"event": "message", "data": {"text": text}}

            if not response.tool_calls:
                return

            results = []
            for call in response.tool_calls:
                yield {"event": "tool_start", "data": {"tool": call.name}}
                try:
                    out, extra_events = await self._run_tool(call.name, call.input)
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
        facts = {
            "address": address,
            "verdict": diagnosis.get("verdict"),
            "jimok": parcel.get("jimok"),
            "zone": (diagnosis.get("regulation") or {}).get("zone"),
            "districts": (diagnosis.get("regulation") or {}).get("districts"),
            "constraints": (diagnosis.get("regulation") or {}).get("constraints"),
            "land_conversion": diagnosis.get("land_conversion"),
            "conversion_charge": diagnosis.get("conversion_charge"),
            "development_charge": diagnosis.get("development_charge"),
            "regulatory_screen": diagnosis.get("regulatory_screen"),
            "road_access": diagnosis.get("road_access"),
            "summary": diagnosis.get("summary"),
        }
        try:
            natural = await self.client.complete(
                system=(
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
                    "공원구역 등은 허용행위에 해당하지 않는 일반 건축이 제한되거나 불가할 "
                    "수 있으므로 실제 진단의 판정을 우선하라. 대체산림자원조성비는 진단 "
                    "자료상 관련성이 있을 때만 언급하고, conversion_charge에 계산값이 있으면 "
                    "전용예상면적·단가·예상액을 자연스럽게 포함하라. development_charge가 "
                    "있으면 대상 가능성과 부과율을 설명하되 정확액 산정에 종료시점 지가와 "
                    "개발비용이 필요하다는 점을 구분하라. 납부하면 허가된다는 식으로 말하지 "
                    "마라. 건축 모델을 추천하거나 종합 진단 카드를 반복하지 말고, 질문의 "
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
            text = " ".join(natural.texts).strip()
            if text:
                return text
        except Exception:
            pass

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
            return (
                f"{address}은 지목이 임야이므로 농지전용 대상이 아니라 산지전용허가 "
                f"대상입니다. {conversion_summary or '산지 구분과 경사도·표고·입목축적을 확인해야 합니다.'}"
                f"{charge_text}"
            )
        if conversion_status == "PERMIT_REQUIRED":
            return (
                f"{address}은 산지전용허가를 받아 전용을 검토할 수 있습니다. "
                f"{conversion_summary or '다만 경사도·표고·입목축적과 복구계획 등을 심사받아야 합니다.'} "
                "허가 여부는 이 심사 결과로 결정되며 대체산림자원조성비 납부만으로 "
                "허가되는 것은 아닙니다."
            )
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
                "용도지역·보전 규제로 일반 건축이 불가한 판정입니다. 해당 시설이 법령상 "
                "예외적인 허용행위에 해당하는지는 관할 행정청에서 별도로 확인해야 합니다."
            )
        if verdict == "conditional":
            return (
                f"{address}은 산지전용허가 가능 여부를 먼저 심사받아야 건축을 검토할 수 "
                "있습니다. 산지전용은 선행 절차이지 건축허가 자체가 아니므로 개발행위, "
                "접도, 용도지역과 개별 보전 규제도 함께 충족해야 합니다."
            )
        return (
            f"{address}은 산지전용만으로 건축 가능하다고 확정할 수 없습니다. 산지 구분과 "
            "보전 규제상 허용행위 해당 여부를 먼저 확인한 뒤 개발행위·건축허가 요건을 "
            "별도로 검토해야 합니다."
        )

    async def _natural_followup_answer(self, user_query: str) -> str:
        """같은 필지 후속 질문에는 전체 보고서 없이 질문한 내용만 답한다."""
        diagnosis = self.diagnosis or {}
        address = (
            (diagnosis.get("parcel") or {}).get("jibun")
            or (diagnosis.get("location") or {}).get("matched_address")
            or "선택한 필지"
        )
        try:
            response = await self.client.complete(
                system=(
                    "같은 필지에 대한 후속 질문이다. 제공된 최신 진단 데이터만 근거로 "
                    "사용자가 방금 물은 내용에만 한국어 자연어로 직접 답하라. 종합 판정 "
                    "보고서, 섹션 제목, 번호 목록, 건축 모델 추천을 다시 출력하지 마라. "
                    "주소는 혼동 방지를 위해 첫 문장에 한 번만 자연스럽게 언급하고, "
                    "결론과 핵심 이유를 최대 3문장으로 간결하게 설명하라. 질문하지 않은 수치와 "
                    "절차를 전부 나열하지 마라. 확인되지 않은 내용은 확정하지 마라."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"후속 질문: {user_query}\n"
                            f"현재 필지: {address}\n"
                            f"최신 진단: {compact(diagnosis)}"
                        ),
                    }
                ],
                tools=[],
                max_tokens=400,
            )
            text = " ".join(response.texts).strip()
            if text:
                return text
        except Exception:
            pass
        verdict = {
            "allowed": "건축 가능한 판정입니다",
            "conditional": "선행 조건을 충족해야 건축을 검토할 수 있습니다",
            "not_allowed": "현재 진단 기준으로는 건축할 수 없습니다",
            "unknown": "추가 규제 확인 전에는 가능 여부를 확정할 수 없습니다",
        }.get(diagnosis.get("verdict"), "추가 확인이 필요합니다")
        reason = (diagnosis.get("regulation") or {}).get("reason") or ""
        return f"**{address}**은 {verdict}. {reason}".strip()

    def _render_event(self) -> dict:
        """현재 진단을 지도 명령으로 바꿔 프론트로 보낼 이벤트."""
        return {
            "event": "map_commands",
            "data": {"commands": build_map_commands(self.diagnosis or {})},
        }

    async def _diagnose_and_emit(
        self, query: str, emit_card: bool = True
    ) -> tuple[dict, list[dict]]:
        """진단을 돌리고 지도 반영·경고를 이벤트로 내보낸다.

        emit_card=True (처음 '지을 수 있어?' 진단): 종합 판정 카드를 확정 형식으로
          한 번 표시한다(사실이 사라지지 않게).
        emit_card=False (recheck_use 등 후속 용도 검토): 지도만 갱신하고 카드는
          다시 찍지 않는다. 모델이 데이터를 근거로 자연어로 답하게 한다.
        """
        events: list[dict] = []
        steps: list[dict] = []
        self.diagnosis = await run_prediagnosis(
            self.client,
            query,
            on_progress=lambda step, payload: steps.append(
                {"event": "diagnosis_step", "data": {"step": step, "input": payload}}
            ),
        )
        location = self.diagnosis.get("location") or {}
        parcel = self.diagnosis.get("parcel") or {}
        if location.get("lon") is not None and location.get("lat") is not None:
            diagnosed_pnu = parcel.get("pnu") or ""
            active_pnu = (self.selected_parcel or {}).get("pnu") or ""
            if not active_pnu or not diagnosed_pnu or active_pnu == diagnosed_pnu:
                self.set_selected_parcel(
                    lon=location["lon"],
                    lat=location["lat"],
                    address=parcel.get("jibun") or location.get("matched_address") or "",
                    pnu=diagnosed_pnu,
                )
        events.extend(steps)
        events.append({"event": "diagnosis", "data": self.diagnosis})

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
        if wants_model and self.diagnosis.get("verdict") != "not_allowed":
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

        # 사용자가 말한 용도가 이 용도지역에서 불가면(예: 제1종전용주거의 일반
        # 상가) 판정 옆에 빨간 경고를 띄운다.
        restriction = detect_use_restriction(
            getattr(self, "_last_query", ""), self.diagnosis
        )
        # 다가구·원룸 같은 '확인 필요'(verification_required)는 답변이 자연어로
        # 설명하므로 팝업 경고 박스를 띄우지 않는다. 진짜 '건축불가'만 박스로 띄운다.
        if restriction and restriction.get("kind") != "verification_required":
            self.diagnosis["use_restriction"] = restriction
            events.append(
                {
                    "event": "map_commands",
                    "data": {
                        "commands": [
                            {
                                "type": "verdict_warning",
                                "label": restriction["label"],
                                "reason": restriction["reason"],
                                "kind": restriction.get("kind", "restriction"),
                            }
                        ]
                    },
                }
            )

        if emit_card:
            # 최초 진단만 확정 형식 카드를 한 번 표시한다.
            events.append(
                {"event": "message", "data": {"text": format_diagnosis_answer(self.diagnosis)}}
            )
            self._diag_shown = True
            note = (
                "종합 판정·건폐율/용적률·규모·부담금·인허가 단계 등 표준 진단 카드는 "
                "시스템이 이미 화면에 표시했다. 그 내용을 표로 다시 나열하지 마라. "
                "자연어 답변 첫 문장에는 진단 데이터의 전체 지번 주소를 한 번 명시하고, "
                "주소 없이 '해당 필지'라고만 쓰지 마라. "
                "사용자가 물은 것의 의도에 맞춰 1~3문장 자연어로 이어 답하라."
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
                "note": (
                    "용도지구를 물으면 districts 목록을 그대로 알려줘라(있으면 '없다'고 하지 마라). "
                    "이 값으로 사용자 질문에 자연어 한두 문장으로 답하라. 종합 판정 카드나 "
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
