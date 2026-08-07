"""사전진단 에이전트.

공간정보(VWorld) 위에서 법령 규제를 조회·해석해 허가 가능성을 판단한다.

설계 노트 — 왜 도구 호출 루프를 쓰지 않는가:

  조회 순서가 고정되어 있다. 주소→좌표→필지→용도지역→규제→매스.
  분기도 하나뿐이다(불허면 매스를 건너뛴다). 이런 확정된 절차를 매 단계
  LLM 에게 물으면 호출이 6~7회로 늘고, 그만큼 지연·비용·실패 지점이 늘어난다.
  (무료 티어에서는 하루 20건 한도를 질의 두어 건으로 소진한다.)

  그래서 LLM 은 사람 말에서 구조를 뽑는 일에만 쓴다:
    - extract_request()  "테헤란로 152에 업무시설" -> (주소, 건축물 용도)
  나머지 조회·판정·계산은 결정적 파이프라인으로 실행한다. LLM 호출 1회.

  판정 근거가 되는 수치는 어차피 도구가 계산하므로, 이 구조가 결과의 정확성을
  떨어뜨리지 않는다. 오히려 모델이 순서를 건너뛰거나 인자를 잘못 넣을 여지가
  사라진다.
"""

from __future__ import annotations

import json
import asyncio
import re
import time
from typing import Callable

from ..config import FLOOR_HEIGHT_M
from ..tools import (
    building_register,
    conversion_charges,
    development_charge,
    law_open,
    legal_conflicts,
    district_plan,
    facility_rules,
    land_conversion,
    massing,
    ordinance,
    ordinance_index,
    permit_requirements,
    regulatory_screen,
    road_access,
    setback_rules,
    site_constraints,
    vworld,
    zoning,
)

BUILDING_USES = [
    "시설물",
    "단독주택",
    "공동주택",
    "제1종근린생활시설",
    "제2종근린생활시설",
    "업무시설",
    "판매시설",
    "숙박시설",
    "공장",
    "창고시설",
    "교육연구시설",
]

EXTRACT_SYSTEM = f"""사용자의 건축 인허가 질의에서 주소·용도·주차계획을 뽑아 도구로 제출한다.

1. address — 조회할 주소. 사용자가 쓴 표현을 최대한 그대로. 시/도가 없으면 붙이지 않는다.
   사용자가 위도·경도를 명시하면 address는 빈 문자열로 두고 lon, lat에 숫자로 제출한다.
2. building_use — 검토할 건축물 용도. 다음 중 하나로 정규화한다:
   {", ".join(BUILDING_USES)}
3. parking_strategy — 지상주차는 surface, 지하주차는 underground,
   기계식주차는 mechanical, 혼합은 mixed. 언급이 없으면 unspecified.

용도가 명시되지 않았으면 다른 단일 용도를 추측하지 말고 building_use="시설물",
inferred=true 로 표시한다. 여기서 시설물은 용도 미정이 아니라 시스템이 지원하는
모든 건축물 용도를 포괄해서 검토한다는 뜻이다.
("상가" -> 제1종근린생활시설, "빌딩"/"사무실" -> 업무시설, "물류창고" -> 창고시설,
 "아파트" -> 공동주택, "펜션"/"호텔" -> 숙박시설)

질의 문장을 해석해 사용자가 '전체 건축물(무엇이든 지을 수 있나)'을 묻는지, 아니면
'특정 시설(움막·농막·태양광 설치 등)'을 콕 집어 묻는지 판단한다. 특정 시설을 물었으면
그 시설명을 requested_facility 에 사용자 표현 그대로 담는다(building_use 는 판정용
표준 용도로 정규화하되, 표준 11용도에 없으면 시설물). 전체 건축물을 뜻하면
requested_facility 는 빈 문자열.

그 특정 시설이 농막·움막·태양광 설비·비닐하우스·정자·컨테이너·축사처럼 일반 건물의
3D 모델과 건폐율·용적률 규모 산정이 부적절한 가설·특수·농림·발전 시설이면
no_building_model=true 로 표시한다(단독주택·상가·빌딩·창고 등 정상 건축물은 false).

주소를 특정할 수 없으면 address 를 빈 문자열로 둔다."""

EXTRACT_TOOL = [
    {
        "name": "submit_request",
        "description": "질의에서 뽑아낸 주소와 건축물 용도를 제출한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "조회할 주소. 없으면 빈 문자열."},
                "lon": {"type": "number", "description": "지도에서 선택한 경도"},
                "lat": {"type": "number", "description": "지도에서 선택한 위도"},
                "building_use": {"type": "string", "enum": BUILDING_USES},
                "requested_facility": {
                    "type": "string",
                    "description": (
                        "질의 문장을 해석해, 사용자가 '전체 건축물'이 아니라 '특정 시설'을 "
                        "콕 집어 물었으면 그 시설명을 사용자 표현 그대로 담는다"
                        "(예: '움막', '농막', '태양광 설치', '단독주택'). 건물 전반을 "
                        "뜻하거나 용도를 지정하지 않았으면 빈 문자열. building_use 는 판정용 "
                        "표준 용도로 정규화하되, 이 필드는 화면의 '검토 용도'로 그대로 쓴다."
                    ),
                },
                "no_building_model": {
                    "type": "boolean",
                    "description": (
                        "requested_facility 가 농막·움막·태양광 설비·비닐하우스·정자·컨테이너·"
                        "축사처럼 '일반 건물 3D 모델과 건폐율·용적률 규모 산정이 부적절한' "
                        "가설·특수·농림·발전 시설이면 true. 단독주택·상가·빌딩·창고·공장처럼 "
                        "정상 건축물이면 false. requested_facility 가 비어 있으면 false."
                    ),
                },
                "inferred": {
                    "type": "boolean",
                    "description": "용도를 질의에서 직접 읽지 않고 추론했으면 true",
                },
                "far_target_pct": {
                    "type": "number",
                    "description": "사용자가 특정 용적률을 지정한 경우에만(예: '용적률 250%로')",
                },
                "parking_strategy": {
                    "type": "string",
                    "enum": ["surface", "underground", "mechanical", "mixed", "unspecified"],
                    "description": "지상, 지하, 기계식, 혼합 주차 계획. 언급이 없으면 unspecified",
                },
            },
            "required": ["address", "building_use", "inferred"],
        },
    }
]


_VERDICT_TEXT = {
    "allowed": "건축 가능",
    "conditional": "조건부 가능",
    "not_allowed": "건축 불가",
    "unknown": "판단 불가",
}

# 법률 적용 관계 절의 상태 줄. 충돌로 막히는 경우와 요건이 쌓이기만 하는 경우를
# 사용자가 한눈에 구분하도록 legal_conflicts.evaluate() 의 status 를 그대로 옮긴다.
_CONFLICT_STATUS_LABELS = {
    "BLOCKED": "충돌 — 최종 허가 제한",
    "UNRESOLVED_CONFLICT": "충돌 미해소 — 예외 요건 입증 필요",
    "CUMULATIVE_REQUIREMENTS": "충돌 없음 · 요건 누적 적용",
    "CLEAR": "충돌 없음",
}


def _won(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


_PYEONG_M2 = 3.3058  # 1평 = 3.3058㎡


def _area(m2) -> str:
    """면적을 실무 표기 '평(㎡)' 순서로. 예: 1,757평(5,808㎡)."""
    try:
        m2 = float(m2)
    except (TypeError, ValueError):
        return "—"
    return f"{m2 / _PYEONG_M2:,.0f}평({m2:,.0f}㎡)"


def _per_pyeong(won_per_m2) -> str:
    """㎡당 금액 -> 평당 금액(원)."""
    try:
        return f"{float(won_per_m2) * _PYEONG_M2:,.0f}"
    except (TypeError, ValueError):
        return "—"


# 지목 부호(한 글자) -> 사용자 표기. 전·답·대는 통칭을 괄호로, 나머지는 정식명.
_JIMOK_LABEL = {
    "전": "전(밭)", "답": "답(논)", "대": "대(대지)", "임": "임야", "과": "과수원",
    "목": "목장용지", "광": "광천지", "염": "염전", "장": "공장용지", "학": "학교용지",
    "차": "주차장", "주": "주유소용지", "창": "창고용지", "도": "도로", "철": "철도용지",
    "제": "제방", "천": "하천", "구": "구거", "유": "유지", "양": "양어장", "수": "수도용지",
    "공": "공원", "체": "체육용지", "원": "유원지", "종": "종교용지", "사": "사적지",
    "묘": "묘지", "잡": "잡종지",
}


def jimok_label(code) -> str:
    if not code:
        return "—"
    return _JIMOK_LABEL.get(code, code)


# 소규모 제1종근린생활시설(1,000㎡ 미만 소매점·휴게음식점 등) 형태로 지을 수 있는
# 상업·음식 계열 일상어. 이런 말은 '소규모 근생'이 가능하면 전면 불가가 아니다.
_SHOP_TERMS: set[str] = {
    "상가", "점포", "상점", "마트", "쇼핑", "백화점",
    "음식점", "식당", "카페", "주점", "술집",
    "상업시설", "상업",
}

# 사용자가 쓴 일상어 용도 -> 그 말이 가리킬 수 있는 정식 용도(판정표 기준).
# '상가'처럼 모호한 말은 소규모 근생(허용)일 수도, 일반 판매시설(불가)일 수도 있다.
_AMBIGUOUS_USE_TERMS: list[tuple[str, list[str]]] = [
    # 다가구는 단독주택, 다세대는 공동주택이다. 이름이 비슷해도 법적
    # 대분류가 다르므로 팝업의 가능/불가 판정도 반드시 구분한다.
    ("다가구", ["단독주택"]),
    ("다세대", ["공동주택"]),
    # 원룸은 소유·세대 구획 방식에 따라 다가구 또는 다세대가 될 수 있다.
    # 둘 중 하나라도 허용되면 전면 불가 경고 대신 세부유형 확인 대상으로 둔다.
    ("원룸", ["단독주택", "공동주택"]),
    # '상업시설'은 '상업'보다 먼저 둔다(부분일치 순서). 소매점·근생부터 대형
    # 판매시설까지 넓게 가리키므로 후보를 폭넓게 잡는다.
    ("상업시설", ["제1종근린생활시설", "제2종근린생활시설", "판매시설"]),
    ("상업", ["제1종근린생활시설", "제2종근린생활시설", "판매시설"]),
    ("상가", ["판매시설", "제2종근린생활시설"]),
    ("점포", ["판매시설", "제2종근린생활시설"]),
    ("상점", ["판매시설"]),
    ("마트", ["판매시설"]),
    ("쇼핑", ["판매시설"]),
    ("백화점", ["판매시설"]),
    ("음식점", ["제2종근린생활시설"]),
    ("식당", ["제2종근린생활시설"]),
    ("카페", ["제2종근린생활시설"]),
    ("주점", ["제2종근린생활시설"]),
    ("술집", ["제2종근린생활시설"]),
    ("모텔", ["숙박시설"]),
    ("호텔", ["숙박시설"]),
    ("펜션", ["숙박시설"]),
    ("공장", ["공장"]),
    ("창고", ["창고시설"]),
    ("학교", ["교육연구시설"]),
    ("교육연구시설", ["교육연구시설"]),
    # '주택'은 단독/공동 어느 쪽도 가리킬 수 있는 넓은 말이라 맨 끝에 둔다
    # (다가구·다세대·원룸 등 구체어가 먼저 매칭되어야 한다).
    ("주택", ["단독주택", "공동주택"]),
]


def detect_use_restriction(query: str, diagnosis: dict) -> dict | None:
    """요청 용도의 확정 불가·건축 불가 사유를 팝업 경고로 반환한다.

    핵심: '상가'처럼 모호한 말은 소규모 형태(1,000㎡ 미만 제1종근생 소매점 등)로는
    조건부 가능한 경우가 많다. 그런데도 '판매시설은 불가'만 보고 빨간 '불가'를
    띄우면, 판정('조건부 가능')과 모순된다. 그래서 **어떤 형태로든 지을 수 있으면
    원칙적으로 경고하지 않는다.** 다만 제1종전용주거지역의 원룸·다가구 계획은
    단독주택 허용만으로 확정하지 않고 세부 지구단위계획 확인 전 '계획 확정 불가'
    경고를 낸다. 정말로 모든 형태가 불가인 경우에는 '건축불가'를 표시한다.
    (뉘앙스 — '대형 판매시설은 제한' 등 — 은 모델의 자연어 답변이 설명한다.)
    """
    reg = diagnosis.get("regulation") or {}
    overview = reg.get("zone_use_overview") or {}
    zone = reg.get("zone") or "이 용도지역"

    if (
        zone == "제1종전용주거지역"
        and query
        and any(term in query for term in ("원룸", "다가구", "다중주택"))
    ):
        return {
            "label": "다가구·원룸 계획 확인 필요",
            "reason": (
                "단독주택은 원칙적으로 가능합니다. 다만 다가구·다중주택 등 임대형 세대 "
                "구성은 지구단위계획·조례에서 제한될 수 있어, 세부계획 확인이 필요합니다."
            ),
            "term": "다가구·원룸",
            "blocked": [],
            "kind": "verification_required",
        }

    not_allowed = set(overview.get("not_allowed") or [])
    if not not_allowed or not query:
        return None
    buildable = set((overview.get("allowed") or []) + (overview.get("conditional") or []))
    for term, candidates in _AMBIGUOUS_USE_TERMS:
        if term not in query:
            continue
        # 상가·음식 계열은 소규모 제1종근생 형태가 있다. 그게 가능하면 '불가' 아님.
        if term in _SHOP_TERMS and "제1종근린생활시설" in buildable:
            return None
        # 이 말이 가리킬 수 있는 용도 중 하나라도 지을 수 있으면 '불가' 아님.
        if any(u in buildable for u in candidates):
            return None
        blocked = [u for u in candidates if u in not_allowed]
        if not blocked:
            return None
        return {
            "label": f"{term} 건축 불가",
            "reason": f"{zone}에서 {'·'.join(blocked)}은(는) 건축할 수 없는 용도입니다.",
            "term": term,
            "blocked": blocked,
        }
    return None


def format_diagnosis_answer(d: dict) -> str:
    """진단 결과 -> 고정 형식 답변(마크다운). LLM 에 형식을 맡기면 번호·섹션이
    들쭉날쭉해서, 답변 본문을 여기서 결정적으로 조립한다."""
    reg = d.get("regulation") or {}
    parcel = d.get("parcel") or {}
    lu = d.get("land_use") or {}
    mass = d.get("massing") or {}
    # 최종 판정은 regulation.verdict 에 있다(지오코딩 실패 경로만 최상위 verdict).
    verdict = reg.get("verdict") or d.get("verdict", "unknown")
    presentation = reg.get("map_presentation") or {}
    display_verdict = presentation.get("verdict") or verdict
    zone = reg.get("zone") or (lu.get("zones") or [""])[0]
    districts = lu.get("districts") or []

    # 주소 미확인 등으로 규제 판정 자체를 못 한 경우 — 요약만 안내한다.
    if not reg:
        return d.get("summary") or "진단을 완료하지 못했습니다. 지번 또는 도로명주소를 알려주세요."

    out: list[str] = []
    n = 1

    # 1. 종합 판정
    out.append(
        f"## {n}. 종합 판정 — "
        f"{presentation.get('label') or _VERDICT_TEXT.get(display_verdict, display_verdict)}"
    )
    n += 1
    out.append(f"- **용도지역:** {zone or '—'}")
    # 걸침 필지 — 둘 이상 용도지역에 걸쳐 있으면 비율·주의를 함께 보인다.
    shares = lu.get("zone_shares") or []
    if lu.get("straddling") and len(shares) >= 2:
        parts = " / ".join(
            f"{s['zone']}({s['share_pct']}%·{s['area_m2']:,.0f}㎡)" for s in shares
        )
        smallest = min((s["area_m2"] for s in shares), default=0)
        if reg.get("weighted_limits", {}).get("applied"):
            note = (
                "국토계획법 제84조에 따라 "
                f"건폐율 {reg['bcr_max_pct']}%·용적률 {reg['far_max_pct']}%로 "
                "면적 가중평균했습니다."
            )
        elif smallest <= 330:
            note = "건폐율·용적률의 면적 가중평균 확인이 필요합니다."
        else:
            note = "국토계획법 제84조에 따라 부분별 규제가 적용될 수 있습니다."
        out.append(f"  - ⚠ **걸침 필지:** {parts} — 위 판정은 최대 면적 부분({zone}) 기준. {note}")
    out.append(f"- **토지이용 규제:** {', '.join(districts) if districts else '지정 없음'}")
    out.append(f"- **지목:** {jimok_label(parcel.get('jimok'))}")
    if parcel.get("area_m2"):
        out.append(f"- **대지면적:** {_area(parcel['area_m2'])}")
    jiga = parcel.get("jiga_won_per_m2")
    if jiga:
        out.append(
            f"- **개별공시지가:** 평당 약 {_per_pyeong(jiga)}원 (㎡당 {_won(jiga)}원)"
        )
    use = reg.get("building_use") or (d.get("request") or {}).get("building_use", "")
    # 용도를 특정하지 않은 포괄 질문(inferred)은 기본값 단독주택을 그대로
    # 노출하면 오해를 준다 — '시설물'로 일반화해 표기한다.
    if (d.get("request") or {}).get("inferred"):
        use = "시설물"
    # 사용자가 특정 시설(움막·농막·태양광 설치 등)을 콕 집어 물었으면, 질의 해석 결과
    # (requested_facility)를 검토 용도로 그대로 보여준다 — '시설물'로 뭉개지 않는다.
    _facility = ((d.get("request") or {}).get("requested_facility") or "").strip()
    if _facility:
        use = _facility
    out.append(f"- **검토 용도:** {use or '—'}")
    # 사용자가 말한 용도가 이 지역에서 불가면(예: 제1종전용주거의 일반 상가) 경고.
    ur = d.get("use_restriction")
    if ur:
        out.append(f"  - 🚫 **{ur['label']}:** {ur['reason']}")
    if reg.get("bcr_max_pct") is not None:
        out.append(
            f"- **건폐율 / 용적률:** {reg['bcr_max_pct']}% / {reg['far_max_pct']}%"
            f"  ({reg.get('legal_basis', '')})"
        )
    # 불가 판정에 법정 상한으로 계산한 가상 건축 규모를 함께 쓰면 실제로
    # 지을 수 있는 규모처럼 읽힌다. 계산값은 내부에 유지하되 답변에서는 숨긴다.
    if mass and display_verdict != "not_allowed":
        if mass.get("exceeds_far_limit"):
            out.append(
                f"- **건축 가능 규모:** 요청 용적률 {mass.get('requested_far_pct')}%는 "
                f"상한 {reg.get('far_max_pct')}% 초과 — 건폐율 기준 최대 건축면적 "
                f"약 {_area(mass['building_area_m2'])}"
            )
        elif mass.get("layout_feasible") is False:
            out.append(
                "- **건축 가능 규모:** 이격거리·주차 반영 후 실질적으로 사용할 수 있는 "
                f"바닥면적이 {mass.get('minimum_practical_footprint_m2', 10):g}㎡ 미만이어서 "
                "**사전진단상 배치 검토 불가**"
            )
        else:
            out.append(
                f"- **개념 배치 규모:** 건축면적 약 {_area(mass['building_area_m2'])} · "
                f"연면적 약 {_area(mass['gross_floor_area_m2'])} · 약 {mass['floors']}개 층 규모 "
                f"(높이 약 {mass['mass_height_m']}m, 층고 3.3m 가정)"
            )
    # 이격거리 안내 — 값이 있으면 값을, 없으면 왜 0인지 사유를 항상 남긴다.
    # (사용자: 이격이 없을 때도 안내문구가 있어야 한다)
    if mass and mass.get("layout_feasible") is not False and not mass.get("exceeds_far_limit"):
        sc = d.get("site_constraints") or {}
        front = float(sc.get("front_setback_m") or 0)
        adjacent = float(sc.get("adjacent_setback_m") or 0)
        north = float(sc.get("north_setback_m") or 0)
        parts = []
        if front > 0:
            parts.append(f"전면(건축선) {front:g}m")
        if adjacent > 0:
            parts.append(f"인접경계 {adjacent:g}m")
        if north > 0:
            parts.append(f"정북일조 {north:g}m")
        if parts:
            out.append(f"- **이격거리:** {' · '.join(parts)} (지도에 치수선 표시)")
        else:
            # 0m일 때도 '왜 0인지'를 이 용도·이 지자체 데이터로 구체적으로 남긴다.
            # (일반론 대신, 검토 용도명·수집 여부·다른 용도의 실제 수치를 근거로)
            rule = sc.get("setback_rule") or {}
            status = rule.get("status")
            jur = d.get("jurisdiction") or ""
            eval_use = reg.get("building_use") or (d.get("request") or {}).get(
                "building_use", ""
            )
            use_label = (
                "시설물" if (d.get("request") or {}).get("inferred") else eval_use
            )
            if status == "NOT_COLLECTED":
                out.append(
                    f"- **이격거리:** {jur or '이 지역'} 건축조례 '대지 안의 공지' 별표를 "
                    "아직 수집하지 못해 0m로 계산했습니다. 실제 이격은 관할 건축조례로 확인이 "
                    "필요합니다."
                )
            else:
                # 이 필지의 용도지역·연면적 기준으로 '실제' 적용되는 용도별 이격
                # 수치를 계산해 보여준다(일반론이 아니라 데이터의 수치를 그대로).
                zone_here = reg.get("zone") or (lu.get("zones") or [""])[0]
                gross_here = float((mass or {}).get("gross_floor_area_m2") or 0)
                applicable = setback_rules.applicable_setbacks(
                    jur, zone_here, gross_here, exclude_use=eval_use
                )
                line = (
                    f"- **이격거리:** {use_label} 용도는 대지 안의 공지(건축법 시행령 별표2) "
                    "대상이 아니어서 0m입니다."
                )
                if applicable:
                    parts_use = []
                    for item in applicable:
                        if item.get("needs_subtype"):
                            parts_use.append(f"{item['use']}(세부유형별)")
                            continue
                        dist = f"전면 {item['front_m']:g}m"
                        if item["adjacent_m"] > 0:
                            dist += f"·인접 {item['adjacent_m']:g}m"
                        parts_use.append(f"{item['use']} {dist}")
                    line += (
                        f" 이 규모(연면적 약 {gross_here:,.0f}㎡)에선 {', '.join(parts_use)}이 "
                        "적용됩니다(해당 용도로 검토 시 지도에 표시)."
                    )
                if rule.get("source"):
                    line += f" (근거: {rule['source'].split(' (')[0]})"
                out.append(line)

    existing = d.get("existing_buildings") or {}
    buildings = existing.get("buildings") or []
    if existing.get("status") == "FOUND" and buildings:
        first = buildings[0]
        details = " · ".join(
            value
            for value in (
                first.get("main_use"),
                (
                    f"지상 {first.get('ground_floors')}층"
                    if first.get("ground_floors") is not None
                    else ""
                ),
                (
                    f"연면적 {_area(first.get('total_area_m2'))}"
                    if first.get("total_area_m2")
                    else ""
                ),
                (
                    f"사용승인 {first.get('use_approval_date')}"
                    if first.get("use_approval_date")
                    else ""
                ),
            )
            if value
        )
        out.append(
            f"- **기존 건축물대장:** {existing.get('count', len(buildings))}건"
            + (f" ({details})" if details else "")
        )
    elif existing.get("status") == "CLEAR":
        out.append("- **기존 건축물대장:** 표제부 조회 없음")
    if verdict == "unknown" and reg.get("reason"):
        out.append("")
        reason_label = (
            "건축 불가 사유"
            if display_verdict == "not_allowed"
            else "판정 보류 사유"
        )
        out.append(f"- **{reason_label}:** {reg['reason']}")

    # 농지·산지 전용
    lc = d.get("land_conversion") or {}
    ji = d.get("jimok_info") or {}
    if ji.get("requires_conversion") or lc.get("status") == "REVIEW":
        out.append("")
        out.append(f"## {n}. 농지·산지 전용")
        n += 1
        out.append(f"- {lc.get('summary') or ji.get('note') or '전용 절차 확인이 필요합니다.'}")
        terrain = lc.get("terrain") or {}
        if terrain.get("status") == "REFERENCE_AVAILABLE":
            out.append(
                "- **지형 참고:** "
                f"평균경사도 {terrain.get('slope_mean_deg')}°"
                f" · 최대경사도 {terrain.get('slope_max_deg')}°"
                f" · 표고 {terrain.get('elevation_min_m')}~"
                f"{terrain.get('elevation_max_m')}m"
                f" (평균 {terrain.get('elevation_mean_m')}m)"
                " — COP30 30m DEM 기반"
            )
        inventory = lc.get("forest_inventory") or []
        if inventory:
            top = inventory[0]
            attributes = [
                top.get("forest_type"),
                top.get("species"),
                top.get("age_class"),
                top.get("diameter_class"),
                f"수관밀도 {top.get('density')}" if top.get("density") else "",
                top.get("stand_height"),
            ]
            details = " · ".join(str(value) for value in attributes if value)
            share = top.get("share_pct")
            share_text = f" ({share:.1f}% 중첩)" if isinstance(share, (int, float)) else ""
            year = top.get("updated_year")
            year_text = f" · 갱신 {year}년" if year else ""
            out.append(
                f"- **임상도 참고:** {details or '속성 확인'}{share_text}{year_text}"
            )
        for u in (lc.get("unknowns") or [])[:4]:
            out.append(f"- 확인 필요: {u}")
        for gap in (lc.get("data_gaps") or [])[:4]:
            source = gap.get("required_source")
            source_text = f" — 필요 자료: {source}" if source else ""
            gap_label = (
                "현장조사 필요"
                if gap.get("status") == "FIELD_SURVEY_REQUIRED"
                else "미수집 데이터"
            )
            out.append(
                f"- **{gap_label}:** {gap.get('item', '항목')}"
                f" ({gap.get('reason', '현재 데이터 없음')}){source_text}"
            )

    # 부담금
    cc = d.get("conversion_charge")
    dc = d.get("development_charge")
    if display_verdict != "not_allowed" and (cc or dc):
        out.append("")
        out.append(f"## {n}. 부담금 (참고)")
        n += 1
        if cc:
            out.append(f"- **{cc.get('label', '부담금')}:** 약 {_won(cc.get('estimated_won'))}원")
            if cc.get("caveat"):
                out.append(f"- {cc['caveat']}")
        if dc:
            out.append(f"- **개발부담금:** {dc['reason']}")
            if dc.get("applicable"):
                if dc.get("rate_note"):
                    out.append(f"  - {dc['rate_note']}")
                avg = dc.get("region_avg_per_case_won") or 0
                out.append(
                    f"  - **지역 통계(이 필지 계산액 아님):** 2025년 "
                    f"{dc.get('region', '전국')} 전체 개발부담금 부과 실적의 건당 평균은 "
                    f"약 {_won(avg)}원입니다."
                )
                if dc.get("calculation_formula"):
                    out.append(f"  - **필지별 산식:** {dc['calculation_formula']}")
                if dc.get("caveat"):
                    out.append(f"  - {dc['caveat']}")
            if dc.get("legal_basis"):
                out.append(f"  - 근거: {dc['legal_basis']}")

    # 도로·접도
    ra = d.get("road_access") or {}
    if ra.get("label"):
        out.append("")
        out.append(f"## {n}. 도로·접도")
        n += 1
        out.append(f"- {ra['label']}: {ra.get('message', '')}")
        road = (ra.get("roads") or [{}])[0]
        contact = road.get("contact_length_m")
        if contact is not None:
            # 도로 필지 규모·지적 폭은 접도 판단에 무의미하므로 쓰지 않는다. 정작 중요한
            # 건 접촉 길이가 건축법 접도 최소(2m)를 넘는지다. 우리 접촉값은 연속지적도
            # 기반 참고치라 '미달 가능'으로 표기하고 현황측량 확정을 안내한다.
            if contact < 2.0:
                extra = " · 건축법 접도 최소 2m 미달 가능 — 현황측량으로 실제 접도 확정 필요"
            else:
                extra = " · 건축법 접도 최소 2m 이상(유효 도로폭·후퇴선은 현황측량 확인)"
            out.append(f"- 접촉 길이 약 {contact}m{extra}")
        if not ra.get("interior_overlap_confirmed"):
            out.append(
                "- **도로 편입·분할:** 현재 자료에서는 필지 내부 도로 중첩이 확인되지 않아 "
                "도로 편입이나 필지분할이 필요하다고 판정하지 않습니다."
            )

    # 재해·환경·국가유산
    rs = d.get("regulatory_screen") or {}
    findings = rs.get("findings") or []
    # 조회 결과가 CLEAR여도 항목을 숨기지 않는다. 중첩 없음 역시 사용자가
    # 확인해야 할 진단 결과이며, 인허가 단계 번호도 결과에 따라 흔들리면 안 된다.
    if d.get("regulatory_screen") is not None:
        out.append("")
        out.append(f"## {n}. 재해·환경·국가유산")
        n += 1
        for f in findings[:6]:
            share = f" {f['share_pct']}% 중첩" if f.get("share_pct") is not None else ""
            out.append(f"- {f.get('category', '')} · {f.get('label', '')}{share}")
        if rs.get("summary"):
            out.append(f"- {rs['summary']}")
        for u in (rs.get("unknowns") or [])[:4]:
            out.append(f"- 추가 확인: {u}")

    # 법률 간 금지·예외·누적 적용 관계
    conflicts = d.get("legal_conflicts") or {}
    evaluations = conflicts.get("evaluations") or []
    if evaluations:
        out.append("")
        out.append(f"## {n}. 법률 적용 관계")
        n += 1
        out.append(f"- 상태: {_CONFLICT_STATUS_LABELS.get(conflicts.get('status'), '검토 필요')}")
        for evaluation in evaluations[:6]:
            # 요건이 쌓이는 규칙은 무엇과 무엇이 더해져 최종 허가가 되는지를
            # 문장이 아니라 한 줄 구성식으로 먼저 보여준다.
            requirements = evaluation.get("requirements") or []
            permits = [r.get("permit") for r in requirements if r.get("permit")]
            laws = [r.get("law") for r in requirements if r.get("law")]
            if permits:
                out.append(f"- **{' + '.join(permits)} → 최종 허가**")
            effect = evaluation.get("effect")
            if effect:
                out.append(f"- {effect}")
            if laws:
                out.append(f"- 근거: {' · '.join(laws)}")
        if conflicts.get("blocks_final_approval"):
            out.append(
                "- **최종 허가 제한:** 위 금지 규정 또는 예외 요건을 해소하기 전에는 "
                "건축허가를 받을 수 없습니다."
            )

    # 예상 인허가·협의 단계
    pr = d.get("permit_requirements") or {}
    items = pr.get("items") or []
    if items:
        out.append("")
        out.append(f"## {n}. 예상 인허가·협의 단계")
        n += 1
        for step_no, it in enumerate(items, start=1):
            days = f" · 법정 {it['processing_days']}일" if it.get("processing_days") else ""
            dept = f" · {it['department']}" if it.get("department") else ""
            out.append(f"{step_no}. **{it.get('name', '')}**{dept}{days}")
            docs = it.get("documents") or []
            if docs:
                out.append(f"   - 서류: {', '.join(docs[:5])}")
            if it.get("basis"):
                out.append(f"   - 근거: {it['basis']}")

    # 국가법령정보센터 원문 검증
    legal = d.get("legal_sources") or {}
    sources = legal.get("sources") or []
    if sources:
        out.append("")
        out.append(f"## {n}. 국가법령정보센터 원문 확인")
        n += 1
        for source in sources:
            effective = source.get("effective_date") or ""
            if len(effective) == 8:
                effective = f"{effective[:4]}-{effective[4:6]}-{effective[6:]}"
            suffix = f" · 시행 {effective}" if effective else ""
            out.append(
                f"- [{source.get('title', source.get('query', '법령 원문'))}]"
                f"({source.get('url', '')}){suffix}"
            )
    elif legal.get("status") in {"NOT_CONFIGURED", "UNAVAILABLE"}:
        out.append("")
        out.append(f"- ⚠ 법령 원문 검증 상태: {legal.get('message', '확인 불가')}")

    legal_evidence = d.get("legal_evidence") or legal.get("evidence") or []
    if legal_evidence:
        out.append("")
        out.append(f"## {n}. 전국 공통 인허가 법령 조문(근거)")
        n += 1
        for evidence in legal_evidence[:6]:
            label = " ".join(
                part for part in (
                    evidence.get("law") or evidence.get("ordinance"),
                    evidence.get("article"),
                    f"({evidence.get('title')})" if evidence.get("title") else "",
                )
                if part
            )
            url = evidence.get("url")
            out.append(f"- [{label}]({url})" if url else f"- {label}")

    # 관할 조례 근거 조문 (벡터 검색으로 찾은 실제 조문 — 판정 근거 추적용)
    evidence = d.get("ordinance_evidence") or []
    district_plan_evidence = d.get("district_plan_evidence") or []
    if evidence or district_plan_evidence:
        out.append("")
        if evidence and district_plan_evidence:
            local_evidence_title = "지자체 조례·지구단위계획 근거자료"
        elif district_plan_evidence:
            local_evidence_title = "지구단위계획 근거자료"
        else:
            local_evidence_title = "지자체 조례 근거자료"
        out.append(f"## {n}. {local_evidence_title}")
        n += 1
        for ev in evidence[:3]:
            eff = ev.get("effective_date") or ""
            if len(eff) == 8:
                eff = f"{eff[:4]}-{eff[4:6]}-{eff[6:]}"
            suffix = f" · 시행 {eff}" if eff else ""
            art = ev.get("article") or ""
            title = ev.get("title") or ""
            label = f"{ev.get('ordinance', '조례')} {art}({title})".strip()
            url = ev.get("url")
            out.append(f"- [{label}]({url}){suffix}" if url else f"- {label}{suffix}")
        for ev in district_plan_evidence:
            plan = ev.get("plan_name") or "지구단위계획"
            notice = ev.get("notice_no") or "관련 고시"
            docs = "·".join(ev.get("document_types") or [])
            suffix = f" — {docs}" if docs else ""
            if ev.get("verification_status") == "LATEST_NOTICE_CHECK_REQUIRED":
                suffix += " (최신 변경고시 확인 필요)"
            out.append(f"- [{plan} {notice} 원문·첨부자료]({ev.get('url')}){suffix}")
            for document in ev.get("documents") or []:
                if document.get("url"):
                    out.append(f"  - [{document.get('label', '첨부자료')}]({document['url']})")

    # 유의사항
    out.append("")
    out.append("## 유의사항")
    # 매번 붙는 일반 면책은 한 줄로(길다는 피드백). 필지별 상황 불릿만 아래에 덧붙는다.
    out.append(
        "- 공공데이터·법령 기반 사전검토 추정값입니다(조례·일조·이격·주차 산정으로 실제 "
        "규모는 줄 수 있음). 구체 인허가는 관할 행정청 확인이 필요합니다."
    )

    # 기존 건축물 — 있으면 신축 시 철거·멸실이 선행되거나, 개발제한·보전구역에서는
    # 개축·재축만 허용되고 신축이 제한·불가할 수 있음을 짚는다(판정을 단정하지 않는다).
    existing_b = d.get("existing_buildings") or {}
    if existing_b.get("status") == "FOUND" and existing_b.get("has_buildings"):
        _cnames = " ".join(
            (c.get("name", "") + c.get("note", "")) if isinstance(c, dict) else str(c)
            for c in (reg.get("constraints") or [])
        )
        # '준보전산지'는 보전산지가 아니라 산지전용허가를 거쳐 이용을 검토하는
        # 별도 분류다. 단순 부분문자열("보전산지")로 강한 보전규제로 읽지 않는다.
        _protected_cnames = _cnames.replace("준보전산지", "")
        if (
            "개발제한" in _protected_cnames
            or "보전산지" in _protected_cnames
            or "공익용산지" in _protected_cnames
            or "임업용산지" in _protected_cnames
        ):
            out.append(
                "- 이 필지엔 기존 건축물이 있고 개발제한·보전 규제가 감지됩니다 — 이런 "
                "구역은 기존 건축물의 개축·재축만 허용되고 신축은 제한·불가할 수 있습니다. "
                "최종 가부는 관할청·건축 설계사무소 확인이 필요합니다."
            )
        else:
            out.append(
                "- 이 필지엔 기존 건축물이 있어, 신축하려면 철거·멸실(해체허가·신고)과 "
                "소유권 확인이 선행됩니다. 용도지역·구역에 따라 개축·재축만 허용되거나 "
                "신축이 제한될 수 있어 최종 가부는 관할청 확인이 필요합니다."
            )

    # 우수·오수 배수 — 인접에 공공 배수처(도로·구거·하천)가 없으면 배수로가 사유지를
    # 지나야 할 수 있어, 토지사용승낙 또는 공유지 우회가 필요함을 짚는다.
    # 단, 농막·움막·태양광 등 특수·가설 시설(no_building_model)은 오수·배수 요건이 사실상
    # 없어 배수 유의사항을 붙이지 않는다(사용자 지적).
    _is_special = bool(
        (d.get("request") or {}).get("no_building_model")
        or ((d.get("regulation") or {}).get("map_presentation") or {}).get("no_building_model")
    )
    drainage = (d.get("road_access") or {}).get("drainage") or {}
    if drainage.get("public_outlet") is False and not _is_special:
        out.append(f"- {drainage.get('note')}")
        # 배수로가 사유지를 지나는 경우의 상세 경고는 소유구분이 '사유'로 확인된 때만 낸다.
        # 지목 기준 추정만으로는 지도에 확정 경로도 없어(사용자 지적) 특정 배수처·필지를
        # 단정하지 않는다.
        enc = drainage.get("encroachment") or {}
        blocking = [
            h for h in (enc.get("crossed_parcels") or [])
            if h.get("ownership") != "국공유"
        ] if enc.get("crosses_private") else []
        confirmed = any(h.get("ownership") == "사유" for h in blocking)
        if confirmed:
            jimoks = "·".join(dict.fromkeys(h.get("jimok", "") for h in blocking if h.get("jimok")))
            has_bldg = any(h.get("has_building") for h in blocking)
            _outlet = enc.get("outlet") or {}
            dest = _outlet.get("jimok") or "공공 배수처"
            dest_addr = _outlet.get("address") or ""
            where = (
                f"가장 가까운 공공 배수처인 {dest}({dest_addr})"
                if dest_addr
                else f"가장 가까운 공공 배수처({dest})"
            )
            basis = "토지특성정보 소유구분상 사유지로 확인됩니다"
            if has_bldg:
                out.append(
                    f"- {where}까지 배수로가 지목 '{jimoks}' 사유지를 지나야 하고 "
                    f"그 필지에 기존 건물이 있어(건축물대장 확인), 통과가 사실상 어렵습니다 — "
                    f"공유지(도로·구거)로 우회하는 경로를 우선 검토해야 합니다. {basis}."
                )
            else:
                out.append(
                    f"- {where}까지 배수로가 지목 '{jimoks}' 사유지를 지나야 합니다. "
                    f"사유지 통과는 토지사용승낙(동의)이 있어야 하며, {basis}. "
                    "지도의 빨간 경로는 방향을 짚는 개념선입니다(현황측량으로 확정)."
                )

    if d.get("jurisdiction_warning"):
        out.append(f"- {d['jurisdiction_warning']}")

    return "\n".join(out)


# 모델이 building_use 를 빼먹었을 때 질의 원문에서 용도를 되찾기 위한 표.
# (Gemini 의 OpenAI 호환 모드는 required 필드를 강제하지 않아, 낮은 확률로
#  address 만 채워서 제출한다 — 그대로 두면 KeyError 로 진단 전체가 죽는다)
_USE_KEYWORDS: list[tuple[str, str]] = [
    ("창고", "창고시설"), ("물류", "창고시설"),
    ("공장", "공장"),
    ("아파트", "공동주택"), ("빌라", "공동주택"),
    ("원룸", "단독주택"), ("다가구", "단독주택"),
    ("주택", "단독주택"), ("집", "단독주택"),
    ("사무", "업무시설"), ("빌딩", "업무시설"), ("오피스", "업무시설"),
    ("상업시설", "판매시설"), ("상업", "판매시설"),
    ("학교", "교육연구시설"), ("교육연구시설", "교육연구시설"),
    ("상가", "제1종근린생활시설"), ("근린생활", "제1종근린생활시설"),
    ("마트", "판매시설"), ("판매", "판매시설"),
    ("호텔", "숙박시설"), ("펜션", "숙박시설"), ("모텔", "숙박시설"),
]

_USE_ALIASES = {
    "다가구주택": "단독주택",
    "다중주택": "단독주택",
    "원룸주택": "단독주택",
    "다세대주택": "공동주택",
    "연립주택": "공동주택",
}


def _guess_use(query: str) -> str:
    for name in BUILDING_USES:          # 정식 용도명이 그대로 들어 있으면 그것
        if name in query:
            return name
    for kw, name in _USE_KEYWORDS:      # 아니면 일상어 키워드로
        if kw in query:
            return name
    return "시설물"


def _normalize_intent_text(query: str) -> str:
    """띄어쓰기·문장부호·영문 대소문자 차이를 제거한 의도 판별용 문자열."""
    return re.sub(r"[^0-9a-z가-힣%]", "", query.lower())


def _has_building_feasibility_intent(query: str) -> bool:
    """표현 하나가 아니라 '건축 대상 + 가능/허가/신축 행위' 의미를 판별한다.

    명확한 필지·좌표가 함께 있는 경우 이 결과로 결정 파이프라인에 바로 진입하고,
    애매한 문장만 LLM 주소·용도 추출로 넘긴다.
    """
    text = _normalize_intent_text(query)
    has_subject = any(
        term in text
        for term in (
            "건물", "건축", "신축", "시설", "주택", "집", "개발",
            "건축허가", "허가",
        )
    )
    has_construction_action = bool(
        re.search(r"지을|짓|지어|세울|세워|올릴|올려|들어갈|신축", text)
    )
    if not has_subject and not has_construction_action:
        return False

    # 활용형을 어간 중심으로 묶는다. '건축 날 수 있어' 같은 구어·오타성
    # 표현도 '날수있' 의미로 받아들이되, 단순 조회·설명 요청은 포함하지 않는다.
    has_feasibility = bool(
        re.search(
            r"가능|지을|짓|지어|지어도|세울|세워|올릴|올려도|"
            r"들어갈|들어오|입지|신축|개발할|개발해도|"
            r"할수있|될수있|날수있|허가나|허가날|"
            r"해도돼|해도되|돼|되나|되니|될까|되는지|"
            r"허용|가능여부|검토",
            text,
        )
    )
    return has_feasibility


def _deterministic_request(query: str) -> dict | None:
    """명확한 전체 지번과 용도는 LLM에 다시 해석시키지 않는다."""
    coordinate_match = re.search(
        r"경도\s*(-?\d+(?:\.\d+)?)[^\d-]+위도\s*(-?\d+(?:\.\d+)?)",
        query,
    )
    address_match = re.search(
        r"((?:[가-힣0-9]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구|읍|면|동|리)\s+)+"
        r"(?:산\s*)?\d+(?:-\d+)?)",
        query,
    )
    if not address_match and not coordinate_match:
        return None

    explicit_use = next((name for name in BUILDING_USES if name in query), None)
    if not explicit_use:
        explicit_use = next(
            (name for keyword, name in _USE_KEYWORDS if keyword in query),
            None,
        )
    generic_building = _has_building_feasibility_intent(query)
    if not explicit_use and not generic_building:
        return None
    # 표준 11용도가 아닌 '특정 시설'(움막·농막·태양광·축사·버섯재배사 등)을 '○○ 짓/설치/
    # 세우' 형태로 콕 집어 물었으면, 결정식으로 '시설물'로 뭉개지 말고 LLM(extract_request)이
    # 문장을 해석해 requested_facility(검토 용도)를 정하게 넘긴다. 결정식 정규식으로 라벨을
    # 직접 뽑지는 않는다(문법 조각 오탐 위험) — 판단은 LLM이 한다.
    # 일반 건축어(건물)·위치/지시어(여기·집 등)·지을 동사 없는 일반 가능여부 질의는 그대로
    # 결정식으로 처리해 속도를 지킨다(회귀 테스트의 포괄 질의가 여기 해당).
    if not explicit_use:
        _m = re.search(
            r"([가-힣A-Za-z0-9]{2,12})\s*(?:을|를)?\s*"
            r"(?:지을|지어|짓|설치|세우|세울|올릴|올려|들일|들여)",
            query,
        )
        if _m:
            _obj = re.sub(r"(에다|에서|에|으로|로|다)$", "", _m.group(1))
            _generic = {
                "건물", "건축물", "시설물", "여기", "거기", "이곳", "그곳",
                "무엇", "무얼", "개발", "허가", "이곳에",
            }
            if _obj and _obj not in _generic and _obj not in BUILDING_USES:
                return None

    parking = (
        "underground" if "지하주차" in query
        else "mechanical" if "기계식주차" in query
        else "surface" if "지상주차" in query
        else "mixed" if "혼합주차" in query
        else "unspecified"
    )
    req = {
        "address": (
            ""
            if coordinate_match
            else address_match.group(1).strip()
            if address_match
            else ""
        ),
        "building_use": explicit_use or "시설물",
        "inferred": explicit_use is None,
        "parking_strategy": parking,
        "requested_facility": "",
        "no_building_model": False,
    }
    if coordinate_match:
        req["lon"] = float(coordinate_match.group(1))
        req["lat"] = float(coordinate_match.group(2))
    far = re.search(r"용적률\s*(\d+(?:\.\d+)?)\s*%", query)
    if far:
        req["far_target_pct"] = float(far.group(1))
    return req


async def extract_request(client, query: str) -> dict:
    """자연어 질의 -> {address, building_use, inferred, far_target_pct?}"""
    deterministic = _deterministic_request(query)
    if deterministic:
        return deterministic

    resp = await client.complete(
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": query}],
        tools=EXTRACT_TOOL,
        max_tokens=1000,
    )
    for call in resp.tool_calls:
        if call.name == "submit_request":
            req = call.input
            # required 여도 빠져서 올 수 있다 — 빠진 필드는 결정적으로 보정한다
            req.setdefault("address", "")
            if not req.get("building_use"):
                req["building_use"] = _guess_use(query)
                req["inferred"] = True
            elif req["building_use"] not in BUILDING_USES:
                # Gemini 호환 모드가 enum 밖의 세부 용도(다가구주택 등)를
                # 반환할 수 있다. 판정표의 건축법상 상위 용도로 정규화한다.
                req["building_use"] = _USE_ALIASES.get(
                    req["building_use"], _guess_use(query)
                )
                req["inferred"] = True
            req.setdefault("inferred", True)
            req.setdefault("parking_strategy", "unspecified")
            req.setdefault("requested_facility", "")
            req.setdefault("no_building_model", False)
            # 용도 미지정 일반 질문은 특정 건축물 용도로 바꾸지 않는다.
            has_explicit_use = any(
                name in query for name in BUILDING_USES
            ) or any(keyword in query for keyword, _name in _USE_KEYWORDS)
            if not has_explicit_use and _has_building_feasibility_intent(query):
                req["building_use"] = "시설물"
                req["inferred"] = True
            return req

    # 도구를 안 부른 경우 — 주소를 못 찾은 것으로 본다
    return {"address": "", "building_use": _guess_use(query), "inferred": True, "parking_strategy": "unspecified"}


def _merge_permit_legal_evidence(
    permit_items: list[dict], semantic_evidence: list[dict] | None
) -> list[dict]:
    """전국 공통 인허가 근거를 검색 순위와 무관하게 우선 보존한다."""
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(evidence: dict) -> None:
        law = str(evidence.get("law") or evidence.get("ordinance") or "").strip()
        article = str(evidence.get("article") or "").strip()
        key = (law, article)
        if not law or key in seen:
            return
        seen.add(key)
        result.append(evidence)

    # permit_rules.json에서 실제 선택된 단계에 연결된 법령은 결정적 근거다.
    # 도로·접도 조문 등이 벡터 검색 상위 건수에서 밀려 사라지면 안 된다.
    for item in permit_items:
        for reference in item.get("legal_references") or []:
            if not isinstance(reference, dict):
                continue
            add({
                "jurisdiction": reference.get("jurisdiction") or "전국",
                "law": reference.get("law"),
                "article": reference.get("article"),
                "title": reference.get("title"),
                "kind": "인허가 규칙 직접 근거",
                "url": reference.get("source_url"),
                "ref_id": reference.get("ref_id"),
            })
    for evidence in semantic_evidence or []:
        if isinstance(evidence, dict):
            add(evidence)
    return result


def _summarize(state: dict) -> str:
    """판정 결과를 사람이 읽을 요약으로. LLM 없이 값에서 조립한다."""
    reg = state["regulation"]
    parcel = state["parcel"]
    mass = state.get("massing")
    jm = state.get("jimok_info", {})
    conversion = state.get("land_conversion", {})
    existing = state.get("existing_buildings", {})
    charge = state.get("conversion_charge")
    road = state.get("road_access", {})
    screen = state.get("regulatory_screen", {})
    permits = state.get("permit_requirements", {})
    site = state.get("site_constraints", {})

    verdict_text = {
        "allowed": "건축 가능합니다",
        "conditional": "조건부로 가능합니다",
        "not_allowed": "건축할 수 없습니다",
        "unknown": "판단하지 못했습니다",
    }[reg.get("verdict", "unknown")]

    # 판정 불가 경로(조례 미수집 비도시지역 등)의 reg 에는 building_use ·
    # 건폐율·근거 키가 없다. 있는 값만으로 요약을 조립해야 한다 —
    # 여기서 KeyError 가 나면 '판단 불가' 안내 대신 진단 전체가 죽는다.
    use = reg.get("building_use") or state.get("request", {}).get("building_use", "")

    lines = [
        f"{parcel['jibun']} ({parcel['area_m2']:,.0f}㎡, 지목 {parcel['jimok']})는 "
        f"{reg['zone']}이며, {use}은(는) {verdict_text}.",
        reg.get("reason", ""),
    ]

    if reg.get("bcr_max_pct") is not None:
        lines.append(
            f"적용 기준: 건폐율 {reg['bcr_max_pct']}%, 용적률 {reg['far_max_pct']}% "
            f"— {reg.get('legal_basis', '')}"
        )

    if reg.get("limit_source") == "statutory":
        lines.append(
            "이 지자체의 도시계획조례를 수집하지 못해 법정 상한을 적용했습니다. "
            "실제 조례는 이보다 강할 수 있어 규모가 과다 산정되었을 수 있습니다."
        )

    if mass and mass.get("exceeds_far_limit"):
        lines.append(
            f"요청 용적률 {mass['requested_far_pct']}%는 적용 상한 "
            f"{mass['far_applied_pct']}%를 초과해, 건폐율 기준 최대 건축면적 "
            f"{mass['building_area_m2']:,.0f}㎡만 표시합니다."
        )
    elif mass:
        lines.append(
            f"산출: 건축면적 {mass['building_area_m2']:,.0f}㎡, "
            f"연면적 {mass['gross_floor_area_m2']:,.0f}㎡, 약 {mass['floors']}층."
        )
        if site:
            parking_text = (
                f"주차 필요량 {site['parking']['spaces']}대"
                if site["parking"].get("estimated")
                else "주차대수는 용도 확정 후 산정"
            )
            lines.append(
                f"배치 제약 검토: 밀도상 건축면적 {site['density_building_area_m2']:,.0f}㎡에서 "
                f"확인된 공지·정북 일조·선택된 주차방식을 반영해 {site['adjusted_building_area_m2']:,.0f}㎡로 "
                f"약 {site['reduction_pct']}% 조정했습니다. {parking_text}, "
                f"정북 이격 {site['north_setback_m']}m입니다. {site['caveat']}"
            )

    if jm.get("requires_conversion"):
        lines.append(f"지목이 '{jm['jimok']}'({jm['label']})이라 {jm['procedure']}가 선행됩니다.")
    if conversion:
        lines.append(
            f"전용 사전검토: {conversion.get('label', '')} — "
            f"{conversion.get('summary', '')}"
        )
        if conversion.get("unknowns"):
            lines.append(
                "추가 확인: " + ", ".join(conversion["unknowns"]) + "."
            )
    if existing.get("status") == "FOUND":
        names = ", ".join(
            building.get("name", "건축물")
            for building in existing.get("buildings", [])[:3]
        )
        lines.append(
            f"기존 건축물대장 {existing.get('count', len(existing.get('buildings', [])))}건"
            f"({names})이 확인됩니다. 신축 전 소유권 확인과 철거·말소 절차가 필요합니다."
        )
    elif existing.get("status") in {"UNAVAILABLE", "NOT_CONFIGURED"}:
        lines.append("기존 건축물대장을 확인하지 못해 현황 건축물 여부는 미확인입니다.")
    if charge:
        lines.append(
            f"{charge['label']}: 약 {charge['estimated_won']:,.0f}원 "
            f"(전용예상면적 {charge['area_m2']:,.1f}㎡ 기준). {charge['caveat']}"
        )
    if road:
        lines.append(f"접도 사전검토: {road.get('label', '')} — {road.get('message', '')}")
    if screen:
        lines.append(f"재해·환경·국가유산 스크리닝: {screen.get('summary', '')}")
        if screen.get("unknowns"):
            lines.append("미연계·추가 확인: " + ", ".join(screen["unknowns"]) + ".")
    if permits:
        names = " → ".join(item["name"] for item in permits.get("items", []))
        lines.append(f"{permits.get('summary', '인허가 절차')}: {names}.")

    lu = state.get("land_use", {})
    if lu.get("straddling"):
        parts = " · ".join(
            f"{s['zone']} {s['share_pct']}%({s['area_m2']:,.0f}㎡)"
            for s in lu.get("zone_shares", [])
        )
        smallest = min(
            (s["area_m2"] for s in lu.get("zone_shares", [])), default=0
        )
        lines.append(
            f"주의: 이 필지는 둘 이상의 용도지역에 걸쳐 있습니다 — {parts}. "
            f"위 판정은 최대 면적 부분({reg['zone']}) 기준입니다. "
            + (
                "국토계획법 제84조에 따라 건폐율 "
                f"{reg['bcr_max_pct']}%·용적률 {reg['far_max_pct']}%로 "
                "면적 가중평균했습니다."
                if reg.get("weighted_limits", {}).get("applied")
                else "가장 작은 부분이 330㎡ 이하이므로 건폐율·용적률의 "
                "면적 가중평균 확인이 필요합니다."
                if smallest <= 330
                else "국토계획법 제84조에 따라 각 부분별로 규제가 적용될 수 있습니다."
            )
        )

    lines.append(
        "이 결과는 사전검토용 개념 배치입니다. 실제 허가도면은 측량성과, 건축선, "
        "지자체 건축·주차장 조례와 구조·피난·소방 계획을 추가 반영해야 합니다."
    )
    return " ".join(lines)


async def run_prediagnosis(
    client,
    query: str,
    on_progress: Callable[[str, dict], None] | None = None,
    max_turns: int = 12,  # 하위 호환 (더 이상 쓰지 않음)
) -> dict:
    """자연어 질의 -> 구조화된 사전진단 결과. LLM 호출 1회.

    on_progress(step_name, payload) 로 진행 상황을 실시간 통보한다.
    """
    state: dict = {}
    started_at = time.perf_counter()
    phase_started_at = started_at
    timings: dict[str, float] = {}

    def step(name: str, payload: dict) -> None:
        nonlocal phase_started_at
        now = time.perf_counter()
        if timings or phase_started_at != started_at:
            timings[f"before_{name}_ms"] = round((now - phase_started_at) * 1000, 1)
        phase_started_at = now
        if on_progress:
            on_progress(name, payload)

    # --- 1) 자연어에서 주소·용도 뽑기 (유일한 LLM 호출) ---
    step("extract_request", {"query": query})
    req = await extract_request(client, query)
    state["request"] = req

    has_coordinates = isinstance(req.get("lon"), (int, float)) and isinstance(
        req.get("lat"), (int, float)
    )
    if not req.get("address") and not has_coordinates:
        return {
            **state,
            "verdict": "unknown",
            "summary": "질의에서 주소를 찾지 못했습니다. 지번 또는 도로명주소를 알려주세요.",
        }

    # --- 2) 이후는 결정적 파이프라인 ---
    if has_coordinates:
        state["location"] = {
            "lon": float(req["lon"]),
            "lat": float(req["lat"]),
            "matched_address": "지도에서 선택한 위치",
        }
    else:
        step("geocode_address", {"address": req["address"]})
        state["location"] = await vworld.geocode(req["address"])
    lon, lat = state["location"]["lon"], state["location"]["lat"]

    # 필지와 용도지역은 서로 독립(둘 다 좌표만 필요)이라 함께 조회한다.
    step("get_parcel", {"lon": lon, "lat": lat})
    step("get_land_use", {"lon": lon, "lat": lat})
    state["parcel"], state["land_use"] = await asyncio.gather(
        vworld.get_parcel(lon, lat),
        vworld.get_land_use(lon, lat),
    )
    if has_coordinates and state["parcel"].get("jibun"):
        state["location"]["matched_address"] = state["parcel"]["jibun"]

    # 용도지구·지구단위계획 등은 VWorld 용도지역 조회로 안 잡힌다. 토지이용계획
    # (토지이음) API 로 필지 PNU 의 '지역지구 등 지정여부'를 보강한다.
    # (LANDUSE_KEY 가 없으면 [] 를 돌려주므로 지금은 영향 없음)
    from ..tools import landuse as landuse_tool

    landuse_designations = await landuse_tool.get_landuse_designations(
        state["parcel"].get("pnu", "")
    )
    if not isinstance(landuse_designations, dict):
        landuse_designations = {
            "status": "UNAVAILABLE",
            "source": "VWorld NED 토지이용계획정보",
            "records": [],
            "active_records": [],
            "error": "INVALID_RESPONSE_TYPE",
        }
    state["land_use"]["designation_lookup"] = landuse_designations
    extra_districts = list(dict.fromkeys(
        record["name"]
        for record in landuse_designations.get("active_records", [])
        if not record.get("is_zoning")
    ))
    if extra_districts:
        merged = list(dict.fromkeys(state["land_use"].get("districts", []) + extra_districts))
        state["land_use"]["districts"] = merged

    from ..tools import jimok as jimok_tool

    state["jimok_info"] = jimok_tool.classify(state["parcel"].get("jimok"))

    # 걸침확인·전용·건축물대장·접도·재해 스크리닝은 모두 필지 폴리곤/PNU 만
    # 있으면 되는 독립 조회다 — 하나씩 기다리지 말고 한꺼번에 병렬로 돌린다.
    # (걸침확인만 순차로 두면 아파트 단지 같은 큰 필지에서 1초 넘게 낭비된다.)
    step("check_zone_overlap", {"pnu": state["parcel"].get("pnu", "")})
    step("check_land_conversion", {"pnu": state["parcel"].get("pnu", "")})
    step("check_existing_buildings", {"pnu": state["parcel"].get("pnu", "")})
    step("check_road_access", {"pnu": state["parcel"].get("pnu", "")})
    step("screen_disaster_environment_heritage", {"pnu": state["parcel"].get("pnu", "")})
    (
        zone_shares,
        state["land_conversion"],
        state["existing_buildings"],
        state["road_access"],
        state["regulatory_screen"],
    ) = await asyncio.gather(
        vworld.get_zone_shares(state["parcel"].get("geometry")),
        land_conversion.assess(
            state["parcel"]["geometry"], state["jimok_info"]
        ),
        building_register.lookup(
            state["parcel"].get("pnu", ""),
            address=state["location"].get("matched_address", ""),
        ),
        road_access.assess(
            state["parcel"]["geometry"], state["parcel"].get("pnu", "")
        ),
        regulatory_screen.assess(
            state["parcel"]["geometry"],
            state["land_use"].get("districts", []),
            designation_lookup=state["land_use"].get("designation_lookup"),
        ),
    )

    # 필지 폴리곤으로 용도지역 걸침을 확인한다. 점 조회는 필지가 경계에
    # 걸쳐 있으면 점 위치에 따라 답이 달라진다 (국토계획법 제84조 케이스).
    if zone_shares:
        # 걸침 조각 면적은 기하면적 기준이라 조각 합이 공부면적(전체면적)과 어긋난다.
        # 공부면적/기하면적 비로 스케일하면 토지이음의 걸침 면적과 맞고 조각 합=전체면적이
        # 된다. share_pct(=조각/기하 비율)는 그대로라 제84조 가중평균 결과는 바뀌지 않고,
        # 지도용 geometry 도 건드리지 않는다.
        ledger_area = state["parcel"].get("area_m2")
        geo_area = vworld.geodesic_area_m2(state["parcel"].get("geometry") or {})
        if ledger_area and geo_area > 0 and abs(ledger_area - geo_area) > 0.05:
            factor = ledger_area / geo_area
            for s in zone_shares:
                s["area_m2"] = round(float(s.get("area_m2") or 0) * factor, 1)
        state["land_use"]["zone_shares"] = zone_shares
        state["land_use"]["straddling"] = len(zone_shares) >= 2
        # 판정 기준 지역은 점이 아니라 최대 면적 부분으로 잡는다
        state["land_use"]["zones"] = list(
            dict.fromkeys(
                [s["zone"] for s in zone_shares] + state["land_use"]["zones"]
            )
        )

    # 연속지적도상 도로 필지가 없어도, 도시계획시설 도로가 필지에 접하면 접도 근거가 될
    # 수 있다. VWorld 도시계획 도로 레이어는 접촉 geometry와 '집행 여부'(미집행=미개설)까지
    # 주므로, 결정만 됐는지/실제 개설됐는지를 갈라 판정한다. 레이어가 비면 토지이음 지정
    # 목록(접함)으로 폴백한다.
    if state["road_access"].get("status") in {"NO_CADASTRAL_ROAD", "UNAVAILABLE"}:
        # 도로 없음일 때만 도시계획 도로 레이어를 단독 조회한다(다른 VWorld 호출과
        # 동시 실행 시 레이트리밋 위험이 있어 gather 밖에서 부른다).
        # VWorld 가 느리면 이 한 호출이 httpx 15초 한도까지 물려 진단 전체가 20초(LLM
        # 클라이언트 한도)를 넘겨 타임아웃난다. 상한을 걸고 초과 시 아래 지정목록 폴백으로.
        try:
            planned = await asyncio.wait_for(
                vworld.get_planned_roads(state["parcel"].get("geometry")), timeout=6.0
            )
        except asyncio.TimeoutError:
            planned = []
        if not planned:  # 레이어가 비면(또는 지연 상한 초과) 토지이음 지정목록의 도로 '접함'으로 폴백
            planned = [
                {"name": r["name"], "executed": ""}
                for r in (state["land_use"]["designation_lookup"].get("records") or [])
                if r.get("relation") == "접함"
                and re.search(r"(광로|대로|중로|소로)", r.get("name") or "")
            ]
        if planned:
            names = list(dict.fromkeys(p["name"] for p in planned))
            unexecuted = any("미집행" in (p.get("executed") or "") for p in planned)
            opened = any(
                p.get("executed") and "미집행" not in p["executed"] for p in planned
            )
            ra = state["road_access"]
            ra["planned_roads"] = planned
            ra["status"] = "PLANNED_ROAD_ABUTS"
            if opened:
                ra["label"] = "도시계획도로 접함(개설)"
                ra["summary"] = ra["message"] = (
                    f"도시계획시설 도로({'·'.join(names)})가 필지에 접하고 개설(집행)된 "
                    "도로입니다. 유효 도로폭·중심선 후퇴선은 현황측량으로 확정해야 합니다."
                )
            elif unexecuted:
                ra["label"] = "도시계획도로 미집행(미개설)"
                ra["summary"] = ra["message"] = (
                    f"도시계획시설 도로({'·'.join(names)})가 필지에 접하지만 아직 개설되지 "
                    "않은 미집행(미개설) 도로입니다. 현재는 실제 도로가 없어 접도 요건을 "
                    "충족하지 못하며, 도로 개설 또는 별도 진입로 확보가 선행되어야 합니다."
                )
            else:
                ra["label"] = "도시계획도로 접함(접도 검토)"
                ra["summary"] = ra["message"] = (
                    f"도시계획시설 도로({'·'.join(names)})가 필지에 접합니다. 개설 여부와 "
                    "유효 도로폭을 현황측량으로 확인하면 접도 요건 검토가 가능합니다."
                )

    zone = state["land_use"]["zones"][0]
    jurisdiction = ordinance.detect_jurisdiction(state["location"]["matched_address"])
    state["jurisdiction"] = jurisdiction

    step("lookup_zoning", {"zone": zone, "building_use": req["building_use"]})
    reg = zoning.lookup_zoning_rules(
        zone=zone,
        building_use=req["building_use"],
        districts=state["land_use"]["districts"],
        jurisdiction=jurisdiction,
        # 검토 용도(농막·축사 등)를 넘겨 시설 특정 제약(가축사육제한구역 등)을
        # 그 시설일 때만 걸리게 한다. 비어 있으면 building_use 로 대체.
        facility=str(req.get("requested_facility") or "").strip(),
    )
    reg = zoning.apply_straddling_limits(reg, zone_shares, jurisdiction)
    state["regulation"] = reg

    # 움막·농막은 표준 용도지역 판정표(11용도) 밖의 농지 가설건축물이다. 농지 위 허용
    # 여부는 지역추천과 공유하는 단일 원본 결정식(facility_rules)이 정한다: 농막은 신고 후
    # 소규모(약 20㎡ 이하) 조건부, 움막은 농지법상 농지에 설치할 수 없어 불가.
    _facility = str(req.get("requested_facility") or "").strip()
    _farm_verdict = facility_rules.farmland_facility_verdict(
        _facility, state["parcel"].get("jimok")
    )
    if _farm_verdict == "not_allowed":
        reg["verdict"] = "not_allowed"
        reg["reason"] = (
            f"{_facility}은(는) 농지법상 농지(전·답·과수원)에 설치할 수 있는 시설이 아니어서 "
            "이 필지에는 설치할 수 없습니다. 농막은 신고 후 소규모 설치가 가능합니다."
        )
    elif _farm_verdict == "conditional":
        reg["verdict"] = "conditional"
        reg["reason"] = (
            f"{_facility}은(는) 농지에 지자체 신고 후 연면적 20㎡ 이하로 설치할 수 있는 "
            "가설건축물입니다. 농지전용 없이 가능하나 입지·신고 기준과 도로·배수 여건을 확인해야 합니다."
        )

    # 협소 필지 — 대지면적이 용도지역 법정 최소 대지면적(시행령 제80조) 미만이면
    # 조건부 가능이라도 배치가 제한될 수 있음을 알린다(법령 값은 데이터파일에서).
    from ..tools import min_lot_area

    state["min_lot_area"] = min_lot_area.check(zone, state["parcel"].get("area_m2"))

    # 기본 진단은 기존 건축물을 그대로 둔 신축을 가정한다. 협소하거나 기존 건물이
    # 확인되면 '실질 배치 불가'로 막고, 사용자가 후속으로 이 사유를 배제해 달라고 할
    # 때만 용도지역·규제 기준의 가능성을 별도로 답한다.
    state["placement_restricted"] = bool(state["min_lot_area"]) or bool(
        (state["existing_buildings"] or {}).get("has_buildings")
    )

    # 필지 분할 성립 사전판정 — '분할해서 지을 수 있어?' 시나리오의 토대. 정확한 분할선이
    # 아니라 성립 여부·유효 대지면적을 기존 데이터로 추정한다(결정적, 사전검토용).
    from ..tools import land_division

    state["land_division"] = land_division.assess(state)

    # 보전산지·공익용산지 또는 공원구역은 용도지역의 건폐율·용적률만으로
    # '조건부 가능'이라 할 수 없다. 법정 예외 허용시설인지 확인하기 전에는
    # 건축 가능 규모를 제시하지 않고 판단을 보류한다.
    protected_districts = [
        name
        for name in state["land_use"].get("districts", [])
        if "공원구역" in name or "도시자연공원" in name
    ]
    restricted_conversion = (
        state["land_conversion"].get("status") == "RESTRICTED_REVIEW"
    )
    if (
        reg.get("verdict") != "not_allowed"
        and (restricted_conversion or protected_districts)
    ):
        reg["verdict"] = "unknown"
        restrictions = []
        restricted_forest_label = ""
        if restricted_conversion:
            forest_overlaps = (
                (state["land_conversion"].get("forest") or {}).get("overlaps") or []
            )
            # 보전산지는 임업용·공익용산지의 상위 분류이므로 세부 분류가 함께
            # 확인되면 상위 명칭을 중복 표기하지 않는다.
            detailed_names = list(dict.fromkeys(
                overlap.get("name") or overlap.get("code")
                for overlap in forest_overlaps
                if (overlap.get("name") or overlap.get("code"))
                in {"임업용산지", "공익용산지", "UFM110", "UFM120"}
            ))
            forest_names = detailed_names or list(dict.fromkeys(
                overlap.get("name") or overlap.get("code")
                for overlap in forest_overlaps
                if overlap.get("name") or overlap.get("code")
            ))
            restricted_forest_label = "·".join(forest_names) or "보전산지"
            restrictions.append(
                f"산림청 산지구분 데이터에서 {restricted_forest_label} 중첩이 "
                f"확인되었습니다. 해당 계획이 {restricted_forest_label} 안의 "
                "허용행위에 해당하는지"
            )
        if protected_districts:
            restrictions.append(
                f"{'·'.join(protected_districts)} 안의 행위허가·허용시설 해당 여부"
            )
        if restricted_forest_label and not protected_districts:
            reg["reason"] = (
                restrictions[0]
                + " 확인하기 전에는 건축이 불가합니다."
            )
        else:
            reg["reason"] = (
                " / ".join(restrictions)
                + "를 확인하기 전에는 건축 가능 여부를 확정할 수 없습니다."
            )
        reg.setdefault("constraints", []).append(reg["reason"])
        # 법적 판정은 예외 허용시설 확인 전이라 unknown을 유지하되, 지도 표현은
        # 건축 가능한 것처럼 보이지 않게 구조화해 전달한다. 지도 제어
        # 코드가 전용 상태 조합을 다시 해석하거나 문구를 하드코딩하지 않는다.
        reg["map_presentation"] = {
            "verdict": "not_allowed",
            "label": "건축 불가",
            "color": "#C62828",
            "show_building_mass": False,
            "show_building_dimensions": False,
            "reason": reg["reason"],
        }

    # 협소·기존건물(placement_restricted)은 신축을 배치할 수 없으므로,
    # 건축 불가와 같은 map_presentation으로 카드·팝업·규모·매스·치수·모델을 억제한다
    # (배지 문구만 '실질 배치 불가'). 위 표현이 이미 있으면 그대로 둔다.
    if state["placement_restricted"] and not reg.get("map_presentation"):
        reg["map_presentation"] = {
            "verdict": "not_allowed",
            "label": "실질 배치 불가",
            "color": "#C62828",
            "show_building_mass": False,
            "show_building_dimensions": False,
        }

    # 농막·움막·태양광처럼 '지을 수는 있으나(조건부/가능) 전용 3D 모델이 아직 구현되지
    # 않은' 특수·가설 시설(no_building_model)은, 용도지역 밀도로 매스·규모를 그리면
    # 오해를 준다(농막은 20㎡). 매스·치수·규모는 감추되 판정(조건부/가능)은 유지하고,
    # 현재 구현된 다른 용도 모델을 보여줄지 되묻도록 표시한다. 불가 판정에는 적용 안 함.
    _facility = str(req.get("requested_facility") or "").strip()
    if (
        req.get("no_building_model")
        and _facility
        and reg.get("verdict") in {"allowed", "conditional"}
        and not reg.get("map_presentation")
    ):
        reg["map_presentation"] = {
            "verdict": reg.get("verdict"),
            "label": "조건부 가능" if reg.get("verdict") == "conditional" else "가능",
            "color": "#F9A825" if reg.get("verdict") == "conditional" else "#2E7D32",
            "show_building_mass": False,
            "show_building_dimensions": False,
            "no_building_model": _facility,
            "reason": (
                f"{_facility}은(는) 표준 건축물이 아니라(가설·특수 시설) 전용 3D 모델·규모 "
                f"산정 대상이 아니어서 매스·규모는 표시하지 않습니다. 다만 용도지역 건폐율·"
                f"용적률 상한은 필지 기준값으로 함께 표시합니다. 현재 구현된 다른 용도의 "
                f"가능한 건물 모델을 보여드릴까요?"
            ),
        }

    # 용도지역상 가능한 용도라도 농지·산지 전용 제한 검토가 남으면
    # 화면의 종합 표시는 최소 '조건부'여야 한다.
    if (
        reg.get("verdict") == "allowed"
        and (
            state["land_conversion"].get("status")
            in {"PERMIT_REQUIRED", "RESTRICTED_REVIEW", "MANUAL_REVIEW", "UNKNOWN"}
            or state["road_access"].get("status")
            in {"NO_CADASTRAL_ROAD", "UNAVAILABLE", "PLANNED_ROAD_ABUTS"}
            or state["regulatory_screen"].get("status") == "REVIEW"
        )
    ):
        reg["verdict"] = "conditional"

    state["legal_conflicts"] = legal_conflicts.evaluate(state)
    if state["legal_conflicts"]["blocks_final_approval"] and reg.get("verdict") in {"allowed", "conditional"}:
        reg["verdict"] = "unknown"
        reg["reason"] = state["legal_conflicts"]["summary"]

    # 불허면 매스를 만들지 않는다 — 지을 수 없는 건물을 그려 보여주지 않기 위해
    if reg["verdict"] in {"allowed", "conditional"} and reg.get("bcr_max_pct"):
        step("calc_massing", {"area_m2": state["parcel"]["area_m2"]})
        state["massing"] = massing.calc_massing(
            area_m2=state["parcel"]["area_m2"],
            bcr_max_pct=reg["bcr_max_pct"],
            far_max_pct=reg["far_max_pct"],
            far_target_pct=req.get("far_target_pct"),
        )
        # 사용자가 주차 방식을 지정하지 않았으면 임의로 지상주차를 가정하지 않는다.
        _parking = req.get("parking_strategy") or "unspecified"
        state["site_constraints"] = site_constraints.apply(
            parcel_geometry=state["parcel"]["geometry"],
            massing=state["massing"],
            building_use=req["building_use"],
            zone=reg.get("zone", zone),
            jurisdiction=jurisdiction,
            road_access=state.get("road_access"),
            parking_strategy=_parking,
        )
        constrained = state["site_constraints"]
        achievable_gross = constrained.get(
            "achievable_gross_floor_area_m2",
            state["massing"]["gross_floor_area_m2"],
        )
        state["massing"].update({
            "density_building_area_m2": state["massing"]["building_area_m2"],
            "building_area_m2": constrained["adjusted_building_area_m2"],
            "gross_floor_area_m2": achievable_gross,
            "floors_theoretical": round(
                achievable_gross
                / constrained["adjusted_building_area_m2"], 2
            ) if constrained["adjusted_building_area_m2"] else 0,
            "floors": constrained["floors"],
            "full_floors": constrained["full_floors"],
            "top_floor_ratio": constrained["top_floor_ratio"],
            "mass_height_m": constrained["mass_height_m"],
            "layout_feasible": constrained.get("layout_feasible", True),
            "minimum_practical_footprint_m2": constrained.get(
                "minimum_practical_footprint_m2"
            ),
            "note": constrained["caveat"],
        })
        state["conversion_charge"] = conversion_charges.estimate(
            jimok_category=state["jimok_info"].get("category", ""),
            conversion=state["land_conversion"],
            conversion_area_m2=state["massing"].get("building_area_m2"),
            official_land_price_won_m2=state["parcel"].get("jiga_won_per_m2"),
        )

    display_verdict = (reg.get("map_presentation") or {}).get("verdict") or reg.get(
        "verdict"
    )
    charge_estimation_allowed = bool(
        display_verdict in {"allowed", "conditional"}
        and state.get("massing")
    )

    # 개발부담금 — 지목변경(전용) 수반 대규모 개발이면 대상 가능성·부과율·지역
    # 참고치를 안내한다(정확 금액은 산정 불가). 단순 건축(전용 불요)이면 None.
    state["development_charge"] = (
        development_charge.assess(
            requires_conversion=bool(state["jimok_info"].get("requires_conversion")),
            area_m2=state["parcel"].get("area_m2"),
            zone=zone,
            jurisdiction=jurisdiction or "",
            address=state["location"]["matched_address"],
        )
        if charge_estimation_allowed
        else None
    )

    state["permit_requirements"] = permit_requirements.build(state)
    step("verify_legal_sources", {})
    state["legal_sources"] = await law_open.verify_legal_sources(state)

    # 관할 조례 원문에서 이번 판정과 관련된 조문을 근거로 찾아 붙인다(벡터 검색).
    # 숫자는 여기서 만들지 않는다 — 이미 산정한 판정의 '근거 조문'을 인용할 뿐이다.
    # jurisdiction 으로 관할을, 시행일(effective_on)로 시점을 제한해 다른 조례를 섞지 않는다.
    step("cite_ordinance_evidence", {})
    if jurisdiction and ordinance_index.available():
        use = req.get("building_use") or ""
        state["ordinance_evidence"] = ordinance_index.search(
            query=f"{zone} {use} 건폐율 용적률 이격 대지 안의 공지 건축 제한",
            jurisdiction=jurisdiction,
            top_k=4,
            scope="ordinance",
        )
    else:
        state["ordinance_evidence"] = []

    # 토지이용계획에서 지구단위계획구역이 실제 확인된 필지는 조례 링크만 보여주지
    # 않고, 수집한 관할 고시문·결정조서·시행지침 원문도 함께 제공한다. 획지/PNU
    # 검증 전 자료는 링크로만 노출하며 계산값을 덮어쓰지는 않는다.
    state["district_plan_evidence"] = district_plan.evidence_for(
        jurisdiction,
        state.get("land_use", {}).get("districts", []),
        address=state.get("location", {}).get("matched_address"),
        pnu=state.get("parcel", {}).get("pnu"),
    )

    # 정형 인허가 규칙이 선택한 단계명·근거만으로 전국 법령 corpus를 검색한다.
    # 조례 검색과 범위를 분리해 다른 지자체 조례나 일반론이 섞이지 않게 한다.
    permit_items = (state.get("permit_requirements") or {}).get("items", [])
    legal_query = " ".join(
        f"{item.get('name', '')} {item.get('basis', '')}"
        for item in permit_items
    ).strip()
    semantic_legal_evidence = (
        ordinance_index.search(
            query=legal_query,
            jurisdiction=jurisdiction,
            top_k=8,
            scope="law",
        )
        if legal_query and ordinance_index.available()
        else []
    )
    state["legal_evidence"] = _merge_permit_legal_evidence(
        permit_items, semantic_legal_evidence
    )
    if isinstance(state.get("legal_sources"), dict):
        state["legal_sources"]["evidence"] = state["legal_evidence"]

    warning = ordinance.separate_ordinance_warning(
        state["location"]["matched_address"], jurisdiction
    )
    state["decision_context"] = {
        "evaluation_mode": (
            "all_supported_uses" if req.get("building_use") == "시설물" else "specified_use"
        ),
        "evaluated_building_use": req.get("building_use"),
        "building_use_inferred": bool(req.get("inferred")),
        "parking_strategy": req.get("parking_strategy", "unspecified"),
        "confidence": "screening_only",
        "assumptions": [
            "시설물은 시스템이 지원하는 모든 건축물 용도를 포괄해 검토"
            if req.get("building_use") == "시설물"
            else f"{req.get('building_use')} 용도 기준으로 검토",
            f"층고 {FLOOR_HEIGHT_M:g}m 개념 가정",
            "지적도 경계 기반 면적으로서 토지대장 공부면적과 차이 가능",
        ],
    }
    timings["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
    state["performance"] = {
        "total_ms": timings["total_ms"],
        "phases_ms": timings,
        "llm_request_extraction_used": not bool(_deterministic_request(query)),
    }

    return {
        **state,
        "verdict": reg["verdict"],
        "summary": _summarize(state),
        "jurisdiction_warning": warning,
        "use_inferred": req.get("inferred", False),
    }


# 오케스트레이터가 진단 결과를 문자열로 모델에 넘길 때 쓰는 축약본.
# 전체 dict 을 그대로 넘기면 경계 폴리곤 좌표 수천 개가 컨텍스트를 잡아먹는다.
def compact(diagnosis: dict) -> str:
    d = {k: v for k, v in diagnosis.items() if k != "parcel"}
    p = diagnosis.get("parcel") or {}
    d["parcel"] = {k: v for k, v in p.items() if k != "geometry"}
    # 걸침 조각의 교차 폴리곤도 좌표 덩어리다 — 모델에게는 비율·면적만 준다
    lu = d.get("land_use")
    if isinstance(lu, dict) and lu.get("zone_shares"):
        d["land_use"] = {
            **lu,
            "zone_shares": [
                {k: v for k, v in s.items() if k != "geometry"}
                for s in lu["zone_shares"]
            ],
        }
    if isinstance(d.get("site_constraints"), dict):
        d["site_constraints"] = {
            k: v for k, v in d["site_constraints"].items()
            if k != "footprint_geometry"
        }
    if isinstance(d.get("road_access"), dict):
        d["road_access"] = {
            k: v for k, v in d["road_access"].items()
            if k != "road_contact_geometry"
        }
    if isinstance(d.get("land_conversion"), dict):
        d["land_conversion"] = {
            k: v for k, v in d["land_conversion"].items()
            if k != "forest_map_overlaps"
        }
        terrain = d["land_conversion"].get("terrain")
        if isinstance(terrain, dict):
            # 지도 렌더링용 DEM 셀 좌표는 최대 2,000개라 LLM 컨텍스트를
            # 잠식한다. 모델에는 통계값만 주고 셀 도형은 map_commands에만 둔다.
            d["land_conversion"]["terrain"] = {
                k: v for k, v in terrain.items() if k != "grid_cells"
            }
    return json.dumps(d, ensure_ascii=False)
