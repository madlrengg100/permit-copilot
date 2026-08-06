import unittest

from app.orchestrator import Orchestrator


class SessionMigrationTest(unittest.TestCase):
    def test_existing_building_alone_no_longer_blocks_whole_parcel(self):
        orch = Orchestrator(None)
        diagnosis = {
            "placement_restricted": True,
            "min_lot_area": None,
            "existing_buildings": {"has_buildings": True, "count": 1},
            "regulation": {
                "verdict": "conditional",
                "map_presentation": {
                    "verdict": "not_allowed",
                    "label": "실질 배치 불가",
                },
            },
        }
        snapshot = orch.snapshot_state()
        snapshot["diagnosis"] = diagnosis
        snapshot["_diagnosis_by_pnu"] = {"4729010600200320000": diagnosis}

        orch.restore_state(snapshot)

        self.assertFalse(orch.diagnosis["placement_restricted"])
        self.assertTrue(orch.diagnosis["existing_building_layout_review"])
        self.assertNotIn("map_presentation", orch.diagnosis["regulation"])

    def test_minimum_lot_failure_remains_blocked(self):
        orch = Orchestrator(None)
        diagnosis = {
            "placement_restricted": True,
            "min_lot_area": {"minimum_m2": 200},
            "existing_buildings": {"has_buildings": True},
            "regulation": {
                "map_presentation": {"label": "실질 배치 불가"},
            },
        }
        snapshot = orch.snapshot_state()
        snapshot["diagnosis"] = diagnosis
        orch.restore_state(snapshot)
        self.assertTrue(orch.diagnosis["placement_restricted"])


if __name__ == "__main__":
    unittest.main()
