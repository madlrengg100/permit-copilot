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
        self.assertEqual(forest["rule_id"], "permit.forest_conversion")
        self.assertEqual(
            forest["legal_references"][0]["ref_id"],
            "law.mountainous_district.article_14",
        )
        building = next(
            item for item in result["items"] if item["id"] == "building_permission"
        )
        self.assertIn("forest_conversion", building["depends_on"])
        self.assertTrue(result["workflow_graph"]["edges"])
        self.assertTrue(
            all("rule_id" in node for node in result["workflow_graph"]["nodes"])
        )

    def test_urban_buildable_parcel_still_has_building_permission(self):
        result = build({
            "jimok_info": {"category": "buildable"},
            "regulation": {"zone": "제3종일반주거지역"},
            "road_access": {"status": "CADASTRAL_CONTACT", "unknowns": []},
            "existing_buildings": {"has_buildings": False},
            "regulatory_screen": {"findings": [], "unknowns": []},
        })
        self.assertEqual([item["id"] for item in result["items"]], ["building_permission"])
        self.assertEqual(result["items"][0]["depends_on"], [])

    def test_not_allowed_parcel_does_not_emit_building_permission(self):
        result = build({
            "jimok_info": {"category": "buildable"},
            "regulation": {
                "zone": "생산관리지역",
                "verdict": "not_allowed",
            },
            "road_access": {"status": "CADASTRAL_CONTACT", "unknowns": []},
            "existing_buildings": {"has_buildings": False},
            "regulatory_screen": {"findings": [], "unknowns": []},
        })
        self.assertNotIn(
            "building_permission",
            [item["id"] for item in result["items"]],
        )

    def test_review_findings_emit_data_driven_agency_steps(self):
        result = build({
            "jimok_info": {"category": "buildable"},
            "regulation": {
                "zone": "제3종일반주거지역",
                "verdict": "conditional",
            },
            "road_access": {"status": "CADASTRAL_CONTACT", "unknowns": []},
            "existing_buildings": {"has_buildings": False},
            "regulatory_screen": {
                "findings": [{
                    "category": "국가유산",
                    "severity": "REVIEW",
                    "basis": "국가유산 관련 법령",
                    "note": "현상변경 허용기준 확인",
                }],
                "unknowns": [],
            },
            "jurisdiction": "아산시",
        })
        review = next(
            item for item in result["items"]
            if item["id"] == "special_국가유산"
        )
        self.assertEqual(review["basis"], "국가유산 관련 법령")
        self.assertEqual(review["department"], "아산시 관련 전문부서")
        building = result["items"][-1]
        self.assertEqual(building["id"], "building_permission")
        self.assertIn("special_국가유산", building["depends_on"])


if __name__ == "__main__":
    unittest.main()
