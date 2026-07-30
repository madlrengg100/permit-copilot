"""주차·대지 안의 공지·정북 일조를 반영한 개념 건축 가능 영역."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import LineString, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import transform, unary_union

from ..config import FLOOR_HEIGHT_M

MIN_PRACTICAL_FOOTPRINT_M2 = 10.0
from . import setback_rules

_BUILDING_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "building_use_rules.json"
)


@lru_cache(maxsize=1)
def _parking_rules() -> dict:
    with _BUILDING_RULES_PATH.open(encoding="utf-8") as file:
        return json.load(file).get("parking_rules", {})

# 주차 방식 상태 -> 완결된 한국어 문장 (caveat 문구용).
# 상태코드를 그대로 노출하지 않고, 주어·서술어를 갖춘 자연스러운 문장으로 쓴다.
# 지상주차 면적은 '지상' 선택 시에만 실제로 차감된다 — 미선택 기본값에서는
# 차감되지 않으므로 '반영되었다'고 쓰면 사실과 어긋난다.
_PARKING_KO = {
    "APPLIED": "지상주차만 반영했습니다",
    "DESIGN_REQUIRED": "주차는 별도 설계가 필요합니다",
    "NEEDS_SELECTION": "주차는 아직 반영하지 않았습니다",
}
NORTH_DAYLIGHT_ZONES = {
    "제1종전용주거지역", "제2종전용주거지역",
    "제1종일반주거지역", "제2종일반주거지역", "제3종일반주거지역",
}


def parking_requirement(building_use: str, gross_floor_area_m2: float) -> dict:
    area = max(0.0, float(gross_floor_area_m2 or 0))
    rules = _parking_rules()
    rule = rules.get(building_use)
    meta = rules.get("_meta", {})
    if building_use == "시설물" or not rule:
        count = 0
        formula = "전체 용도 통합 검토이므로 용도별로 다른 주차대수 산정 제외"
    elif rule["mode"] == "detached_house":
        free = float(rule["free_up_to_m2"])
        one = float(rule["one_space_up_to_m2"])
        additional = float(rule["additional_area_per_space_m2"])
        count = 0 if area <= free else (1 if area <= one else 1 + math.ceil((area - one) / additional))
        formula = f"{free:g}㎡ 초과 {one:g}㎡ 이하 1대, 초과 {additional:g}㎡당 1대 추가"
    elif rule["mode"] == "household_proxy":
        household_area = float(rule["area_per_household_m2"])
        estimated_households = max(1, math.ceil(area / household_area)) if area else 0
        count = estimated_households
        formula = f"세대수 미확인으로 {household_area:g}㎡당 1세대·세대당 1대 가정"
    else:
        ratio = float(rule["area_per_space_m2"])
        count = math.ceil(area / ratio) if area else 0
        formula = f"시설면적 {ratio:g}㎡당 1대 참고"
    surface_area_per_space = float(meta.get("surface_area_per_space_m2") or 0)
    return {
        "spaces": count,
        "estimated": bool(rule) and building_use != "시설물",
        "formula": formula,
        "surface_area_m2": round(count * surface_area_per_space, 1),
        "basis": meta.get("source", ""),
        "rule_status": meta.get("status", "unknown"),
        "caveat": "지자체 주차장 조례, 세대수, 장애인주차 및 기계식·지하주차 계획에 따라 달라집니다.",
    }


def _local_geometry(geojson: dict):
    source = shape(geojson).buffer(0)
    center = source.representative_point()
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    return transform(forward, source), forward, inverse


def _north_boundary(parcel):
    """지적 북쪽 끝 한 줄이 아니라 외향 법선이 북향인 실제 경계 조각."""
    polygon = _largest(parcel)
    if polygon is None:
        return None
    ring = list(orient(polygon, sign=1.0).exterior.coords)
    lines = []
    for start, end in zip(ring, ring[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        # CCW 외곽링의 외향 법선은 (dy, -dx). 북향 성분이 충분한 조각만 선택.
        if length > 0.05 and (-dx / length) > 0.25:
            lines.append(LineString([start, end]))
    return unary_union(lines) if lines else None


def _largest(geometry):
    if geometry.is_empty:
        return None
    if geometry.geom_type == "Polygon":
        return geometry
    polygons = [part for part in geometry.geoms if part.geom_type == "Polygon"]
    return max(polygons, key=lambda part: part.area) if polygons else None


def apply(
    *,
    parcel_geometry: dict,
    massing: dict,
    building_use: str,
    zone: str,
    jurisdiction: str = "",
    road_access: dict | None = None,
    parking_strategy: str = "unspecified",
) -> dict:
    parcel, forward, inverse = _local_geometry(parcel_geometry)
    parking = parking_requirement(building_use, massing.get("gross_floor_area_m2", 0))
    parking["strategy"] = parking_strategy
    if parking_strategy == "surface":
        parking["applied_surface_area_m2"] = parking["surface_area_m2"]
        parking["strategy_status"] = "APPLIED"
    elif parking_strategy in {"underground", "mechanical", "mixed"}:
        parking["applied_surface_area_m2"] = 0.0
        parking["strategy_status"] = "DESIGN_REQUIRED"
    else:
        parking["applied_surface_area_m2"] = 0.0
        parking["strategy_status"] = "NEEDS_SELECTION"

    density_area = float(massing.get("building_area_m2") or 0)
    gross = float(massing.get("gross_floor_area_m2") or 0)
    # 공지·주차 반영으로 바닥면적이 매우 작아졌다고 남은 용적률을 수만 층으로
    # 쌓을 수 있는 것은 아니다. 최초 건폐율·용적률 산정 층수를 계획 상한으로
    # 유지하고, 수용하지 못한 연면적은 아래에서 achievable 값으로 줄인다.
    planned_floors = int(
        massing.get("floors")
        or (max(1, math.ceil(gross / density_area - 1e-9)) if density_area > 0 else 0)
    )
    daylight_applies = zone in NORTH_DAYLIGHT_ZONES
    rule = setback_rules.lookup(jurisdiction, building_use, zone, gross)
    front = float(rule["front_m"] or 0) if rule["status"] == "APPLIED" else 0.0
    adjacent = float(rule["adjacent_m"] or 0) if rule["status"] == "APPLIED" else 0.0

    road_line = None
    road_geometry = (road_access or {}).get("road_contact_geometry")
    if road_geometry:
        road_line = transform(forward, shape(road_geometry))
    nonroad_boundary = parcel.boundary
    if road_line is not None and not road_line.is_empty:
        nonroad_boundary = nonroad_boundary.difference(road_line.buffer(0.25))
    north_boundary = _north_boundary(parcel)

    height = float(massing.get("mass_height_m") or FLOOR_HEIGHT_M)
    envelope = None
    north_setback = 0.0
    adjusted_area = density_area
    for _ in range(6):
        north_setback = (
            (1.5 if height <= 10 else height / 2)
            if daylight_applies else 0.0
        )
        base = parcel
        if adjacent and not nonroad_boundary.is_empty:
            base = base.difference(nonroad_boundary.buffer(adjacent, cap_style=2, join_style=2))
        if front and road_line is not None and not road_line.is_empty:
            base = base.difference(road_line.buffer(front, cap_style=2, join_style=2))
        if daylight_applies and north_boundary is not None and not north_boundary.is_empty:
            base = base.difference(north_boundary.buffer(north_setback, cap_style=2, join_style=2))
        envelope = _largest(base)
        envelope_area = envelope.area if envelope is not None else 0.0
        surface_limit = max(0.0, parcel.area - parking["applied_surface_area_m2"])
        adjusted_area = min(density_area, envelope_area, surface_limit)
        floors_raw = max(1, math.ceil(gross / adjusted_area - 1e-9)) if adjusted_area > 0 else 0
        floors = min(floors_raw, planned_floors) if planned_floors else 0
        new_height = floors * FLOOR_HEIGHT_M if floors else 0
        if abs(new_height - height) < 0.1:
            height = new_height
            break
        height = new_height

    # 수㎡짜리 잔여 조각은 수학상 면적이 있어도 출입·계단·벽체를 수용할 수
    # 있는 건축 바닥으로 제시하면 안 된다. 사전진단에서는 실질 배치 불가로 둔다.
    layout_feasible = adjusted_area >= MIN_PRACTICAL_FOOTPRINT_M2
    if not layout_feasible:
        adjusted_area = 0.0

    footprint = None
    if layout_feasible and envelope is not None and adjusted_area > 0:
        if adjusted_area < envelope.area:
            low, high = 0.0, max(envelope.bounds[2] - envelope.bounds[0], envelope.bounds[3] - envelope.bounds[1])
            best = envelope
            for _ in range(42):
                distance = (low + high) / 2
                candidate = _largest(envelope.buffer(-distance, join_style="mitre"))
                candidate_area = candidate.area if candidate is not None else 0
                if candidate_area >= adjusted_area:
                    best, low = candidate, distance
                else:
                    high = distance
            envelope = best
        footprint = mapping(transform(inverse, envelope))

    achievable_gross = min(gross, adjusted_area * planned_floors) if planned_floors else 0
    floors_theoretical = achievable_gross / adjusted_area if adjusted_area else 0
    floors = max(1, math.ceil(floors_theoretical - 1e-9)) if adjusted_area else 0
    full_floors = math.floor(floors_theoretical + 1e-9) if floors else 0
    top_ratio = 1.0 if floors == full_floors else floors_theoretical - full_floors

    # 이격 caveat — 0m일 때도 '왜 0인지'를 이 지자체·용도 데이터로 구체화한다.
    # (일반론 대신 조례 수집 여부·다른 용도의 실제 수치를 근거로)
    if front > 0 or adjacent > 0 or north_setback > 0:
        setback_caveat = (
            f"이격거리를 규모 계산에 반영: 전면 {front:g}m·인접 {adjacent:g}m"
            + (f"·정북일조 {north_setback:g}m" if north_setback > 0 else "")
            + "(지도에 치수선 표시). "
        )
    elif rule.get("status") == "NOT_COLLECTED":
        setback_caveat = (
            f"{jurisdiction or '이 지역'} 건축조례 대지 안의 공지 별표가 아직 수집되지 "
            "않아 이격 0m로 계산했습니다. "
        )
    else:
        _other_uses = setback_rules.setback_uses(jurisdiction, building_use)
        setback_caveat = (
            f"이 필지에서 {building_use} 용도는 대지 안의 공지(건축법 시행령 제80조의2·"
            "별표2, 연면적 500㎡ 이상 공장·창고 등 대상) 대상이 아니어서 이격 0m입니다"
            + (
                f"(같은 조례에서 {' · '.join(_other_uses)} 용도는 규모·지역에 따라 이격 적용). "
                if _other_uses
                else ". "
            )
        )
    return {
        "footprint_geometry": footprint,
        "density_building_area_m2": round(density_area, 2),
        "adjusted_building_area_m2": round(adjusted_area, 2),
        "reduction_m2": round(max(0, density_area - adjusted_area), 2),
        "reduction_pct": round((1 - adjusted_area / density_area) * 100, 1) if density_area else 0,
        "setback_rule": rule,
        "front_setback_m": front,
        "adjacent_setback_m": adjacent,
        "building_line_status": (
            "CADASTRAL_ROAD_EDGE_PROVISIONAL" if road_line is not None
            else "LEGAL_BUILDING_LINE_UNAVAILABLE"
        ),
        "north_daylight_applies": daylight_applies,
        "north_setback_m": round(north_setback, 1),
        "north_boundary_status": "CADASTRAL_DIRECTIONAL_BOUNDARY",
        "parking": parking,
        "floors": floors,
        "full_floors": full_floors,
        "top_floor_ratio": round(top_ratio, 3),
        "mass_height_m": round(floors * FLOOR_HEIGHT_M, 1) if floors else 0,
        "achievable_gross_floor_area_m2": round(achievable_gross, 2),
        "layout_feasible": layout_feasible,
        "minimum_practical_footprint_m2": MIN_PRACTICAL_FOOTPRINT_M2,
        "basis": [
            "건축법 시행령 제80조의2 및 별표 2(대지 안의 공지)",
            "건축법 시행령 제86조(정북방향 일조)",
            parking["basis"],
        ],
        "caveat": (
            (
                f"이격·주차 반영 후 사용 가능한 바닥면적이 {MIN_PRACTICAL_FOOTPRINT_M2:g}㎡ "
                "미만이어서 사전진단상 실질 배치가 어렵습니다. "
                if not layout_feasible
                else ""
            )
            # 이격 문구는 위에서 status·타 용도까지 반영해 조립한 값을 쓴다.
            + setback_caveat
            +
            # 주차는 '개념 면적 차감'이지 지도에 주차 라인을 그리는 것이 아니다.
            (
                f"지상주차 {parking['spaces']}대 개념 면적을 규모에서 차감(배치 라인은 미표시). "
                if float(parking.get("applied_surface_area_m2") or 0) > 0
                else "지상주차 면적 차감은 없습니다(미선택 또는 불필요). "
            )
            + "건축선·도로 후퇴선은 현황측량으로 확정해야 합니다."
        ),
    }
