import unittest

from app.agents.prediagnosis import format_diagnosis_answer
from app.tools.regulatory_screen import assess

PARCEL = {
    "type": "Polygon",
    "coordinates": [[[127, 37], [127.01, 37], [127.01, 37.01], [127, 37.01], [127, 37]]],
}


class RegulatoryScreenTest(unittest.IsolatedAsyncioTestCase):
    def test_clear_screen_is_still_printed_in_diagnosis(self):
        answer = format_diagnosis_answer({
            "regulation": {
                "verdict": "conditional",
                "zone": "계획관리지역",
                "building_use": "창고시설",
            },
            "regulatory_screen": {
                "status": "CLEAR",
                "findings": [],
                "unknowns": [],
                "summary": "토지이용계획상 재해·환경·국가유산 관련 규제 없음",
            },
        })

        self.assertIn("재해·환경·국가유산", answer)
        self.assertIn("관련 규제 없음", answer)

    async def test_ecological_grades_and_separate_management_are_calculated(self):
        async def inspect(*_args):
            return [
                {
                    "layer_id": "disaster_risk_zone",
                    "status": "CLEAR",
                    "overlaps": [],
                },
                {
                    "layer_id": "ecological_nature",
                    "status": "OVERLAP",
                    "overlaps": [
                        {
                            "name": "생태·자연도 1등급",
                            "code": "1",
                            "share_pct": 30.0,
                            "area_m2": 300,
                            "geometry": PARCEL,
                        },
                        {
                            "name": "생태·자연도 3등급",
                            "code": "3",
                            "share_pct": 70.0,
                            "area_m2": 700,
                            "geometry": PARCEL,
                        },
                    ],
                },
                {
                    "layer_id": "ecological_separate_management",
                    "status": "OVERLAP",
                    "overlaps": [{
                        "name": "습지보호: 한강하구",
                        "code": "SPECIAL",
                        "share_pct": 10.0,
                        "area_m2": 100,
                        "geometry": PARCEL,
                    }],
                },
            ]

        result = await assess(PARCEL, [], inspect)
        self.assertNotIn("생태·자연도 등급", result["unknowns"])
        self.assertEqual(
            [item["code"] for item in result["ecological_nature"]["overlaps"]],
            ["1", "3"],
        )
        self.assertNotIn(
            "geometry",
            result["ecological_separate_management"]["overlaps"][0],
        )
        self.assertTrue(any(
            item["label"] == "생태·자연도 1등급"
            and item["severity"] == "REVIEW"
            for item in result["findings"]
        ))

    async def test_disaster_overlap_and_heritage_are_findings(self):
        async def inspect(*_args):
            return [{
                "layer_id": "disaster_risk_zone",
                "title": "재해위험지구",
                "status": "OVERLAP",
                "overlaps": [{
                    "name": "침수위험지구", "code": "UQ129",
                    "share_pct": 25.0, "geometry": PARCEL,
                }],
            }]
        result = await assess(PARCEL, ["문화재보호구역"], inspect)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(len(result["findings"]), 2)
        self.assertNotIn("geometry", result["disaster"]["overlaps"][0])

    async def test_unavailable_is_unknown(self):
        async def inspect(*_args):
            return [{"status": "UNAVAILABLE", "overlaps": []}]
        result = await assess(PARCEL, [], inspect)
        self.assertIn("재해위험지구 전국 공간자료", result["unknowns"])

    async def test_available_landuse_does_not_replace_disaster_spatial_data(self):
        async def inspect(*_args):
            return [{"status": "NOT_CONFIGURED", "overlaps": []}]

        result = await assess(
            PARCEL,
            ["상수원보호구역", "습지보호지역", "역사문화환경보존지역"],
            inspect,
            designation_lookup={
                "status": "AVAILABLE",
                "source": "VWorld NED 토지이용계획정보",
                "updated_at": "2026-06-07",
            },
        )

        self.assertIn("재해위험지구 전국 공간자료", result["unknowns"])
        self.assertIn("생태·자연도 등급", result["unknowns"])
        self.assertEqual(
            [finding["category"] for finding in result["findings"]],
            ["상수원·수질", "습지", "국가유산"],
        )
        self.assertEqual(
            result["designation_lookup"]["updated_at"],
            "2026-06-07",
        )

    async def test_available_landuse_can_report_no_designated_overlap(self):
        async def inspect(*_args):
            return [{"status": "NOT_CONFIGURED", "overlaps": []}]

        result = await assess(
            PARCEL,
            [],
            inspect,
            designation_lookup={"status": "AVAILABLE"},
        )

        self.assertEqual(
            result["summary"],
            "토지이용계획상 재해·환경·국가유산 관련 규제 없음",
        )
        self.assertIn("재해위험지구 전국 공간자료", result["unknowns"])


if __name__ == "__main__":
    unittest.main()
