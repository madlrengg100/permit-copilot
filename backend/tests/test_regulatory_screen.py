import unittest

from app.tools.regulatory_screen import assess

PARCEL = {
    "type": "Polygon",
    "coordinates": [[[127, 37], [127.01, 37], [127.01, 37.01], [127, 37.01], [127, 37]]],
}


class RegulatoryScreenTest(unittest.IsolatedAsyncioTestCase):
    async def test_disaster_overlap_and_heritage_are_findings(self):
        async def inspect(*_args):
            return [{
                "layer_id": "disaster_risk_zone",
                "title": "재해위험지구",
                "status": "OVERLAP",
                "overlaps": [{
                    "name": "침수위험지구", "code": "UQ129",
                    "share_pct": 25.0, "geometry": PARCEL,
                }],
            }]
        result = await assess(PARCEL, ["문화재보호구역"], inspect)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(len(result["findings"]), 2)
        self.assertNotIn("geometry", result["disaster"]["overlaps"][0])

    async def test_unavailable_is_unknown(self):
        async def inspect(*_args):
            return [{"status": "UNAVAILABLE", "overlaps": []}]
        result = await assess(PARCEL, [], inspect)
        self.assertIn("재해위험지구", result["unknowns"])


if __name__ == "__main__":
    unittest.main()
