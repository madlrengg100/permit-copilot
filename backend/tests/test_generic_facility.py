import unittest

from app.agents.prediagnosis import _deterministic_request, _guess_use
from app.tools.site_constraints import parking_requirement
from app.tools.zoning import lookup_zoning_rules


class GenericFacilityTest(unittest.TestCase):
    def test_unspecified_use_stays_generic_facility(self):
        request = _deterministic_request("경상북도 경산시 백천동 562-5에 건축 가능해?")

        self.assertIsNotNone(request)
        self.assertEqual(request["building_use"], "시설물")
        self.assertTrue(request["inferred"])
        self.assertEqual(_guess_use("여기 건축 가능해?"), "시설물")

    def test_generic_facility_does_not_invent_parking_count(self):
        parking = parking_requirement("시설물", 1296)

        self.assertEqual(parking["spaces"], 0)
        self.assertFalse(parking["estimated"])
        self.assertEqual(parking["surface_area_m2"], 0)

    def test_generic_facility_only_returns_density_screening(self):
        result = lookup_zoning_rules(
            "제2종일반주거지역",
            "시설물",
            jurisdiction="경상북도 경산시",
        )

        self.assertEqual(result["verdict"], "conditional")
        self.assertEqual(result["building_use"], "시설물")
        self.assertIn("전체 건축물 용도를 포괄", result["reason"])
        self.assertTrue(result["zone_use_overview"]["allowed"])

    def test_production_management_warehouse_requires_subtype_check(self):
        result = lookup_zoning_rules(
            "생산관리지역",
            "창고시설",
            jurisdiction="충청남도 아산시",
        )

        self.assertEqual(result["verdict"], "conditional")
        self.assertIn("농업·임업·축산업·수산업용", result["reason"])
        self.assertIn(
            "창고시설",
            result["zone_use_overview"]["conditional"],
        )


if __name__ == "__main__":
    unittest.main()
