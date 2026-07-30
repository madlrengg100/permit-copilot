import unittest
from unittest.mock import AsyncMock, patch

from app.tools.zoning import apply_straddling_limits
from app.tools import vworld


class StraddlingLimitsTest(unittest.TestCase):
    def test_gyeongsan_weighted_limits_use_actual_piece_areas(self):
        result = apply_straddling_limits(
            {
                "bcr_max_pct": 60,
                "far_max_pct": 250,
                "legal_basis": "경산시 도시계획 조례",
            },
            [
                {"zone": "제2종일반주거지역", "area_m2": 647},
                {"zone": "자연녹지지역", "area_m2": 62},
            ],
            "경상북도 경산시",
        )

        self.assertEqual(result["bcr_max_pct"], 56.5)
        self.assertEqual(result["far_max_pct"], 236.9)
        self.assertTrue(result["weighted_limits"]["applied"])

    def test_piece_over_330_does_not_use_whole_site_weighted_limit(self):
        original = {
            "bcr_max_pct": 60,
            "far_max_pct": 250,
            "legal_basis": "경산시 도시계획 조례",
        }
        result = apply_straddling_limits(
            original,
            [
                {"zone": "제2종일반주거지역", "area_m2": 647},
                {"zone": "자연녹지지역", "area_m2": 331},
            ],
            "경상북도 경산시",
        )

        self.assertNotIn("weighted_limits", result)
        self.assertEqual(result["bcr_max_pct"], 60)


class ZoneShareOverlapTest(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_zone_features_cannot_exceed_whole_parcel(self):
        parcel = {
            "type": "Polygon",
            "coordinates": [[
                [126.97, 36.84],
                [126.98, 36.84],
                [126.98, 36.85],
                [126.97, 36.85],
                [126.97, 36.84],
            ]],
        }
        duplicated_response = {
            "response": {
                "result": {
                    "featureCollection": {
                        "features": [
                            {
                                "properties": {"uname": "계획관리지역"},
                                "geometry": parcel,
                            },
                            {
                                "properties": {"uname": "계획관리지역"},
                                "geometry": parcel,
                            },
                        ]
                    }
                }
            }
        }
        vworld.get_zone_shares.cache_clear()
        with (
            patch.object(vworld, "USE_MOCK", False),
            patch.object(vworld, "LAYERS_ZONING", ["layer"]),
            patch.object(vworld, "_get", AsyncMock(return_value=duplicated_response)),
        ):
            shares = await vworld.get_zone_shares(parcel)

        self.assertEqual(len(shares), 1)
        self.assertLessEqual(shares[0]["share_pct"], 100.0)
        self.assertEqual(shares[0]["share_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
