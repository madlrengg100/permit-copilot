import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.tools.terrain import analyze_terrain


class TerrainAnalysisTest(unittest.TestCase):
    def test_dem_returns_elevation_and_slope_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dem.tif"
            values = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=10,
                height=10,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(127.0, 37.01, 0.001, 0.001),
            ) as target:
                target.write(values, 1)

            parcel = {
                "type": "Polygon",
                "coordinates": [[
                    [127.002, 37.002],
                    [127.008, 37.002],
                    [127.008, 37.008],
                    [127.002, 37.008],
                    [127.002, 37.002],
                ]],
            }
            result = analyze_terrain(parcel, str(path))

        self.assertEqual(result["status"], "REFERENCE_AVAILABLE")
        self.assertGreater(result["elevation_max_m"], result["elevation_min_m"])
        self.assertGreater(result["slope_mean_deg"], 0)
        self.assertGreater(len(result["grid_cells"]), 0)
        self.assertIn("geometry", result["grid_cells"][0])
        self.assertIn("slope_deg", result["grid_cells"][0])
        self.assertEqual(
            round(sum(item["share_pct"] for item in result["slope_bands"]), 1),
            100.0,
        )


if __name__ == "__main__":
    unittest.main()
