import unittest
from app.tools.legal_conflicts import evaluate

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
