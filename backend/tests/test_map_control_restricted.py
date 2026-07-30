import copy
import unittest

from app.agents.map_control import build_map_commands
from app.agents.prediagnosis import format_diagnosis_answer


class RestrictedMapRenderingTest(unittest.TestCase):
    def test_requested_use_restriction_suppresses_computed_mass(self):
        diagnosis = {
            "verdict": "conditional",
            "location": {
                "lon": 127.05,
                "lat": 36.84,
                "matched_address": "충청남도 아산시 음봉면 신수리 347",
            },
            "parcel": {
                "pnu": "4420036027103470000",
                "jibun": "충청남도 아산시 음봉면 신수리 347",
                "jimok": "대",
                "area_m2": 738,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [127.04, 36.83], [127.06, 36.83], [127.06, 36.85],
                        [127.04, 36.85], [127.04, 36.83],
                    ]],
                },
            },
            "land_use": {"zones": ["계획관리지역"], "districts": []},
            "regulation": {
                "zone": "계획관리지역",
                "verdict": "conditional",
                "bcr_max_pct": 40,
                "far_max_pct": 100,
            },
            "massing": {
                "layout_feasible": True,
                "building_area_m2": 295,
                "gross_floor_area_m2": 738,
                "bcr_applied_pct": 40,
                "mass_height_m": 9.9,
                "floors": 3,
            },
            "use_restriction": {
                "label": "판매시설 건축 불가",
                "reason": "계획관리지역에서 판매시설은 건축할 수 없는 용도입니다.",
                "blocked": ["판매시설"],
            },
        }

        commands = build_map_commands(diagnosis)
        command_types = [command["type"] for command in commands]
        panel = next(command for command in commands if command["type"] == "show_panel")

        self.assertEqual(command_types[0], "clear_mass")
        self.assertNotIn("extrude_mass", command_types)
        self.assertNotIn("show_dimensions", command_types)
        self.assertEqual(panel["verdict"], "not_allowed")
        self.assertEqual(panel["verdict_label"], "건축 불가")
        self.assertIsNone(panel["massing"])

    def test_restricted_review_has_no_mass_or_dimensions(self):
        diagnosis = {
            "verdict": "unknown",
            "location": {"lon": 128.71, "lat": 35.80, "matched_address": "경산시 남천면 구일리 산 40"},
            "parcel": {
                "pnu": "4729032029100400000",
                "jibun": "경산시 남천면 구일리 산 40",
                "jimok": "임",
                "area_m2": 25223,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [128.70, 35.79],
                        [128.72, 35.79],
                        [128.72, 35.81],
                        [128.70, 35.81],
                        [128.70, 35.79],
                    ]],
                },
            },
            "land_use": {"zones": ["자연녹지지역"], "districts": ["보전산지"]},
            "regulation": {
                "zone": "자연녹지지역",
                "reason": "보전산지의 산지전용 허용행위 확인 필요",
                "map_presentation": {
                    "verdict": "not_allowed",
                    "label": "건축 불가",
                    "color": "#C62828",
                    "show_building_mass": False,
                    "show_building_dimensions": False,
                },
            },
            "land_conversion": {
                "status": "RESTRICTED_REVIEW",
                "terrain": {
                    "status": "REFERENCE_AVAILABLE",
                    "source": "Copernicus DEM GLO-30",
                    "resolution_m": 30,
                    "elevation_min_m": 100.0,
                    "elevation_max_m": 140.0,
                    "elevation_mean_m": 120.0,
                    "slope_max_deg": 18.0,
                    "slope_mean_deg": 12.0,
                    "grid_cells": [{
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [128.70, 35.79],
                                [128.71, 35.79],
                                [128.71, 35.80],
                                [128.70, 35.80],
                                [128.70, 35.79],
                            ]],
                        },
                        "elevation_m": 120.0,
                        "slope_deg": 12.0,
                    }],
                },
                "forest_map_overlaps": [{
                    "name": "공익용산지",
                    "code": "UFM120",
                    "share_pct": 100.0,
                    "area_m2": 25223.0,
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [128.70, 35.79],
                            [128.72, 35.79],
                            [128.72, 35.81],
                            [128.70, 35.81],
                            [128.70, 35.79],
                        ]],
                    },
                }],
            },
            "massing": None,
            "conversion_charge": {
                "label": "대체산림자원조성비 참고액",
                "estimated_won": 273433086,
            },
            "development_charge": {
                "reason": "개발부담금 대상 가능성이 있습니다.",
                "applicable": True,
            },
        }

        commands = build_map_commands(diagnosis)
        command_types = [command["type"] for command in commands]
        panel = next(command for command in commands if command["type"] == "show_panel")

        self.assertNotIn("extrude_mass", command_types)
        self.assertNotIn("show_dimensions", command_types)
        self.assertNotIn("show_restriction_pieces", command_types)
        self.assertIn("set_slope_data", command_types)
        slope_data = next(
            command for command in commands if command["type"] == "set_slope_data"
        )
        self.assertEqual(slope_data["mean_slope_deg"], 12.0)
        self.assertEqual(slope_data["cells"][0]["slope_deg"], 12.0)
        self.assertEqual(panel["verdict_label"], "건축 불가")
        self.assertEqual(panel["color"], "#C62828")

        answer = format_diagnosis_answer(diagnosis)
        self.assertIn("종합 판정 — 건축 불가", answer)
        self.assertIn("**건축 불가 사유:**", answer)
        self.assertNotIn("부담금 (참고)", answer)
        self.assertNotIn("273,433,086", answer)

        conditional = copy.deepcopy(diagnosis)
        conditional["verdict"] = "conditional"
        conditional["regulation"]["verdict"] = "conditional"
        conditional["regulation"].pop("map_presentation", None)
        conditional_answer = format_diagnosis_answer(conditional)
        self.assertIn("부담금 (참고)", conditional_answer)
        self.assertIn("273,433,086", conditional_answer)


if __name__ == "__main__":
    unittest.main()
