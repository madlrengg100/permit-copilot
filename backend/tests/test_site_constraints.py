import unittest

from app.tools.site_constraints import apply, parking_requirement

PARCEL = {
    "type": "Polygon",
    "coordinates": [[[127, 37], [127.001, 37], [127.001, 37.001], [127, 37.001], [127, 37]]],
}


class SiteConstraintsTest(unittest.TestCase):
    def test_office_parking_uses_150_square_meters(self):
        result = parking_requirement("업무시설", 451)
        self.assertEqual(result["spaces"], 4)
        self.assertEqual(result["surface_area_m2"], 100)

    def test_detached_house_parking_formula(self):
        self.assertEqual(parking_requirement("단독주택", 120)["spaces"], 1)
        self.assertEqual(parking_requirement("단독주택", 251)["spaces"], 3)

    def test_residential_zone_reduces_footprint_and_applies_north(self):
        result = apply(
            parcel_geometry=PARCEL,
            massing={
                "building_area_m2": 9800,
                "gross_floor_area_m2": 9000,
                "mass_height_m": 9.9,
            },
            building_use="단독주택",
            zone="제1종일반주거지역",
        )
        self.assertTrue(result["north_daylight_applies"])
        self.assertGreaterEqual(result["north_setback_m"], 1.5)
        self.assertLess(result["adjusted_building_area_m2"], 9800)
        self.assertIsNotNone(result["footprint_geometry"])
        self.assertEqual(result["parking"]["strategy_status"], "NEEDS_SELECTION")
        self.assertEqual(result["parking"]["applied_surface_area_m2"], 0)

    def test_asan_warehouse_uses_current_ordinance_setback(self):
        result = apply(
            parcel_geometry=PARCEL,
            massing={
                "building_area_m2": 1000,
                "gross_floor_area_m2": 1200,
                "mass_height_m": 9.9,
            },
            building_use="창고시설",
            zone="계획관리지역",
            jurisdiction="충청남도 아산시",
            road_access=None,
            parking_strategy="underground",
        )
        self.assertEqual(result["setback_rule"]["status"], "APPLIED")
        self.assertEqual(result["front_setback_m"], 3)
        self.assertEqual(result["adjacent_setback_m"], 0)
        self.assertEqual(result["building_line_status"], "LEGAL_BUILDING_LINE_UNAVAILABLE")
        self.assertEqual(result["parking"]["strategy_status"], "DESIGN_REQUIRED")

    def test_tiny_remaining_footprint_does_not_create_absurd_floors(self):
        result = apply(
            parcel_geometry=PARCEL,
            massing={
                "building_area_m2": 9800,
                "gross_floor_area_m2": 19600,
                "floors": 2,
                "mass_height_m": 6.6,
            },
            building_use="단독주택",
            zone="제1종일반주거지역",
            parking_strategy="surface",
        )
        self.assertLessEqual(result["floors"], 2)
        self.assertLessEqual(
            result["achievable_gross_floor_area_m2"],
            result["adjusted_building_area_m2"] * 2,
        )

    def test_sub_ten_square_meter_footprint_is_not_presented_as_a_building(self):
        tiny = {
            "type": "Polygon",
            "coordinates": [[
                [127, 37],
                [127.00002, 37],
                [127.00002, 37.00002],
                [127, 37.00002],
                [127, 37],
            ]],
        }
        result = apply(
            parcel_geometry=tiny,
            massing={
                "building_area_m2": 2,
                "gross_floor_area_m2": 5,
                "floors": 3,
                "mass_height_m": 9.9,
            },
            building_use="제1종근린생활시설",
            zone="제2종일반주거지역",
            parking_strategy="surface",
        )
        self.assertFalse(result["layout_feasible"])
        self.assertEqual(result["adjusted_building_area_m2"], 0)
        self.assertEqual(result["floors"], 0)
        self.assertIsNone(result["footprint_geometry"])


if __name__ == "__main__":
    unittest.main()
