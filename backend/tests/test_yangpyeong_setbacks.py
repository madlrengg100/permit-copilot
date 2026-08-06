import unittest

from app.tools import setback_rules


class YangpyeongSetbackRulesTest(unittest.TestCase):
    def setUp(self):
        setback_rules._load.cache_clear()

    def test_single_house_in_natural_green_zone(self):
        result = setback_rules.lookup(
            "경기도 양평군", "단독주택", "자연녹지지역", 500,
        )
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(result["front_m"], 1.0)
        self.assertEqual(result["adjacent_m"], 0.5)
        self.assertIn("양평군 건축 조례 제23조", result["source"])

    def test_single_house_in_exclusive_residential_zone(self):
        result = setback_rules.lookup(
            "경기도 양평군", "단독주택", "제1종전용주거지역", 500,
        )
        self.assertEqual(result["front_m"], 1.0)
        self.assertEqual(result["adjacent_m"], 1.0)


if __name__ == "__main__":
    unittest.main()
