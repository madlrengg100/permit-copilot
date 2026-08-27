"""같은 필지를 다시 물어도 가능 모델 버튼은 나와야 한다.

'○○에 건물 지을 수 있어?'를 같은 필지에 다시 물으면 종합 판정 카드는 중복이라
다시 띄우지 않는다(emit_card=False). 그런데 모델 버튼까지 함께 빠지면, 프런트가
필지 전환·지도 클릭 때 기존 버튼을 지우므로 가능 판정인데도 모델을 영영 고를 수
없게 된다.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.orchestrator import Orchestrator, _model_options_for_diagnosis


def _buildable_diagnosis() -> dict:
    return {
        "parcel": {"pnu": "4729012600102310000", "jibun": "경상북도 경산시 사동 231"},
        "location": {"matched_address": "경상북도 경산시 사동 231"},
        "request": {"building_use": "시설물", "requested_facility": ""},
        "regulation": {
            "zone": "제1종전용주거지역",
            "verdict": "conditional",
            "zone_use_overview": {
                "allowed": ["단독주택", "제1종근린생활시설"],
                "conditional": ["교육연구시설"],
                "not_allowed": ["공동주택", "공장"],
            },
        },
        "massing": {"floors": 2, "layout_feasible": True, "exceeds_far_limit": False},
        "placement_restricted": False,
    }


class SameParcelModelOptionsTest(unittest.IsolatedAsyncioTestCase):
    def test_options_are_available_for_the_state(self):
        options = _model_options_for_diagnosis(
            _buildable_diagnosis(), include_alternatives=True
        )

        self.assertEqual(
            [item["action"] for item in options],
            ["housing:detached", "housing:commercial"],
        )

    async def test_same_parcel_followup_still_emits_models(self):
        orchestrator = Orchestrator(client=None)
        diagnosis = _buildable_diagnosis()
        orchestrator.diagnosis = diagnosis

        async def _diagnose(query, emit_card=True, lines_only=None):
            # 같은 필지이므로 카드는 내지 않는다(emit_card=False 로 호출된다).
            self.assertFalse(emit_card)
            return diagnosis, []

        with (
            patch.object(orchestrator, "_diagnose_and_emit", side_effect=_diagnose),
            patch.object(
                orchestrator,
                "_natural_followup_answer",
                AsyncMock(return_value="조건부로 건축이 가능합니다."),
            ),
        ):
            events = [
                event
                async for event in orchestrator.ask(
                    "경상북도 경산시 사동 231 에 건물 지을 수 있어"
                )
            ]

        with_options = [
            event for event in events
            if event.get("event") == "message" and (event.get("data") or {}).get("options")
        ]
        self.assertTrue(with_options, "같은 필지 재질문에서 가능 모델이 빠졌다")
        actions = [
            option["action"]
            for event in with_options
            for option in event["data"]["options"]
        ]
        self.assertIn("housing:detached", actions)


if __name__ == "__main__":
    unittest.main()
