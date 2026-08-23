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

    def test_law_name_after_article_reference_is_not_truncated(self):
        """앞 조문의 '조'가 뒤 법령명에 붙어 검증에서 누락되면 안 된다."""
        state = {
            "regulation": {
                "legal_basis": (
                    "건축법 제11조 및 국토의 계획 및 이용에 관한 법률 제56조"
                )
            }
        }
        self.assertEqual(
            law_open.extract_law_names(state),
            ["건축법", "국토의 계획 및 이용에 관한 법률"],
        )

    def test_law_name_followed_by_particle_is_extracted(self):
        """'…법에 따른' 처럼 조문이 뒤따르지 않아도 법령명을 뽑는다."""
        state = {
            "permit_requirements": {
                "items": [{"basis": "자연재해대책법에 따른 재해위험지구"}]
            }
        }
        self.assertEqual(
            law_open.extract_law_names(state), ["자연재해대책법"]
        )

    def test_article_with_sub_number_is_a_boundary(self):
        """'제47조의2' 같은 가지번호 조문도 법령명 경계로 처리한다."""
        state = {
            "conversion_charge": {
                "legal_basis": (
                    "농지법 제38조, 농지법 시행령 제53조, "
                    "농지법 시행규칙 제47조의2"
                )
            }
        }
        self.assertEqual(
            law_open.extract_law_names(state),
            ["농지법", "농지법 시행령", "농지법 시행규칙"],
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
