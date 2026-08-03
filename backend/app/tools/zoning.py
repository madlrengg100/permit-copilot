"""용도지역별 규제 룰 엔진.

건폐율·용적률 수치는 이 파일에 하드코딩하지 않는다. data/ordinances.json 에
담긴 시행령 원문값과 지자체 조례값을 tools/ordinance.py 를 통해 읽는다.
(손으로 옮겨 적은 테이블에서 용적률 하한 7건이 틀렸던 전례가 있다.)

이 파일이 담당하는 것은 건축법 시행령 별표1 기준 '용도별 허용 여부' 판정과,
용도지구에 따른 추가 제약 표시다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import ordinance

_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "building_use_rules.json"


@lru_cache(maxsize=1)
def _building_rules() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _use_matrix() -> dict[str, dict[str, list[str]]]:
    return _building_rules().get("uses", {})

# 용도지구/구역 중 별도 심의·제한이 걸리는 것.
# 토지이용계획(getLandUseAttr)의 실제 명칭은 조례·연도에 따라 변형이 많다
# (경관지구→중점경관관리구역, 문화재보호구역→문화유산보호구역 등). 그래서
# 이름을 정확히 일치시키지 않고, 지역지구명에 아래 키워드가 들어가면 매칭한다.
_CONSTRAINT_KEYWORDS: list[tuple[str, str]] = [
    ("지구단위계획", "지구단위계획으로 용도·밀도·높이가 별도 지정되므로 계획 내용 확인 필요"),
    ("경관", "높이·형태·색채 제한. 경관심의 대상 여부 확인 필요"),
    ("고도지구", "최고 높이가 별도 지정되어 용적률 상한을 못 쓸 수 있음"),
    ("방화지구", "건축물 주요 구조부 내화구조 의무"),
    ("개발제한", "원칙적으로 건축 불가. 예외 허가 대상만 가능"),
    ("문화재", "현상변경 허가 및 문화재청(국가유산청) 협의 필요"),
    ("문화유산", "현상변경 허가 및 국가유산청 협의 필요"),
    ("문화유산보호", "현상변경 허가 및 국가유산청 협의 필요"),
    # 지정문화유산 주변 보존지역 — 건설공사 시 현상변경 허가·영향검토가 필요하다
    # (문화재보호법 제13조/국가유산기본법). '문화재'로 안 잡히는 별도 명칭이라 명시.
    ("역사문화환경", "지정문화유산 역사문화환경 보존지역 — 건설공사 시 현상변경 허가 및 국가유산청(지자체) 영향검토 필요"),
    ("학교", "정화구역이면 숙박·유흥시설 등 금지시설 존재 — 교육환경 확인 필요"),
    ("상수원보호", "건축·행위 제한. 상수원보호구역 규제 확인 필요"),
    ("수질보전특별대책", "수질보전 특별대책지역 — 권역별로 오수·폐수 배출과 건축·개발이 제한. 행위제한 권역 확인 및 유역환경청 협의 필요"),
    ("특별대책지역", "환경정책기본법상 특별대책지역 — 오수·폐수 배출과 건축·개발 행위가 제한. 권역별 기준 확인 필요"),
    ("배출시설설치제한", "폐수·오수 배출시설 설치제한지역 — 배출시설 설치·운영이 제한되어 오수처리계획 확인 필요"),
    ("수변구역", "한강수계 등 수변구역 — 오수 배출·건축 행위 제한. 유역환경청 확인 필요"),
    ("군사", "군사기지·시설보호구역이면 국방부(관할 부대) 협의 필요"),
    ("비행안전", "고도 제한. 공항 주변 비행안전구역 확인 필요"),
    ("가축사육제한", "가축분뇨법상 축사 제한 — 축산 시설이면 확인 필요"),
]


# 특정 시설에만 걸리는 제약 — 검토 용도가 아래 부류가 아니면 그 지구는 이 필지의
# 건축 제약이 아니다. 예: 가축사육제한구역은 축사·축산시설에만, 교육환경보호(학교정화)
# 구역의 금지시설 제한은 숙박·유흥 등에만 걸린다. 일반 건축물(농막·단독주택 등)엔 무관.
_FACILITY_SCOPED: dict[str, tuple[str, ...]] = {
    "가축사육제한": (
        "축사", "축산", "가축", "우사", "돈사", "계사", "양계", "양돈",
        "사육", "축분", "퇴비", "젖소", "종축", "부화",
    ),
    "학교": (
        "숙박", "여관", "호텔", "모텔", "유흥", "단란", "위험물", "노래연습장",
        "당구장", "게임", "피시방", "PC", "만화", "담배", "폐기물", "장례",
    ),
}


def _match_constraints(districts: list[str], facility: str = "") -> list[dict]:
    """지역지구명 목록 -> 심의·제한 노트. 같은 노트는 한 번만.

    facility(검토 용도)가 주어지면 시설 특정 제약(_FACILITY_SCOPED)은 그 시설에
    해당할 때만 남긴다 — 축산이 아닌 농막에 가축사육제한구역을 걸지 않기 위함."""
    fac = facility or ""
    out: list[dict] = []
    used_notes: set[str] = set()
    for d in districts:
        for kw, note in _CONSTRAINT_KEYWORDS:
            if kw not in d or note in used_notes:
                continue
            scope = _FACILITY_SCOPED.get(kw)
            if scope and not any(s in fac for s in scope):
                break  # 이 시설엔 해당 없는 지구 — 제약으로 세지 않는다
            out.append({"name": d, "note": note})
            used_notes.add(note)
            break
    return out


def uses_for_zone(zone: str) -> dict:
    """용도지역 하나에서 각 건축물 용도의 허용 상태. USE_MATRIX 를 역산한다.

    "이 필지에 뭘 지을 수 있어?" 류의 열거형 질문에 답하기 위한 것.
    특정 용도 1건만 검토하는 lookup_zoning_rules 로는 다른 용도들이
    되는지 안 되는지를 모델이 알 길이 없다.
    """
    out: dict[str, list[str]] = {"allowed": [], "conditional": [], "not_allowed": []}
    for use, matrix in _use_matrix().items():
        if zone in matrix["allowed"]:
            out["allowed"].append(use)
        elif zone in matrix["conditional"]:
            out["conditional"].append(use)
        else:
            out["not_allowed"].append(use)
    return out


def lookup_zoning_rules(
    zone: str,
    building_use: str,
    districts: list[str] | None = None,
    jurisdiction: str | None = None,
    facility: str = "",
) -> dict:
    """용도지역 + 건축물 용도 -> 허용 여부와 밀도 상한.

    jurisdiction 이 주어지면 해당 지자체 도시계획조례값을 우선 적용하고,
    조례에 규정이 없는 항목만 법정 상한으로 채운다.

    Returns:
        verdict: "allowed" | "conditional" | "not_allowed" | "unknown"
    """
    districts = districts or []

    limits = ordinance.resolve_limits(zone, jurisdiction)
    if not limits["found"]:
        return {
            "verdict": "unknown",
            "zone": zone,
            "reason": limits["reason"],
            "requires_ordinance": limits.get("requires_ordinance", False),
            "jurisdiction": limits.get("jurisdiction"),
            "statutory_limits": limits.get("statutory"),
        }

    bcr_max = limits["bcr_max_pct"]
    far_min = limits["far_min_pct"]
    far_max = limits["far_max_pct"]
    basis = limits["source_label"]

    matrix = _use_matrix().get(building_use)
    if building_use == "시설물":
        overview = uses_for_zone(zone)
        possible_count = len(overview["allowed"]) + len(overview["conditional"])
        total_count = sum(len(items) for items in overview.values())
        verdict = "conditional"
        reason = (
            f"시설물은 지원하는 전체 건축물 용도를 포괄해 검토합니다. {zone}에서는 "
            f"{total_count}개 용도 대분류 중 {possible_count}개가 가능 또는 조건부 범위이며, "
            "용도별 상세 결과는 전체 용도 판정표를 따릅니다."
        )
    elif matrix is None:
        verdict = "unknown"
        reason = f"'{building_use}'은(는) 판정표에 없는 용도입니다. 건축법 시행령 별표1 확인 필요."
    elif zone in matrix["allowed"]:
        verdict = "allowed"
        reason = f"{zone}에서 {building_use}은(는) 건축 가능한 용도입니다."
    elif zone in matrix["conditional"]:
        verdict = "conditional"
        # 근거를 뭉뚱그리지 않는다 — 해당 지자체 도시계획조례가 확인되면 그 조례명·
        # 조문·시행일을 인용하고, 조례를 못 찾았을 때만 일반 '도시계획조례'로 둔다.
        _basis_cite = basis if "조례" in basis else "도시계획조례"
        if zone == "생산관리지역" and building_use == "창고시설":
            reason = (
                "생산관리지역의 창고시설은 농업·임업·축산업·수산업용에 한해 "
                "허용될 수 있으므로 창고의 실제 용도와 세부 기준을 확인해야 합니다."
            )
        else:
            reason = (
                f"{zone}에서 {building_use}은(는) {_basis_cite}가 정하는 "
                "범위에서 조건부 허용됩니다."
            )
    else:
        verdict = "not_allowed"
        reason = f"{zone}에서 {building_use}은(는) 건축할 수 없는 용도입니다."

    constraints = _match_constraints(districts, facility or building_use)
    if constraints and verdict in ("allowed", "conditional"):
        verdict = "conditional"

    # 최종 판정이 조건부면(용도지구 제약으로 조건부가 된 경우 포함) 대안 마련 경로를
    # 안내한다. 조건부는 설계로 요건을 맞추면 허가가 가능한 경우가 많기 때문이다.
    if verdict == "conditional":
        reason = (
            reason.rstrip(".")
            + ". 조건부는 지구단위계획·조례 세부기준과 개별 심의로 가능 여부가 갈리므로, "
            "설계사무소를 통해 설계도면 조정과 필요한 증빙자료 준비로 허가 가능성을 "
            "의뢰받으시기 바랍니다."
        )

    return {
        "verdict": verdict,
        "zone": zone,
        "building_use": building_use,
        "bcr_max_pct": bcr_max,
        "far_min_pct": far_min,
        "far_max_pct": far_max,
        "legal_basis": basis,
        "reason": reason,
        "constraints": constraints,
        # "다른 용도는 뭐가 되나" 류 질문에 답할 수 있도록, 이 용도지역의
        # 전체 용도 허용 현황을 함께 넘긴다 (판정표 9개 대분류 기준).
        "zone_use_overview": uses_for_zone(zone),
        # 조례 적용 여부를 답변에서 밝힐 수 있도록 근거를 함께 넘긴다
        "limit_source": limits["source"],          # "ordinance" | "statutory"
        "jurisdiction": limits.get("jurisdiction"),
        "statutory_limits": limits["statutory"],   # 비교용 법정 상한
        "ordinance_note": limits.get("ordinance_note"),
    }


def apply_straddling_limits(
    regulation: dict,
    zone_shares: list[dict],
    jurisdiction: str | None,
) -> dict:
    """국토계획법 제84조에 따른 걸침 대지의 건폐율·용적률 가중평균."""
    usable = [
        share for share in (zone_shares or [])
        if share.get("zone") and float(share.get("area_m2") or 0) > 0
    ]
    if len(usable) < 2 or min(float(s["area_m2"]) for s in usable) > 330:
        return regulation

    total_area = sum(float(s["area_m2"]) for s in usable)
    if total_area <= 0:
        return regulation

    components = []
    for share in usable:
        limits = ordinance.resolve_limits(share["zone"], jurisdiction)
        if not limits.get("found"):
            regulation["weighted_limits_note"] = (
                f"{share['zone']}의 건폐율·용적률을 확인하지 못해 "
                "걸침 필지 가중평균을 계산하지 못했습니다."
            )
            return regulation
        components.append({
            "zone": share["zone"],
            "area_m2": float(share["area_m2"]),
            "bcr_max_pct": float(limits["bcr_max_pct"]),
            "far_max_pct": float(limits["far_max_pct"]),
        })

    weighted_bcr = sum(
        row["area_m2"] * row["bcr_max_pct"] for row in components
    ) / total_area
    weighted_far = sum(
        row["area_m2"] * row["far_max_pct"] for row in components
    ) / total_area

    regulation["bcr_max_pct"] = round(weighted_bcr, 1)
    regulation["far_max_pct"] = round(weighted_far, 1)
    regulation["weighted_limits"] = {
        "applied": True,
        "total_area_m2": round(total_area, 1),
        "bcr_max_pct": round(weighted_bcr, 1),
        "far_max_pct": round(weighted_far, 1),
        "components": components,
        "legal_basis": "국토의 계획 및 이용에 관한 법률 제84조",
    }
    regulation["legal_basis"] = (
        f"{regulation.get('legal_basis', '').strip()} · "
        "국토계획법 제84조 면적 가중평균"
    ).strip(" ·")
    return regulation
