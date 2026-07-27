import unittest

from app.tools.road_access import assess


PARCEL = {
    "type": "Polygon",
    "coordinates": [[[127, 37], [127.001, 37], [127.001, 37.001], [127, 37.001], [127, 37]]],
}
ROAD = {
    "type": "Polygon",
    "coordinates": [[[127.001, 37], [127.00105, 37], [127.00105, 37.001], [127.001, 37.001], [127.001, 37]]],
}


class RoadAccessTest(unittest.IsolatedAsyncioTestCase):
    async def test_adjacent_road_is_found(self):
        async def fetch(*_args):
            return [{"pnu": "road", "jimok": "도", "address": "도로", "geometry": ROAD}]
        result = await assess(PARCEL, "parcel", fetch)
        self.assertEqual(result["status"], "CADASTRAL_CONTACT")
        self.assertGreater(result["roads"][0]["contact_length_m"], 2)

    async def test_no_road_is_not_reported_as_accessible(self):
        async def fetch(*_args):
            return []
        result = await assess(PARCEL, "parcel", fetch)
        self.assertEqual(result["status"], "NO_CADASTRAL_ROAD")
        self.assertIn("맹지", result["message"])

    async def test_failure_is_unknown_not_no_road(self):
        async def fetch(*_args):
            raise RuntimeError("down")
        result = await assess(PARCEL, "parcel", fetch)
        self.assertEqual(result["status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
