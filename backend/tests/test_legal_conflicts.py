import unittest
from app.tools.legal_conflicts import evaluate
from app.agents.prediagnosis import format_diagnosis_answer

class LegalConflictTest(unittest.TestCase):
    def test_prohibition_blocks(self):
        result = evaluate({"regulation": {"verdict": "not_allowed"}})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["blocks_final_approval"])
    def test_forest_is_cumulative(self):
        result = evaluate({"jimok_info": {"category": "forest"}})
        self.assertEqual(result["status"], "CUMULATIVE_REQUIREMENTS")
    def test_restricted_conversion_blocks_until_exception(self):
        result = evaluate({"jimok_info":{"category":"forest"},"land_conversion":{"status":"RESTRICTED_REVIEW"}})
        self.assertEqual(result["status"], "UNRESOLVED_CONFLICT")
        self.assertTrue(result["blocks_final_approval"])

    def test_conflict_is_visible_in_diagnosis_text(self):
        conflicts = evaluate({"regulation": {"verdict": "not_allowed"}})
        text = format_diagnosis_answer({
            "verdict": "not_allowed",
            "regulation": {"verdict": "not_allowed", "zone": "제3종일반주거지역"},
            "parcel": {"jimok": "대", "area_m2": 100},
            "land_use": {"districts": []},
            "request": {"building_use": "판매시설"},
            "legal_conflicts": conflicts,
            "regulatory_screen": {"findings": [], "unknowns": [], "summary": "규제 없음"},
            "permit_requirements": {"items": []},
        })
        self.assertIn("법률 적용 관계", text)
        self.assertIn("최종 허가 제한", text)
