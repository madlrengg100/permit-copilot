import unittest

from app.tools import land_division


class RoadContactDivisionSemanticsTest(unittest.TestCase):
    def test_zone_straddling_is_optional_not_required_division(self):
        diagnosis = {
            "parcel": {"area_m2": 3000},
            "regulation": {"zone": "자연녹지지역"},
            "land_use": {
                "zone_shares": [
                    {"zone": "자연녹지지역", "area_m2": 2000},
                    {"zone": "제1종일반주거지역", "area_m2": 1000},
                ]
            },
            "road_access": {"status": "CADASTRAL_CONTACT", "roads": []},
        }
        result = land_division.assess(diagnosis)
        self.assertEqual(result["status"], "FEASIBLE")
        self.assertEqual(result["division_recommendation"], "OPTIONAL")
        self.assertTrue(all(m["necessity"] == "OPTIONAL" for m in result["methods"]))

    def test_narrow_road_contact_requires_measurement_not_automatic_division(self):
        diagnosis = {
            "parcel": {"area_m2": 3000},
            "regulation": {"zone": "자연녹지지역"},
            "land_use": {},
            "road_access": {
                "status": "CADASTRAL_CONTACT",
                "roads": [{
                    "cadastral_width_estimate_m": 2.3,
                    "contact_length_m": 36.5,
                }],
            },
        }
        result = land_division.assess(diagnosis)
        self.assertEqual(result["division_recommendation"], "OPTIONAL")
        self.assertEqual(result["methods"][0]["necessity"], "MEASUREMENT_REQUIRED")
        self.assertIn("건축선 후퇴", result["methods"][0]["method"])


if __name__ == "__main__":
    unittest.main()
