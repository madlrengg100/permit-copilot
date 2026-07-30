"""개발부담금(개발이익환수에 관한 법률) 대상 판정·안내.

정확한 금액은 종료시점 지가(감정평가)와 개발비용이 있어야 산정할 수 있어 사전에는
낼 수 없다. 그래서 이 모듈은 금액을 지어내지 않고 다음만 제공한다.
  (1) 대상 여부      — 지목변경(전용) 수반 + 면적 요건 충족 여부
  (2) 부과율·근거    — 개발이익의 25%(개별입지)/20%(계획입지), 개발이익환수법
  (3) 지역 참고치    — 시도별 '건당 평균 부과액' (국토교통부 통계누리 2025년 실적)

지역 참고치는 필지별 금액이 아니라 '그 지역에서 실제로 건당 얼마가 부과됐나'를 보여주는
맥락일 뿐이다. 사업 규모 편차가 커서 시도별로 32배까지 차이가 난다.
출처: 국토교통부 통계누리 '개발부담금 부과 및 징수현황'(2025), docs/ CSV.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "charge_rules.json"


@lru_cache(maxsize=1)
def _rules() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def _per_case_won(counts_amount: tuple[int, int]) -> int:
    cnt, amt_mil = counts_amount
    return round(amt_mil * 1_000_000 / cnt) if cnt else 0


def _sido_of(jurisdiction: str, address: str) -> str:
    """관할·주소 문자열 -> 시도 약칭(서울/경기/충북 …). 못 찾으면 ''."""
    blob = f"{jurisdiction or ''} {address or ''}"
    # 긴 정식명이 약칭보다 먼저 오도록 정렬된 후보.
    table = [
        ("서울", "서울"), ("부산", "부산"), ("대구", "대구"), ("인천", "인천"),
        ("광주", "광주"), ("대전", "대전"), ("울산", "울산"), ("세종", "세종"),
        ("경기", "경기"), ("강원", "강원"),
        ("충청북", "충북"), ("충북", "충북"), ("충청남", "충남"), ("충남", "충남"),
        ("전라북", "전북"), ("전북", "전북"), ("전라남", "전남"), ("전남", "전남"),
        ("경상북", "경북"), ("경북", "경북"), ("경상남", "경남"), ("경남", "경남"),
        ("제주", "제주"),
    ]
    for key, sido in table:
        if key in blob:
            return sido
    return ""


def _area_requirement_m2(sido: str, zone: str) -> int:
    """개발이익환수법 시행령 제4조 면적 요건(지목변경 수반 개발 기준)."""
    rule = _rules()["development_charge"]
    is_non_urban = any(z in (zone or "") for z in rule["non_urban_zone_terms"])
    if is_non_urban:
        return int(rule["non_urban_threshold_m2"])
    if sido in set(rule["metro_regions"]):
        return int(rule["urban_metro_threshold_m2"])
    return int(rule["urban_other_threshold_m2"])


def assess(
    *,
    requires_conversion: bool,
    area_m2: float | None,
    zone: str,
    jurisdiction: str,
    address: str,
) -> dict | None:
    """개발부담금 대상 판정. 대상 가능성이 없으면 None(부담금 섹션에 안 띄움)."""
    if not requires_conversion:
        # 지목변경(전용)이 없으면 대부분 단순 건축 — 개발부담금 대상 아님.
        return None
    if not area_m2:
        return None

    sido = _sido_of(jurisdiction, address)
    req = _area_requirement_m2(sido, zone)
    meets_area = area_m2 >= req

    rules = _rules()
    charge_rule = rules["development_charge"]
    stats = rules["development_charge_statistics"]
    national = tuple(stats["national"])
    region_avg = _per_case_won(tuple(stats["regions"].get(sido, national)))
    national_avg = _per_case_won(national)

    return {
        "label": "개발부담금",
        "applicable": meets_area,
        "reason": (
            f"지목변경(전용)이 수반되고 사업 대상 토지면적({area_m2:,.0f}㎡)이 "
            f"부과대상 토지면적({req:,}㎡ 이상)을 충족해 개발부담금 대상 가능성이 있습니다."
            if meets_area
            else f"지목변경(전용)은 수반되나 사업 대상 토지면적({area_m2:,.0f}㎡)이 "
                 f"부과대상 토지면적({req:,}㎡ 이상)에 미치지 않아 면적 요건을 충족하지 않습니다."
        ),
        "rate_note": (
            f"부과율은 개발이익의 {float(charge_rule['individual_site_rate']) * 100:g}%"
            f"(개별입지)이며, 택지·산업단지 등 계획입지는 "
            f"{float(charge_rule['planned_site_rate']) * 100:g}%입니다."
        ),
        "calculation_formula": (
            "개발이익 = 종료시점지가 - 개시시점지가 - 정상지가상승분 - 인정 개발비용; "
            "개발부담금 = 개발이익 × 부과율"
        ),
        "area_requirement_m2": req,
        "assessed_area_m2": round(float(area_m2), 1),
        "region": sido or "전국",
        "statistics_year": stats["year"],
        "statistics_source": stats["source"],
        "statistics_status": stats["status"],
        "region_avg_per_case_won": region_avg,
        "national_avg_per_case_won": national_avg,
        "legal_basis": "개발이익환수에 관한 법률 제5조·제13조, 같은 법 시행령 제4조",
        "caveat": (
            "정확한 금액은 종료시점 지가 감정평가와 개발비용이 확정돼야 산정됩니다. "
            "위 지역 평균은 필지별 금액이 아니라 참고용 지역 통계일 뿐입니다."
        ),
    }
