import unittest

from app.tools.conversion_charges import estimate


class ConversionChargeTest(unittest.TestCase):
    def test_farmland_outside_promotion_uses_20_percent(self):
        result = estimate(
            jimok_category="farmland",
            conversion={"agriculture": {"status": "CLEAR"}},
            conversion_area_m2=100,
            official_land_price_won_m2=100_000,
        )
        self.assertEqual(result["unit_won_m2"], 20_000)
        self.assertEqual(result["estimated_won"], 2_000_000)

    def test_farmland_unit_price_is_capped(self):
        result = estimate(
            jimok_category="farmland",
            conversion={"agriculture": {"status": "OVERLAP"}},
            conversion_area_m2=10,
            official_land_price_won_m2=1_000_000,
        )
        self.assertEqual(result["unit_won_m2"], 50_000)

    def test_2026_conservation_forest_rate(self):
        result = estimate(
            jimok_category="forest",
            conversion={"forest": {"overlaps": [{"code": "UFM110"}]}},
            conversion_area_m2=100,
            official_land_price_won_m2=1_000_000,
        )
        self.assertEqual(result["unit_won_m2"], 11_840)


if __name__ == "__main__":
    unittest.main()
