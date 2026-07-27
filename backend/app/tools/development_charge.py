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

# 2025년 시도별 (부과건수, 부과금액[백만원]) — 통계누리 실적에서 파싱.
_2025_BY_SIDO: dict[str, tuple[int, int]] = {
    "서울": (15, 11249), "부산": (47, 2953), "대구": (42, 2148),
    "인천": (142, 7300), "광주": (29, 4429), "대전": (29, 681),
    "울산": (41, 2085), "세종": (41, 1261), "경기": (2589, 253170),
    "강원": (186, 25675), "충북": (214, 5015), "충남": (248, 15613),
    "전북": (43, 1768), "전남": (46, 2436), "경북": (206, 8024),
    "경남": (152, 8232), "제주": (428, 20940),
}
_2025_NATIONAL = (4498, 372979)  # 전국 계

# 특별시·광역시(도시지역 면적 요건 660㎡ 적용 대상).
_METRO = {"서울", "부산", "대구", "인천", "광주", "대전", "울산"}

# 비도시(도시지역 외) 용도지역 — 면적 요건 1,650㎡.
_NON_URBAN_ZONE = ("관리지역", "농림지역", "자연환경보전지역")


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
    is_non_urban = any(z in (zone or "") for z in _NON_URBAN_ZONE)
    if is_non_urban:
        return 1650  # 도시지역 외
    if sido in _METRO:
        return 660   # 특별시·광역시 도시지역
    return 990       # 그 밖의 도시지역


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

    region_avg = _per_case_won(_2025_BY_SIDO.get(sido, _2025_NATIONAL))
    national_avg = _per_case_won(_2025_NATIONAL)

    return {
        "label": "개발부담금",
        "applicable": meets_area,
        "reason": (
            f"지목변경(전용)이 수반되고 개발면적이 요건({req:,}㎡) 이상이라 "
            f"개발부담금 대상 가능성이 있습니다."
            if meets_area
            else f"지목변경(전용)은 수반되나 개발면적이 요건({req:,}㎡)에 미치지 못해 "
                 f"현재 규모로는 개발부담금 대상이 아닐 수 있습니다."
        ),
        "rate_note": "부과율은 개발이익의 25%(개별입지)이며, 택지·산업단지 등 계획입지는 20%입니다.",
        "calculation_formula": (
            "개발이익 = 종료시점지가 - 개시시점지가 - 정상지가상승분 - 인정 개발비용; "
            "개발부담금 = 개발이익 × 부과율"
        ),
        "area_requirement_m2": req,
        "region": sido or "전국",
        "region_avg_per_case_won": region_avg,
        "national_avg_per_case_won": national_avg,
        "legal_basis": "개발이익환수에 관한 법률 제5조·제13조, 같은 법 시행령 제4조",
        "caveat": (
            "정확한 금액은 종료시점 지가 감정평가와 개발비용이 확정돼야 산정됩니다. "
            "위 지역 평균은 필지별 금액이 아니라 참고용 지역 통계일 뿐입니다."
        ),
    }
