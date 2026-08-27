"""농지법 제32조는 건축법 용도로 허용행위를 열거하지 않는다.

조문에 '창고시설'·'교육연구시설' 같은 건축법 용도는 나오지 않는다. 시행령
제29조제4항제2호가목이 정하는 것은 "농업인 또는 농업법인이 자기가 생산한
농산물을 건조·보관하기 위하여 설치하는 시설"이다. 일반 창고시설 질문을
농업용 시설로 가정해 예외를 열어 주면 조문에 없는 판정이 나간다.
"""

import unittest

from app.tools import district_use
from app.tools.zoning import lookup_zoning_rules, uses_for_zone


class DistrictUseMatchPolicyTest(unittest.TestCase):
    def test_generic_warehouse_is_not_allowed_in_promotion_district(self):
        result = district_use.evaluate(["농업진흥구역"], "", "창고시설")

        self.assertEqual(result["verdict"], "not_allowed")
        # 불가로 끝내지 않고 조문상 열리는 경로를 함께 알린다.
        self.assertIn("제32조제1항제3호", result["reason"])

    def test_named_agricultural_facility_opens_the_exception(self):
        result = district_use.evaluate(["농업진흥구역"], "농산물 건조시설", "창고시설")

        self.assertEqual(result["verdict"], "conditional")
        self.assertEqual(result["matched"][0]["match"], "facility")

    def test_generic_detached_house_is_not_allowed_in_promotion_district(self):
        """농업진흥구역에서 일반 단독주택은 불가. 농업인 주택만 예외다."""
        plain = district_use.evaluate(["농업진흥구역"], "", "단독주택")
        farmer = district_use.evaluate(["농업진흥구역"], "농업인 주택", "단독주택")

        self.assertEqual(plain["verdict"], "not_allowed")
        self.assertEqual(farmer["verdict"], "conditional")

    def test_zone_table_uses_building_use_matching(self):
        """별표 21 은 건축법 용도로 열거하므로 용도 매칭이 성립한다."""
        result = district_use.evaluate(["농림지역"], "", "창고시설")

        self.assertEqual(result["verdict"], "conditional")
        self.assertEqual(result["matched"][0]["match"], "building_use")

    def test_overview_labels_carry_the_statutory_qualifier(self):
        """'창고시설'이 아니라 '창고(농업·임업·축산업·수산업용)'로 표기한다."""
        overview = uses_for_zone("농림지역")

        self.assertIn("창고(농업·임업·축산업·수산업용)", overview["conditional"])
        self.assertNotIn("창고시설", overview["conditional"])
        self.assertIn("단독주택(부지면적 1천㎡ 미만)", overview["conditional"])

    def test_promotion_district_overview_offers_facility_paths(self):
        """일반 용도가 전부 불가여도 조문상 시설 경로는 남는다."""
        overview = uses_for_zone("농림지역", ["농업진흥구역"])

        self.assertEqual(overview["conditional"], [])
        self.assertIn("농업인 주택·어업인 주택", overview["facility_specific"])

    def test_unspecified_use_is_not_flatly_denied(self):
        """'시설물'은 용도 미지정 표식이지 건축물 용도가 아니다."""
        result = lookup_zoning_rules(
            zone="농림지역",
            building_use="시설물",
            jurisdiction="충청남도 예산군",
            land_districts=["농업진흥구역"],
        )

        self.assertEqual(result["verdict"], "conditional")
        self.assertIn("농업인 주택", result["reason"])


if __name__ == "__main__":
    unittest.main()


class SupersessionTest(unittest.TestCase):
    """국토계획법 제76조제5항제3호 — 농림지역 중 농업진흥지역·보전산지는
    제1항부터 제4항까지의 규정에도 불구하고 농지법·산지관리법에 따른다.

    별표 21 은 제1항 위임이므로 이때 적용되지 않는다. 누적이 아니라 대체다.
    """

    def test_promotion_district_supersedes_farmforest_zone(self):
        governing = district_use.resolve_governing(["농림지역", "농업진흥구역"])

        self.assertEqual(governing["applied"], ["농업진흥구역"])
        self.assertEqual(governing["superseded"], ["농림지역"])
        self.assertIn("제76조제5항제3호", governing["basis"][0])

    def test_farmforest_zone_alone_is_not_superseded(self):
        governing = district_use.resolve_governing(["농림지역"])

        self.assertEqual(governing["applied"], ["농림지역"])
        self.assertEqual(governing["superseded"], [])

    def test_farmer_house_is_possible_under_farmland_act(self):
        """농업진흥구역에서 가능한 것은 별표 21 의 단독주택이 아니라
        농지법 제32조제1항제3호의 농업인 주택이다."""
        result = district_use.evaluate(
            ["농림지역", "농업진흥구역"], "농업인 주택", "단독주택"
        )

        self.assertEqual(result["verdict"], "conditional")
        self.assertEqual(result["matched"][0]["checked_district"], "농업진흥구역")
        self.assertIn("제32조제1항제3호", result["matched"][0]["clause"])
        # 어느 법으로 판정했는지 이유에 먼저 밝힌다.
        self.assertIn("제76조제5항제3호", result["reason"])

    def test_plain_house_loses_the_table21_allowance(self):
        """농림지역 단독 판정에서는 별표 21 로 가능하지만,
        농업진흥구역이 겹치면 그 근거가 사라진다."""
        alone = district_use.evaluate(["농림지역"], "", "단독주택")
        with_district = district_use.evaluate(
            ["농림지역", "농업진흥구역"], "", "단독주택"
        )

        self.assertEqual(alone["verdict"], "conditional")
        self.assertEqual(with_district["verdict"], "not_allowed")

    def test_forest_district_supersedes_too(self):
        governing = district_use.resolve_governing(["농림지역", "임업용산지"])

        self.assertEqual(governing["applied"], ["임업용산지"])
        self.assertEqual(governing["superseded"], ["농림지역"])
