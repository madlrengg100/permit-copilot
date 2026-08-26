import unittest
from unittest.mock import AsyncMock

from app.orchestrator import Orchestrator


class AgriculturalHouseFollowupTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_use_followup_forces_recheck_and_map_update(self):
        orchestrator = Orchestrator(client=object())
        orchestrator.diagnosis = {
            "location": {"lon": 126.7, "lat": 36.7},
            "parcel": {"pnu": "test-pnu", "jibun": "테스트 필지"},
            "request": {
                "building_use": "단독주택",
                "requested_facility": "",
            },
            "regulation": {"verdict": "not_allowed"},
            "verdict": "not_allowed",
        }
        orchestrator._interpret_followup = AsyncMock(return_value={
            "intent": "followup_explanation",
            "answer": "설명만 하는 잘못된 경로",
        })

        updated = {
            **orchestrator.diagnosis,
            "request": {
                "building_use": "단독주택",
                "requested_facility": "농업인 주택",
            },
            "regulation": {"verdict": "conditional"},
            "verdict": "conditional",
        }

        async def diagnose(query, emit_card=True, lines_only=None):
            orchestrator.diagnosis = updated
            return updated, [{
                "event": "map_commands",
                "data": {"commands": [{
                    "type": "show_panel",
                    "building_use": "농업인 주택",
                    "verdict": "conditional",
                }]},
            }]

        orchestrator._diagnose_and_emit = AsyncMock(side_effect=diagnose)
        orchestrator._natural_followup_answer = AsyncMock(
            return_value="농업인 주택은 조건부 가능합니다."
        )

        events = [event async for event in orchestrator.ask(
            "선택 필지에 농업인 주택은 가능하다고 하는데",
            continuation=True,
        )]

        orchestrator._diagnose_and_emit.assert_awaited_once()
        self.assertEqual(
            orchestrator.diagnosis["request"]["requested_facility"],
            "농업인 주택",
        )
        panel = next(
            command
            for event in events if event["event"] == "map_commands"
            for command in event["data"]["commands"]
            if command["type"] == "show_panel"
        )
        self.assertEqual(panel["building_use"], "농업인 주택")
        self.assertEqual(panel["verdict"], "conditional")


if __name__ == "__main__":
    unittest.main()
