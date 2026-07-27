"""수집 완료된 지자체 건축조례의 대지 공지 기준."""

from __future__ import annotations


def lookup(
    jurisdiction: str,
    building_use: str,
    zone: str,
    gross_floor_area_m2: float,
) -> dict:
    if jurisdiction != "충청남도 아산시":
        return {
            "status": "NOT_COLLECTED",
            "front_m": None,
            "adjacent_m": None,
            "source": None,
            "note": f"{jurisdiction or '해당 지자체'} 건축조례 별표의 대지 공지 기준을 아직 수집하지 않았습니다.",
        }

    source = (
        "아산시 건축 조례 제37조·별표 4 "
        "(충청남도아산시조례 제2716호, 시행 2026-02-25)"
    )
    commercial = "상업지역" in zone
    industrial_exempt = zone in {"전용공업지역", "일반공업지역"}
    front = adjacent = 0.0
    status = "APPLIED"
    note = "아산시 별표 4에서 해당하는 대지 공지 기준을 적용했습니다."

    if building_use == "단독주택":
        if "전용주거지역" in zone:
            adjacent = 1.0
        note = "단독주택 중 다가구·다중주택은 창문 면적에 따라 0.5~1m가 별도로 적용될 수 있습니다."
    elif building_use == "공동주택":
        status = "NEEDS_SUBTYPE"
        front = adjacent = None
        note = "아파트·연립·다세대·도시형생활주택별 1.5~6m로 달라 주택 세부유형이 필요합니다."
    elif building_use == "공장" and gross_floor_area_m2 >= 500 and not industrial_exempt:
        if zone == "준공업지역":
            front, adjacent = 1.5, 1.0
        else:
            front = 3.0
            adjacent = 1.5 if gross_floor_area_m2 < 1000 else 3.0
    elif building_use == "창고시설" and gross_floor_area_m2 >= 500 and not industrial_exempt:
        front = 1.5 if zone == "준공업지역" else 3.0
        adjacent = 0.0
    elif building_use == "판매시설" and gross_floor_area_m2 >= 1000:
        front, adjacent = 3.0, (0.0 if commercial else 1.5)
    elif building_use == "숙박시설":
        status = "NEEDS_SUBTYPE"
        front = adjacent = None
        note = "일반숙박시설 여부와 바닥면적에 따라 건축선 2~3m, 인접경계 1~1.5m로 달라 세부유형이 필요합니다."

    return {
        "status": status,
        "front_m": front,
        "adjacent_m": adjacent,
        "source": source,
        "note": note,
    }
