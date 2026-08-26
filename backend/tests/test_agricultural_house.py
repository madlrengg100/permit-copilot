import unittest

from app.agents.prediagnosis import _deterministic_request, detect_use_restriction
from app.orchestrator import _concise_verdict_judgment, _model_options_for_diagnosis
from app.tools.zoning import lookup_zoning_rules


class AgriculturalHouseTest(unittest.TestCase):
    def test_request_preserves_agricultural_house_label(self):
        request = _deterministic_request(
            "충청남도 예산군 고덕면 대천리 241-1에 농업인 주택 지을 수 있어?"
        )

        self.assertEqual(request["building_use"], "단독주택")
        self.assertEqual(request["requested_facility"], "농업인 주택")
        self.assertFalse(request["inferred"])

    def test_agricultural_house_is_conditional_in_agricultural_zone(self):
        result = lookup_zoning_rules(
            zone="농림지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
            facility="농업인 주택",
        )

        self.assertEqual(result["verdict"], "conditional")
        self.assertIn("농업인 주택", result["zone_use_overview"]["conditional"])
        self.assertIn("농업인 자격", result["reason"])

    def test_plain_detached_house_remains_not_allowed(self):
        result = lookup_zoning_rules(
            zone="농림지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
        )

        self.assertEqual(result["verdict"], "not_allowed")

    def test_agricultural_house_does_not_get_generic_house_restriction(self):
        regulation = lookup_zoning_rules(
            zone="농림지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
            facility="농업인 주택",
        )
        diagnosis = {
            "request": {"requested_facility": "농업인 주택"},
            "regulation": regulation,
        }

        self.assertIsNone(
            detect_use_restriction("농업인 주택 지을 수 있어?", diagnosis)
        )

    def test_review_uses_requested_facility_label(self):
        text = _concise_verdict_judgment({
            "parcel": {"jibun": "충청남도 예산군 고덕면 대천리 241-1"},
            "request": {
                "building_use": "단독주택",
                "requested_facility": "농업인 주택",
            },
            "regulation": {"verdict": "conditional"},
            "verdict": "conditional",
        })

        self.assertIn("농업인 주택은", text)
        self.assertNotIn("단독주택은", text)

    def test_agricultural_house_uses_only_detached_house_model(self):
        diagnosis = {
            "request": {
                "building_use": "단독주택",
                "requested_facility": "농업인 주택",
            },
            "regulation": {
                "verdict": "conditional",
                "zone_use_overview": {
                    "allowed": [],
                    "conditional": ["농업인 주택", "창고시설"],
                    "not_allowed": ["단독주택"],
                },
            },
            "massing": {
                "floors": 4,
                "layout_feasible": True,
                "exceeds_far_limit": False,
            },
        }

        options = _model_options_for_diagnosis(
            diagnosis, include_alternatives=True
        )
        self.assertEqual([item["action"] for item in options], ["housing:detached"])
        self.assertEqual(options[0]["label"], "4층 단독주택형")


if __name__ == "__main__":
    unittest.main()
