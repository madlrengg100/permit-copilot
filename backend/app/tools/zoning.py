"""용도지역별 규제 룰 엔진.

건폐율·용적률 수치는 이 파일에 하드코딩하지 않는다. data/ordinances.json 에
담긴 시행령 원문값과 지자체 조례값을 tools/ordinance.py 를 통해 읽는다.
(손으로 옮겨 적은 테이블에서 용적률 하한 7건이 틀렸던 전례가 있다.)

이 파일이 담당하는 것은 건축법 시행령 별표1 기준 '용도별 허용 여부' 판정과,
용도지구에 따른 추가 제약 표시다.
"""

from __future__ import annotations

from . import ordinance

# 건축물 용도 대분류 -> 허용되는 용도지역 (간이 판정표)
#   allowed  : 조례 없이 원칙적으로 허용
#   permitted: 도시계획조례가 정하는 바에 따라 허용 (조건부)
USE_MATRIX: dict[str, dict[str, list[str]]] = {
    "단독주택": {
        "allowed": [
            "제1종전용주거지역", "제2종전용주거지역", "제1종일반주거지역",
            "제2종일반주거지역", "제3종일반주거지역", "준주거지역",
        ],
        "permitted": [
            "근린상업지역", "일반상업지역", "자연녹지지역", "계획관리지역",
            "생산관리지역", "보전관리지역",
        ],
    },
    "공동주택": {
        "allowed": [
            "제2종전용주거지역", "제2종일반주거지역", "제3종일반주거지역", "준주거지역",
        ],
        "permitted": ["제1종일반주거지역", "근린상업지역", "일반상업지역", "준공업지역"],
    },
    "제1종근린생활시설": {
        "allowed": [
            "제1종전용주거지역", "제2종전용주거지역", "제1종일반주거지역",
            "제2종일반주거지역", "제3종일반주거지역", "준주거지역",
            "중심상업지역", "일반상업지역", "근린상업지역", "유통상업지역",
            "전용공업지역", "일반공업지역", "준공업지역",
        ],
        "permitted": ["자연녹지지역", "생산녹지지역", "계획관리지역"],
    },
    "제2종근린생활시설": {
        "allowed": [
            "준주거지역", "중심상업지역", "일반상업지역", "근린상업지역",
            "유통상업지역", "일반공업지역", "준공업지역",
        ],
        "permitted": [
            "제1종일반주거지역", "제2종일반주거지역", "제3종일반주거지역",
            "자연녹지지역", "계획관리지역",
        ],
    },
    "업무시설": {
        "allowed": ["준주거지역", "중심상업지역", "일반상업지역", "근린상업지역", "준공업지역"],
        "permitted": [
            "제2종일반주거지역", "제3종일반주거지역",
            "유통상업지역", "일반공업지역",
        ],
    },
    "판매시설": {
        "allowed": ["중심상업지역", "일반상업지역", "유통상업지역", "근린상업지역"],
        "permitted": ["준주거지역", "준공업지역"],
    },
    "숙박시설": {
        "allowed": ["중심상업지역", "일반상업지역", "유통상업지역"],
        "permitted": ["근린상업지역", "계획관리지역", "자연녹지지역"],
    },
    "공장": {
        "allowed": ["전용공업지역", "일반공업지역", "준공업지역"],
        "permitted": ["계획관리지역", "생산녹지지역", "자연녹지지역"],
    },
    "창고시설": {
        "allowed": ["유통상업지역", "전용공업지역", "일반공업지역", "준공업지역"],
        "permitted": ["계획관리지역", "생산녹지지역", "자연녹지지역", "농림지역"],
    },
    "교육연구시설": {
        # 국토계획법 시행령 별표(용도지역 안에서 건축할 수 있는 건축물) 기준.
        # 학교(초·중·고 등)를 대표로 한 단순화이며, 세부 유형(학원·연구소·직업
        # 훈련소)에 따라 달라질 수 있어 상세는 관할 조례·별표로 확인해야 한다.
        "allowed": [
            "제1종일반주거지역", "제2종일반주거지역", "제3종일반주거지역",
            "준주거지역", "중심상업지역", "일반상업지역", "근린상업지역",
        ],
        "permitted": [
            "제1종전용주거지역", "제2종전용주거지역", "준공업지역", "일반공업지역",
            "자연녹지지역", "생산녹지지역", "보전녹지지역", "계획관리지역",
            "생산관리지역", "보전관리지역", "농림지역",
        ],
        # 전용공업지역·유통상업지역·자연환경보전지역 등은 목록에 없으므로 불가.
    },
}

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
    ("학교", "정화구역이면 숙박·유흥시설 등 금지시설 존재 — 교육환경 확인 필요"),
    ("상수원보호", "건축·행위 제한. 상수원보호구역 규제 확인 필요"),
    ("군사", "군사기지·시설보호구역이면 국방부(관할 부대) 협의 필요"),
    ("비행안전", "고도 제한. 공항 주변 비행안전구역 확인 필요"),
    ("가축사육제한", "가축분뇨법상 축사 제한 — 축산 시설이면 확인 필요"),
]


def _match_constraints(districts: list[str]) -> list[dict]:
    """지역지구명 목록 -> 심의·제한 노트. 같은 노트는 한 번만."""
    out: list[dict] = []
    used_notes: set[str] = set()
    for d in districts:
        for kw, note in _CONSTRAINT_KEYWORDS:
            if kw in d and note not in used_notes:
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
    for use, matrix in USE_MATRIX.items():
        if zone in matrix["allowed"]:
            out["allowed"].append(use)
        elif zone in matrix["permitted"]:
            out["conditional"].append(use)
        else:
            out["not_allowed"].append(use)
    return out


def lookup_zoning_rules(
    zone: str,
    building_use: str,
    districts: list[str] | None = None,
    jurisdiction: str | None = None,
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

    matrix = USE_MATRIX.get(building_use)
    if matrix is None:
        verdict = "unknown"
        reason = f"'{building_use}'은(는) 판정표에 없는 용도입니다. 건축법 시행령 별표1 확인 필요."
    elif zone in matrix["allowed"]:
        verdict = "allowed"
        reason = f"{zone}에서 {building_use}은(는) 원칙적으로 건축 가능합니다."
    elif zone in matrix["permitted"]:
        verdict = "conditional"
        # 근거를 뭉뚱그리지 않는다 — 해당 지자체 도시계획조례가 확인되면 그 조례명·
        # 조문·시행일을 인용하고, 조례를 못 찾았을 때만 일반 '도시계획조례'로 둔다.
        _basis_cite = basis if "조례" in basis else "도시계획조례"
        reason = f"{zone}에서 {building_use}은(는) {_basis_cite}가 정하는 범위에서 조건부 허용됩니다."
    else:
        verdict = "not_allowed"
        reason = f"{zone}에서 {building_use}은(는) 건축할 수 없는 용도입니다."

    constraints = _match_constraints(districts)
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
