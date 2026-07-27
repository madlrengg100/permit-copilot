import unittest

from app.tools.land_conversion import assess


PARCEL = {
    "type": "Polygon",
    "coordinates": [[[127, 37], [127.01, 37], [127.01, 37.01], [127, 37.01], [127, 37]]],
}


def inspector(results):
    async def inspect(_geometry, _layers):
        return results
    return inspect


class LandConversionTest(unittest.IsolatedAsyncioTestCase):
    async def test_farmland_promotion_overlap_is_restricted_review(self):
        result = await assess(
            PARCEL,
            {"category": "farmland"},
            inspector([
                {
                    "layer_id": "agricultural_promotion",
                    "title": "농업진흥지역",
                    "status": "OVERLAP",
                    "overlaps": [{
                        "name": "농업진흥구역",
                        "code": "UEA110",
                        "share_pct": 72.4,
                        "area_m2": 724,
                        "geometry": PARCEL,
                    }],
                },
                {"layer_id": "forest_class", "title": "산지구분", "status": "CLEAR", "overlaps": []},
            ]),
        )
        self.assertEqual(result["status"], "RESTRICTED_REVIEW")
        self.assertIn("72.4%", result["summary"])
        self.assertNotIn("geometry", result["agriculture"]["overlaps"][0])

    async def test_semi_conservation_forest_requires_permit(self):
        result = await assess(
            PARCEL,
            {"category": "forest"},
            inspector([
                {"layer_id": "agricultural_promotion", "title": "농업진흥지역", "status": "CLEAR", "overlaps": []},
                {
                    "layer_id": "forest_class",
                    "title": "산지구분",
                    "status": "OVERLAP",
                    "overlaps": [{
                        "name": "준보전산지",
                        "code": "UFM200",
                        "share_pct": 100,
                        "area_m2": 1000,
                    }],
                },
            ]),
        )
        self.assertEqual(result["status"], "PERMIT_REQUIRED")
        self.assertIn("입목축적", result["unknowns"])

    async def test_failed_lookup_is_not_clear(self):
        result = await assess(
            PARCEL,
            {"category": "farmland"},
            inspector([
                {"layer_id": "agricultural_promotion", "title": "농업진흥지역", "status": "UNAVAILABLE", "overlaps": []},
                {"layer_id": "forest_class", "title": "산지구분", "status": "CLEAR", "overlaps": []},
            ]),
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("확정할 수 없습니다", result["summary"])

    async def test_agriculture_code_has_readable_name(self):
        result = await assess(
            PARCEL,
            {"category": "farmland"},
            inspector([
                {
                    "layer_id": "agricultural_promotion",
                    "title": "농업진흥지역",
                    "status": "OVERLAP",
                    "overlaps": [{"name": None, "code": "UEA120", "share_pct": 100}],
                },
                {"layer_id": "forest_class", "title": "산지구분", "status": "CLEAR", "overlaps": []},
            ]),
        )
        self.assertIn("농업보호구역", result["summary"])


if __name__ == "__main__":
    unittest.main()
