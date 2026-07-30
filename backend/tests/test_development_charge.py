import unittest

from app.tools.development_charge import assess


class DevelopmentChargeTest(unittest.TestCase):
    def _assess(self, area_m2: float) -> dict:
        result = assess(
            requires_conversion=True,
            area_m2=area_m2,
            zone="자연녹지지역",
            jurisdiction="경상북도 경산시",
            address="경상북도 경산시 백천동 산 32",
        )
        self.assertIsNotNone(result)
        return result

    def test_non_metro_urban_area_at_or_above_990_is_applicable(self):
        result = self._assess(1296)

        self.assertTrue(result["applicable"])
        self.assertEqual(result["area_requirement_m2"], 990)
        self.assertEqual(result["assessed_area_m2"], 1296)
        self.assertIn("1,296㎡", result["reason"])
        self.assertIn("충족해", result["reason"])

    def test_non_metro_urban_area_below_990_is_not_applicable(self):
        result = self._assess(989)

        self.assertFalse(result["applicable"])
        self.assertIn("989㎡", result["reason"])
        self.assertIn("충족하지 않습니다", result["reason"])


if __name__ == "__main__":
    unittest.main()
