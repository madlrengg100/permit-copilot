import unittest

from app.tools.landuse import _parse_landuse_payload


class LandUseDesignationTest(unittest.TestCase):
    def test_only_included_or_conflicting_records_are_active(self):
        result = _parse_landuse_payload({
            "landUses": {
                "field": [
                    {
                        "prposAreaDstrcCodeNm": "준보전산지",
                        "prposAreaDstrcCode": "UFM200",
                        "cnflcAt": "3",
                        "cnflcAtNm": "접함",
                        "lastUpdtDt": "2026-06-07",
                    },
                    {
                        "prposAreaDstrcCodeNm": "가축사육제한구역",
                        "prposAreaDstrcCode": "UMZ100",
                        "cnflcAt": "1",
                        "cnflcAtNm": "포함",
                        "lastUpdtDt": "2026-06-07",
                    },
                    {
                        "prposAreaDstrcCodeNm": "상수원보호구역",
                        "prposAreaDstrcCode": "UEA100",
                        "cnflcAt": "2",
                        "cnflcAtNm": "저촉",
                        "lastUpdtDt": "2026-06-07",
                    },
                    {
                        "prposAreaDstrcCodeNm": "계획관리지역",
                        "prposAreaDstrcCode": "UQB100",
                        "cnflcAt": "1",
                        "cnflcAtNm": "포함",
                        "lastUpdtDt": "2026-06-07",
                    },
                ]
            }
        })

        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(
            [record["name"] for record in result["active_records"]],
            ["가축사육제한구역", "상수원보호구역", "계획관리지역"],
        )
        self.assertFalse(result["records"][0]["active"])
        self.assertTrue(result["active_records"][-1]["is_zoning"])
        self.assertEqual(result["updated_at"], "2026-06-07")

    def test_region_named_regulation_is_not_dropped_as_zoning(self):
        result = _parse_landuse_payload({
            "landUses": {
                "field": {
                    "prposAreaDstrcCodeNm": "생태·경관보전지역",
                    "prposAreaDstrcCode": "UEC100",
                    "cnflcAt": "1",
                    "cnflcAtNm": "포함",
                }
            }
        })

        self.assertFalse(result["active_records"][0]["is_zoning"])


if __name__ == "__main__":
    unittest.main()
