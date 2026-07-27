import unittest

from app.tools.permit_requirements import build


class PermitRequirementsTest(unittest.TestCase):
    def test_forest_existing_building_has_ordered_steps(self):
        result = build({
            "parcel": {"pnu": "1"},
            "jimok_info": {"category": "forest"},
            "regulation": {"zone": "계획관리지역"},
            "existing_buildings": {"has_buildings": True},
            "road_access": {"status": "NO_CADASTRAL_ROAD", "unknowns": ["도로폭"]},
            "regulatory_screen": {"findings": [], "unknowns": ["환경"]},
            "jurisdiction": "아산시",
        })
        ids = [item["id"] for item in result["items"]]
        self.assertEqual(ids[0], "demolition")
        self.assertIn("forest_conversion", ids)
        self.assertIn("development_activity", ids)
        self.assertEqual(ids[-1], "building_permission")
        forest = next(item for item in result["items"] if item["id"] == "forest_conversion")
        self.assertEqual(forest["processing_days"], 30)

    def test_urban_buildable_parcel_still_has_building_permission(self):
        result = build({
            "jimok_info": {"category": "buildable"},
            "regulation": {"zone": "제3종일반주거지역"},
            "road_access": {"status": "CADASTRAL_CONTACT", "unknowns": []},
            "existing_buildings": {"has_buildings": False},
            "regulatory_screen": {"findings": [], "unknowns": []},
        })
        self.assertEqual([item["id"] for item in result["items"]], ["building_permission"])


if __name__ == "__main__":
    unittest.main()
