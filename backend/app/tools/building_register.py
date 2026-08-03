"""건축HUB 건축물대장 표제부 조회."""

from __future__ import annotations

from typing import Any

import httpx

from ..config import DATA_GO_KR_SERVICE_KEY, JUSO_CONFM_KEY
from ..cache import async_ttl_cache

# apis.data.go.kr 은 http 로는 TCP 연결만 받고 HTTP 응답을 주지 않아 15초 타임아웃으로
# 조회가 실패한다(2026-08 확인). https 는 즉시 정상 응답하므로 https 로 호출한다.
BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService"
JUSO_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"


def pnu_params(pnu: str) -> dict[str, str]:
    """19자리 PNU를 건축HUB의 시군구·법정동·본번·부번으로 분해한다."""
    digits = "".join(ch for ch in (pnu or "") if ch.isdigit())
    if len(digits) != 19:
        raise ValueError("건축물대장 조회에는 19자리 PNU가 필요합니다.")
    return {
        "sigunguCd": digits[:5],
        "bjdongCd": digits[5:10],
        "platGbCd": "1" if digits[10] == "2" else "0",
        "bun": digits[11:15],
        "ji": digits[15:19],
    }


def _items(data: dict) -> list[dict[str, Any]]:
    body = data.get("response", {}).get("body") or {}
    raw = (body.get("items") or {}).get("item") or []
    if isinstance(raw, dict):
        return [raw]
    return raw if isinstance(raw, list) else []


def _result(data: dict, source: str) -> dict:
    items = _items(data)
    buildings = [
        {
            "name": (item.get("bldNm") or item.get("dongNm") or "건축물").strip(),
            "dong": str(item.get("dongNm") or "").strip(),
            "main_use": str(item.get("mainPurpsCdNm") or item.get("etcPurps") or "").strip(),
            "structure": str(item.get("strctCdNm") or item.get("etcStrct") or "").strip(),
            "ground_floors": item.get("grndFlrCnt"),
            "underground_floors": item.get("ugrndFlrCnt"),
            "building_area_m2": item.get("archArea"),
            "total_area_m2": item.get("totArea"),
            "use_approval_date": str(item.get("useAprDay") or "").strip(),
            "register_type": str(item.get("regstrGbCdNm") or "").strip(),
        }
        for item in items
    ]
    total = (
        data.get("response", {}).get("body", {}).get("totalCount")
        or len(buildings)
    )
    return {
        "status": "FOUND" if buildings else "CLEAR",
        "has_buildings": bool(buildings),
        "count": int(total),
        "buildings": buildings,
        "source": source,
        "note": (
            "기존 건축물이 확인됩니다. 신축을 전제로 할 경우 소유권·임대차 관계를 "
            "확인하고 철거 및 건축물대장 말소 절차를 별도로 검토해야 합니다."
            if buildings
            else "건축물대장 표제부가 조회되지 않았습니다. 현장 건축물과 무허가·미등재 건축물은 별도 확인해야 합니다."
        ),
    }


async def _query_title(loc: dict, timeout: float, source: str) -> dict:
    """건축HUB 표제부를 지번 파라미터(시군구·법정동·본번·부번)로 조회한다."""
    params = {
        "serviceKey": DATA_GO_KR_SERVICE_KEY,
        **loc,
        # 건축HUB는 일부 필지에서 100건 요청 시 500을 반환한다.
        # 공식 예시와 같은 10건으로 조회하고 전체 건수는 totalCount로 알린다.
        "numOfRows": "10",
        "pageNo": "1",
        "_type": "json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{BASE_URL}/getBrTitleInfo", params=params)
        response.raise_for_status()
        data = response.json()
    header = data.get("response", {}).get("header") or {}
    if str(header.get("resultCode", "")) not in {"00", "0"}:
        raise RuntimeError(header.get("resultMsg") or "건축물대장 API 오류")
    return _result(data, source)


async def _juso_loc(address: str, timeout: float) -> dict | None:
    """도로명/지번 주소를 juso로 검색해 건축HUB 조회용 지번 파라미터를 얻는다.
    건축물대장이 토지 필지가 아닌 건물 대표지번에 등록된 경우를 보정한다."""
    keyword = (address or "").split("(")[0].strip()  # 지오코더가 붙인 '(동,건물명)' 제거
    if not (JUSO_CONFM_KEY and keyword):
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(JUSO_URL, params={
                "confmKey": JUSO_CONFM_KEY, "currentPage": "1",
                "countPerPage": "1", "keyword": keyword, "resultType": "json",
            })
            r.raise_for_status()
            juso = ((r.json().get("results") or {}).get("juso") or [])
    except (httpx.HTTPError, ValueError):
        return None
    if not juso:
        return None
    j = juso[0]
    adm = "".join(ch for ch in str(j.get("admCd") or "") if ch.isdigit())
    if len(adm) != 10 or not str(j.get("lnbrMnnm") or "").isdigit():
        return None
    return {
        "sigunguCd": adm[:5],
        "bjdongCd": adm[5:10],
        "platGbCd": "1" if str(j.get("mtYn")) == "1" else "0",
        "bun": f"{int(j['lnbrMnnm']):04d}",
        "ji": f"{int(j.get('lnbrSlno') or 0):04d}",
    }


@async_ttl_cache(ttl_seconds=300, maxsize=2048)
async def lookup(pnu: str, timeout: float = 15.0, address: str = "") -> dict:
    if not DATA_GO_KR_SERVICE_KEY:
        return {
            "status": "NOT_CONFIGURED",
            "has_buildings": None,
            "buildings": [],
            "message": "공공데이터포털 건축물대장 인증키가 설정되지 않았습니다.",
        }
    src = "국토교통부 건축HUB 건축물대장 표제부"
    result: dict | None = None
    try:
        result = await _query_title(pnu_params(pnu), timeout, src)
    except httpx.HTTPStatusError as exc:
        # httpx 예외 문자열에는 serviceKey가 포함된 요청 URL이 들어갈 수 있다.
        # 외부 응답·로그로 인증키가 새지 않도록 상태 코드만 보존한다.
        result = {"status": "UNAVAILABLE", "has_buildings": None, "buildings": [],
                  "message": f"건축물대장 API HTTP {exc.response.status_code}"}
    except (httpx.HTTPError, RuntimeError):
        result = {"status": "UNAVAILABLE", "has_buildings": None, "buildings": [],
                  "message": "건축물대장 조회 실패"}
    except ValueError as exc:  # 19자리 PNU가 아님 — 주소 폴백으로 이어간다.
        result = {"status": "UNAVAILABLE", "has_buildings": None, "buildings": [],
                  "message": str(exc)}

    # PNU(토지 필지)로 대장이 안 잡히면 주소 기반 건물 대표지번으로 한 번 더 조회한다.
    if not result.get("has_buildings"):
        loc = await _juso_loc(address, timeout)
        if loc is not None:
            try:
                alt = await _query_title(loc, timeout, src + " (주소 보정)")
                if alt.get("has_buildings"):
                    return alt
            except (httpx.HTTPError, ValueError, RuntimeError):
                pass
    return result
