import unittest
from unittest.mock import AsyncMock, patch

from app.tools.building_register import lookup, pnu_params


class MockResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "response": {
                "header": {"resultCode": "00"},
                "body": {
                    "totalCount": 1,
                    "items": {"item": {
                        "bldNm": "테스트동",
                        "mainPurpsCdNm": "단독주택",
                        "grndFlrCnt": 2,
                        "archArea": 80.5,
                        "totArea": 150.2,
                    }},
                },
            }
        }


class BuildingRegisterTest(unittest.IsolatedAsyncioTestCase):
    def test_pnu_is_split_for_hub_api(self):
        self.assertEqual(
            pnu_params("1168010300100120000"),
            {
                "sigunguCd": "11680",
                "bjdongCd": "10300",
                "platGbCd": "0",
                "bun": "0012",
                "ji": "0000",
            },
        )

    @patch("app.tools.building_register.DATA_GO_KR_SERVICE_KEY", "key")
    @patch("httpx.AsyncClient.get", new_callable=AsyncMock)
    async def test_existing_building_is_reported(self, get):
        get.return_value = MockResponse()
        result = await lookup("1168010300100120000")
        self.assertEqual(result["status"], "FOUND")
        self.assertTrue(result["has_buildings"])
        self.assertEqual(result["buildings"][0]["main_use"], "단독주택")


if __name__ == "__main__":
    unittest.main()
