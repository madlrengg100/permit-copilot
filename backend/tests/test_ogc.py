import json
import os
import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.tools.ogc import (
    LayerRegistry,
    OGCClient,
    OGCError,
    SpatialLayer,
    calculate_overlaps,
    inspect_layers,
)
from app.tools.local_spatial import LocalSpatialStore
from shapely import wkb
from shapely.geometry import shape


PARCEL = {
    "type": "Polygon",
    "coordinates": [[
        [127.0, 37.0],
        [127.001, 37.0],
        [127.001, 37.001],
        [127.0, 37.001],
        [127.0, 37.0],
    ]],
}


class StubClient:
    async def get_features(self, layer, bbox):
        return [{
            "type": "Feature",
            "properties": {"name": "농업진흥구역", "code": "A01"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [127.0005, 37.0],
                    [127.0015, 37.0],
                    [127.0015, 37.001],
                    [127.0005, 37.001],
                    [127.0005, 37.0],
                ]],
            },
        }]


class OGCTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.layer = SpatialLayer(
            id="agri",
            title="농업진흥지역",
            provider="test",
            type_name="test:agri",
            enabled=True,
            wfs_url="https://example.com/wfs",
            wms_url="https://example.com/wms",
            property_names={"name": "name", "code": "code"},
        )

    def test_calculate_overlap(self):
        result = calculate_overlaps(
            PARCEL,
            [{
                "type": "Feature",
                "properties": {"name": "농업진흥구역", "code": "A01"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [127.0005, 37.0],
                        [127.0015, 37.0],
                        [127.0015, 37.001],
                        [127.0005, 37.001],
                        [127.0005, 37.0],
                    ]],
                },
            }],
            self.layer,
        )
        self.assertEqual(result["status"], "OVERLAP")
        self.assertEqual(result["overlaps"][0]["name"], "농업진흥구역")
        self.assertAlmostEqual(result["overlaps"][0]["share_pct"], 50.0, delta=0.2)

    def test_wms_url_is_bounded_and_transparent(self):
        url = OGCClient().build_wms_url(
            self.layer, (127.0, 37.0, 127.01, 37.01), 9999, 1
        )
        self.assertIn("width=2048", url)
        self.assertIn("height=64", url)
        self.assertIn("transparent=true", url)

    def test_http_endpoint_is_rejected(self):
        bad = SpatialLayer(
            id="bad",
            title="bad",
            provider="test",
            type_name="bad",
            enabled=True,
            wfs_url="http://example.com/wfs",
            wms_url="http://example.com/wms",
        )
        with self.assertRaises(OGCError):
            OGCClient().build_wms_url(bad, (0, 0, 1, 1))

    async def test_inspect_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layers.json"
            path.write_text(json.dumps({"layers": [{
                "id": self.layer.id,
                "title": self.layer.title,
                "provider": self.layer.provider,
                "type_name": self.layer.type_name,
                "enabled": True,
                "wfs_url": self.layer.wfs_url,
                "wms_url": self.layer.wms_url,
                "property_names": self.layer.property_names,
            }]}), encoding="utf-8")
            results = await inspect_layers(
                PARCEL,
                ["agri"],
                registry=LayerRegistry(path),
                client=StubClient(),
            )
        self.assertEqual(results[0]["status"], "OVERLAP")
        self.assertAlmostEqual(
            results[0]["overlaps"][0]["share_pct"], 50.0, delta=0.2
        )

    async def test_unconfigured_layer_reports_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layers.json"
            path.write_text(json.dumps({"layers": [{
                "id": "forest",
                "title": "산지구분",
                "provider": "test",
                "type_name": "",
                "enabled": False,
                "wfs_url": "",
                "wms_url": "",
            }]}), encoding="utf-8")
            results = await inspect_layers(
                PARCEL, ["forest"], registry=LayerRegistry(path)
            )
        self.assertEqual(results[0]["status"], "NOT_CONFIGURED")

    def test_local_spatial_rtree_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.sqlite"
            geom = shape(PARCEL)
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE features (
                        id INTEGER PRIMARY KEY, region TEXT, zone_name TEXT,
                        zone_code TEXT, properties TEXT, geom_wkb BLOB
                    );
                    CREATE VIRTUAL TABLE feature_bounds USING rtree(
                        id, minx, maxx, miny, maxy
                    );
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
                """)
                db.execute(
                    "INSERT INTO features VALUES (1,'서울','보전산지','UFM',?,?)",
                    (json.dumps({"name": "보전산지"}), wkb.dumps(geom)),
                )
                minx, miny, maxx, maxy = geom.bounds
                db.execute(
                    "INSERT INTO feature_bounds VALUES (?,?,?,?,?)",
                    (1, minx, maxx, miny, maxy),
                )
                db.commit()
            features = LocalSpatialStore(str(path)).get_features(
                (127.0002, 37.0002, 127.0003, 37.0003)
            )
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["name"], "보전산지")


if __name__ == "__main__":
    unittest.main()
