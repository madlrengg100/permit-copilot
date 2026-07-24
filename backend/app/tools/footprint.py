"""필지 경계를 벗어나지 않는 개념 건축면적 형상 생성."""

from __future__ import annotations

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.ops import transform


def _largest_polygon(geometry):
    """Polygon/MultiPolygon/GeometryCollection에서 가장 큰 폴리곤을 고른다."""
    if geometry.geom_type == "Polygon":
        return geometry
    polygons = [part for part in geometry.geoms if part.geom_type == "Polygon"]
    return max(polygons, key=lambda polygon: polygon.area) if polygons else None


def inset_for_area(geojson: dict, area_ratio: float) -> dict | None:
    """필지 내부에서 목표 면적비를 갖는 음수 버퍼 폴리곤을 반환한다.

    경위도 좌표를 필지 중심의 미터 좌표계(AEQD)로 투영한 뒤 음수 buffer를
    적용한다. 단순 중심축소와 달리 결과는 원래 필지의 부분집합이므로 오목한
    필지에서도 경계를 벗어나지 않는다.
    """
    source = make_valid(shape(geojson))
    source = _largest_polygon(source)
    if source is None or source.is_empty:
        return None

    ratio = max(0.001, min(1.0, float(area_ratio)))
    center = source.representative_point()
    local_crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
    inverse = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
    parcel = transform(forward, source)
    target_area = parcel.area * ratio

    if ratio >= 0.999999:
        result = parcel
    else:
        # 충분히 큰 상한을 찾은 뒤, 면적이 target에 가까워지는 이격거리를 탐색한다.
        low = 0.0
        high = max(parcel.bounds[2] - parcel.bounds[0], parcel.bounds[3] - parcel.bounds[1])
        best = parcel
        for _ in range(48):
            distance = (low + high) / 2
            candidate = _largest_polygon(parcel.buffer(-distance, join_style="mitre"))
            area = candidate.area if candidate is not None and not candidate.is_empty else 0.0
            if area >= target_area:
                best = candidate
                low = distance
            else:
                high = distance
        result = best

    if result is None or result.is_empty:
        return None
    contained = result.intersection(parcel)
    contained = _largest_polygon(contained)
    if contained is None or contained.is_empty:
        return None
    return mapping(transform(inverse, contained))
