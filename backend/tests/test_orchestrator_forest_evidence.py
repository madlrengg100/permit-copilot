import json
import re
import unittest

from app.agents.prediagnosis import compact
from app.orchestrator import (
    Orchestrator,
    _all_uses_verdict_judgment,
    _collected_forest_evidence,
    _deterministic_verdict_judgment,
    _ensure_collected_forest_evidence,
    _ensure_query_evidence,
    _limit_review_length,
    _normalize_numbered_headings,
    _strip_internal_field_names,
)


DIAGNOSIS = {
    "verdict": "conditional",
    "request": {"building_use": "창고시설", "inferred": False},
    "parcel": {"jimok": "임", "jibun": "충청남도 아산시 음봉면 신수리 산 100-1"},
    "location": {"lon": 126.975, "lat": 36.85},
    "regulation": {"zone": "계획관리지역", "zone_use_overview": {}},
    "road_access": {
        "status": "CADASTRAL_CONTACT",
        "summary": "지적도상 도로 접함: 지목 '도로' 필지 1개와 접합니다",
    },
    "site_constraints": {
        "front_setback_m": 3,
        "adjacent_setback_m": 0,
        "north_setback_m": 0,
        "parking": {"estimated": False},
    },
    "permit_requirements": {
        "items": [
            {"name": "산지전용허가"},
            {"name": "개발행위허가"},
            {"name": "건축허가 또는 건축신고"},
        ]
    },
    "land_conversion": {
        "terrain": {
            "status": "REFERENCE_AVAILABLE",
            "source": "Copernicus DEM GLO-30",
            "slope_mean_deg": 14.3,
            "slope_max_deg": 26.4,
            "elevation_min_m": 91.4,
            "elevation_max_m": 129.7,
            "elevation_mean_m": 114.1,
            "grid_cells": [{"geometry": {"coordinates": [1, 2]}}] * 10,
        },
        "forest_inventory": [{
            "forest_type": "침엽수림",
            "species": "소나무",
            "age_class": "5영급",
            "diameter_class": "중경목",
            "density": "밀",
            "stand_height": "임분고 15m 이상 17m미만",
            "share_pct": 39.5,
            "updated_year": "2020",
        }],
    },
}


class GenericClient:
    async def complete(self, **_kwargs):
        return type("Result", (), {
            "texts": ["산지전용허가와 경사도조사서 및 산림조사서 검토가 필요하여 조건부 가능합니다."]
        })()


class OrchestratorForestEvidenceTest(unittest.IsolatedAsyncioTestCase):
    def test_permit_steps_keep_separate_numbered_paragraphs(self):
        raw = (
            "필요한 절차입니다. **1. 개발행위허가** "
            "- **담당부서:** 도시계획 담당부서 - **내용:** 진입도로를 심사합니다. "
            "**2. 건축법상 도로·접도 확인** "
            "- **담당부서:** 건축·도로 담당부서 - **설명:** 도로대장을 확인합니다. "
            "**3. 건축허가 또는 건축신고** "
            "- **담당부서:** 건축 담당부서"
        )
        text = _normalize_numbered_headings(_strip_internal_field_names(raw))

        self.assertIn("절차입니다.\n\n**1. 개발행위허가**\n\n", text)
        self.assertIn("\n\n**2. 건축법상 도로·접도 확인**\n\n", text)
        self.assertIn("\n\n**3. 건축허가 또는 건축신고**\n\n", text)
        self.assertNotIn("합니다. **2.", text)
        self.assertEqual(text.count("**담당부서:**"), 3)
        self.assertNotRegex(text, r"(?m)^[-*]\s*$")

    def test_internal_field_cleanup_preserves_markdown_line_breaks(self):
        raw = "**1. 개발행위허가**\n\n**담당부서:** 도시계획\n\nsite_constraints"
        text = _strip_internal_field_names(raw)
        self.assertIn("**1. 개발행위허가**\n\n**담당부서:**", text)

    def test_review_length_keeps_first_three_and_final_sentence(self):
        text = _limit_review_length(
            "첫째입니다. 둘째입니다. 셋째입니다. 넷째입니다. 최종 허가 조건입니다."
        )
        self.assertEqual(
            text,
            "첫째입니다. 둘째입니다. 셋째입니다. 최종 허가 조건입니다.",
        )

    def test_review_length_removes_truncated_tail(self):
        text = _limit_review_length(
            "첫 문장입니다. 둘째 문장입니다. 최종 건축을 위해서는 구"
        )
        self.assertEqual(text, "첫 문장입니다. 둘째 문장입니다.")

    def test_compact_keeps_statistics_but_removes_map_cells(self):
        data = json.loads(compact(DIAGNOSIS))
        terrain = data["land_conversion"]["terrain"]
        self.assertEqual(terrain["slope_mean_deg"], 14.3)
        self.assertNotIn("grid_cells", terrain)

    def test_collected_evidence_contains_actual_values(self):
        text = _collected_forest_evidence(DIAGNOSIS)
        for value in ("14.3°", "26.4°", "91.4~129.7m", "소나무", "5영급", "39.5%"):
            self.assertIn(value, text)

    def test_generic_answer_is_supplemented(self):
        text = _ensure_collected_forest_evidence("산림조사서가 필요합니다.", DIAGNOSIS)
        self.assertIn("14.3°", text)
        self.assertIn("소나무", text)

    def test_generic_road_answer_gets_actual_road_result(self):
        text = _ensure_query_evidence("도로대장을 확인해야 합니다.", DIAGNOSIS, "접도 상태는?")
        self.assertIn("도로 접함", text)
        self.assertIn("1개", text)

    def test_generic_setback_answer_gets_calculated_values(self):
        text = _ensure_query_evidence("설계 시 이격을 검토합니다.", DIAGNOSIS, "이격거리 얼마야?")
        self.assertIn("전면 건축선 이격 3m", text)
        self.assertIn("정북 일조 이격 0m", text)

    def test_permit_answer_cannot_omit_later_steps(self):
        text = _ensure_query_evidence(
            "산지전용허가가 필요합니다.", DIAGNOSIS, "개별 심의 및 허가 요건은?"
        )
        self.assertIn("개발행위허가", text)
        self.assertIn("건축허가 또는 건축신고", text)

    def test_single_use_has_deterministic_review_summary(self):
        text = _deterministic_verdict_judgment(DIAGNOSIS)
        self.assertIn("신수리 산 100-1", text)
        self.assertIn("창고시설", text)
        self.assertIn("현재 조건부 가능합니다", text)
        self.assertIn("도로 접함", text)
        self.assertIn(
            "허가·협의 요건의 충족 여부를 확인해야 하므로 현재 조건부 가능합니다",
            text,
        )
        self.assertIn("최종 허가가 가능합니다", text)
        self.assertNotIn("판정에는", text)
        self.assertNotIn("이(가)", text)

    def test_single_use_review_keeps_each_fact_as_a_sentence(self):
        diagnosis = {
            **DIAGNOSIS,
            "land_conversion": {
                **DIAGNOSIS["land_conversion"],
                "summary": (
                    "준보전산지 100.0% 중첩입니다. "
                    "산지전용허가와 대체산림자원조성비 검토가 필요합니다."
                ),
            },
            "regulation": {
                **DIAGNOSIS["regulation"],
                "constraints": [{
                    "name": "가축사육제한구역",
                    "note": "가축분뇨법상 축사 제한 — 축산 시설이면 확인 필요",
                }],
            },
        }

        text = _deterministic_verdict_judgment(diagnosis)

        self.assertIn("필지 전체가 준보전산지와 중첩되어", text)
        self.assertIn("대체산림자원조성비가 부과될 수 있으나", text)
        self.assertIn("감면 또는 면제될 수 있습니다", text)
        self.assertIn("축산 관련 시설인 경우 별도 확인이 필요합니다.", text)
        self.assertIn("관계기관 협의와 필요한 심의를 포함한 허가 과정에서", text)
        self.assertIn("각 법령의 허가·협의 조건을 모두 충족해야", text)
        self.assertNotIn("필요합니다,", text)
        self.assertNotIn("필요합니다이(가)", text)

    def test_all_uses_review_is_short_and_explains_use_specific_results(self):
        diagnosis = {
            **DIAGNOSIS,
            "request": {"building_use": "시설물", "inferred": True},
            "regulation": {
                **DIAGNOSIS["regulation"],
                "zone_use_overview": {
                    "allowed": ["단독주택"],
                    "conditional": ["창고시설", "교육연구시설"],
                    "not_allowed": ["판매시설"],
                },
            },
        }
        text = _all_uses_verdict_judgment(diagnosis)
        self.assertIn("현재 조건부 가능합니다", text)
        self.assertIn(
            "계획관리지역입니다. 건축물 용도 중 "
            "단독주택·창고시설·교육연구시설은 건축 가능하거나 "
            "조건부로 검토할 수 있습니다",
            text,
        )
        self.assertIn("다만", text)
        self.assertIn("산지전용허가·개발행위허가", text)
        self.assertIn("관계기관 협의와 필요한 심의", text)
        self.assertNotIn("용도 대분류 중", text)
        self.assertNotIn("평균경사도", text)
        self.assertNotIn("소나무", text)
        self.assertNotIn("등처럼", text)
        self.assertNotIn("중경목로", text)
        self.assertNotIn("공동주택", text)

    def test_all_uses_review_formats_farmland_and_structured_constraint(self):
        diagnosis = {
            **DIAGNOSIS,
            "request": {"building_use": "시설물", "inferred": True},
            "parcel": {
                "jimok": "전",
                "jibun": "충청남도 아산시 음봉면 신수리 337",
            },
            "land_conversion": {
                "summary": (
                    "농업진흥지역 중첩은 확인되지 않았지만 "
                    "농지전용허가·협의와 농지보전부담금 검토가 필요합니다."
                )
            },
            "regulation": {
                **DIAGNOSIS["regulation"],
                "constraints": [{
                    "name": "가축사육제한구역",
                    "note": "가축분뇨법상 축사 제한 — 축산 시설이면 확인 필요",
                }],
            },
        }
        text = _all_uses_verdict_judgment(diagnosis)
        self.assertIn("농업진흥지역과 중첩되지는 않지만, 농지이므로", text)
        self.assertIn("농지보전부담금이 부과될 수 있으나", text)
        self.assertNotIn("가축사육제한구역에서는", text)
        self.assertNotIn("{'name'", text)
        self.assertNotIn("'note'", text)
        self.assertNotIn("은(는)", text)

    async def test_verdict_judgment_cannot_drop_collected_evidence(self):
        orchestrator = Orchestrator(GenericClient())
        orchestrator.diagnosis = DIAGNOSIS
        text = await orchestrator._verdict_judgment(
            "이 필지에 창고를 지을 때 거쳐야 할 개별 심의 및 허가 요건은?"
        )
        self.assertIn("14.3°", text)
        self.assertIn("26.4°", text)
        self.assertLessEqual(len(re.split(r"(?<=[.!?])\s+", text)), 4)

    async def test_followup_understands_permit_requirement_intent(self):
        orchestrator = Orchestrator(GenericClient())
        orchestrator.diagnosis = DIAGNOSIS
        text = await orchestrator._natural_followup_answer(
            "창고 지을 때 거쳐야 할 개별 심의 및 허가 요건은?"
        )
        self.assertIn("14.3°", text)
        self.assertIn("소나무", text)

    async def test_coordinate_permit_question_routes_to_grounded_answer(self):
        orchestrator = Orchestrator(GenericClient())
        orchestrator.diagnosis = DIAGNOSIS
        events = [
            event async for event in orchestrator.ask(
                "지도에서 선택한 위치(경도 126.975, 위도 36.85)의 "
                "창고 개별 심의 및 허가 요건은?"
            )
        ]
        text = " ".join(
            event["data"]["text"]
            for event in events
            if event.get("event") == "message"
        )
        self.assertIn("산지전용허가", text)
        self.assertIn("개발행위허가", text)
        self.assertIn("건축허가 또는 건축신고", text)
        self.assertIn("14.3°", text)


if __name__ == "__main__":
    unittest.main()
