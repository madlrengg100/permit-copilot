"""대용량 로컬 벡터를 SQLite RTree로 조회한다."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pyproj import Transformer
from shapely import wkb
from shapely.geometry import box
from shapely.ops import transform

from .ogc import OGCError


class LocalSpatialStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def get_features(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        if not self.path.exists():
            raise OGCError(f"로컬 공간DB가 없습니다: {self.path}")
        west, south, east, north = bbox
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            try:
                metadata = dict(db.execute(
                    "SELECT key, value FROM metadata"
                ).fetchall())
            except sqlite3.OperationalError:
                metadata = {}
            source_crs = metadata.get("crs", "EPSG:4326")
            transformer_to_source = None
            transformer_to_wgs84 = None
            if source_crs.upper() != "EPSG:4326":
                transformer_to_source = Transformer.from_crs(
                    "EPSG:4326", source_crs, always_xy=True
                )
                transformer_to_wgs84 = Transformer.from_crs(
                    source_crs, "EPSG:4326", always_xy=True
                )
                west, south, east, north = transform(
                    transformer_to_source.transform,
                    box(west, south, east, north),
                ).bounds
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(features)").fetchall()
            }
            has_zone_columns = {"zone_name", "zone_code"}.issubset(columns)
            select = (
                "f.geom_wkb, f.properties, f.zone_name, f.zone_code"
                if has_zone_columns
                else "f.geom_wkb, f.properties"
            )
            rows = db.execute(
                f"""
                SELECT {select}
                FROM feature_bounds b
                JOIN features f ON f.id = b.id
                WHERE b.minx <= ? AND b.maxx >= ?
                  AND b.miny <= ? AND b.maxy >= ?
                """,
                (east, west, north, south),
            ).fetchall()
        features = []
        for row in rows:
            geom_wkb, properties = row[:2]
            props = json.loads(properties)
            if has_zone_columns:
                props["name"] = row[2]
                props["code"] = row[3]
            geom = wkb.loads(bytes(geom_wkb))
            if transformer_to_wgs84 is not None:
                geom = transform(transformer_to_wgs84.transform, geom)
            features.append({
                "type": "Feature",
                "geometry": geom.__geo_interface__,
                "properties": props,
            })
        return features

    def metadata(self) -> dict:
        if not self.path.exists():
            return {}
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            try:
                return dict(db.execute("SELECT key, value FROM metadata").fetchall())
            except sqlite3.OperationalError:
                return {}
