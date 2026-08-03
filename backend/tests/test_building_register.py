import pathlib
import unittest
from unittest.mock import AsyncMock, patch

from app.tools.building_register import lookup, pnu_params


class DataGoKrSchemeTest(unittest.TestCase):
    """공공데이터포털(apis.data.go.kr)은 http 로는 TCP 연결만 받고 HTTP 응답을 주지
    않아 15초 타임아웃으로 조회가 통째로 실패한다(건축물대장·토지이용계획·토지소유).
    반드시 https 로 호출해야 한다(2026-08 회귀 방지)."""

    def test_no_http_apis_data_go_kr_in_source(self):
        app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
        offenders = []
        for path in app_dir.rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "http://apis.data.go.kr" in line:
                    offenders.append(f"{path.name}:{lineno}")
        self.assertEqual(
            offenders, [], f"apis.data.go.kr 는 https 여야 한다: {offenders}"
        )


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
