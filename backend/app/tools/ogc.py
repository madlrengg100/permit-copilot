"""신뢰된 공간정보 제공기관을 위한 읽기 전용 OGC WFS/WMS 클라이언트.

WFS는 판정에 사용할 벡터 객체를 가져오고, WMS는 화면 표시용 GetMap URL만
생성한다. 외부 URL은 API 요청에서 받지 않고 서버의 레이어 등록부에만 둔다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

from .vworld import geodesic_area_m2


class OGCError(RuntimeError):
    pass


_ENV_PATTERN = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def _env_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _ENV_PATTERN.match(value)
    return os.getenv(match.group(1), "") if match else value


@dataclass(frozen=True)
class SpatialLayer:
    id: str
    title: str
    provider: str
    type_name: str
    enabled: bool = False
    wfs_url: str = ""
    wms_url: str = ""
    version: str = "2.0.0"
    crs: str = "EPSG:4326"
    property_names: dict[str, str] = field(default_factory=dict)
    source_type: str = "wfs"
    local_path: str = ""
    source_date: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SpatialLayer":
        values = {key: _env_value(value) for key, value in raw.items()}
        values["property_names"] = {
            key: _env_value(value)
            for key, value in (raw.get("property_names") or {}).items()
        }
        return cls(**values)

    @property
    def ready(self) -> bool:
        if not self.enabled:
            return False
        if self.source_type == "local_sqlite":
            return bool(self.local_path and Path(self.local_path).exists())
        return bool(self.wfs_url and self.type_name)


class LayerRegistry:
    def __init__(self, path: str | Path | None = None):
        default = Path(__file__).resolve().parents[1] / "data" / "spatial_layers.json"
        self.path = Path(path or os.getenv("SPATIAL_LAYERS_FILE", "") or default)
        self._layers = self._load()

    def _load(self) -> dict[str, SpatialLayer]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OGCError(f"공간레이어 설정을 읽을 수 없습니다: {exc}") from exc
        layers = [SpatialLayer.from_dict(item) for item in raw.get("layers", [])]
        if len({layer.id for layer in layers}) != len(layers):
            raise OGCError("공간레이어 ID가 중복되었습니다.")
        for layer in layers:
            for endpoint in (layer.wfs_url, layer.wms_url):
                if endpoint:
                    _validate_endpoint(endpoint)
        return {layer.id: layer for layer in layers}

    def list(self) -> list[SpatialLayer]:
        return list(self._layers.values())

    def get(self, layer_id: str) -> SpatialLayer:
        try:
            return self._layers[layer_id]
        except KeyError as exc:
            raise OGCError(f"등록되지 않은 공간레이어입니다: {layer_id}") from exc


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    allow_http = os.getenv("OGC_ALLOW_HTTP", "").lower() in {"1", "true", "yes"}
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        raise OGCError("OGC 엔드포인트는 HTTPS만 허용됩니다.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise OGCError("올바르지 않은 OGC 엔드포인트입니다.")


def _transform_geometry(geometry: dict, source_crs: str, target_crs: str) -> dict:
    if source_crs.upper() == target_crs.upper():
        return geometry
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    return mapping(transform(transformer.transform, shape(geometry)))


def _transform_bbox(
    bbox: tuple[float, float, float, float],
    source_crs: str,
    target_crs: str,
) -> tuple[float, float, float, float]:
    if source_crs.upper() == target_crs.upper():
        return bbox
    west, south, east, north = bbox
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    points = [
        transformer.transform(west, south),
        transformer.transform(west, north),
        transformer.transform(east, south),
        transformer.transform(east, north),
    ]
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


class OGCClient:
    def __init__(self, timeout: float = 15.0, max_features: int = 1000):
        self.timeout = timeout
        self.max_features = max_features

    async def get_features(
        self,
        layer: SpatialLayer,
        bbox_wgs84: tuple[float, float, float, float],
    ) -> list[dict]:
        if not layer.ready:
            raise OGCError(f"공간레이어가 활성화되지 않았습니다: {layer.id}")
        _validate_endpoint(layer.wfs_url)
        bbox = _transform_bbox(bbox_wgs84, "EPSG:4326", layer.crs)
        params = {
            "service": "WFS",
            "version": layer.version,
            "request": "GetFeature",
            ("typeName" if layer.version.startswith("1.") else "typeNames"):
                layer.type_name,
            "srsName": layer.crs,
            "bbox": ",".join(map(str, bbox)) + f",{layer.crs}",
            "outputFormat": "application/json",
            ("maxFeatures" if layer.version.startswith("1.") else "count"):
                str(self.max_features),
        }
        if layer.source_type == "vworld_wfs":
            from ..config import VWORLD_DOMAIN, VWORLD_KEY
            params["key"] = VWORLD_KEY
            params["domain"] = VWORLD_DOMAIN
            # WFS 1.1 + EPSG:4326은 VWorld가 위도,경도 순 BBOX를 요구한다.
            west, south, east, north = bbox_wgs84
            params["bbox"] = (
                f"{south},{west},{north},{east},EPSG:4326"
            )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(layer.wfs_url, params=params)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OGCError(f"{layer.title} WFS 조회에 실패했습니다: {exc}") from exc

        if data.get("type") != "FeatureCollection":
            raise OGCError(
                f"{layer.title} WFS가 GeoJSON FeatureCollection을 반환하지 않았습니다."
            )
        features = data.get("features") or []
        if len(features) >= self.max_features:
            raise OGCError(
                f"{layer.title} 조회 결과가 {self.max_features}건에 도달했습니다. "
                "조회 범위를 줄여야 합니다."
            )
        normalized = []
        for feature in features:
            geometry = feature.get("geometry")
            if not geometry:
                continue
            normalized.append({
                **feature,
                "geometry": _transform_geometry(
                    geometry, layer.crs, "EPSG:4326"
                ),
            })
        return normalized

    def build_wms_url(
        self,
        layer: SpatialLayer,
        bbox: tuple[float, float, float, float],
        width: int = 1024,
        height: int = 768,
    ) -> str:
        if not layer.enabled or not layer.wms_url or not layer.type_name:
            raise OGCError(f"WMS가 활성화되지 않았습니다: {layer.id}")
        _validate_endpoint(layer.wms_url)
        params = {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": layer.type_name,
            "styles": "",
            "srs": "EPSG:4326",
            "bbox": ",".join(map(str, bbox)),
            "width": str(min(max(width, 64), 2048)),
            "height": str(min(max(height, 64), 2048)),
            "format": "image/png",
            "transparent": "true",
        }
        if layer.source_type == "vworld_wfs":
            from ..config import VWORLD_DOMAIN, VWORLD_KEY
            params["key"] = VWORLD_KEY
            params["domain"] = VWORLD_DOMAIN
        separator = "&" if "?" in layer.wms_url else "?"
        return layer.wms_url + separator + urlencode(params)


def calculate_overlaps(
    parcel_geometry: dict,
    features: list[dict],
    layer: SpatialLayer,
) -> dict:
    try:
        parcel = shape(parcel_geometry).buffer(0)
    except Exception as exc:
        raise OGCError("필지 기하정보가 올바르지 않습니다.") from exc
    if parcel.is_empty:
        raise OGCError("필지 기하정보가 비어 있습니다.")

    parcel_area = geodesic_area_m2(mapping(parcel))
    overlaps = []
    name_field = layer.property_names.get("name", "")
    code_field = layer.property_names.get("code", "")
    for feature in features:
        try:
            intersection = parcel.intersection(shape(feature["geometry"]).buffer(0))
        except Exception:
            continue
        if intersection.is_empty:
            continue
        area = geodesic_area_m2(mapping(intersection))
        if area <= 0:
            continue
        props = feature.get("properties") or {}
        overlaps.append({
            "name": props.get(name_field, "") if name_field else "",
            "code": props.get(code_field, "") if code_field else "",
            "area_m2": round(area, 1),
            "share_pct": round(area / parcel_area * 100, 1) if parcel_area else 0,
            "geometry": mapping(intersection),
        })

    overlaps.sort(key=lambda item: -item["area_m2"])
    return {
        "layer_id": layer.id,
        "title": layer.title,
        "provider": layer.provider,
        "status": "OVERLAP" if overlaps else "CLEAR",
        "overlaps": overlaps,
    }


async def inspect_layers(
    parcel_geometry: dict,
    layer_ids: list[str] | None = None,
    registry: LayerRegistry | None = None,
    client: OGCClient | None = None,
) -> list[dict]:
    registry = registry or LayerRegistry()
    client = client or OGCClient()
    selected = (
        [registry.get(layer_id) for layer_id in layer_ids]
        if layer_ids
        else [layer for layer in registry.list() if layer.ready]
    )
    parcel = shape(parcel_geometry)
    bbox = tuple(parcel.bounds)
    results = []
    for layer in selected:
        if not layer.ready:
            results.append({
                "layer_id": layer.id,
                "title": layer.title,
                "provider": layer.provider,
                "status": "NOT_CONFIGURED",
                "overlaps": [],
            })
            continue
        try:
            if layer.source_type == "local_sqlite":
                from .local_spatial import LocalSpatialStore
                features = LocalSpatialStore(layer.local_path).get_features(bbox)
            else:
                features = await client.get_features(layer, bbox)
            results.append(calculate_overlaps(parcel_geometry, features, layer))
        except OGCError as exc:
            results.append({
                "layer_id": layer.id,
                "title": layer.title,
                "provider": layer.provider,
                "status": "UNAVAILABLE",
                "message": str(exc),
                "overlaps": [],
            })
    return results
