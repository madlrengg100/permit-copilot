"""연속지적도 도로 필지 기반 접도 사전검토.

지목이 '도로'인 인접 필지를 찾는 1차 검사다. 건축법상 도로 지정 여부와
현황 폭원은 건축행정시스템·도로대장 또는 현장측량으로 별도 확인해야 한다.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from . import vworld

Fetcher = Callable[[float, float, float, float], Awaitable[list[dict]]]
TO_METERS = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def _metric(geometry: dict):
    return transform(TO_METERS.transform, shape(geometry).buffer(0))


def _estimated_width_m(road) -> float | None:
    """도로 지적 필지 최소회전사각형의 짧은 변(참고치)."""
    if road.is_empty:
        return None
    coords = list(road.minimum_rotated_rectangle.exterior.coords)
    lengths = [
        ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        for (x1, y1), (x2, y2) in zip(coords, coords[1:])
    ]
    positive = [value for value in lengths if value > 0.05]
    return round(min(positive), 1) if positive else None


async def assess(
    parcel_geometry: dict,
    pnu: str = "",
    fetch: Fetcher = vworld.get_parcel_features_bbox,
) -> dict:
    parcel = _metric(parcel_geometry)
    # 약 3m 외곽 후보를 받는다. 실제 접촉 판정 허용오차는 0.35m로 제한한다.
    minx, miny, maxx, maxy = shape(parcel_geometry).bounds
    pad = 0.00004
    try:
        candidates = await fetch(minx - pad, miny - pad, maxx + pad, maxy + pad)
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "label": "접도 확인 불가",
            "message": f"주변 지적도 조회 실패: {type(exc).__name__}",
            "roads": [],
            "unknowns": ["건축법상 도로 지정 여부", "도로 현황 폭원"],
        }

    roads = []
    road_geometries = []
    for item in candidates:
        if item.get("pnu") == pnu or item.get("jimok") not in {"도", "도로"}:
            continue
        road = _metric(item["geometry"])
        distance = parcel.distance(road)
        if distance > 0.35:
            continue
        contact = parcel.boundary.intersection(road.buffer(0.35)).length
        road_geometries.append(road)
        roads.append({
            "pnu": item.get("pnu", ""),
            "address": item.get("address", ""),
            "contact_length_m": round(contact, 1),
            "cadastral_width_estimate_m": _estimated_width_m(road),
        })

    roads.sort(key=lambda item: -item["contact_length_m"])
    if not roads:
        return {
            "status": "NO_CADASTRAL_ROAD",
            "label": "지적도상 접도 미확인",
            "message": (
                "선택 필지와 맞닿은 지목 '도로' 필지를 찾지 못했습니다. "
                "맹지 여부와 건축법상 도로 지정·통행권을 별도 확인해야 합니다."
            ),
            "roads": [],
            "unknowns": ["건축법상 도로 지정 여부", "현황도로·통행권"],
            "legal_basis": "건축법 제2조제1항제11호, 제44조",
        }

    widest = max(
        (road["cadastral_width_estimate_m"] or 0 for road in roads), default=0
    )
    road_union = unary_union(road_geometries)
    road_contact = parcel.boundary.intersection(road_union.buffer(0.35))
    inverse = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
    setback = (
        "지적 필지 폭 참고치가 4m 미만입니다. 도로 중심선에서 2m 후퇴 대상인지 현황측량이 필요합니다."
        if widest and widest < 4
        else "4m 미만 도로 여부와 도로 중심선 후퇴는 도로대장·현황측량으로 확정해야 합니다."
    )
    return {
        "status": "CADASTRAL_CONTACT",
        "label": "지적도상 도로 접함",
        "message": f"지목 '도로' 필지 {len(roads)}개와 접합니다. {setback}",
        "roads": roads,
        "road_contact_geometry": (
            transform(inverse.transform, road_contact).__geo_interface__
            if not road_contact.is_empty else None
        ),
        "unknowns": ["건축법상 도로 지정 여부", "유효 도로폭", "도로 중심선 후퇴선"],
        "legal_basis": "건축법 제2조제1항제11호, 제44조",
        "caveat": "지적도 기반 참고 판정이며 접도 길이 2m 충족과 실제 도로폭을 확정하지 않습니다.",
    }
