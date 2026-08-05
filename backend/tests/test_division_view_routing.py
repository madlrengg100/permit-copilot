import unittest

from app.orchestrator import Orchestrator, _division_view_request


class DivisionViewRoutingTest(unittest.TestCase):
    def test_before_view_phrases(self):
        self.assertEqual(_division_view_request("분할 전 건축물 보여줘"), "before")
        self.assertEqual(_division_view_request("원본 건물 보기"), "before")

    def test_after_view_phrases(self):
        self.assertEqual(_division_view_request("분할 후 건축물 보여줘"), "after")
        self.assertEqual(_division_view_request("분할 후 건축물 모델 표시"), "after")

    def test_general_model_request_is_not_division_toggle(self):
        self.assertIsNone(_division_view_request("가능한 건축물 모델 보여줘"))

    def test_division_answer_groups_methods_with_their_basis(self):
        orchestrator = object.__new__(Orchestrator)
        orchestrator.diagnosis = {
            "regulation": {"bcr_max_pct": 20, "far_max_pct": 100},
            "land_division": {
                "status": "FEASIBLE",
                "zone": "자연녹지지역",
                "parcel_area_m2": 3005,
                "needs_dev_permit": True,
                "followups": ["개발행위허가(분할 포함)", "건축허가"],
                "methods": [
                    {"method": "규제 분리(용도지역 걸침)", "note": "자연녹지지역 2,918㎡", "buildable_area_m2": 2918},
                    {"method": "도로 후퇴(미달도로 편입)", "note": "후퇴 1.2m", "buildable_area_m2": 3002},
                ],
            },
        }
        text = orchestrator._division_scenario_answer()
        self.assertIn("분할 방법·관련 조례·법령 조문(근거)", text)
        self.assertIn("분할 후 계산 결과", text)
        self.assertIn("건축법 제57조", text)
        self.assertIn("국토의 계획 및 이용에 관한 법률 제56조", text)
        self.assertIn("건축법 제46조", text)


if __name__ == "__main__":
    unittest.main()
