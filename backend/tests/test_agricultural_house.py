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
        # 농업진흥지역 지정이 없는 농림지역은 농지법 제32조가 아니라 국토계획법
        # 시행령 별표 21 이 근거다.
        restriction = result["district_restriction"]
        self.assertEqual(restriction["districts"], ["농림지역"])
        self.assertIn("별표 21", restriction["matched"][0]["clause"])

    def test_plain_detached_house_is_conditional_by_area_requirement(self):
        """농림지역의 일반 단독주택은 불가가 아니다.

        국토계획법 시행령 별표 21 제1호가목이 부지면적 1천㎡ 미만의 단독주택을
        조례 없이 건축할 수 있는 건축물로 열거한다. 농업인 자격은 요건이 아니다.
        """
        result = lookup_zoning_rules(
            zone="농림지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
        )

        self.assertEqual(result["verdict"], "conditional")
        self.assertTrue(
            any(
                "1천" in condition
                for condition in result["district_restriction"]["conditions"]
            )
        )

    def test_promotion_district_adds_farmland_act_conditions(self):
        """농업진흥구역이 겹치면 농지법 제32조 요건이 함께 붙는다."""
        result = lookup_zoning_rules(
            zone="농림지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
            facility="농업인 주택",
            land_districts=["농업진흥구역"],
        )
        restriction = result["district_restriction"]

        self.assertEqual(result["verdict"], "conditional")
        self.assertIn("농업진흥구역", restriction["districts"])
        self.assertIn("law.farmland.article_32", restriction["legal_references"])
        self.assertTrue(
            any("660" in condition for condition in restriction["conditions"])
        )

    def test_promotion_district_blocks_unlisted_use(self):
        """농업진흥구역의 허용행위 열거에 없는 용도는 금지된다."""
        result = lookup_zoning_rules(
            zone="농림지역",
            building_use="숙박시설",
            jurisdiction="충청남도 예산군",
            land_districts=["농업진흥구역"],
        )

        self.assertEqual(result["verdict"], "not_allowed")
        self.assertIn("농업진흥구역", result["district_restriction"]["districts"])

    def test_nature_conservation_allows_only_farmer_house(self):
        """자연환경보전지역 별표 22 는 단독주택 중 농어가주택만 허용한다."""
        plain = lookup_zoning_rules(
            zone="자연환경보전지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
        )
        farmer = lookup_zoning_rules(
            zone="자연환경보전지역",
            building_use="단독주택",
            jurisdiction="충청남도 예산군",
            facility="농업인 주택",
        )

        self.assertEqual(plain["verdict"], "not_allowed")
        self.assertEqual(farmer["verdict"], "conditional")

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
