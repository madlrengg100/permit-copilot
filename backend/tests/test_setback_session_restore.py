import unittest
from unittest.mock import patch

from app import main


class _RestoredOrchestrator:
    def __init__(self):
        self.diagnosis = {
            "jurisdiction": "경기도 양평군",
            "land_use": {"zones": ["자연녹지지역"], "districts": []},
            "site_constraints": {},
            "massing": {"gross_floor_area_m2": 300},
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
                "roads": [{"contact_length_m": 88.9}],
                "drainage": {"route_geometry": {
                    "type": "LineString",
                    "coordinates": [[127.0005, 37.0005], [127.0005, 37.0]],
                }},
            },
            "location": {"lon": 127.0005, "lat": 37.0005},
        }
        self.context_updates = []

    def update_conversation_context(self, **kwargs):
        self.context_updates.append(kwargs)


class SetbackSessionRestoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_setback_uses_persisted_session_restore_path(self):
        restored = _RestoredOrchestrator()

        with (
            patch.object(main, "_get_session", return_value=restored) as get_session,
            patch.object(main, "_save_session") as save_session,
        ):
            result = await main.setback_for_use(
                "restored-session",
                main.SetbackForUseRequest(use="창고시설"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["zone"], "자연녹지지역")
        get_session.assert_called_once_with("restored-session")
        save_session.assert_called_once_with("restored-session", restored)
        self.assertEqual(
            restored.context_updates[0]["active_building_use"], "창고시설"
        )
        self.assertEqual(restored.diagnosis["request"]["building_use"], "창고시설")
        self.assertIn("setback_rule", restored.diagnosis["site_constraints"])
        self.assertTrue(restored.diagnosis["active_model_selected"])
        self.assertEqual(
            restored.diagnosis["site_constraints"]["front_setback_m"],
            result["front_setback_m"],
        )
        self.assertEqual(
            restored.diagnosis["site_constraints"]["adjacent_setback_m"],
            result["adjacent_setback_m"],
        )

    async def test_same_standard_use_preserves_specific_facility(self):
        restored = _RestoredOrchestrator()
        restored.diagnosis["request"] = {
            "building_use": "창고시설",
            "requested_facility": "저온저장고",
        }

        with (
            patch.object(main, "_get_session", return_value=restored),
            patch.object(main, "_save_session"),
        ):
            result = await main.setback_for_use(
                "same-use-session",
                main.SetbackForUseRequest(use="창고시설"),
            )

        self.assertEqual(result["display_use"], "저온저장고")
        self.assertEqual(
            restored.diagnosis["request"]["requested_facility"], "저온저장고"
        )

    async def test_different_standard_use_clears_specific_facility(self):
        restored = _RestoredOrchestrator()
        restored.diagnosis["request"] = {
            "building_use": "창고시설",
            "requested_facility": "저온저장고",
        }

        with (
            patch.object(main, "_get_session", return_value=restored),
            patch.object(main, "_save_session"),
        ):
            result = await main.setback_for_use(
                "different-use-session",
                main.SetbackForUseRequest(use="공장"),
            )

        self.assertEqual(result["display_use"], "공장")
        self.assertEqual(restored.diagnosis["request"]["requested_facility"], "")


if __name__ == "__main__":
    unittest.main()
