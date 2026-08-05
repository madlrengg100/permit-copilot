import unittest

from app.orchestrator import _division_view_request


class DivisionViewRoutingTest(unittest.TestCase):
    def test_before_view_phrases(self):
        self.assertEqual(_division_view_request("분할 전 건축물 보여줘"), "before")
        self.assertEqual(_division_view_request("원본 건물 보기"), "before")

    def test_after_view_phrases(self):
        self.assertEqual(_division_view_request("분할 후 건축물 보여줘"), "after")
        self.assertEqual(_division_view_request("분할 후 건축물 모델 표시"), "after")

    def test_general_model_request_is_not_division_toggle(self):
        self.assertIsNone(_division_view_request("가능한 건축물 모델 보여줘"))


if __name__ == "__main__":
    unittest.main()
