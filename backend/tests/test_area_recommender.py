import unittest

from app.agents.area_recommender import _region_address_terms, _region_core
from app.agents.prediagnosis import (
    _deterministic_request,
    _guess_use,
    _USE_ALIASES,
    detect_use_restriction,
)
from app.orchestrator import Orchestrator, _same_parcel_address


class NaturalLanguageRegressionTest(unittest.TestCase):
    def test_region_address_terms_keeps_city_and_dong(self):
        self.assertEqual(
            _region_address_terms("경기도 의왕시 초평동"),
            ["의왕시", "초평동"],
        )

    def test_region_address_terms_keeps_county_and_town(self):
        self.assertEqual(
            _region_address_terms("충청남도 아산시 음봉면"),
            ["아산시", "음봉면"],
        )

    def test_region_core_supports_short_region_name(self):
        self.assertEqual(_region_core("양평"), "양평")

    def test_one_room_is_detached_house(self):
        self.assertEqual(_guess_use("1층 원룸 2층 주인 거주"), "단독주택")
        self.assertEqual(_USE_ALIASES["다가구주택"], "단독주택")

    def test_multifamily_and_first_exclusive_one_room_warnings(self):
        diagnosis = {
            "regulation": {
                "zone": "제1종전용주거지역",
                "zone_use_overview": {
                    "allowed": ["단독주택"],
                    "conditional": [],
                    "not_allowed": ["공동주택"],
                }
            }
        }
        warning = detect_use_restriction("다세대주택을 지어줘", diagnosis)
        self.assertIsNotNone(warning)
        self.assertIn("건축불가", warning["label"])
        one_room_warning = detect_use_restriction(
            "1층 원룸, 2층 주인 거주", diagnosis
        )
        self.assertIsNotNone(one_room_warning)
        self.assertIn("계획 확인 필요", one_room_warning["label"])

    def test_full_asan_address_is_not_reinterpreted_as_another_sinsu_ri(self):
        request = _deterministic_request(
            "충청남도 아산시 음봉면 신수리 100에 창고 지을 수 있어?"
        )
        self.assertIsNotNone(request)
        self.assertEqual(
            request["address"],
            "충청남도 아산시 음봉면 신수리 100",
        )
        self.assertEqual(request["building_use"], "창고시설")
        self.assertFalse(request["inferred"])

    def test_full_mountain_lot_address_is_preserved(self):
        request = _deterministic_request(
            "서울특별시 종로구 청운동 산 4-39에 건물 가능해?"
        )
        self.assertEqual(
            request["address"],
            "서울특별시 종로구 청운동 산 4-39",
        )

    def test_selected_parcel_coordinates_are_not_treated_as_an_address(self):
        request = _deterministic_request(
            '지도에서 선택한 위치(경도 127.1234567, 위도 36.7654321)에서 '
            '사용자가 원하는 건축물 용도는 "어떤 건물 가능해"이다. '
            "건축 가능 여부를 검토해줘"
        )
        self.assertEqual(request["address"], "")
        self.assertEqual(request["lon"], 127.1234567)
        self.assertEqual(request["lat"], 36.7654321)
        self.assertEqual(request["building_use"], "단독주택")

    def test_selected_parcel_use_followup_keeps_coordinates_and_use(self):
        request = _deterministic_request(
            "지도에서 선택한 위치(경도 127.1234567, 위도 36.7654321)의 "
            "필지에 대한 질문이다: 상가 용도는 무엇으로 한정되어 있어"
        )
        self.assertEqual(request["address"], "")
        self.assertEqual(request["lon"], 127.1234567)
        self.assertEqual(request["lat"], 36.7654321)
        self.assertEqual(request["building_use"], "제1종근린생활시설")

    def test_same_full_address_is_recognized_as_followup(self):
        diagnosis = {
            "request": {"address": "충청남도 아산시 음봉면 신수리 100"},
            "parcel": {"jibun": "충청남도 아산시 음봉면 신수리 100-1"},
        }
        self.assertTrue(
            _same_parcel_address(
                "충청남도 아산시 음봉면 신수리 100", diagnosis
            )
        )
        self.assertFalse(
            _same_parcel_address(
                "충청남도 아산시 음봉면 신수리 101", diagnosis
            )
        )

    def test_different_yangju_lot_number_starts_a_new_parcel(self):
        diagnosis = {
            "request": {"address": "경기도 양주시 만송동 691-5"},
            "parcel": {
                "pnu": "4163010700106910005",
                "jibun": "경기도 양주시 만송동 691-5",
            },
        }
        self.assertFalse(
            _same_parcel_address("경기도 양주시 만송동 693-1", diagnosis)
        )

    def test_mouse_click_marks_only_a_different_pnu_as_new(self):
        orchestrator = Orchestrator(client=None)
        orchestrator.diagnosis = {"parcel": {"pnu": "4163010700106910005"}}
        orchestrator.set_selected_parcel(
            lon=127.0,
            lat=37.0,
            pnu="4163010700106920002",
            from_mouse=True,
        )
        self.assertTrue(orchestrator._selection_changed)
        self.assertIsNone(orchestrator.diagnosis)
        orchestrator.set_selected_parcel(
            lon=127.0,
            lat=37.0,
            pnu="4163010700106920002",
            from_mouse=True,
        )
        self.assertTrue(orchestrator._selection_changed)


if __name__ == "__main__":
    unittest.main()
