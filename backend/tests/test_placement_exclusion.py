import unittest

from app.orchestrator import (
    Orchestrator,
    _all_uses_verdict_judgment,
    _ignores_placement_restriction,
    _model_options_for_diagnosis,
)


class PlacementExclusionTest(unittest.TestCase):
    def test_explicit_exclusion_phrases(self):
        self.assertTrue(_ignores_placement_restriction(
            "실질 배치 불가 배제하고 건물 지을 수 있어?"
        ))
        self.assertTrue(_ignores_placement_restriction(
            "기존 건물은 없다고 보고 건축 가능한가?"
        ))
        self.assertFalse(_ignores_placement_restriction("건물 지을 수 있어?"))

    def test_answer_keeps_non_placement_conditions(self):
        orch = Orchestrator(None)
        orch.diagnosis = {
            "location": {"matched_address": "경상북도 경산시 백천동 산 32"},
            "request": {"building_use": "단독주택"},
            "regulation": {"verdict": "conditional"},
            "land_conversion": {
                "status": "PERMIT_REQUIRED",
                "summary": "준보전산지로 산지전용허가가 필요합니다.",
            },
            "road_access": {
                "status": "PLANNED_ROAD_ABUTS",
                "summary": "미개설 도로라 실제 진입로 확보가 필요합니다.",
            },
        }
        answer = orch._answer_ignoring_placement_restriction()
        self.assertIn("조건부로 가능합니다", answer)
        self.assertIn("산지전용허가", answer)
        self.assertIn("진입로 확보", answer)

    def test_exclusion_updates_panel_and_mass_presentation(self):
        orch = Orchestrator(None)
        orch.diagnosis = {
            "placement_restricted": True,
            "min_lot_area": None,
            "existing_buildings": {"has_buildings": True, "count": 1},
            "regulation": {
                "verdict": "conditional",
                "map_presentation": {
                    "verdict": "not_allowed",
                    "label": "실질 배치 불가",
                    "show_building_mass": False,
                },
            },
        }
        self.assertTrue(orch._apply_placement_exclusion())
        presentation = orch.diagnosis["regulation"]["map_presentation"]
        self.assertFalse(orch.diagnosis["placement_restricted"])
        self.assertEqual(presentation["verdict"], "conditional")
        self.assertEqual(presentation["label"], "조건부 가능(배치 불가 배제)")
        self.assertTrue(presentation["show_building_mass"])

    def test_exclusion_does_not_override_minimum_lot_failure(self):
        orch = Orchestrator(None)
        orch.diagnosis = {
            "placement_restricted": True,
            "min_lot_area": {"minimum_m2": 200},
            "existing_buildings": {"has_buildings": True},
            "regulation": {"verdict": "conditional"},
        }
        self.assertFalse(orch._apply_placement_exclusion())
        self.assertTrue(orch.diagnosis["placement_restricted"])

    def test_generic_facility_exclusion_lists_all_possible_models(self):
        diagnosis = {
            "placement_restricted": False,
            "request": {"building_use": "시설물"},
            "parcel": {"jibun": "경상북도 경산시 백천동 산 32"},
            "regulation": {
                "verdict": "conditional",
                "zone": "자연녹지지역",
                "map_presentation": {"verdict": "conditional"},
                "zone_use_overview": {
                    "allowed": [],
                    "conditional": ["단독주택", "공장", "창고시설"],
                },
            },
            "massing": {"floors": 5, "layout_feasible": True},
            "land_conversion": {"summary": "준보전산지 산지전용허가가 필요합니다."},
            "road_access": {},
            "permit_requirements": {},
        }
        answer = _all_uses_verdict_judgment(diagnosis)
        options = _model_options_for_diagnosis(diagnosis, include_alternatives=True)
        self.assertIn("단독주택·공장·창고시설", answer)
        self.assertEqual(
            [option["action"] for option in options],
            ["housing:detached", "housing:factory", "housing:warehouse"],
        )


if __name__ == "__main__":
    unittest.main()
