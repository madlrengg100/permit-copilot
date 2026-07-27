import unittest
from unittest.mock import AsyncMock, patch

from app.tools import law_open


class LawOpenTest(unittest.IsolatedAsyncioTestCase):
    def test_extracts_unique_law_names_from_diagnosis(self):
        state = {
            "regulation": {
                "legal_basis": "국토의 계획 및 이용에 관한 법률 시행령 제84조"
            },
            "road_access": {
                "legal_basis": "건축법 제2조제1항제11호, 제44조"
            },
            "permit_requirements": {
                "items": [
                    {"basis": "건축법 제11조·제14조"},
                    {"basis": "산지관리법 제14조 및 같은 법 시행규칙"},
                ]
            },
        }
        self.assertEqual(
            law_open.extract_law_names(state),
            [
                "국토의 계획 및 이용에 관한 법률 시행령",
                "건축법",
                "산지관리법",
                "산지관리법 시행규칙",
            ],
        )

    async def test_verification_failure_does_not_break_diagnosis(self):
        state = {"regulation": {"legal_basis": "건축법 제11조"}}
        with (
            patch.object(law_open, "LAW_OPEN_API_OC", "configured"),
            patch.object(
                law_open,
                "_search_one",
                new=AsyncMock(side_effect=OSError("network")),
            ),
        ):
            result = await law_open.verify_legal_sources(state)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["failed_queries"], ["건축법"])


if __name__ == "__main__":
    unittest.main()
