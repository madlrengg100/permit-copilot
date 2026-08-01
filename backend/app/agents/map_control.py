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

import logging
import math

from shapely.geometry import shape

from ..tools.footprint import inset_for_area

logger = logging.getLogger(__name__)

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


def _restriction_color(label: str) -> str:
    """규제 중첩 범례 색 — 생태·자연도 등급/재해 유형별 관례색."""
    s = label or ""
    if "1등급" in s:
        return "#C62828"  # 빨강(보전가치 최상)
    if "2등급" in s:
        return "#EF6C00"  # 주황
    if "3등급" in s:
        return "#9E9E9E"  # 회색(참고)
    if "별도관리" in s:
        return "#6A1B9A"  # 보라
    if "재해" in s or "위험" in s:
        return "#AD1457"  # 자홍
    return "#EF6C00"


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
            logger.debug("라벨 위치 계산 실패, 중심점으로 폴백", exc_info=True)
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

    # 높이 — 가로·세로 치수선이 만나는 모서리(남서)에서 수직으로 올려 3축을 이룬다.
    # 프론트가 height_m 을 보고 그 모서리에서 매스와 같은 기준으로 수직선을 그린다.
    if _mass_top > 0:
        _floors = mass.get("floors")
        _corner = [minlon - pad_lon, minlat - pad_lat]
        segments.append(
            {
                "positions": [_corner, _corner],
                "height_m": round(_mass_top, 1),
                "label": (
                    f"높이 약 {_mass_top:,.1f}m"
                    + (f" · {_floors}층" if _floors else "")
                ),
                # 색 지정 없음 → 가로·세로와 같은 노랑(3축 동일색).
            }
        )

    # 이격거리 — 값이 있을 때만(수집된 지자체만; 아산 등). 건물 용도·지역·규모에
    # 따라 정해진 전면(건축선)·인접경계 이격을, 필지 경계선에서 '안쪽으로 그 거리만큼'
    # 뻗는 오프셋 치수선으로 각각 표시해 가상 건물이 얼마나 물러나 앉는지 보이게 한다.
    # 이격거리 — 값이 0이면 표시하지 않고, 값이 있으면 '방향을 맞춰' 실제 경계에서
    # 안쪽으로 그 거리만큼 오프셋 치수선을 그린다.
    #   · 전면(건축선): 실제 도로측 경계(도로 접촉 geometry)에서 안쪽으로.
    #   · 정북일조: 진북(북쪽) 경계에서 남쪽 안쪽으로.
    #   · 인접경계: 전면(도로)과 겹치지 않는 대표 측면 경계에서 안쪽으로.
    sc = diagnosis.get("site_constraints") or {}
    front = float(sc.get("front_setback_m") or 0)
    adjacent = float(sc.get("adjacent_setback_m") or 0)
    north = float(sc.get("north_setback_m") or 0)
    mid_lon = (minlon + maxlon) / 2
    deg_per_m_lat = 1.0 / 111320.0
    deg_per_m_lon = 1.0 / (111320.0 * max(0.1, math.cos(math.radians(mid_lat))))

    # bbox 네 변의 중점과 '안쪽(중심)으로' 향하는 단위방향(경도·위도 부호).
    edges = {
        "S": (mid_lon, minlat, 0, +1),
        "N": (mid_lon, maxlat, 0, -1),
        "W": (minlon, mid_lat, +1, 0),
        "E": (maxlon, mid_lat, -1, 0),
    }

    def _offset_segment(edge_key: str, dist_m: float, label: str) -> dict:
        lo, la, sx, sy = edges[edge_key]
        return {
            "positions": [
                [lo, la],
                [lo + sx * dist_m * deg_per_m_lon, la + sy * dist_m * deg_per_m_lat],
            ],
            "label": label,
        }

    def _building_line(edge_key: str, dist_m: float, label: str) -> dict:
        """해당 변에서 안쪽으로 dist_m 물러난 '건축선'을, 그 변과 나란한 선으로
        (변 전체 길이만큼) 그린다. 건물이 물러나 앉는 기준선이 보이게 한다."""
        if edge_key in ("S", "N"):
            la = (minlat if edge_key == "S" else maxlat) + (
                dist_m * deg_per_m_lat * (1 if edge_key == "S" else -1)
            )
            return {"positions": [[minlon, la], [maxlon, la]], "label": label}
        lo = (minlon if edge_key == "W" else maxlon) + (
            dist_m * deg_per_m_lon * (1 if edge_key == "W" else -1)
        )
        return {"positions": [[lo, minlat], [lo, maxlat]], "label": label}

    # 도로측 변 찾기 — 도로 접촉선 중점에 가장 가까운 bbox 변. 도로중점(rmx,rmy)은
    # 전면이격 눈금을 '실제 도로측 경계점'에서 시작하는 데 쓴다.
    front_edge = "S"
    rmx = rmy = None
    road_geom = (diagnosis.get("road_access") or {}).get("road_contact_geometry")
    try:
        coords = []
        if road_geom and road_geom.get("type") == "MultiLineString":
            for ln in road_geom["coordinates"]:
                coords.extend(ln)
        elif road_geom and road_geom.get("type") == "LineString":
            coords = road_geom["coordinates"]
        if coords:
            rmx = sum(c[0] for c in coords) / len(coords)
            rmy = sum(c[1] for c in coords) / len(coords)
            front_edge = min(
                edges,
                key=lambda k: (edges[k][0] - rmx) ** 2 + (edges[k][1] - rmy) ** 2,
            )
    except Exception:
        logger.debug("전면 경계 판정 실패, 남측(S)으로 폴백", exc_info=True)
        front_edge = "S"

    # 필지 경계 점들 — 이격 눈금을 '실제 경계점'에서 건축선까지 잇는 데 쓴다.
    try:
        _pg = shape(geometry)
        _poly = _pg if _pg.geom_type == "Polygon" else max(_pg.geoms, key=lambda g: g.area)
        ring_pts = [(float(x), float(y)) for x, y in _poly.exterior.coords]
    except Exception:
        logger.debug("필지 경계점 추출 실패", exc_info=True)
        ring_pts = []

    # 이격거리 = '인접대지경계선(지적선) → 건축선' 사이 거리.
    #   · 건축선: 실제 필지 경계를 안쪽으로 이격만큼 오프셋한 선(경계 모양을 따라감).
    #   · 이격 눈금: 주황, 경계선에서 건축선까지 수직으로 이은 선(그 거리 라벨).
    # 건축선은 기본 빨강이지만, 사유지 침범 배수로가 빨강으로 그려질 때는 색이 겹쳐
    # 구분이 안 되므로 건축선을 보라로 바꾼다(같은 화면에 두 빨강 방지).
    _drain = (diagnosis.get("road_access") or {}).get("drainage") or {}
    _enc_red = bool((_drain.get("encroachment") or {}).get("crosses_private"))
    building_line = "#7E57C2" if _enc_red else "#E53935"
    tick_color = "#FF8A00"

    def _inset_ring(setback_m: float) -> list[list[float]] | None:
        """필지 경계를 안쪽으로 setback_m 만큼 들인 '건축선'(경계 형상 유지)."""
        try:
            d_deg = setback_m / (111320.0 * max(0.1, math.cos(math.radians(mid_lat))))
            inner = shape(geometry).buffer(-d_deg)
            if inner.is_empty:
                return None
            poly = inner if inner.geom_type == "Polygon" else max(inner.geoms, key=lambda g: g.area)
            return [[float(x), float(y)] for x, y in poly.exterior.coords]
        except Exception:
            logger.debug("건축선(이격 오프셋) 계산 실패", exc_info=True)
            return None

    # 건축선(경계 형상을 따라 이격만큼 안쪽) — 전면·인접 이격 중 큰 값으로 한 줄.
    main_setback = max(front, adjacent)
    if main_setback > 0:
        ring = _inset_ring(main_setback)
        if ring:
            segments.append(
                {"positions": ring, "label": "건축선(이격 후)", "color": building_line, "width": 4}
            )

    # 이격 눈금: '실제 경계점 → 안쪽(중심 방향)으로 이격만큼' = 경계↔건축선을 잇는다.
    def _tick_from_point(px: float, py: float, dist_m: float, label: str) -> dict | None:
        dxm = (mid_lon - px) * 111320.0 * math.cos(math.radians(mid_lat))
        dym = (mid_lat - py) * 111320.0
        d = math.hypot(dxm, dym)
        if d < 1e-6:
            return None
        ex = px + (dxm / d * dist_m) / (111320.0 * max(0.1, math.cos(math.radians(mid_lat))))
        ey = py + (dym / d * dist_m) / 111320.0
        return {"positions": [[px, py], [ex, ey]], "label": label, "color": tick_color, "width": 6}

    # 전면: 실제 도로측 경계점(도로중점)에서. 없으면 남쪽 변 중점.
    fpx, fpy = (rmx, rmy) if (rmx is not None) else (mid_lon, minlat)
    if front > 0:
        tk = _tick_from_point(fpx, fpy, front, f"전면이격 {front:g}m")
        if tk:
            segments.append(tk)
    # 정북: 최북단 경계점에서 남쪽으로.
    if north > 0 and ring_pts:
        npx, npy = max(ring_pts, key=lambda p: p[1])
        tk = _tick_from_point(npx, npy, north, f"정북일조 {north:g}m")
        if tk:
            segments.append(tk)
    # 인접: 도로중점에서 가장 먼 경계점(실제 인접경계)에서.
    if adjacent > 0 and ring_pts:
        ax, ay = (
            max(ring_pts, key=lambda p: (p[0] - (rmx if rmx is not None else mid_lon)) ** 2
                + (p[1] - (rmy if rmy is not None else mid_lat)) ** 2)
        )
        tk = _tick_from_point(ax, ay, adjacent, f"인접이격 {adjacent:g}m")
        if tk:
            segments.append(tk)

    # 도로 접촉 — 필지가 실제로 도로와 맞닿는 '그 선'을 파란색으로 그리고 길이를
    # 라벨로 붙인다. 접촉선 기하(road_contact_geometry)를 그대로 사용한다.
    road_access = diagnosis.get("road_access") or {}
    roads = road_access.get("roads") or []
    rgeom = road_access.get("road_contact_geometry")
    road_color = "#D500F9"  # 자주(마젠타) = 도로 접촉선 (파란 지적 경계선과 구분)
    contact_lines: list[list[list[float]]] = []
    if isinstance(rgeom, dict):
        if rgeom.get("type") == "MultiLineString":
            contact_lines = [ln for ln in rgeom.get("coordinates", []) if len(ln) >= 2]
        elif rgeom.get("type") == "LineString" and len(rgeom.get("coordinates", [])) >= 2:
            contact_lines = [rgeom["coordinates"]]
    if contact_lines:
        for i, line in enumerate(contact_lines):
            length = roads[i].get("contact_length_m") if i < len(roads) else None
            segments.append(
                {
                    "positions": [[float(p[0]), float(p[1])] for p in line],
                    "label": f"도로 접촉 {length:g}m" if length else "도로 접촉",
                    "color": road_color,
                    "width": 9,  # 굵게
                    "onTop": True,  # 지적 경계선(청록) 위 우선순위로 그려 통짜 자주색
                }
            )
    elif roads and roads[0].get("contact_length_m"):
        # 접촉선 기하가 없으면(구버전) 라벨만.
        labels.append(
            {
                "lon": (minlon + maxlon) / 2,
                "lat": minlat - pad_lat * 2.2,
                "text": f"도로 접촉 {roads[0]['contact_length_m']}m",
            }
        )


    # 우수·오수 '개념' 배수로 — 가장 가까운 공공 배수처(도로측구·구거·하천)까지.
    # 사전검토일 뿐이라 파랑으로 얇게 그리고 '개념·현장확인' 라벨을 붙인다.
    # 실제 경로·방류지점은 설계사무소 현장확인·현황측량으로 확정한다.
    drainage = (diagnosis.get("road_access") or {}).get("drainage") or {}
    route = drainage.get("route_geometry")
    if route and route.get("coordinates"):
        coords = [[float(p[0]), float(p[1])] for p in route["coordinates"]]
        outlet = "·".join(drainage.get("outlets") or ["공공 배수처"]).replace("(도로측구)", "")
        segments.append(
            {
                "positions": coords,
                "label": f"우수 방류→{outlet} · 개념(현장확인)",
                "color": "#1E88E5",  # 파랑(물)
                "width": 4,
                "onTop": True,
            }
        )

    # 인접에 공공 배수처가 없어 배수로가 남의 사유지를 지나야 하는 경우 — 가장 가까운
    # 공공 배수처까지의 '개념' 경로를 빨강으로 그리고 통과 사유지를 경고한다. 지목 기준
    # 사전검토이며(소유권은 토지대장·현황측량으로 확정), 건물 유무는 건축물대장 참고치.
    enc = drainage.get("encroachment") or {}
    enc_route = enc.get("route_geometry")
    if enc_route and enc_route.get("coordinates"):
        coords = [[float(p[0]), float(p[1])] for p in enc_route["coordinates"]]
        if enc.get("crosses_private"):
            # 소유구분이 국·공유인 필지는 통과 가능이라 제외하고, 사유(또는 미상)만 경고 대상.
            blocking = [
                h for h in (enc.get("crossed_parcels") or [])
                if h.get("ownership") != "국공유"
            ]
            jimoks = "·".join(dict.fromkeys(h.get("jimok", "") for h in blocking if h.get("jimok")))
            confirmed = any(h.get("ownership") == "사유" for h in blocking)  # 소유구분 확인됨
            has_bldg = any(h.get("has_building") for h in blocking)
            kind = "사유지" if confirmed else "사유추정지"  # 미상이면 지목 proxy
            extra = ", 건물有" if has_bldg else ""
            need = "사실상 우회 필요" if has_bldg else "토지사용승낙/우회 필요"
            tail = need if confirmed else f"{need}(소유구분 확인)"
            dest = (enc.get("outlet") or {}).get("jimok") or "공공 배수처"  # 방류 목적지
            label = f"⚠ 우수 방류→{dest} · {kind}({jimoks}{extra}) 통과 · {tail}"
            color = "#C62828"  # 빨강 = 침범 경고
        else:
            label = "우수 방류→공공용지 통과 · 개념(현장확인)"
            color = "#1E88E5"
        segments.append(
            {"positions": coords, "label": label, "color": color, "width": 4, "onTop": True}
        )

    return {"type": "show_dimensions", "segments": segments, "labels": labels}


# 사용자가 특정 선만 지도에서 보고 싶어 할 때 골라 그리는 라벨 매핑.
# _build_dimensions 가 붙이는 라벨 접두어와 일치해야 한다.
_OVERLAY_LABEL_KINDS = {
    "road": ("도로 접촉",),
    "building_line": ("건축선", "전면이격", "정북일조", "인접이격"),
    "drainage": ("우수 방류",),
    "dimensions": ("가로", "세로", "높이"),  # 필지 가로·세로 + 건물 높이 3축 치수
    "area": ("대지면적", "건축면적"),  # 면적 라벨(세그먼트 아닌 labels 에 있음)
}


def build_lines_only_commands(diagnosis: dict, kinds) -> list[dict]:
    """'선만' 보여주는 최소 지도 명령. 필지로 이동·강조하고 요청한 선(도로접촉/건축선/
    이격/배수로)만 얹는다. 3D 매스·가능여부 팝업·전체 치수선은 내지 않는다.
    선만 묻는 질문(예: '건축선 그려줘', '도로 접촉 있어?') 전용이다.
    """
    base_types = {"clear_mass", "fly_to", "highlight_parcel"}
    cmds = [c for c in build_map_commands(diagnosis) if c.get("type") in base_types]
    overlay = overlay_command(diagnosis, kinds)
    if overlay:
        cmds.append(overlay)
    return cmds


def _may_show_building_dimensions(diagnosis: dict) -> bool:
    """건물 치수선(건축선·이격)을 지도에 그려도 되는지. 불가 판정·확정 용도제한이면
    그리지 않는다(build_map_commands 의 게이팅과 같은 규칙)."""
    verdict = diagnosis.get("verdict", "unknown")
    presentation = (diagnosis.get("regulation") or {}).get("map_presentation") or {}
    use_restriction = diagnosis.get("use_restriction") or {}
    use_is_not_allowed = bool(
        use_restriction and use_restriction.get("kind") != "verification_required"
    )
    show = presentation.get(
        "show_building_dimensions", verdict in {"allowed", "conditional"}
    )
    return bool(show) and not use_is_not_allowed


def overlay_command(diagnosis: dict, kinds) -> dict | None:
    """요청한 종류의 선(도로접촉/건축선·이격/배수로)만 골라 지도에 다시 얹는다.
    카메라·3D 매스는 건드리지 않고 show_dimensions 세그먼트만 돌려준다(없으면 None).
    건축선·이격선은 건축 가능(가능/조건부) 판정일 때만 포함한다.
    """
    kinds = [k for k in (kinds or []) if k in _OVERLAY_LABEL_KINDS]
    if "building_line" in kinds and not _may_show_building_dimensions(diagnosis):
        kinds = [k for k in kinds if k != "building_line"]
    if not kinds:
        return None
    loc = diagnosis.get("location") or {}
    anchor_lon, anchor_lat = loc.get("lon"), loc.get("lat")
    if anchor_lon is None or anchor_lat is None:
        box = _bbox((diagnosis.get("parcel") or {}).get("geometry"))
        if not box:
            return None
        anchor_lon, anchor_lat = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    dims = _build_dimensions(diagnosis, anchor_lon, anchor_lat)
    if not dims:
        return None
    wanted = tuple(p for k in kinds for p in _OVERLAY_LABEL_KINDS[k])
    segs = [
        s for s in dims.get("segments", [])
        if any(p in s.get("label", "") for p in wanted)
    ]
    # 대지면적·건축면적은 세그먼트가 아니라 labels 에 있으므로 함께 필터한다.
    labs = [
        lab for lab in dims.get("labels", [])
        if any(p in lab.get("text", "") for p in wanted)
    ]
    if not segs and not labs:
        return None
    return {"type": "show_dimensions", "segments": segs, "labels": labs}


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
    presentation = regulation.get("map_presentation") or {}
    use_restriction = diagnosis.get("use_restriction") or {}
    use_is_not_allowed = bool(
        use_restriction
        and use_restriction.get("kind") != "verification_required"
    )
    display_verdict = presentation.get("verdict") or verdict
    display_label = presentation.get("label")
    display_color = presentation.get("color") or VERDICT_COLOR[verdict]
    show_building_mass = presentation.get(
        "show_building_mass", verdict in {"allowed", "conditional"}
    )
    show_building_dimensions = presentation.get(
        "show_building_dimensions", verdict in {"allowed", "conditional"}
    )
    # 호출 순서나 구버전 진단 객체와 무관하게, 확정 용도 제한이 있으면 지도에
    # 건물·치수를 절대 내보내지 않는다.
    if use_is_not_allowed:
        display_verdict = "not_allowed"
        display_label = "건축 불가"
        display_color = "#C62828"
        show_building_mass = False
        show_building_dimensions = False

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
                "color": display_color,
            }
        )

    # 3.5) 용도지역 걸침 조각 오버레이 — 필지가 둘 이상 지역에 걸친 경우에만.
    #      조각을 지역별 색으로 깔면 색이 갈리는 곳이 곧 경계선이라, 경계를
    #      따로 그리지 않아도 어디서 규제가 바뀌는지 눈에 보인다.
    #      프론트 우측 범례 창도 이 명령의 pieces 를 그대로 쓴다.
    zone_shares = land_use.get("zone_shares") or []
    pieces = [
        s for s in zone_shares
        if s.get("geometry")
        and float(s.get("share_pct") or 0) > 0
        and float(s.get("area_m2") or 0) > 0
    ]
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

    # 3.6) 생태·자연도·재해위험지구 등 규제 중첩 범례 — overlap 에 조각 지오메트리가
    #      없어 지도에 깔지 않고 우하단 범례(show_restriction_pieces)로만 안내한다.
    #      같은 라벨(등급)은 합쳐 한 줄로 보여준다.
    restriction_pieces: dict[str, dict] = {}
    restriction_titles: list[str] = []
    for layer in (
        regulatory_screen.get("ecological_nature") or {},
        regulatory_screen.get("ecological_separate_management") or {},
        regulatory_screen.get("disaster") or {},
    ):
        overlaps = [
            o for o in (layer.get("overlaps") or [])
            if float(o.get("share_pct") or 0) > 0
        ]
        if overlaps and layer.get("title"):
            restriction_titles.append(layer["title"])
        for ov in overlaps:
            label = ov.get("name") or layer.get("title") or "규제 중첩"
            piece = restriction_pieces.setdefault(
                label,
                {"label": label, "share_pct": 0.0, "area_m2": 0.0,
                 "color": _restriction_color(label)},
            )
            piece["share_pct"] = round(piece["share_pct"] + float(ov.get("share_pct") or 0), 1)
            piece["area_m2"] = round(piece["area_m2"] + float(ov.get("area_m2") or 0), 1)
    if restriction_pieces:
        commands.append(
            {
                "type": "show_restriction_pieces",
                "title": "·".join(dict.fromkeys(restriction_titles)) + " 중첩",
                "note": "환경·재해 중첩 (사전검토 참고용)",
                "pieces": list(restriction_pieces.values()),
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
        and show_building_mass
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
                "color": display_color,
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
    #      단, 건축 불가면 세울 건물이 없으므로 가로·세로·도로접촉·이격 치수선을
    #      그리지 않는다(매스를 안 그리는 것과 동일한 기준).
    if show_building_dimensions:
        dims = _build_dimensions(diagnosis, anchor_lon, anchor_lat)
        if dims:
            commands.append(dims)

    # 경사도 버튼과 본문이 서로 다른 지형값을 표시하지 않도록 서버에서 계산한
    # COP30 DEM 셀·통계를 지도에 전달한다. 이 명령은 즉시 표시하지 않고,
    # 프론트가 경사도 버튼을 켰을 때 같은 셀을 그리는 데이터 갱신 명령이다.
    terrain_analysis = conversion.get("terrain") or {}
    if terrain_analysis.get("status") == "REFERENCE_AVAILABLE":
        commands.append({
            "type": "set_slope_data",
            "source": terrain_analysis.get("source"),
            "resolution_m": terrain_analysis.get("resolution_m"),
            "min_elevation_m": terrain_analysis.get("elevation_min_m"),
            "max_elevation_m": terrain_analysis.get("elevation_max_m"),
            "mean_elevation_m": terrain_analysis.get("elevation_mean_m"),
            "max_slope_deg": terrain_analysis.get("slope_max_deg"),
            "mean_slope_deg": terrain_analysis.get("slope_mean_deg"),
            "cells": terrain_analysis.get("grid_cells") or [],
        })

    # 5) 정보 패널
    #    anchor 는 패널을 매스 꼭대기 위에 띄우기 위한 지도상 기준점이다.
    #    프론트가 이 좌표를 화면 픽셀로 투영해 패널을 붙인다.
    #
    #    height 는 **지면 위 높이**다(프론트에서 지형 표고를 더한다).
    mass_top = (
        mass["mass_height_m"]
        if mass and show_building_mass and not mass.get("exceeds_far_limit")
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
        else display_verdict
    )
    panel_label = (
        "요청값 적용 불가" if limit_exceeded
        else "실질 배치 불가" if layout_infeasible
        else display_label if display_label
        else "개별규제 확인 필요"
        if verdict == "unknown" and regulation.get("reason")
        else VERDICT_LABEL[verdict]
    )
    panel_color = (
        "#C62828"
        if limit_exceeded
        or layout_infeasible
        else display_color
    )

    commands.append(
        {
            "type": "show_panel",
            "anchor": anchor,
            "verdict": panel_verdict,
            "verdict_label": panel_label,
            "color": panel_color,
            "address": location.get("matched_address", ""),
            "pnu": (parcel or {}).get("pnu", ""),
            "zone": regulation.get("zone") or (land_use.get("zones") or [""])[0],
            "districts": land_use.get("districts", []),
            "jimok": (parcel or {}).get("jimok", ""),
            "building_use": (
                "시설물"
                if (diagnosis.get("request") or {}).get("inferred")
                else regulation.get("building_use", "")
            ),
            "site_area_m2": (parcel or {}).get("area_m2"),
            "jiga_won_per_m2": (parcel or {}).get("jiga_won_per_m2"),
            "bcr_max_pct": regulation.get("bcr_max_pct"),
            "far_max_pct": regulation.get("far_max_pct"),
            "legal_basis": regulation.get("legal_basis", ""),
            "constraints": regulation.get("constraints", []),
            "zone_use_overview": regulation.get("zone_use_overview", {}),
            # 계산 자료는 진단 객체에 보존하되, 건축 불가 패널에는 가능 규모나
            # 모델 생성의 근거로 노출하지 않는다.
            "massing": mass if display_verdict != "not_allowed" else None,
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
