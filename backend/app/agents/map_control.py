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

from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform, unary_union

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


# 용도지역 대분류(국토계획법 제36조). 토지이음은 "도시지역 / 제1종일반주거지역"처럼
# 상위·세분을 함께 적는데 연속주제도 WFS 는 세분만 준다. 범례 첫 줄에 상위를 덧붙여
# 토지이음 표기와 눈으로 맞춘다. 판정 수치에는 쓰지 않는다(표기 전용).
_MANAGEMENT_ZONES = {"보전관리지역", "생산관리지역", "계획관리지역"}
_TIER1_NAMES = {"도시지역", "관리지역", "농림지역", "자연환경보전지역"}
_URBAN_SUFFIXES = ("주거지역", "상업지역", "공업지역", "녹지지역")


def zone_tier1(name: str) -> str:
    """세분 용도지역 이름 -> 대분류. 판단 불가면 빈 문자열."""
    name = (name or "").strip()
    if name in _MANAGEMENT_ZONES:
        return "관리지역"
    if name in {"농림지역", "자연환경보전지역"}:
        return name
    if name.endswith(_URBAN_SUFFIXES):
        return "도시지역"
    return ""


def _iter_linestrings(geom):
    from shapely.geometry import GeometryCollection, LineString, MultiLineString
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, (MultiLineString, GeometryCollection)):
        for part in geom.geoms:
            yield from _iter_linestrings(part)


def road_setback_pieces(diagnosis: dict) -> tuple[list[dict], dict | None]:
    """미달도로(지적 추정폭<4m) 접한 변의 '도로 후퇴 편입분'을 지도용 조각 + 라벨/후퇴선으로.

    분할 후 건축물과 함께 '어느 변이 얼마나 도로로 편입되는지'를 실제 접한 변 위에
    그린다(규제 분리의 '분할 제외'와 색·라벨을 달리해 헷갈리지 않게 한다). 접한 변은
    road_contact_geometry(접촉선) 중 그 도로 접촉길이에 가장 가까운 조각으로 특정하고,
    그 선을 안쪽으로 후퇴폭만큼 부풀려 필지와 교차한 띠가 편입분이다. 좌표는 지적 기반
    추정이라 실제 위치·면적은 현황측량 후 확정된다.

    반환: (show_zone_pieces 용 조각 리스트, show_dimensions 명령 또는 None)
    """
    ra = diagnosis.get("road_access") or {}
    roads = ra.get("roads") or []
    parcel_geom = (diagnosis.get("parcel") or {}).get("geometry") or diagnosis.get("geometry")
    rcg = ra.get("road_contact_geometry")
    if not parcel_geom or not rcg:
        return [], None
    sub = [
        r for r in roads
        if (r.get("cadastral_width_estimate_m") or 99) and float(r["cadastral_width_estimate_m"]) < 4
        and float(r.get("contact_length_m") or 0) > 0
    ]
    if not sub:
        return [], None
    try:
        parcel = shape(parcel_geom).buffer(0)
        mls = shape(rcg)
    except Exception:
        return [], None
    parts = list(mls.geoms) if mls.geom_type == "MultiLineString" else [mls]
    if not parts:
        return [], None
    mid_lat = parcel.centroid.y
    m_per_deg_lon = 111320.0 * max(0.1, math.cos(math.radians(mid_lat)))

    def _seg_len_m(ls) -> float:
        cs = list(ls.coords)
        total = 0.0
        for i in range(1, len(cs)):
            dx = (cs[i][0] - cs[i - 1][0]) * m_per_deg_lon
            dy = (cs[i][1] - cs[i - 1][1]) * 111320.0
            total += math.hypot(dx, dy)
        return total

    used: set[int] = set()
    pieces: list[dict] = []
    labels: list[dict] = []
    segments: list[dict] = []
    for r in sub:
        w = float(r["cadastral_width_estimate_m"])
        clen = float(r["contact_length_m"])
        setback = (4 - w) / 2  # 소요너비 4m 확보를 위한 중심선 후퇴폭
        # 이 도로의 접촉선: 아직 안 쓴 조각 중 접촉길이가 가장 가까운 것.
        best_i, best_diff = None, None
        for i, ls in enumerate(parts):
            if i in used:
                continue
            diff = abs(_seg_len_m(ls) - clen)
            if best_diff is None or diff < best_diff:
                best_i, best_diff = i, diff
        if best_i is None:
            continue
        used.add(best_i)
        contact = parts[best_i]
        try:
            origin_x, origin_y = parcel.centroid.x, parcel.centroid.y

            def _to_m(x, y, z=None):
                return ((x - origin_x) * m_per_deg_lon, (y - origin_y) * 111320.0)

            def _to_deg(x, y, z=None):
                return (origin_x + x / m_per_deg_lon, origin_y + y / 111320.0)

            parcel_m = transform(_to_m, parcel)
            contact_m = transform(_to_m, contact)
            strip_m = contact_m.buffer(setback, cap_style=2, join_style=2).intersection(parcel_m).buffer(0)
            strip = transform(_to_deg, strip_m)
        except Exception:
            logger.debug("도로 후퇴 편입 띠 계산 실패", exc_info=True)
            continue
        if strip.is_empty:
            continue
        area = round(clen * setback, 1)
        polys = [strip] if strip.geom_type == "Polygon" else [
            g for g in getattr(strip, "geoms", []) if g.geom_type == "Polygon"
        ]
        for poly in polys:
            # 색은 분할 제외(빨강)·분할 대상(초록)과 확실히 다른 파랑으로 — 같은 계열이
            # 겹치면 구분이 안 됐다.
            pieces.append({
                "zone": "도로 편입(후퇴)", "color": "#1565C0",
                "geometry": mapping(poly), "area_m2": area, "share_pct": None,
            })
        rp = strip.representative_point()
        # 도로후퇴선은 1.2m 길이의 짧은 치수선이 아니다. 현재 도로 경계에서 필지
        # 안쪽으로 1.2m 평행 이동한 선이며, 파란 편입 예정면의 안쪽 긴 경계다.
        # strip 경계에서 현재 접촉선 주변을 제외한 조각 중 가장 긴 것을 후퇴선으로 쓴다.
        try:
            # 접촉선을 좌·우로 정확히 setback만큼 평행 이동한 두 후보 중 파란 편입면
            # 및 필지 안에 놓이는 쪽을 고른다. strip 경계를 통째로 쓰면 양끝 짧은
            # 연결선까지 U자 형태로 붙으므로 평행선 자체만 사용한다.
            offset_parts = []
            for side in ("left", "right"):
                offset = contact_m.parallel_offset(setback, side, join_style=2)
                offset_parts.extend(_iter_linestrings(offset))
            if offset_parts:
                inner = max(
                    offset_parts,
                    key=lambda ls: strip_m.buffer(0.05).intersection(ls).length,
                ).intersection(strip_m.buffer(0.05))
                inner_lines = list(_iter_linestrings(inner))
                inner = max(inner_lines, key=lambda ls: ls.length) if inner_lines else None
            else:
                inner = None
            if inner is not None and not inner.is_empty:
                inner = transform(_to_deg, inner)
                segments.append({
                    "positions": [[float(x), float(y)] for x, y in inner.coords],
                    "label": f"도로후퇴선 {setback:.1f}m",
                    "color": "#7B1FA2", "width": 4, "onTop": True,
                    "kind": "setback",
                })
        except Exception:
            logger.debug("도로후퇴선 계산 실패", exc_info=True)

        # 파란 라벨은 편입되는 '면적'만 설명한다. 후퇴 거리는 위 보라색 거리선이 맡는다.
        labels.append({
            "lon": rp.x, "lat": rp.y,
            "text": f"도로 편입 약 {area:,.0f}㎡",
            "color": "#1565C0",  # 도로 편입 파랑(면 색과 동일)
            "kind": "road_area",
        })
    dims = None
    if labels or segments:
        # persist=True: 분할 오버레이와 같은 지속 레이어에 — 모델을 세워도 남는다.
        dims = {"type": "show_dimensions", "segments": segments, "labels": labels, "persist": True}
    return pieces, dims


def division_dimensions(kept_zone: str, kept_geom: dict, kept_area: float,
                        excluded: list[dict]) -> dict:
    """필지 분할을 '치수선처럼' 보이게 — 면(분할 대상·제외)에는 면적(㎡) 라벨을, 대상과
    제외가 맞닿는 분할 경계선은 빨간 선으로 그리는 show_dimensions 명령을 만든다."""
    labels: list[dict] = []
    segments: list[dict] = []
    kept = shape(kept_geom).buffer(0)
    kc = kept.representative_point()
    # 라벨 배경색을 해당 면 색과 맞춘다 — 어느 면 얘긴지 색으로 바로 읽히게.
    labels.append({
        "lon": kc.x, "lat": kc.y, "text": f"면적 · 분할 대상 {kept_area:,.0f}㎡",
        "color": "#2E7D32",  # 분할 대상 초록
        "kind": "division_area",
    })
    for ex in excluded:
        if not ex.get("geometry"):
            continue
        eg = shape(ex["geometry"]).buffer(0)
        ec = eg.representative_point()
        labels.append({
            "lon": ec.x, "lat": ec.y,
            "text": f"면적 · 분할 제외 {float(ex.get('area_m2') or 0):,.0f}㎡",
            # 반투명 빨강 면이 바탕 지도와 섞여 보이는 실제 황토색에 맞춘다.
            "color": "#B56F43",
            "kind": "division_area",
        })
        try:
            _first = True
            for ls in _iter_linestrings(kept.boundary.intersection(eg.boundary)):
                if ls.length > 0:
                    # 흰 점선 + 가위 표식으로 면 색(빨강)과 시각 문법을 완전히 분리한다.
                    # 라벨은 declutter 로
                    # 근처 '분할 제외 N㎡' 와 안 겹치게 자동 배치된다.
                    segments.append({
                        "positions": [[float(x), float(y)] for x, y in ls.coords],
                        "label": "분할 경계선" if _first else "",
                        "color": "#FFFFFF", "width": 5, "onTop": True,
                        "kind": "division",
                    })
                    _first = False
        except Exception:
            logger.debug("분할 경계선 계산 실패", exc_info=True)
    # persist=True: 분할선·면적 라벨은 지속 레이어에 그려, 이후 모델 배치·이격선
    # 표시(clearDimensions)로도 지워지지 않고 건물 옆에 계속 남는다.
    return {"type": "show_dimensions", "segments": segments, "labels": labels, "persist": True}


def _restriction_color(label: str) -> str:
    """규제 범례 색 = '건축 가부 심각도'(규제 종류가 아니라). 사용자가 색만 보고
    가부를 읽을 수 있게 통일한다: 빨강 = 원칙 건축 불가/강한 제한(허가·예외 확인 전에는
    막힘), 주황 = 조건부(협의·심의·허가로 가능), 회색 = 참고."""
    s = label or ""
    # 판정 배지와 같은 신호등 색으로 통일한다(주황=조건부 #F9A825, 빨강=불가 #C62828).
    RED, ORANGE, GRAY = "#C62828", "#F9A825", "#9E9E9E"
    if "3등급" in s:  # 생태·자연도 3등급은 참고정보
        return GRAY
    # 준보전산지는 이름에 '보전산지'가 들어가지만 법적으로 보전산지가 아니다.
    # 산지전용허가를 거쳐 이용을 검토하는 조건부 항목이므로 빨강으로 분류하지 않는다.
    if "준보전산지" in s:
        return ORANGE
    # 원칙 불가/강한 제한 — 개발제한, 맹지(접도 미충족), 보전산지·농업진흥(전용 제한),
    # 상수원보호, 생태 1등급, 재해위험지구.
    if any(k in s for k in (
        "개발제한", "맹지", "도로 접촉 없음", "보전산지", "상수원",
        "농업진흥", "1등급", "재해", "위험",
    )):
        return RED
    # 그 밖의 지정 규제(수질보전·배출시설제한·문화재·군사·경관·고도·지구단위·가축·
    # 농업보호·특별대책·수변·별도관리·생태 2등급 등)는 협의·허가로 가능한 조건부.
    return ORANGE


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
    diagnosis: dict,
    anchor_lon: float,
    anchor_lat: float,
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

    # 세 축이 정확히 만나는 공통 원점. 가로·세로 선도 이 좌표까지 이어져야
    # 높이선이 별도로 떠 있지 않고 하나의 3축 치수처럼 읽힌다.
    dimension_origin = [minlon - pad_lon, minlat - pad_lat]
    segments = [
        {  # 가로(동서) — 남쪽 변 아래에
            "positions": [dimension_origin, [maxlon, minlat - pad_lat]],
            "label": f"가로 약 {width_m:,.0f}m",
            "kind": "dimension",
        },
        {  # 세로(남북) — 서쪽 변 왼쪽에
            "positions": [dimension_origin, [minlon - pad_lon, maxlat]],
            "label": f"세로 약 {depth_m:,.0f}m",
            "kind": "dimension",
        },
    ]

    labels = []
    mass = diagnosis.get("massing") or {}
    _mass_top = 0.0 if mass.get("exceeds_far_limit") else float(mass.get("mass_height_m") or 0)
    # 최초 결과에는 대지면적만 표시한다. 분할 후에는 분할 면적 라벨과 중복되므로
    # 검은 면적 박스를 숨긴다.
    if not diagnosis.get("assume_divided"):
        top_h = _mass_top + 2
        site_area = float(parcel.get("area_m2") or 0)
        if site_area > 0:
            labels.append({
                "lon": anchor_lon, "lat": anchor_lat, "height": top_h,
                "text": f"대지면적 {site_area / 3.3058:,.0f}평({site_area:,.0f}㎡)",
            })

    # 높이 — 가로·세로 치수선의 공통 원점에서 수직으로 올려 3축을 이룬다.
    if _mass_top > 0:
        _floors = mass.get("floors")
        segments.append(
            {
                "positions": [dimension_origin, dimension_origin],
                "height_m": round(_mass_top, 1),
                "label": (
                    f"높이 약 {_mass_top:,.1f}m"
                    + (f" · {_floors}층" if _floors else "")
                ),
                "kind": "dimension",
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
    # 용도별 건축선은 초기 진단의 임의·포괄 용도를 기본 모델처럼 그리지 않는다.
    # 사용자가 모델 버튼을 선택한 뒤 setback-for-use가 표시한 용도에 대해서만 그린다.
    model_selected = bool(diagnosis.get("active_model_selected"))
    front = float(sc.get("front_setback_m") or 0) if model_selected else 0.0
    adjacent = float(sc.get("adjacent_setback_m") or 0) if model_selected else 0.0
    north = float(sc.get("north_setback_m") or 0) if model_selected else 0.0
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

    # 도로측 경계 찾기. 원본 road_contact_geometry의 모든 좌표를 평균하면 서로 다른
    # 도로(예: 36.5m 주 전면 + 2.3m 보조 접촉)가 섞여 평균점이 실제 경계 밖에 놓인다.
    # 현재 대지(분할 후면 분할 대지) 경계에 실제로 붙는 조각만 남기고 가장 긴 접촉선을
    # 주 전면으로 선택한다. 그 선의 중점과 접선에 직각인 안쪽 단위벡터를 함께 구한다.
    front_edge = "S"
    rmx = rmy = None
    front_inward_m: tuple[float, float] | None = None
    primary_front = None
    road_geom = (diagnosis.get("road_access") or {}).get("road_contact_geometry")
    try:
        parcel_shape = shape(geometry).buffer(0)
        road_shape = shape(road_geom) if road_geom else None
        # 지적/WFS 좌표의 미세 오차만 허용(약 0.75m). 분할 후에는 원본 접촉선 중
        # 새 대지경계와 맞닿는 부분만 이 범위에 남는다.
        boundary_tol = 0.75 / 111320.0
        clipped_lines = []
        if road_shape is not None:
            clipped = road_shape.intersection(parcel_shape.boundary.buffer(boundary_tol))
            for ls in _iter_linestrings(clipped):
                projected = []
                for coord in ls.coords:
                    point = shape({"type": "Point", "coordinates": coord})
                    boundary_point = parcel_shape.boundary.interpolate(
                        parcel_shape.boundary.project(point)
                    )
                    projected.append((float(boundary_point.x), float(boundary_point.y)))
                if len(projected) >= 2:
                    line = LineString(projected)
                    if not line.is_empty and line.length > 0:
                        clipped_lines.append(line)
        if clipped_lines:
            def _metric_length(ls) -> float:
                pts = list(ls.coords)
                return sum(
                    math.hypot(
                        (b[0] - a[0]) * 111320.0 * math.cos(math.radians(mid_lat)),
                        (b[1] - a[1]) * 111320.0,
                    )
                    for a, b in zip(pts, pts[1:])
                )

            # 분할 후에는 원래 주 도로가 분할 제외 조각 바깥에 남아 현재 대지와 직접
            # 겹치지 않을 수 있다. 이때 오른쪽 끝의 짧은 접촉점(0.4m)을 전면으로
            # 오인하지 않고, 원래 최장 도로선과 가장 가까운 분할 경계를 전면으로 쓴다.
            primary = max(clipped_lines, key=_metric_length)
            if diagnosis.get("assume_divided") and road_shape is not None:
                original_parts = [ls for ls in _iter_linestrings(road_shape) if not ls.is_empty]
                if original_parts:
                    original_main = max(original_parts, key=_metric_length)
                    boundary_candidates = []
                    boundary_coords = list(parcel_shape.exterior.coords)
                    for start, end in zip(boundary_coords, boundary_coords[1:]):
                        edge = LineString([start, end])
                        if _metric_length(edge) > 1.0:
                            boundary_candidates.append(edge)
                    if boundary_candidates:
                        primary = min(
                            boundary_candidates,
                            key=lambda edge: (edge.distance(original_main), -_metric_length(edge)),
                        )
            primary_front = primary
            projected = 0.5
            mid_pt = primary.interpolate(projected, normalized=True)
            mid_pt = parcel_shape.boundary.interpolate(parcel_shape.boundary.project(mid_pt))
            before = primary.interpolate(max(0.0, projected - 0.05), normalized=True)
            after = primary.interpolate(min(1.0, projected + 0.05), normalized=True)
            rmx, rmy = float(mid_pt.x), float(mid_pt.y)
            tx = (after.x - before.x) * 111320.0 * math.cos(math.radians(mid_lat))
            ty = (after.y - before.y) * 111320.0
            tangent_len = math.hypot(tx, ty)
            if tangent_len > 1e-6:
                normals = [(-ty / tangent_len, tx / tangent_len), (ty / tangent_len, -tx / tangent_len)]
                # 두 법선 중 0.5m 진행했을 때 현재 대지 안에 놓이는 쪽이 안쪽이다.
                for nx, ny in normals:
                    probe = shape({
                        "type": "Point",
                        "coordinates": [
                            rmx + nx * 0.5 * deg_per_m_lon,
                            rmy + ny * 0.5 * deg_per_m_lat,
                        ],
                    })
                    if parcel_shape.buffer(boundary_tol * 0.1).contains(probe):
                        front_inward_m = (nx, ny)
                        break
                if front_inward_m is None:
                    # 수치오차 폴백: 대지 내부점과 내적이 양수인 법선을 고른다.
                    inside = parcel_shape.representative_point()
                    vx = (inside.x - rmx) * 111320.0 * math.cos(math.radians(mid_lat))
                    vy = (inside.y - rmy) * 111320.0
                    front_inward_m = max(normals, key=lambda n: n[0] * vx + n[1] * vy)
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

    # 대표 인접대지 경계: 꼭짓점은 양쪽 변이 만나는 곳이라 직각 방향이 하나로 정해지지
    # 않는다. 현재 대지 외곽의 각 직선 구간 중 주 도로 전면과 겹치는 구간을 제외하고,
    # 가장 긴 구간의 중점과 안쪽 법선을 사용한다.
    adjacent_anchor: tuple[float, float] | None = None
    adjacent_inward_m: tuple[float, float] | None = None
    try:
        parcel_shape = shape(geometry).buffer(0)
        boundary_tol = 0.75 / 111320.0
        candidates = []
        for a, b in zip(ring_pts, ring_pts[1:]):
            segment = LineString([a, b])
            if segment.is_empty:
                continue
            if primary_front is not None:
                # 도로 전면과 꼭짓점 하나를 공유하는 측면은 정상적인 인접경계다.
                # 단순 distance=0이 아니라 선 길이의 절반 이상이 도로 접촉 띠와
                # 실제로 겹칠 때만 전면 구간으로 제외한다.
                overlap = segment.intersection(primary_front.buffer(boundary_tol)).length
                if overlap >= segment.length * 0.5:
                    continue
            dx = (b[0] - a[0]) * 111320.0 * math.cos(math.radians(mid_lat))
            dy = (b[1] - a[1]) * 111320.0
            length_m = math.hypot(dx, dy)
            if length_m > 0.2:
                candidates.append((length_m, segment, dx, dy))
        if candidates:
            _, side, tx, ty = max(candidates, key=lambda item: item[0])
            mid_pt = side.interpolate(0.5, normalized=True)
            adjacent_anchor = (float(mid_pt.x), float(mid_pt.y))
            tangent_len = math.hypot(tx, ty)
            normals = [(-ty / tangent_len, tx / tangent_len), (ty / tangent_len, -tx / tangent_len)]
            ax0, ay0 = adjacent_anchor
            for nx, ny in normals:
                probe = shape({
                    "type": "Point",
                    "coordinates": [
                        ax0 + nx * 0.5 * deg_per_m_lon,
                        ay0 + ny * 0.5 * deg_per_m_lat,
                    ],
                })
                if parcel_shape.buffer(boundary_tol * 0.1).contains(probe):
                    adjacent_inward_m = (nx, ny)
                    break
            if adjacent_inward_m is None:
                inside = parcel_shape.representative_point()
                vx = (inside.x - ax0) * 111320.0 * math.cos(math.radians(mid_lat))
                vy = (inside.y - ay0) * 111320.0
                adjacent_inward_m = max(normals, key=lambda n: n[0] * vx + n[1] * vy)
    except Exception:
        logger.debug("대표 인접대지 경계 계산 실패", exc_info=True)

    # 이격거리 = '인접대지경계선(지적선) → 건축선' 사이 거리.
    #   · 건축선: 실제 필지 경계를 안쪽으로 이격만큼 오프셋한 선(경계 모양을 따라감).
    #   · 이격 눈금: 주황, 경계선에서 건축선까지 수직으로 이은 선(그 거리 라벨).
    # 건축선은 기본 빨강이지만, 사유지 침범 배수로가 빨강으로 그려질 때는 색이 겹쳐
    # 구분이 안 되므로 건축선을 보라로 바꾼다(같은 화면에 두 빨강 방지).
    _drain = (diagnosis.get("road_access") or {}).get("drainage") or {}
    _enc_red = bool((_drain.get("encroachment") or {}).get("crosses_private"))
    building_line = "#7E57C2" if _enc_red else "#E53935"
    tick_color = "#FF8A00"

    def _composite_building_lines() -> list[list[list[float]]]:
        """경계별 이격 띠를 빼서 전면·인접·정북 기준이 합쳐진 건축 가능선을 만든다.

        필지 전체를 max(전면, 인접)로 buffer(-d)하면 인접면 0.5m에도 전면 1m가
        잘못 적용된다. 각 외곽 세그먼트를 실제 미터 좌표로 옮겨 도로 전면/인접/북측
        거리를 개별 적용한 띠를 만들고, 그 띠의 합집합을 대지에서 제외한다.
        """
        if max(front, adjacent, north) <= 0 or not ring_pts:
            return []
        try:
            parcel_deg = shape(geometry).buffer(0)
            origin_x, origin_y = parcel_deg.centroid.x, parcel_deg.centroid.y
            meters_per_lon = 111320.0 * max(0.1, math.cos(math.radians(mid_lat)))

            def _to_m(x, y, z=None):
                return ((x - origin_x) * meters_per_lon, (y - origin_y) * 111320.0)

            def _to_deg(x, y, z=None):
                return (origin_x + x / meters_per_lon, origin_y + y / 111320.0)

            # 지적 원본의 측량오차 수준 미세 절곡은 이격선에 그대로 증폭시키지 않는다.
            # 0.25m 이내만 정리하고 실제 필지의 큰 꺾임과 오목 형상은 유지한다.
            parcel_m = transform(_to_m, parcel_deg).simplify(0.25, preserve_topology=True)
            front_m = transform(_to_m, primary_front) if primary_front is not None else None
            max_y = parcel_m.bounds[3]
            edge_rules: list[tuple[LineString, float]] = []
            metric_ring = list(
                (parcel_m if parcel_m.geom_type == "Polygon" else max(
                    parcel_m.geoms, key=lambda geom: geom.area
                )).exterior.coords
            )
            for a, b in zip(metric_ring, metric_ring[1:]):
                edge = LineString([a, b])
                if edge.length <= 0.05:
                    continue
                is_front = False
                if front_m is not None:
                    overlap = edge.intersection(front_m.buffer(0.8)).length
                    is_front = overlap >= edge.length * 0.5
                distance_m = front if is_front else adjacent
                # 정북 일조는 북측 경계에 추가되는 더 강한 제한이다. 수평에 가까운
                # 북측 세그먼트의 중점이 최북단 부근이면 기존 이격과 큰 값을 적용한다.
                midpoint = edge.interpolate(0.5, normalized=True)
                dx, dy = b[0] - a[0], b[1] - a[1]
                horizontal = abs(dx) >= abs(dy)
                if north > distance_m and horizontal and max_y - midpoint.y <= 1.0:
                    distance_m = north
                if distance_m > 0:
                    edge_rules.append((edge, distance_m))
            if not edge_rules:
                return []

            # 모든 변에 공통으로 적용되는 최소 이격은 GEOS의 단일 inward buffer로
            # 처리한다. 이후 더 큰 이격이 필요한 전면·정북·인접 변만 거리별로 묶어
            # 추가 차감한다. 세그먼트별 사각 buffer를 합치던 기존 방식은 모서리에서
            # 되접힌 V자·삼각형 자기교차선을 만들었다.
            base_distance = min(distance for _, distance in edge_rules)
            buildable_m = (
                parcel_m.buffer(-base_distance, join_style=2, mitre_limit=2.0)
                if base_distance > 0
                else parcel_m
            )
            extra_by_distance: dict[float, list[LineString]] = {}
            for edge, distance in edge_rules:
                if distance > base_distance + 1e-6:
                    extra_by_distance.setdefault(distance, []).append(edge)
            for distance, grouped_edges in extra_by_distance.items():
                # 연결된 실제 경계를 먼저 하나로 합친 뒤 둥근 끝으로 차감해 거리 전환
                # 지점에서도 틈이나 가시가 생기지 않게 한다.
                boundary_group = unary_union(grouped_edges)
                extra_strip = boundary_group.buffer(
                    distance, cap_style=1, join_style=2
                ).intersection(parcel_m)
                buildable_m = buildable_m.difference(extra_strip)
            buildable_m = buildable_m.buffer(0)
            if buildable_m.is_empty:
                return []
            # 오목하거나 목이 좁은 필지는 경계별 이격 후 유효영역이 여러 조각으로
            # 나뉠 수 있다. 가장 큰 조각 하나만 고르면 화면에 짧은 선 하나만 남아
            # 전체 건축선처럼 오인된다. 면적이 있는 모든 외곽선을 함께 반환한다.
            metric_polygons = (
                [buildable_m]
                if buildable_m.geom_type == "Polygon"
                else [geom for geom in getattr(buildable_m, "geoms", []) if geom.geom_type == "Polygon"]
            )
            metric_polygons = sorted(metric_polygons, key=lambda geom: geom.area, reverse=True)
            largest_area = metric_polygons[0].area if metric_polygons else 0
            # 0.15m 이하의 지적 미세 굴곡은 화면 건축선에서 정리한다. 실제 형상과
            # 분리 영역은 preserve_topology로 유지하고, 측량오차 수준의 잔조각만 뺀다.
            meaningful = [
                poly.simplify(0.15, preserve_topology=True)
                for poly in metric_polygons
                if poly.area >= max(0.5, largest_area * 0.005)
            ]
            return [
                [[float(x), float(y)] for x, y in transform(_to_deg, poly).exterior.coords]
                for poly in meaningful
                if not poly.is_empty and poly.area >= 0.5
            ]
        except Exception:
            logger.debug("복합 건축선 계산 실패", exc_info=True)
            return []

    composite_rings = _composite_building_lines()
    for index, composite_ring in enumerate(composite_rings):
        segments.append({
            "positions": composite_ring,
            # 라벨은 가장 큰 주 건축가능영역에만 한 번 표시한다.
            "label": "복합 건축선" if index == 0 else "",
            "color": building_line,
            "width": 4,
            "kind": "building_line",
        })

    # 이격 눈금: '실제 경계점 → 안쪽(중심 방향)으로 이격만큼' = 경계↔건축선을 잇는다.
    def _tick_from_point(
        px: float, py: float, dist_m: float, label: str,
        inward_m: tuple[float, float] | None = None,
        kind: str = "setback_tick",
    ) -> dict | None:
        dxm, dym = inward_m or (
            (mid_lon - px) * 111320.0 * math.cos(math.radians(mid_lat)),
            (mid_lat - py) * 111320.0,
        )
        d = math.hypot(dxm, dym)
        if d < 1e-6:
            return None
        ex = px + (dxm / d * dist_m) / (111320.0 * max(0.1, math.cos(math.radians(mid_lat))))
        ey = py + (dym / d * dist_m) / 111320.0
        return {"positions": [[px, py], [ex, ey]], "label": label, "color": tick_color, "width": 6, "kind": kind}

    # 전면: 실제 도로측 경계점(도로중점)에서. 없으면 남쪽 변 중점.
    fpx, fpy = (rmx, rmy) if (rmx is not None) else (mid_lon, minlat)
    if front > 0:
        tk = _tick_from_point(
            fpx, fpy, front, f"전면이격 {front:g}m", front_inward_m, "front_setback"
        )
        if tk:
            segments.append(tk)
    # 정북: 최북단 경계점에서 남쪽으로.
    if north > 0 and ring_pts:
        npx, npy = max(ring_pts, key=lambda p: p[1])
        tk = _tick_from_point(npx, npy, north, f"정북일조 {north:g}m", kind="north_setback")
        if tk:
            segments.append(tk)
    # 인접: 주 도로 전면을 제외한 대표 인접경계의 중점에서 그 경계에 직각으로.
    if adjacent > 0 and adjacent_anchor:
        ax, ay = adjacent_anchor
        tk = _tick_from_point(
            ax, ay, adjacent, f"인접이격 {adjacent:g}m", adjacent_inward_m, "adjacent_setback"
        )
        if tk:
            segments.append(tk)

    # 도로 접촉 — 원본 접촉선을 그대로 그리면 분할 전 필지의 선이 분할 후 대지를
    # 지나치거나 끝점이 경계 밖으로 튄다. 현재 표시 중인 대지 경계와 겹치는 부분만
    # 남기고 각 점을 실제 경계에 투영해 그린다.
    road_access = diagnosis.get("road_access") or {}
    roads = road_access.get("roads") or []
    rgeom = road_access.get("road_contact_geometry")
    road_color = "#D500F9"  # 자주(마젠타) = 도로 접촉선 (파란 지적 경계선과 구분)
    contact_lines: list[tuple[list[list[float]], float]] = []
    if isinstance(rgeom, dict):
        try:
            parcel_boundary = shape(geometry).buffer(0).boundary
            tolerance = 0.75 / 111320.0
            clipped = shape(rgeom).intersection(parcel_boundary.buffer(tolerance))
            for line_shape in _iter_linestrings(clipped):
                projected = []
                for point in line_shape.coords:
                    source_point = shape({"type": "Point", "coordinates": point})
                    boundary_point = parcel_boundary.interpolate(
                        parcel_boundary.project(source_point)
                    )
                    projected.append([float(boundary_point.x), float(boundary_point.y)])
                if len(projected) < 2:
                    continue
                length_m = sum(
                    math.hypot(
                        (b[0] - a[0]) * 111320.0 * math.cos(math.radians(mid_lat)),
                        (b[1] - a[1]) * 111320.0,
                    )
                    for a, b in zip(projected, projected[1:])
                )
                if length_m > 0.05:
                    contact_lines.append((projected, length_m))
        except Exception:
            logger.debug("도로 접촉선 현재 대지경계 절단 실패", exc_info=True)
    if contact_lines:
        for line, length in contact_lines:
            segments.append(
                {
                    "positions": line,
                    "label": f"도로 접촉 {length:.1f}m",
                    "color": road_color,
                    "width": 9,  # 굵게
                    "onTop": True,  # 지적 경계선(청록) 위 우선순위로 그려 통짜 자주색
                    "kind": "road_contact",
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
                "kind": "drainage",
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
            {"positions": coords, "label": label, "color": color, "width": 4, "onTop": True, "kind": "drainage"}
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

    # 1) 매스는 항상 먼저 지운다 — 이전 질의 결과가 겹쳐 보이지 않도록.
    #    분할 오버레이(지속 레이어)도 전체 재구성 시점엔 함께 지운다 — 새 필지 진단이나
    #    '분할 전' 복귀에서 이전 분할선·면적이 남지 않도록. 분할 실행은 이 재구성 뒤에
    #    다시 그리므로 살아남는다.
    commands.append({"type": "clear_mass"})
    commands.append({"type": "clear_division_overlay"})

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
    # WFS 에 없고 토지이음(NED 지정목록)에만 있는 용도지역 — 조각 면적이 없어
    # 지도에는 못 깔지만 걸침 판단과 범례 목록에는 넣어야 표기가 토지이음과 맞는다.
    _wfs_zones = {s["zone"] for s in pieces}
    ned_only = []
    for record in (land_use.get("designation_lookup") or {}).get("records") or []:
        if not (record.get("is_zoning") and record.get("active")):
            continue
        zone_name = (record.get("name") or "").strip()
        if not zone_name or zone_name in _wfs_zones or zone_name in _TIER1_NAMES:
            continue
        if zone_name not in ned_only:
            ned_only.append(zone_name)
    # 조각이 하나뿐이어도 토지이음이 다른 용도지역을 '저촉'으로 적으면 걸침으로 본다.
    # 조각이 0개면 지도도 색도 만들 수 없으므로 최소 1개는 있어야 한다.
    if pieces and len(pieces) + len(ned_only) >= 2:
        piece_colors = _piece_colors([s["zone"] for s in pieces])
        piece_cmds = [
            {
                "zone": s["zone"],
                "share_pct": s["share_pct"],
                "area_m2": s["area_m2"],
                "color": piece_colors[i],
                "geometry": s["geometry"],
            }
            for i, s in enumerate(pieces)
        ]
        # 범례 첫 줄 = 대분류(도시지역·관리지역 …). 지도 조각이 아니라 표기용이므로
        # pieces 가 아니라 legend_items 로만 보낸다(지도에 덧칠하지 않는다).
        legend_items = []
        tier1 = []
        for zone_name in [s["zone"] for s in pieces] + ned_only:
            t = zone_tier1(zone_name)
            if t and t not in tier1:
                tier1.append(t)
        if tier1:
            legend_items.append({
                "label": " · ".join(tier1), "symbol": "tier", "color": "",
                "note": "국토계획법 제36조 용도지역 대분류 — 토지이음도 이 상위 구분을 함께 표기한다",
            })
        for piece in piece_cmds:
            legend_items.append({
                "label": piece["zone"], "color": piece["color"], "symbol": "area",
                "share_pct": piece["share_pct"], "area_m2": piece["area_m2"],
            })
        # 연속주제도 WFS 는 면적으로 조각을 만들어 아주 작은 조각이 떨어져 나간다.
        # 토지이음(NED 지정목록)은 면적과 무관하게 '저촉'으로 적으므로, WFS 에 없는
        # 용도지역만 목록에 덧붙여 표기를 맞춘다. 지도 조각은 만들지 않는다.
        for zone_name in ned_only:
            legend_items.append({
                "label": f"{zone_name} · 저촉(면적 미미)", "color": "", "symbol": "tier",
                "note": "토지이음 지정목록에는 있으나 연속주제도상 조각 면적이 없어 지도에는 표시되지 않는다",
            })
        commands.append(
            {
                "type": "show_zone_pieces",
                "pieces": piece_cmds,
                "legend_items": legend_items,
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

    # 건축 불가·제약 사유도 같은 범례에 함께 보여준다(용도지역 걸침·환경중첩처럼 한눈에):
    # 농업보호구역/농업진흥지역·보전산지 중첩(전용 제한), 도로 접촉 없음(맹지). 데이터는
    # 진단이 이미 만든 것(land_conversion·road_access)을 그대로 조각으로 옮길 뿐이다.
    constraint_shown = False
    for layer in (conversion.get("agriculture") or {}, conversion.get("forest") or {}):
        for ov in (layer.get("overlaps") or []):
            if float(ov.get("share_pct") or 0) <= 0:
                continue
            label = (
                (ov.get("properties") or {}).get("uname")
                or ov.get("name") or layer.get("title") or "전용 제한"
            )
            piece = restriction_pieces.setdefault(
                label,
                {"label": label, "share_pct": 0.0, "area_m2": 0.0,
                 "color": _restriction_color(label)},
            )
            piece["share_pct"] = round(piece["share_pct"] + float(ov.get("share_pct") or 0), 1)
            piece["area_m2"] = round(piece["area_m2"] + float(ov.get("area_m2") or 0), 1)
            constraint_shown = True
    # 맹지(도로 접촉 없음)는 면적 비율이 아닌 '조건'이라 share 없이 라벨만 낸다.
    if road_access.get("status") in {"NO_CADASTRAL_ROAD", "NO_ROAD"}:
        label = "도로 접촉 없음(맹지 가능성)"
        restriction_pieces.setdefault(
            label,
            {"label": label, "share_pct": None, "area_m2": None,
             "color": _restriction_color(label)},
        )
        constraint_shown = True

    # 토지이용계획(getLandUseAttr)이 잡아낸 용도지구·구역 규제(개발제한·군사·경관·고도지구·
    # 지구단위계획·문화재 등)도 같은 범례에 라벨로 얹는다. zoning._match_constraints 가
    # 이미 만든 것(regulation.constraints)을 그대로 쓴다 — 여기서 새 규칙을 만들지 않는다.
    # 면적 조각·지오메트리가 없어 share 없이 이름+심의/협의 성격만 색으로 구분한다.
    for con in (regulation.get("constraints") or []):
        label = str(con.get("name") or "").strip()
        if not label:
            continue
        restriction_pieces.setdefault(
            label,
            {"label": label, "share_pct": None, "area_m2": None,
             "note": con.get("note"), "color": _restriction_color(label)},
        )
        constraint_shown = True

    if restriction_pieces:
        commands.append(
            {
                "type": "show_restriction_pieces",
                "title": (
                    "규제 중첩·건축 제약" if constraint_shown
                    else "·".join(dict.fromkeys(restriction_titles)) + " 중첩"
                ),
                "note": (
                    "이 필지에 지정된 규제입니다 (사전검토용)" if constraint_shown
                    else "환경·재해 중첩 (사전검토 참고용)"
                ),
                # 색이 무슨 뜻인지 범례에 키로 보여준다(색=건축 가부 심각도).
                "color_key": (
                    [
                        {"color": "#C62828", "label": "원칙 불가"},
                        {"color": "#F9A825", "label": "조건부(협의·허가)"},
                    ]
                    if constraint_shown
                    else [
                        {"color": "#C62828", "label": "1등급(보전 최상)"},
                        {"color": "#F9A825", "label": "2등급·조건부"},
                        {"color": "#9E9E9E", "label": "3등급 참고"},
                    ]
                ),
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
            # 프런트가 '현재 분석 중인 필지'로 선택 상태를 맞추는 데 쓴다. 주소를
            # 입력해 다른 필지를 진단했는데 프런트가 옛 선택을 계속 보내면, 다음
            # 턴에서 그 값이 새 지도 클릭처럼 해석돼 필지가 되돌아간다.
            "lon": location.get("lon"),
            "lat": location.get("lat"),
            "zone": regulation.get("zone") or (land_use.get("zones") or [""])[0],
            "districts": land_use.get("districts", []),
            "jimok": (parcel or {}).get("jimok", ""),
            # 팝업 '검토 용도' — 사용자가 특정 시설(움막·농막·태양광 등)을 콕 집어
            # 물었으면 질의 해석 결과(requested_facility)를 그대로 보여준다. 없으면
            # 포괄 질문은 시설물, 그 외는 판정용 표준 용도.
            "building_use": (
                ((diagnosis.get("request") or {}).get("requested_facility") or "").strip()
                or (
                    "시설물"
                    if (diagnosis.get("request") or {}).get("inferred")
                    else regulation.get("building_use", "")
                )
            ),
            "site_area_m2": (parcel or {}).get("area_m2"),
            "jiga_won_per_m2": (parcel or {}).get("jiga_won_per_m2"),
            "bcr_max_pct": regulation.get("bcr_max_pct"),
            "far_max_pct": regulation.get("far_max_pct"),
            "legal_basis": regulation.get("legal_basis", ""),
            "constraints": regulation.get("constraints", []),
            "zone_use_overview": regulation.get("zone_use_overview", {}),
            # 계산 자료는 진단 객체에 보존하되, 건축 불가 패널이나 매스를 숨기는 표현
            # (no_building_model 등 show_building_mass=False)에는 가능 규모를 노출하지 않는다.
            "massing": (
                mass
                if display_verdict != "not_allowed" and show_building_mass
                else None
            ),
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
