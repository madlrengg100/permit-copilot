"""농지·산지 전용 관련 부담금의 참고 추정."""

from __future__ import annotations


def estimate(
    *,
    jimok_category: str,
    conversion: dict,
    conversion_area_m2: float | None,
    official_land_price_won_m2: float | None,
) -> dict | None:
    area = float(conversion_area_m2 or 0)
    price = float(official_land_price_won_m2 or 0)
    if area <= 0 or price <= 0:
        return None

    if jimok_category == "farmland":
        in_promotion = conversion.get("agriculture", {}).get("status") == "OVERLAP"
        rate = 0.30 if in_promotion else 0.20
        unit = min(price * rate, 50_000)
        return {
            "type": "farmland_conservation",
            "label": "농지보전부담금 참고액",
            "estimated_won": round(area * unit),
            "area_m2": round(area, 1),
            "unit_won_m2": round(unit),
            "formula": (
                f"전용예상면적 × 개별공시지가 × {int(rate * 100)}%"
                " (㎡당 최대 50,000원)"
            ),
            "legal_basis": "농지법 제38조, 농지법 시행령 제53조",
            "caveat": (
                "건축면적을 전용예상면적으로 가정한 참고액입니다. 실제 전용허가면적과 "
                "농지법 시행령 별표 2 감면 여부에 따라 달라집니다."
            ),
        }

    if jimok_category == "forest":
        overlap = (conversion.get("forest", {}).get("overlaps") or [{}])[0]
        code = overlap.get("code", "")
        # 단위면적당 금액(가목) — 보전산지/준보전산지. 현재 데이터로 전용제한지역은
        # 별도 식별하지 못하므로 두 구분만 쓴다.
        base = 10_840 if code in {"UFM100", "UFM110", "UFM120"} else 8_340
        # 공시지가 반영액(나목) = 개별공시지가의 1000분의 1(=0.1%).
        # 다만 그 금액이 가목(단위면적당 금액)을 초과하면 가목 금액으로 제한한다.
        land_component = min(price * 0.001, base)
        unit = base + land_component
        return {
            "type": "forest_replacement",
            "label": "대체산림자원조성비 참고액",
            "estimated_won": round(area * unit),
            "area_m2": round(area, 1),
            "unit_won_m2": round(unit),
            "base_won_m2": base,
            "land_component_won_m2": round(land_component),
            "formula": (
                "전용예상면적 × (단위면적당 금액 + 개별공시지가 0.1%, "
                "공시지가 반영액 상한=단위면적당 금액)"
            ),
            "legal_basis": "산지관리법 제19조, 같은 법 시행령 제24조",
            "caveat": (
                "건축면적을 전용예상면적으로 가정한 참고액입니다. 전용제한지역 여부, "
                "실제 허가면적, 연도별 산림청 고시 단가와 산지관리법 시행령 별표 5 "
                "감면 여부에 따라 달라집니다."
            ),
        }
    return None
