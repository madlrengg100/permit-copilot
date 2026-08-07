import unittest

from app.agents.map_control import _build_dimensions
from shapely.geometry import LineString


class FrontSetbackGeometryTest(unittest.TestCase):
    def test_divided_view_hides_only_black_area_labels(self):
        base = {
            "parcel": {
                "area_m2": 2918,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[127.0, 37.0], [127.001, 37.0], [127.001, 37.001],
                                     [127.0, 37.001], [127.0, 37.0]]],
                },
            },
            "massing": {"building_area_m2": 584, "mass_height_m": 15},
            "road_access": {"roads": []},
            "site_constraints": {},
        }
        before = _build_dimensions(base, 127.0005, 37.0005)
        self.assertEqual(
            {label["text"].split()[0] for label in before["labels"]},
            {"대지면적"},
        )
        after = _build_dimensions({**base, "assume_divided": True}, 127.0005, 37.0005)
        self.assertFalse(any(
            label["text"].startswith(("대지면적", "건축면적"))
            for label in after["labels"]
        ))
        after_model_click = _build_dimensions({
            **base, "assume_divided": True, "active_model_selected": True,
        }, 127.0005, 37.0005)
        self.assertFalse(any(
            label["text"].startswith(("대지면적", "건축면적"))
            for label in after_model_click["labels"]
        ))

    def test_initial_diagnosis_does_not_draw_use_setbacks_before_model_click(self):
        diagnosis = {
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[127.0, 37.0], [127.001, 37.0], [127.001, 37.001],
                                     [127.0, 37.001], [127.0, 37.0]]],
                }
            },
            "road_access": {"roads": []},
            "site_constraints": {"front_setback_m": 1.0, "adjacent_setback_m": 0.5},
        }
        command = _build_dimensions(diagnosis, 127.0005, 37.0005)
        labels = {segment.get("label") for segment in command["segments"]}
        self.assertNotIn("복합 건축선", labels)
        self.assertNotIn("전면이격 1m", labels)
        self.assertNotIn("인접이격 0.5m", labels)

    def test_front_tick_starts_on_longest_road_boundary_and_is_perpendicular(self):
        diagnosis = {
            "active_model_selected": True,
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[127.0, 37.0], [127.001, 37.0], [127.001, 37.001],
                                     [127.0, 37.001], [127.0, 37.0]]],
                }
            },
            "road_access": {
                "road_contact_geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[127.0, 37.0], [127.001, 37.0]],       # 긴 남측 전면
                        [[127.0, 37.0], [127.0, 37.00005]],    # 짧은 서측 접촉
                    ],
                },
                "roads": [],
            },
            "site_constraints": {"front_setback_m": 1.0},
        }

        command = _build_dimensions(diagnosis, 127.0005, 37.0005)
        tick = next(
            segment for segment in command["segments"]
            if segment.get("label") == "전면이격 1m"
        )
        start, end = tick["positions"]

        self.assertAlmostEqual(start[1], 37.0, places=7)
        self.assertAlmostEqual(start[0], 127.0005, places=5)
        self.assertAlmostEqual(end[0], start[0], places=7)
        self.assertGreater(end[1], start[1])

    def test_divided_front_uses_boundary_facing_original_main_road(self):
        diagnosis = {
            "assume_divided": True,
            "active_model_selected": True,
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[127.0001, 37.0], [127.001, 37.0], [127.001, 37.001],
                                     [127.0001, 37.001], [127.0001, 37.0]]],
                }
            },
            "road_access": {
                "road_contact_geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[127.0, 37.0], [127.0, 37.001]],
                        [[127.001, 37.0], [127.001, 37.00001]],
                    ],
                },
                "roads": [],
            },
            "site_constraints": {"front_setback_m": 1.0, "adjacent_setback_m": 0.5},
        }
        command = _build_dimensions(diagnosis, 127.0005, 37.0005)
        tick = next(s for s in command["segments"] if s.get("label") == "전면이격 1m")
        start = tick["positions"][0]
        self.assertAlmostEqual(start[0], 127.0001, places=7)
        self.assertAlmostEqual(start[1], 37.0005, places=5)

    def test_adjacent_tick_starts_at_side_midpoint_not_corner(self):
        diagnosis = {
            "active_model_selected": True,
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[127.0, 37.0], [127.001, 37.0], [127.001, 37.001],
                                     [127.0, 37.001], [127.0, 37.0]]],
                }
            },
            "road_access": {
                "road_contact_geometry": {
                    "type": "LineString",
                    "coordinates": [[127.0, 37.0], [127.001, 37.0]],
                },
                "roads": [],
            },
            "site_constraints": {"front_setback_m": 1.0, "adjacent_setback_m": 0.5},
        }

        command = _build_dimensions(diagnosis, 127.0005, 37.0005)
        tick = next(
            segment for segment in command["segments"]
            if segment.get("label") == "인접이격 0.5m"
        )
        start, end = tick["positions"]

        # 정사각형에서 남측 도로를 제외한 첫 최장 대표변은 동측 경계다.
        self.assertAlmostEqual(start[0], 127.001, places=7)
        self.assertAlmostEqual(start[1], 37.0005, places=5)
        self.assertLess(end[0], start[0])
        self.assertAlmostEqual(end[1], start[1], places=7)

    def test_composite_building_line_uses_front_and_adjacent_distances(self):
        diagnosis = {
            "active_model_selected": True,
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[127.0, 37.0], [127.001, 37.0], [127.001, 37.001],
                                     [127.0, 37.001], [127.0, 37.0]]],
                }
            },
            "road_access": {
                "road_contact_geometry": {
                    "type": "LineString",
                    "coordinates": [[127.0, 37.0], [127.001, 37.0]],
                },
                "roads": [],
            },
            "site_constraints": {"front_setback_m": 1.0, "adjacent_setback_m": 0.5},
        }

        command = _build_dimensions(diagnosis, 127.0005, 37.0005)
        line = next(
            segment for segment in command["segments"]
            if segment.get("label") == "복합 건축선"
        )
        xs = [position[0] for position in line["positions"]]
        ys = [position[1] for position in line["positions"]]

        south_offset_m = (min(ys) - 37.0) * 111320.0
        north_offset_m = (37.001 - max(ys)) * 111320.0
        west_offset_m = (min(xs) - 127.0) * 111320.0 * 0.8
        east_offset_m = (127.001 - max(xs)) * 111320.0 * 0.8
        self.assertAlmostEqual(south_offset_m, 1.0, delta=0.08)
        self.assertAlmostEqual(north_offset_m, 0.5, delta=0.08)
        self.assertAlmostEqual(west_offset_m, 0.5, delta=0.08)
        self.assertAlmostEqual(east_offset_m, 0.5, delta=0.08)

    def test_composite_building_line_removes_corner_sliver_artifacts(self):
        # 짧은 세그먼트가 많은 오목 필지에서도 모서리 틈 때문에 작은 꺾임선이
        # 난립하지 않고 의미 있는 폐합선만 표시해야 한다.
        diagnosis = {
            "active_model_selected": True,
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [127.0, 37.0], [127.00004, 37.0],
                        [127.00004, 37.00004], [127.00010, 37.00004],
                        [127.00010, 37.0], [127.00014, 37.0],
                        [127.00014, 37.00010], [127.00010, 37.00010],
                        [127.00010, 37.00006], [127.00004, 37.00006],
                        [127.00004, 37.00010], [127.0, 37.00010],
                        [127.0, 37.0],
                    ]],
                }
            },
            "road_access": {"roads": []},
            "site_constraints": {"adjacent_setback_m": 0.5},
        }

        command = _build_dimensions(diagnosis, 127.00007, 37.00005)
        lines = [
            segment for segment in command["segments"]
            if segment.get("color") == "#E53935" and len(segment.get("positions", [])) > 2
        ]
        self.assertGreaterEqual(len(lines), 1)
        self.assertEqual(sum(line.get("label") == "복합 건축선" for line in lines), 1)
        for line in lines:
            self.assertEqual(line["positions"][0], line["positions"][-1])
            self.assertGreaterEqual(len(line["positions"]), 4)
            self.assertTrue(LineString(line["positions"]).is_simple)

    def test_divided_parcel_reclips_contact_and_rebuilds_line(self):
        # 원필지 도로 접촉선이 더 길어도 분할 후 geometry의 경계까지만 표시하고,
        # 복합 건축선 역시 분할 후 필지 내부에서 새로 폐합되어야 한다.
        diagnosis = {
            "active_model_selected": True,
            "parcel": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [127.0, 37.0], [127.0005, 37.0], [127.0005, 37.001],
                        [127.0, 37.001], [127.0, 37.0],
                    ]],
                }
            },
            "road_access": {
                "road_contact_geometry": {
                    "type": "LineString",
                    "coordinates": [[127.0, 37.0], [127.001, 37.0]],
                },
                "roads": [{"contact_length_m": 89.0}],
            },
            "site_constraints": {"front_setback_m": 1.0, "adjacent_setback_m": 0.5},
        }

        command = _build_dimensions(diagnosis, 127.00025, 37.0005)
        contact = next(
            segment for segment in command["segments"]
            if str(segment.get("label", "")).startswith("도로 접촉")
        )
        self.assertLessEqual(max(p[0] for p in contact["positions"]), 127.0005 + 1e-8)
        building = next(
            segment for segment in command["segments"]
            if segment.get("label") == "복합 건축선"
        )
        self.assertTrue(all(127.0 <= p[0] <= 127.0005 for p in building["positions"]))
        self.assertTrue(all(37.0 <= p[1] <= 37.001 for p in building["positions"]))


if __name__ == "__main__":
    unittest.main()
