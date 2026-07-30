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
        gap_items = [gap["item"] for gap in result["data_gaps"]]
        self.assertIn("산지전용 심사용 경사도·표고", gap_items)
        self.assertIn("입목축적", gap_items)
        self.assertNotIn("입목축적", result["unknowns"])

    async def test_forest_inventory_is_reference_not_confirmed_stock(self):
        result = await assess(
            PARCEL,
            {"category": "forest"},
            inspector([
                {"layer_id": "agricultural_promotion", "title": "농업진흥지역", "status": "CLEAR", "overlaps": []},
                {"layer_id": "forest_class", "title": "산지구분", "status": "CLEAR", "overlaps": []},
                {
                    "layer_id": "forest_inventory",
                    "title": "1:5,000 임상도",
                    "status": "OVERLAP",
                    "overlaps": [{
                        "name": "소나무",
                        "code": "21",
                        "share_pct": 80,
                        "area_m2": 800,
                        "properties": {
                            "FRTP_NM": "침엽수림",
                            "KOFTR_NM": "소나무",
                            "AGCLS_NM": "4영급",
                            "DMCLS_NM": "중경목",
                            "DNST_NM": "밀",
                            "HEIGHT_NM": "임분고 15m 이상 17m 미만",
                            "갱신년도": "2023",
                        },
                    }],
                },
            ]),
        )
        self.assertEqual(result["forest_inventory"][0]["species"], "소나무")
        stock_gap = next(
            gap for gap in result["data_gaps"]
            if gap["item"] == "입목축적 확정값"
        )
        self.assertEqual(stock_gap["status"], "FIELD_SURVEY_REQUIRED")

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

    async def test_zero_percent_boundary_sliver_is_not_an_overlap(self):
        result = await assess(
            PARCEL,
            {"category": "other"},
            inspector([
                {
                    "layer_id": "agricultural_promotion",
                    "title": "농업진흥지역",
                    "status": "CLEAR",
                    "overlaps": [],
                },
                {
                    "layer_id": "forest_class",
                    "title": "산지구분",
                    "status": "OVERLAP",
                    "overlaps": [{
                        "name": "준보전산지",
                        "code": "UFM200",
                        "share_pct": 0.0,
                        "area_m2": 0.2,
                        "geometry": PARCEL,
                    }],
                },
            ]),
        )
        self.assertEqual(result["status"], "CLEAR")
        self.assertEqual(result["forest"]["status"], "CLEAR")
        self.assertEqual(result["forest"]["overlaps"], [])
        self.assertNotIn("준보전산지", result["summary"])


if __name__ == "__main__":
    unittest.main()
