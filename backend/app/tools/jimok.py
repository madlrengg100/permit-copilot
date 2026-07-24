"""지목 기반 전용허가 필요성 판정.

비도시지역에서는 용도지역만큼 지목이 중요하다. 지목이 '대'가 아니면 건축 전에
농지전용(농지법) 또는 산지전용(산지관리법) 절차가 선행되어야 하고, 이게 실제로
사업 기간과 비용을 좌우한다.

여기서 하는 것은 **절차 필요성 플래그**까지다. 전용 가능 여부 자체는 농업진흥지역
지정, 보전산지 구분, 경사도 같은 공간 조건에 달려 있고 그건 별도 레이어 조회가
필요하다. 여기서 "가능/불가"를 단정하지 않는다.

지목 분류는 공간정보관리법 시행령 제58조의 28개 지목 기준이다.
"""

from __future__ import annotations

# VWorld 연속지적도는 지목을 한 글자 코드로 준다(공간정보관리법 시행령 제58조).
# '전'처럼 코드와 이름이 같은 것도 있고 '임'(임야), '과'(과수원)처럼 다른 것도 있다.
JIMOK_CODE = {
    "전": "전", "답": "답", "과": "과수원", "목": "목장용지", "임": "임야",
    "광": "광천지", "염": "염전", "대": "대", "장": "공장용지", "학": "학교용지",
    "차": "주차장", "주": "주유소용지", "창": "창고용지", "도": "도로",
    "철": "철도용지", "제": "제방", "천": "하천", "구": "구거", "유": "유지",
    "양": "양어장", "수": "수도용지", "공": "공원", "체": "체육용지",
    "원": "유원지", "종": "종교용지", "사": "사적지", "묘": "묘지", "잡": "잡종지",
}


def normalize(code_or_name: str) -> str:
    """'유' -> '유지', '임야' -> '임야'. 이미 전체 이름이면 그대로."""
    v = (code_or_name or "").strip()
    if not v:
        return ""
    if v in JIMOK_CODE.values():
        return v
    return JIMOK_CODE.get(v, v)


# 농지법 제2조제1호 — 전·답·과수원은 농지
FARMLAND = {"전", "답", "과수원"}

# 산지관리법 제2조 — 임야가 산지의 지목
FOREST = {"임야"}

# 이미 건축이 예정된 지목
BUILDABLE = {"대", "공장용지", "창고용지", "학교용지", "주차장", "주유소용지", "종교용지"}

_CATEGORY = {
    "farmland": {
        "label": "농지",
        "law": "농지법",
        "procedure": "농지전용허가(또는 협의)",
        "note": (
            "건축 전 농지전용 절차가 선행됩니다. 농업진흥지역(농업진흥구역/보호구역)에 "
            "해당하면 전용이 크게 제한되므로 농업진흥지역 지정 여부를 먼저 확인해야 합니다. "
            "농지보전부담금이 발생합니다."
        ),
    },
    "forest": {
        "label": "산지",
        "law": "산지관리법",
        "procedure": "산지전용허가",
        "note": (
            "건축 전 산지전용 절차가 선행됩니다. 보전산지(임업용/공익용)면 전용이 크게 "
            "제한되므로 보전산지 여부를 먼저 확인해야 합니다. 경사도·표고·입목축적이 "
            "지자체 개발행위허가 기준에 걸리는지도 함께 봐야 합니다. "
            "대체산림자원조성비가 발생합니다."
        ),
    },
    "buildable": {
        "label": "건축 가능 지목",
        "law": None,
        "procedure": None,
        "note": "지목상 전용 절차는 불필요합니다.",
    },
    "other": {
        "label": "기타 지목",
        "law": None,
        "procedure": "지목변경 필요 여부 확인",
        "note": (
            "건축 가능 지목이 아닙니다. 해당 지목의 개별 법령과 지목변경 요건을 "
            "직접 확인해야 합니다."
        ),
    },
}


def classify(jimok: str | None) -> dict:
    """지목 -> 전용허가 절차 필요성.

    반환값의 requires_conversion 은 '절차가 필요한가'이지 '전용이 가능한가'가 아니다.
    가능 여부는 농업진흥지역·보전산지 등 공간 조건을 봐야 알 수 있다.
    """
    # 모든 분기가 같은 키 집합을 돌려줘야 한다 — 소비하는 쪽에서 분기별로
    # 키 존재를 확인하게 만들면 결국 KeyError 로 터진다.
    if not jimok:
        return {
            "jimok": None,
            "category": "unknown",
            "label": "지목 미확인",
            "requires_conversion": None,
            "law": None,
            "procedure": None,
            "note": "지목을 확인하지 못했습니다. 토지대장으로 확인이 필요합니다.",
            "check_layers": [],
        }

    j = normalize(jimok)

    if j in FARMLAND:
        cat = "farmland"
        layers = ["농업진흥지역"]
    elif j in FOREST:
        cat = "forest"
        layers = ["보전산지구분", "경사도(DEM)"]
    elif j in BUILDABLE:
        cat = "buildable"
        layers = []
    else:
        cat = "other"
        layers = []

    info = _CATEGORY[cat]
    return {
        "jimok": j,
        "category": cat,
        "label": info["label"],
        "requires_conversion": cat in ("farmland", "forest"),
        "law": info["law"],
        "procedure": info["procedure"],
        "note": info["note"],
        # 판정을 확정하려면 추가로 조회해야 하는 공간 레이어
        "check_layers": layers,
    }
