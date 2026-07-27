"""대용량 로컬 벡터를 SQLite RTree로 조회한다."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from shapely import wkb

from .ogc import OGCError


class LocalSpatialStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def get_features(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        if not self.path.exists():
            raise OGCError(f"로컬 공간DB가 없습니다: {self.path}")
        west, south, east, north = bbox
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as db:
            rows = db.execute(
                """
                SELECT f.geom_wkb, f.properties, f.zone_name, f.zone_code
                FROM feature_bounds b
                JOIN features f ON f.id = b.id
                WHERE b.minx <= ? AND b.maxx >= ?
                  AND b.miny <= ? AND b.maxy >= ?
                """,
                (east, west, north, south),
            ).fetchall()
        features = []
        for geom_wkb, properties, zone_name, zone_code in rows:
            props = json.loads(properties)
            props["name"] = zone_name
            props["code"] = zone_code
            features.append({
                "type": "Feature",
                "geometry": wkb.loads(bytes(geom_wkb)).__geo_interface__,
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
