"""연속지적도 도로 필지 기반 접도 사전검토.

지목이 '도로'인 인접 필지를 찾는 1차 검사다. 건축법상 도로 지정 여부와
현황 폭원은 건축행정시스템·도로대장 또는 현장측량으로 별도 확인해야 한다.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

from pyproj import Transformer
from shapely.geometry import LineString, shape
from shapely.ops import nearest_points, transform, unary_union

from . import building_register, land_ownership, vworld

logger = logging.getLogger(__name__)

Fetcher = Callable[[float, float, float, float], Awaitable[list[dict]]]
TO_METERS = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
_SOURCES_PATH = Path(__file__).resolve().parent.parent / "data" / "road_data_sources.json"


@lru_cache(maxsize=1)
def _verification_sources() -> dict:
    with _SOURCES_PATH.open(encoding="utf-8") as file:
        data = json.load(file)
    return {
        "collected_on": data["_meta"]["collected_on"],
        "final_legal_determination": data["_meta"]["final_legal_determination"],
        "sources": [
            {
                "id": source["id"],
                "name": source["name"],
                "status": source["status"],
            }
            for source in data["sources"]
        ],
    }


def _metric(geometry: dict):
    return transform(TO_METERS.transform, shape(geometry).buffer(0))


def _estimated_width_m(road) -> float | None:
    """도로 지적필지 최소회전사각형의 짧은 변(내부 후퇴폭 판정용 참고치).

    곧은 도로는 실제 폭에 가깝지만 굽거나 가지친 도로 필지는 의미가 약하다. 그래서
    화면 표기에는 쓰지 않고, 4m 미만 여부 등 내부 참고 판정에만 쓴다.
    """
    if road.is_empty:
        return None
    coords = list(road.minimum_rotated_rectangle.exterior.coords)
    lengths = [
        ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        for (x1, y1), (x2, y2) in zip(coords, coords[1:])
    ]
    positive = [value for value in lengths if value > 0.05]
    return round(min(positive), 1) if positive else None


# 우수·오수를 방류할 수 있는 공공 배수처의 지목(단자·정식명 둘 다).
# 배수로가 사유지(전·답·대 등)를 지나면 토지사용승낙 없이는 불가하므로, 인접에
# 이 지목이 있는지가 방류처 확보의 1차 근거가 된다.
_DRAINAGE_OUTLET_JIMOK = {
    "구": "구거", "구거": "구거",
    "천": "하천", "하천": "하천",
}


def _drainage(roads: list, adjacent_nonroads: list) -> dict:
    """인접 지목으로 우수·오수 방류처(도로측구·구거·하천) 확보 여부를 사전검토한다.
    지적도 인접 판정일 뿐, 실제 방류 가능·경사·관경은 현황측량·토목설계로 확정한다.
    """
    outlets: list[str] = []
    if roads:
        outlets.append("도로(도로측구)")
    seen = set()
    for item in adjacent_nonroads:
        name = _DRAINAGE_OUTLET_JIMOK.get(item.get("jimok"))
        if name and name not in seen:
            seen.add(name)
            outlets.append(name)
    if outlets:
        note = (
            f"인접에 공공 배수처({' · '.join(outlets)})가 있어 우수·오수 방류가 "
            "비교적 유리합니다. 실제 방류 가능 여부·경사·관경은 현황측량으로 확인합니다."
        )
    else:
        note = (
            "인접 필지에 지목 '도로·구거·하천' 같은 공공 배수처가 확인되지 않았습니다. "
            "우수·오수 배수로가 사유지를 지나야 하면 토지사용승낙이 필요하거나 공유지로 "
            "우회해야 하며, 방류처 확보는 개발행위허가 심사 대상입니다(현황측량·토목설계 확인)."
        )
    return {"public_outlet": bool(outlets), "outlets": outlets, "note": note}


# 배수로가 '지나갈 수 있는' 공공 지목(도로·구거·하천). 나머지(전·답·대 등)는
# 사유지라 통과하려면 토지사용승낙이 필요하다.
_PUBLIC_DRAIN_JIMOK = {"도", "도로", "구", "구거", "천", "하천"}
# 방류 목적지 지목을 사람이 읽는 이름으로(라벨·안내용).
_OUTLET_DISPLAY = {
    "도": "도로", "도로": "도로", "구": "구거", "구거": "구거", "천": "하천", "하천": "하천",
}
# 인접에 공공 배수처가 없을 때, 가장 가까운 공공 배수처를 찾으려 넓히는 조회 반경(도 단위, 약 180m).
_WIDE_DRAIN_PAD = 0.0018


def _encroachment_route(parcel, candidates: list, target_pnu: str, inverse) -> dict | None:
    """인접에 공공 배수처가 없을 때, 넓게 조회한 필지들에서 가장 가까운 공공 배수처
    (도로·구거·하천)까지의 '개념' 경로를 만들고, 그 직선이 지나는 사유지 필지를 검출한다.
    사유지를 지나면 토지사용승낙 없이 배수로를 낼 수 없으므로 '침범' 경고용으로 쓴다.
    실제 경로가 아니라 방류 방향·통과 사유지를 짚는 개념선이며 현황측량으로 확정한다.
    """
    outlets = []  # (metric geom, 표시지목, address)
    others = []  # (metric geom, jimok, address, pnu)
    for item in candidates:
        if item.get("pnu") == target_pnu:
            continue
        try:
            geom = _metric(item["geometry"])
        except Exception:
            logger.debug("배수 후보 필지 기하 변환 실패(스킵)", exc_info=True)
            continue
        jm = item.get("jimok")
        if jm in _PUBLIC_DRAIN_JIMOK:
            outlets.append((geom, _OUTLET_DISPLAY.get(jm, jm), item.get("address", "")))
        else:
            others.append(
                (geom, jm or "미상", item.get("address", ""), item.get("pnu", ""))
            )
    if not outlets:
        return None
    try:
        start = parcel.representative_point()
        _, discharge = nearest_points(start, unary_union([o[0] for o in outlets]))
        route = LineString([(start.x, start.y), (discharge.x, discharge.y)])
    except Exception:
        logger.debug("배수 경로(공공배수처 방향) 계산 실패", exc_info=True)
        return None
    if route.length < 0.5:  # 사실상 공공 배수처에 붙어 있으면 침범이 아니다
        return None
    # 방류 목적지 — 끝점이 닿는 공공 배수처(가장 가까운 것)
    _og, outlet_jimok, outlet_addr = min(outlets, key=lambda o: o[0].distance(discharge))
    crossed = []
    for geom, jimok, address, pnu in others:
        if route.intersects(geom):
            seg = route.intersection(geom)
            crossed.append({
                "jimok": jimok,
                "address": address,
                "pnu": pnu,
                "cross_length_m": round(getattr(seg, "length", 0.0), 1),
            })
    crossed.sort(key=lambda item: -item["cross_length_m"])
    return {
        "route_geometry": transform(inverse.transform, route).__geo_interface__,
        "length_m": round(route.length, 1),
        "crosses_private": bool(crossed),
        "crossed_parcels": crossed,
        "outlet": {"jimok": outlet_jimok, "address": outlet_addr},
    }


def _drainage_route(parcel, outlet_geometries: list, inverse) -> dict | None:
    """필지 안쪽에서 가장 가까운 공공 배수처(도로측구·구거·하천)까지의 '개념' 배수로
    경로를 WGS84 LineString 으로 만든다. 확정 경로가 아니라 방류 방향을 짚는 용도이며,
    실제 경로·경사·방류 지점은 현황측량·토목설계로 확정한다.
    """
    if not outlet_geometries:
        return None
    try:
        union = unary_union(outlet_geometries)
        start = parcel.representative_point()  # 오목한 필지에서도 내부에 떨어진다
        _, discharge = nearest_points(start, union)  # 가장 가까운 방류 지점
        route = LineString([(start.x, start.y), (discharge.x, discharge.y)])
        if route.length < 0.5:  # 이미 배수처에 붙어 있으면 선이 의미 없다
            return None
        return transform(inverse.transform, route).__geo_interface__
    except Exception:
        logger.debug("최근접 배수처 경로 기하 계산 실패", exc_info=True)
        return None


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
            "verification_sources": _verification_sources(),
        }

    roads = []
    road_geometries = []
    drainage_geometries = []  # 인접 구거·하천(방류처) 형상 — 가상 배수로 경로 산출용
    adjacent_nonroads = []
    _inverse = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
    for item in candidates:
        if item.get("pnu") == pnu:
            continue
        candidate = _metric(item["geometry"])
        distance = parcel.distance(candidate)
        if distance > 0.35:
            continue
        contact = parcel.boundary.intersection(candidate.buffer(0.35)).length
        if item.get("jimok") not in {"도", "도로"}:
            adjacent_nonroads.append({
                "pnu": item.get("pnu", ""),
                "address": item.get("address", ""),
                "jimok": item.get("jimok") or "미상",
                "contact_length_m": round(contact, 1),
            })
            if item.get("jimok") in _DRAINAGE_OUTLET_JIMOK:  # 구거·하천
                drainage_geometries.append(candidate)
            continue
        road = candidate
        road_geometries.append(road)
        roads.append({
            "pnu": item.get("pnu", ""),
            "address": item.get("address", ""),
            "contact_length_m": round(contact, 1),
            "cadastral_width_estimate_m": _estimated_width_m(road),  # 내부 후퇴폭 참고
        })

    roads.sort(key=lambda item: -item["contact_length_m"])
    adjacent_nonroads.sort(key=lambda item: -item["contact_length_m"])

    # 배수 사전검토 + 가장 가까운 방류처까지 '개념' 배수로 경로(있으면). 두 반환에서 공용.
    drainage_info = _drainage(roads, adjacent_nonroads)
    _route = _drainage_route(parcel, road_geometries + drainage_geometries, _inverse)
    if _route:
        drainage_info["route_geometry"] = _route
    elif not drainage_info["public_outlet"]:
        # 인접에 공공 배수처가 없다 → 넓게 조회해 가장 가까운 도로·구거·하천까지의
        # '개념' 경로를 만들고, 그 직선이 지나는 사유지를 검출한다(침범 사전검토).
        try:
            wider = await fetch(
                minx - _WIDE_DRAIN_PAD, miny - _WIDE_DRAIN_PAD,
                maxx + _WIDE_DRAIN_PAD, maxy + _WIDE_DRAIN_PAD,
            )
            enc = _encroachment_route(parcel, wider, pnu, _inverse)
        except Exception:
            # 조회 실패는 정상 폴백이지만, 코드 버그(NameError 등)까지 조용히 삼키지
            # 않도록 로그를 남긴다(침범 경로가 사라진 원인을 추적 가능하게).
            logger.warning("침범 배수 경로 계산 실패", exc_info=True)
            enc = None
        if enc:
            # 통과 필지(길게 지나는 것부터 최대 3필지)의 실제 소유구분을 확인한다. 사유지면
            # 승낙 없이 통과 불가, 국·공유지면 통과 가능이라 침범이 아니다. 소유구분이 1차 근거,
            # 건물 유무(건축물대장)는 '사실상 우회' 강조용 보조 정보다.
            for parcel_hit in enc.get("crossed_parcels", [])[:3]:
                hit_pnu = parcel_hit.get("pnu")
                if not hit_pnu:
                    continue
                try:
                    own = await land_ownership.lookup_ownership(hit_pnu)
                    if own.get("ownership"):
                        parcel_hit["ownership"] = own["ownership"]  # "사유"/"국공유"
                        parcel_hit["ownership_detail"] = own.get("detail")
                except Exception:
                    logger.debug("통과필지 소유구분 조회 실패(사유 추정 유지)", exc_info=True)
                try:
                    reg = await building_register.lookup(hit_pnu)
                    if reg.get("status") in {"FOUND", "CLEAR"}:
                        parcel_hit["has_building"] = bool(reg.get("has_buildings"))
                except Exception:
                    logger.debug("통과필지 건축물대장 조회 실패", exc_info=True)
            # 차단(침범) 여부 재계산: 소유구분이 국·공유면 통과 가능(차단 아님), 사유면 차단.
            # 소유구분 미상이면 지목 proxy(비공공 지목이라 사유로 간주)로 보수적으로 본다.
            enc["crosses_private"] = any(
                hit.get("ownership") != "국공유"  # 사유 또는 미상
                for hit in enc.get("crossed_parcels", [])
            )
            drainage_info["encroachment"] = enc
    if not roads:
        categories: dict[str, int] = {}
        for item in adjacent_nonroads:
            categories[item["jimok"]] = categories.get(item["jimok"], 0) + 1
        adjacent_text = (
            " 맞닿은 비도로 필지는 "
            + ", ".join(f"지목 '{jimok}' {count}필지" for jimok, count in categories.items())
            + "로 확인됩니다."
            if categories
            else ""
        )
        return {
            "status": "NO_CADASTRAL_ROAD",
            "label": "지적도상 도로 접촉 없음",
            "message": (
                "인접 필지 중 지목 '도로'는 확인되지 않았습니다."
                + adjacent_text
                + " 지적도상 맹지 가능성이 있습니다. 다만 건축법상 지정도로·"
                  "현황도로·통행권은 도로대장과 현황측량으로 확인해야 합니다."
            ),
            "roads": [],
            "adjacent_nonroad_parcels": adjacent_nonroads,
            "drainage": drainage_info,
            "unknowns": ["건축법상 도로 지정 여부", "현황도로·통행권"],
            "legal_basis": "건축법 제2조제1항제11호, 제44조",
            "verification_sources": _verification_sources(),
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
        "adjacent_nonroad_parcels": adjacent_nonroads,
        "drainage": drainage_info,
        "road_contact_geometry": (
            transform(inverse.transform, road_contact).__geo_interface__
            if not road_contact.is_empty else None
        ),
        "unknowns": ["건축법상 도로 지정 여부", "유효 도로폭", "도로 중심선 후퇴선"],
        "legal_basis": "건축법 제2조제1항제11호, 제44조",
        "caveat": "지적도 기반 참고 판정이며 접도 길이 2m 충족과 실제 도로폭을 확정하지 않습니다.",
        "verification_sources": _verification_sources(),
    }
