import unittest

from app.tools import setback_rules


class SetbackDefaultRuleTest(unittest.TestCase):
    def test_yangpyeong_warehouse_uses_all_other_buildings_rule(self):
        result = setback_rules.lookup(
            "경기도 양평군", "창고시설", "자연녹지지역", 300
        )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["front_m"], 1.0)
        self.assertEqual(result["adjacent_m"], 0.5)
        self.assertIn("모든 건축물", result["note"])

    def test_specific_rule_wins_over_default_rule(self):
        result = setback_rules.lookup(
            "경기도 양평군", "공장", "자연녹지지역", 1200
        )
        self.assertEqual(result["front_m"], 3.0)
        self.assertEqual(result["adjacent_m"], 1.5)


if __name__ == "__main__":
    unittest.main()
