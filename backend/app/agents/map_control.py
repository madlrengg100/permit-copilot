"""지도제어 에이전트.

사전진단 결과를 받아 프론트엔드 VWorld 3D 지도가 실행할 명령 시퀀스로 번역한다.
LLM 을 쓰지 않는 결정적 변환기 — 진단이 이미 판단을 끝냈으므로 여기서는
'무엇을 어떻게 그릴지'만 결정하면 되고, 그 규칙은 확정적이다.

명령 종류:
  fly_to            해당 위치로 카메라 이동
  highlight_parcel  필지 경계 폴리곤 강조 + 라벨
  extrude_mass      필지 경계를 밀어올려 가상 건물 매스 생성
  clear_mass        기존 매스 제거
  show_panel        지도 위 정보 패널 표시
"""

from __future__ import annotations

import math

from shapely.geometry import shape

from ..tools.footprint import inset_for_area

# 판정 결과 -> 매스 색상 (지도에서 허가 가능성이 한눈에 보이도록)
VERDICT_COLOR = {
    "allowed": "#2E7D32",       # 초록 — 가능
    "conditional": "#F9A825",   # 노랑 — 조건부
    "not_allowed": "#C62828",   # 빨강 — 불가
    "unknown": "#616161",       # 회색 — 판단 불가
}

VERDICT_LABEL = {
    "allowed": "건축 가능",
    "conditional": "조건부 가능",
    "not_allowed": "건축 불가",
    "unknown": "판단 불가",
}

# 용도지역 -> 오버레이 색. 지적편집도 관례(주거 노랑 / 상업 분홍 / 공업 보라 /
# 녹지 초록 / 관리 연두 계열)를 따르되, 같은 계열끼리는 톤으로 구분한다.
ZONE_COLORS = {
    "제1종전용주거지역": "#FFF9C4", "제2종전용주거지역": "#FFF59D",
    "제1종일반주거지역": "#FFE082", "제2종일반주거지역": "#FFD54F",
    "제3종일반주거지역": "#FFCA28", "준주거지역": "#FFB74D",
    "중심상업지역": "#F06292", "일반상업지역": "#F48FB1",
    "근린상업지역": "#F8BBD0", "유통상업지역": "#CE93D8",
    "전용공업지역": "#9575CD", "일반공업지역": "#B39DDB", "준공업지역": "#C5CAE9",
    "보전녹지지역": "#388E3C", "생산녹지지역": "#66BB6A", "자연녹지지역": "#A5D6A7",
    "보전관리지역": "#26A69A", "생산관리지역": "#80CBC4", "계획관리지역": "#DCE775",
    "농림지역": "#558B2F", "자연환경보전지역": "#33691E",
}


def zone_color(zone: str) -> str:
    return ZONE_COLORS.get(zone, "#B0BEC5")  # 미지정은 회청색


def _hue(hex_color: str) -> float:
    """#RRGGBB -> 색상(0~360). 색 계열 충돌 판정용."""
    import colorsys

    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def _piece_colors(zones: list[str]) -> list[str]:
    """걸침 조각별 색.

    관례색을 우선 쓰되, 걸친 지역들이 같은 색 계열이면(자연녹지·생산녹지처럼
    둘 다 초록) 반투명으로 깔았을 때 사실상 같은 색이 되어 경계가 무의미해진다.
    그 경우에만 뒤 조각을 대비색으로 교체한다. 색의 의미는 범례가 설명한다.
    """
    contrast = ["#29B6F6", "#AB47BC", "#FF7043", "#FFD600"]  # 파랑/보라/주황/노랑

    def clashes(hue: float, used: list[float]) -> bool:
        return any(abs((hue - u + 180) % 360 - 180) < 40 for u in used)

    used_hues: list[float] = []
    out: list[str] = []
    for zone in zones:
        c = zone_color(zone)
        if clashes(_hue(c), used_hues):
            c = next(
                (fb for fb in contrast if not clashes(_hue(fb), used_hues)),
                contrast[0],
            )
        used_hues.append(_hue(c))
        out.append(c)
    return out


def _view_altitude_m(area_m2: float) -> float:
    """필지 크기에 맞는 카메라 고도(m). 해수면이 아니라 **지면 위 높이**다.

    고정 고도를 쓰면 작은 필지는 점처럼 보이고 큰 필지는 화면을 넘친다.
    한 변 길이(=√면적)에 비례시켜 필지가 화면을 일정 비율로 채우게 한다.

    배율 계산: 부각 35도에서 카메라~대상 시거리는 h/sin35 ≈ 1.74h, 수직화각
    60도면 화면에 담기는 폭은 그 1.155배 ≈ 2h. 필지가 화면의 1/4 정도를
    차지하게 하려면 2h ≈ 4·side, 즉 h ≈ 2·side.
    """
    side = max(area_m2, 1) ** 0.5
    return max(60.0, min(700.0, round(side * 2)))


def _camera_heading(geometry: dict | None) -> float:
    """필지의 가장 긴 변을 화면 가로로 보이게 하는 카메라 방위각."""
    if not geometry:
        return 0.0
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    else:
        rings = [coords[0]] if coords else []
    edges: list[tuple[float, float, float]] = []
    for ring in rings:
        for (lon1, lat1), (lon2, lat2) in zip(ring, ring[1:]):
            mean_lat = math.radians((lat1 + lat2) / 2)
            east = (lon2 - lon1) * math.cos(mean_lat)
            north = lat2 - lat1
            edges.append((east * east + north * north, east, north))
    if not edges:
        return 0.0
    _, east, north = max(edges)
    edge_bearing = math.degrees(math.atan2(east, north))
    return round((edge_bearing + 90) % 360, 1)


def _outer_rings(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "MultiPolygon":
        return [poly[0] for poly in coords if poly]
    if geometry.get("type") == "Polygon":
        return [coords[0]] if coords else []
    return []


def _bbox(geometry: dict | None):
    """가장 넓은 외곽 링의 경위도 bbox (minlon, minlat, maxlon, maxlat)."""
    rings = _outer_rings(geometry)
    if not rings:
        return None
    ring = max(rings, key=len)
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def _centroid(geometry: dict | None):
    rings = _outer_rings(geometry)
    if not rings:
        return None
    ring = max(rings, key=len)
    n = len(ring)
    return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n


def _meters_ew(dlon: float, lat: float) -> float:
    return abs(dlon) * 111320.0 * math.cos(math.radians(lat))


def _meters_ns(dlat: float) -> float:
    return abs(dlat) * 111320.0


def _build_dimensions(
    diagnosis: dict, anchor_lon: float, anchor_lat: float
) -> dict | None:
    """필지 가로·세로, 이격, 면적을 지도에 그릴 치수선/라벨로 만든다.
    검은 텍스트 박스를 대신해 VWorld 측정선 모양으로 지도에 직접 얹는다.
    """
    parcel = diagnosis.get("parcel") or {}
    geometry = parcel.get("geometry")
    box = _bbox(geometry)
    if not box:
        return None
    minlon, minlat, maxlon, maxlat = box
    mid_lat = (minlat + maxlat) / 2

    width_m = _meters_ew(maxlon - minlon, mid_lat)
    depth_m = _meters_ns(maxlat - minlat)
    # 치수선을 필지 밖으로 살짝 빼 읽기 쉽게 한다(대략 필지 폭의 6%)
    pad_lat = (maxlat - minlat) * 0.06
    pad_lon = (maxlon - minlon) * 0.06

    segments = [
        {  # 가로(동서) — 남쪽 변 아래에
            "positions": [[minlon, minlat - pad_lat], [maxlon, minlat - pad_lat]],
            "label": f"가로 약 {width_m:,.0f}m",
        },
        {  # 세로(남북) — 서쪽 변 왼쪽에
            "positions": [[minlon - pad_lon, minlat], [minlon - pad_lon, maxlat]],
            "label": f"세로 약 {depth_m:,.0f}m",
        },
    ]

    labels = []
    mass = diagnosis.get("massing") or {}
    # 면적 라벨을 **팝업 앵커와 정확히 같은 높이**에 둔다(아래 show_panel 의
    # anchor height = mass_top + 2 와 동일). 높이가 다르면 그 차이가 확대할수록
    # 화면에서 벌어져 라벨이 접기 버튼에서 떨어진다.
    _mass_top = 0.0 if mass.get("exceeds_far_limit") else float(mass.get("mass_height_m") or 0)
    top_h = _mass_top + 2

    # 대지면적 — 필지 중심(내부점)에. representative_point 는 오목한 필지에서도
    # 폴리곤 안에 떨어진다.
    if parcel.get("area_m2"):
        try:
            pcen = shape(geometry).representative_point()
            plon, plat = pcen.x, pcen.y
        except Exception:
            cen = _centroid(geometry)
            plon, plat = (cen if cen else (0, 0))
        labels.append(
            {
                "lon": plon, "lat": plat, "height": top_h,
                "text": f"대지면적 {parcel['area_m2'] / 3.3058:,.0f}평({parcel['area_m2']:,.0f}㎡)",
            }
        )

    if mass.get("building_area_m2"):
        # 건축면적 — 건물(매스) 중심(anchor)에. 대지면적과 지리적으로 떨어져 겹치지 않는다.
        labels.append(
            {
                "lon": anchor_lon,
                "lat": anchor_lat,
                "height": top_h,
                "text": f"건축면적 {mass['building_area_m2'] / 3.3058:,.0f}평({mass['building_area_m2']:,.0f}㎡)",
            }
        )

    # 이격거리 — 값이 있을 때만(아산 계획관리는 0이라 생략됨)
    sc = diagnosis.get("site_constraints") or {}
    front = sc.get("front_setback_m") or 0
    adjacent = sc.get("adjacent_setback_m") or 0
    setback = max(float(front), float(adjacent))
    if setback > 0:
        segments.append(
            {
                "positions": [[minlon, maxlat + pad_lat], [maxlon, maxlat + pad_lat]],
                "label": f"이격 {setback:g}m",
            }
        )

    # 도로 접촉 길이 — 접촉 세그먼트 기하가 없어 라벨만(남쪽 변 중앙 아래)
    road = (diagnosis.get("road_access") or {}).get("roads") or []
    if road and road[0].get("contact_length_m"):
        labels.append(
            {
                "lon": (minlon + maxlon) / 2,
                "lat": minlat - pad_lat * 2.2,
                "text": f"도로 접촉 {road[0]['contact_length_m']}m",
            }
        )

    return {"type": "show_dimensions", "segments": segments, "labels": labels}


def build_map_commands(diagnosis: dict) -> list[dict]:
    """진단 결과 -> 지도 명령 시퀀스."""
    commands: list[dict] = []

    location = diagnosis.get("location")
    parcel = diagnosis.get("parcel")
    land_use = diagnosis.get("land_use", {})
    regulation = diagnosis.get("regulation", {})
    mass = diagnosis.get("massing")
    conversion = diagnosis.get("land_conversion", {})
    existing_buildings = diagnosis.get("existing_buildings", {})
    conversion_charge = diagnosis.get("conversion_charge")
    development_charge = diagnosis.get("development_charge")
    road_access = diagnosis.get("road_access", {})
    regulatory_screen = diagnosis.get("regulatory_screen", {})
    permit_requirements = diagnosis.get("permit_requirements", {})
    legal_sources = diagnosis.get("legal_sources", {})
    site_constraints = diagnosis.get("site_constraints", {})
    verdict = diagnosis.get("verdict", "unknown")

    if not location:
        return commands

    lon, lat = location["lon"], location["lat"]

    # 1) 매스는 항상 먼저 지운다 — 이전 질의 결과가 겹쳐 보이지 않도록
    commands.append({"type": "clear_mass"})

    # 2) 대상 위치로 이동.
    #    고도는 필지 크기에 맞춘다. 고정값을 쓰면 작은 필지(수백 ㎡)는 점처럼
    #    보이고 큰 필지는 화면을 넘친다.
    commands.append(
        {
            "type": "fly_to",
            "lon": lon,
            "lat": lat,
            "altitude": _view_altitude_m((parcel or {}).get("area_m2") or 0),
            "tilt": 55,
            "heading": _camera_heading((parcel or {}).get("geometry")),
        }
    )

    # 3) 필지 경계 강조
    if parcel and parcel.get("geometry"):
        commands.append(
            {
                "type": "highlight_parcel",
                "geometry": parcel["geometry"],
                "pnu": parcel.get("pnu", ""),
                "label": f"{parcel.get('jibun', '')} · {parcel.get('area_m2', 0):,.0f}㎡",
                "color": VERDICT_COLOR[verdict],
            }
        )

    # 3.5) 용도지역 걸침 조각 오버레이 — 필지가 둘 이상 지역에 걸친 경우에만.
    #      조각을 지역별 색으로 깔면 색이 갈리는 곳이 곧 경계선이라, 경계를
    #      따로 그리지 않아도 어디서 규제가 바뀌는지 눈에 보인다.
    #      프론트 우측 범례 창도 이 명령의 pieces 를 그대로 쓴다.
    zone_shares = land_use.get("zone_shares") or []
    pieces = [s for s in zone_shares if s.get("geometry")]
    if len(pieces) >= 2:
        piece_colors = _piece_colors([s["zone"] for s in pieces])
        commands.append(
            {
                "type": "show_zone_pieces",
                "pieces": [
                    {
                        "zone": s["zone"],
                        "share_pct": s["share_pct"],
                        "area_m2": s["area_m2"],
                        "color": piece_colors[i],
                        "geometry": s["geometry"],
                    }
                    for i, s in enumerate(pieces)
                ],
            }
        )

    # 4) 매스 — 진단이 매스를 산출했고, 건축이 불가하지 않은 경우에만.
    #    불허 필지에 건물을 세워 보여주면 가능한 것처럼 읽히므로 그리지 않는다.
    footprint_geometry = None
    anchor_lon, anchor_lat = lon, lat
    if (
        mass
        and mass.get("layout_feasible", True)
        and parcel
        and parcel.get("geometry")
        and verdict != "not_allowed"
    ):
        footprint_geometry = site_constraints.get("footprint_geometry")
        if not footprint_geometry and not site_constraints:
            footprint_geometry = inset_for_area(
                parcel["geometry"], mass["bcr_applied_pct"] / 100.0
            )
        top_footprint_geometry = None
        top_ratio = mass.get("top_floor_ratio", 1.0)
        if footprint_geometry and 0 < top_ratio < 0.999:
            top_footprint_geometry = inset_for_area(footprint_geometry, top_ratio)
        if footprint_geometry:
            footprint_point = shape(footprint_geometry).representative_point()
            anchor_lon, anchor_lat = footprint_point.x, footprint_point.y
        commands.append(
            {
                "type": "extrude_mass",
                "geometry": parcel["geometry"],
                "footprint_geometry": footprint_geometry,
                "top_footprint_geometry": top_footprint_geometry,
                "anchor": {"lon": anchor_lon, "lat": anchor_lat},
                "height_m": mass["mass_height_m"],
                "floors": mass["floors"],
                "full_floors": mass.get("full_floors", mass["floors"]),
                "top_floor_ratio": mass.get("top_floor_ratio", 1.0),
                "flat_only": mass.get("exceeds_far_limit", False),
                # 건폐율만큼만 바닥을 차지하므로 폴리곤을 그 비율로 축소해 그린다
                "footprint_ratio": mass["bcr_applied_pct"] / 100.0,
                "color": VERDICT_COLOR[verdict],
                "opacity": 0.55,
                "label": (
                    f"최대 건축면적 {mass['building_area_m2']:,.0f}㎡"
                    if mass.get("exceeds_far_limit")
                    else f"신축 추정 {mass['floors']}층 · 연면적 {mass['gross_floor_area_m2']:,.0f}㎡"
                ),
            }
        )

    # 4.5) 치수선·면적 라벨 — 검은 텍스트 박스 대신 지도에 직접 얹는다.
    #      건축면적은 매스 중심(anchor), 대지면적은 필지 중심에 배치한다.
    dims = _build_dimensions(diagnosis, anchor_lon, anchor_lat)
    if dims:
        commands.append(dims)

    # 5) 정보 패널
    #    anchor 는 패널을 매스 꼭대기 위에 띄우기 위한 지도상 기준점이다.
    #    프론트가 이 좌표를 화면 픽셀로 투영해 패널을 붙인다.
    #
    #    height 는 **지면 위 높이**다(프론트에서 지형 표고를 더한다).
    mass_top = (
        mass["mass_height_m"]
        if mass and not mass.get("exceeds_far_limit")
        else 0.0
    )

    # 검색 좌표가 아니라 실제로 그린 건축면적 형상의 내부점을 사용해야
    # 말풍선이 정확히 건물 위에 붙는다. 지붕에서 살짝만 띄워 기존 매스 라벨
    # 자리에 표시한다.
    anchor = {"lon": anchor_lon, "lat": anchor_lat, "height": mass_top + 2}

    limit_exceeded = bool(mass and mass.get("exceeds_far_limit"))
    layout_infeasible = bool(mass and mass.get("layout_feasible") is False)
    panel_verdict = (
        "limit_exceeded" if limit_exceeded
        else "site_infeasible" if layout_infeasible
        else verdict
    )
    panel_label = (
        "요청값 적용 불가" if limit_exceeded
        else "실질 배치 불가" if layout_infeasible
        else (
            "전용·개별규제 확인 필요"
            if verdict == "unknown"
            and regulation.get("reason")
            and conversion.get("status") == "RESTRICTED_REVIEW"
            else "개별규제 확인 필요"
        )
        if verdict == "unknown" and regulation.get("reason")
        else VERDICT_LABEL[verdict]
    )
    panel_color = (
        "#C62828"
        if limit_exceeded
        or layout_infeasible
        else "#616161"
        if verdict == "unknown" and regulation.get("reason")
        else VERDICT_COLOR[verdict]
    )

    commands.append(
        {
            "type": "show_panel",
            "anchor": anchor,
            "verdict": panel_verdict,
            "verdict_label": panel_label,
            "color": panel_color,
            "address": location.get("matched_address", ""),
            "zone": regulation.get("zone") or (land_use.get("zones") or [""])[0],
            "districts": land_use.get("districts", []),
            "jimok": (parcel or {}).get("jimok", ""),
            "building_use": regulation.get("building_use", ""),
            "site_area_m2": (parcel or {}).get("area_m2"),
            "jiga_won_per_m2": (parcel or {}).get("jiga_won_per_m2"),
            "bcr_max_pct": regulation.get("bcr_max_pct"),
            "far_max_pct": regulation.get("far_max_pct"),
            "legal_basis": regulation.get("legal_basis", ""),
            "constraints": regulation.get("constraints", []),
            "zone_use_overview": regulation.get("zone_use_overview", {}),
            "massing": mass,
            "land_conversion": conversion,
            "existing_buildings": existing_buildings,
            "conversion_charge": conversion_charge,
            "development_charge": development_charge,
            "road_access": road_access,
            "regulatory_screen": regulatory_screen,
            "permit_requirements": permit_requirements,
            "legal_sources": legal_sources,
            "site_constraints": {
                k: v for k, v in site_constraints.items()
                if k != "footprint_geometry"
            },
        }
    )

    return commands
