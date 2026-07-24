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
from typing import Callable

from ..tools import massing, ordinance, vworld, zoning

BUILDING_USES = [
    "단독주택",
    "공동주택",
    "제1종근린생활시설",
    "제2종근린생활시설",
    "업무시설",
    "판매시설",
    "숙박시설",
    "공장",
    "창고시설",
]

EXTRACT_SYSTEM = f"""사용자의 건축 인허가 질의에서 두 가지를 뽑아 도구로 제출한다.

1. address — 조회할 주소. 사용자가 쓴 표현을 최대한 그대로. 시/도가 없으면 붙이지 않는다.
   사용자가 위도·경도를 명시하면 address는 빈 문자열로 두고 lon, lat에 숫자로 제출한다.
2. building_use — 검토할 건축물 용도. 다음 중 하나로 정규화한다:
   {", ".join(BUILDING_USES)}

용도가 명시되지 않았으면 문맥에서 가장 그럴듯한 것을 고르고 inferred=true 로 표시한다.
("상가" -> 제1종근린생활시설, "빌딩"/"사무실" -> 업무시설, "물류창고" -> 창고시설,
 "아파트" -> 공동주택, "펜션"/"호텔" -> 숙박시설)

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
                "inferred": {
                    "type": "boolean",
                    "description": "용도를 질의에서 직접 읽지 않고 추론했으면 true",
                },
                "far_target_pct": {
                    "type": "number",
                    "description": "사용자가 특정 용적률을 지정한 경우에만(예: '용적률 250%로')",
                },
            },
            "required": ["address", "building_use", "inferred"],
        },
    }
]


# 모델이 building_use 를 빼먹었을 때 질의 원문에서 용도를 되찾기 위한 표.
# (Gemini 의 OpenAI 호환 모드는 required 필드를 강제하지 않아, 낮은 확률로
#  address 만 채워서 제출한다 — 그대로 두면 KeyError 로 진단 전체가 죽는다)
_USE_KEYWORDS: list[tuple[str, str]] = [
    ("창고", "창고시설"), ("물류", "창고시설"),
    ("공장", "공장"),
    ("아파트", "공동주택"), ("빌라", "공동주택"),
    ("주택", "단독주택"), ("집", "단독주택"),
    ("사무", "업무시설"), ("빌딩", "업무시설"), ("오피스", "업무시설"),
    ("상가", "제1종근린생활시설"), ("근린생활", "제1종근린생활시설"),
    ("마트", "판매시설"), ("판매", "판매시설"),
    ("호텔", "숙박시설"), ("펜션", "숙박시설"), ("모텔", "숙박시설"),
]


def _guess_use(query: str) -> str:
    for name in BUILDING_USES:          # 정식 용도명이 그대로 들어 있으면 그것
        if name in query:
            return name
    for kw, name in _USE_KEYWORDS:      # 아니면 일상어 키워드로
        if kw in query:
            return name
    return "업무시설"


async def extract_request(client, query: str) -> dict:
    """자연어 질의 -> {address, building_use, inferred, far_target_pct?}"""
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
            req.setdefault("inferred", True)
            return req

    # 도구를 안 부른 경우 — 주소를 못 찾은 것으로 본다
    return {"address": "", "building_use": _guess_use(query), "inferred": True}


def _summarize(state: dict) -> str:
    """판정 결과를 사람이 읽을 요약으로. LLM 없이 값에서 조립한다."""
    reg = state["regulation"]
    parcel = state["parcel"]
    mass = state.get("massing")
    jm = state.get("jimok_info", {})

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

    if jm.get("requires_conversion"):
        lines.append(f"지목이 '{jm['jimok']}'({jm['label']})이라 {jm['procedure']}가 선행됩니다.")

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
                "가장 작은 부분이 330㎡ 이하이므로 국토계획법 제84조에 따라 "
                "건폐율·용적률은 면적 가중평균이 적용될 수 있습니다."
                if smallest <= 330
                else "국토계획법 제84조에 따라 각 부분별로 규제가 적용될 수 있습니다."
            )
        )

    lines.append(
        "이 수치는 밀도 규제만 반영한 이론값입니다. 일조권 사선제한, 정북방향 이격, "
        "대지 안의 공지, 주차대수 산정으로 실제 규모는 더 줄어듭니다."
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

    def step(name: str, payload: dict) -> None:
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

    step("get_parcel", {"lon": lon, "lat": lat})
    state["parcel"] = await vworld.get_parcel(lon, lat)
    if has_coordinates and state["parcel"].get("jibun"):
        state["location"]["matched_address"] = state["parcel"]["jibun"]

    step("get_land_use", {"lon": lon, "lat": lat})
    state["land_use"] = await vworld.get_land_use(lon, lat)

    # 필지 폴리곤으로 용도지역 걸침을 확인한다. 점 조회는 필지가 경계에
    # 걸쳐 있으면 점 위치에 따라 답이 달라진다 (국토계획법 제84조 케이스).
    step("check_zone_overlap", {"pnu": state["parcel"].get("pnu", "")})
    zone_shares = await vworld.get_zone_shares(state["parcel"].get("geometry"))
    if zone_shares:
        state["land_use"]["zone_shares"] = zone_shares
        state["land_use"]["straddling"] = len(zone_shares) >= 2
        # 판정 기준 지역은 점이 아니라 최대 면적 부분으로 잡는다
        state["land_use"]["zones"] = list(
            dict.fromkeys(
                [s["zone"] for s in zone_shares] + state["land_use"]["zones"]
            )
        )

    from ..tools import jimok as jimok_tool

    state["jimok_info"] = jimok_tool.classify(state["parcel"].get("jimok"))

    zone = state["land_use"]["zones"][0]
    jurisdiction = ordinance.detect_jurisdiction(state["location"]["matched_address"])

    step("lookup_zoning", {"zone": zone, "building_use": req["building_use"]})
    reg = zoning.lookup_zoning_rules(
        zone=zone,
        building_use=req["building_use"],
        districts=state["land_use"]["districts"],
        jurisdiction=jurisdiction,
    )
    state["regulation"] = reg

    # 불허면 매스를 만들지 않는다 — 지을 수 없는 건물을 그려 보여주지 않기 위해
    if reg["verdict"] != "not_allowed" and reg.get("bcr_max_pct"):
        step("calc_massing", {"area_m2": state["parcel"]["area_m2"]})
        state["massing"] = massing.calc_massing(
            area_m2=state["parcel"]["area_m2"],
            bcr_max_pct=reg["bcr_max_pct"],
            far_max_pct=reg["far_max_pct"],
            far_target_pct=req.get("far_target_pct"),
        )

    warning = ordinance.separate_ordinance_warning(
        state["location"]["matched_address"], jurisdiction
    )

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
    d = {k: v for k, v in diagnosis.items() if k not in ("parcel", "request")}
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
    return json.dumps(d, ensure_ascii=False)
