import unittest

from app.tools import setback_rules


class EumseongSetbackRulesTest(unittest.TestCase):
    def setUp(self):
        setback_rules._load.cache_clear()

    def test_single_house_uses_verified_general_rule(self):
        result = setback_rules.lookup(
            "충청북도 음성군", "단독주택", "계획관리지역", 300,
        )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["front_m"], 0.0)
        self.assertEqual(result["adjacent_m"], 0.5)
        self.assertIn("음성군 건축 조례 제30조", result["source"])

    def test_factory_keeps_different_front_and_adjacent_values(self):
        result = setback_rules.lookup(
            "충청북도 음성군", "공장", "계획관리지역", 1000,
        )
        self.assertEqual(result["front_m"], 3.0)
        self.assertEqual(result["adjacent_m"], 1.5)

    def test_verified_rules_override_corrupted_auto_parse(self):
        result = setback_rules.lookup(
            "충청북도 음성군", "숙박시설", "계획관리지역", 2000,
        )
        self.assertEqual(result["status"], "NEEDS_SUBTYPE")


if __name__ == "__main__":
    unittest.main()
