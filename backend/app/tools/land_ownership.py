"""토지 소유구분(국유·공유·사유) 조회 — 배수로가 지나는 필지가 실제 사유지인지
판정하는 데 쓴다. 공공데이터포털/NSDI '국토교통부_토지소유정보'(토지대장 소유현황)에서
PNU 단위로 소유구분명(posesnSeCodeNm)을 받아 '국공유'(통과 가능)/'사유'(승낙 필요)로 분류한다.

지목은 사유 여부의 proxy일 뿐이고(지목 '도로'라도 사도, '구거'라도 사유일 수 있음),
소유구분이 실제 근거다. API 키·엔드포인트가 없거나 조회가 안 되면 ownership=None 을
돌려주고, 호출부는 지목 proxy 로 폴백한다.
"""

from __future__ import annotations

import os

import httpx

from ..cache import async_ttl_cache
from ..config import USE_MOCK, VWORLD_DOMAIN, VWORLD_KEY

# 토지소유정보(소유구분)는 VWorld 국토정보(NED) getPossessionAttr 에서 받는다. 토지이용계획
# (getLandUseAttr)과 같은 플랫폼·키(VWORLD_KEY)라 별도 data.go.kr 활용신청이 필요 없다.
# (data.go.kr/1611000 NSDI 토지소유 서비스는 이 키에 미등록이라 NO_OPENAPI_SERVICE 400을 낸다.)
# 소유구분명은 posesnSeCodeNm 이다: 개인·법인·종중·종교단체·외국인=사유,
# 국유지·시 도유지·군유지·구유지=국공유.
LAND_OWNERSHIP_API_URL = os.getenv(
    "LAND_OWNERSHIP_API_URL",
    "https://api.vworld.kr/ned/data/getPossessionAttr",
)

# 소유구분명 → 통과 가능(공공) / 사유. 명칭 표기가 기관마다 달라 토큰 포함으로 판정한다.
_PUBLIC_OWNER_TOKENS = ("국유", "공유", "시유", "도유", "군유", "구유", "국·공유", "공공")
_PRIVATE_OWNER_TOKENS = (
    "개인", "법인", "사유", "종중", "종교", "외국인", "민유", "기타",
)
# 소유구분 값이 담긴 필드명 후보(소문자 비교). NSDI/타 기관 표기를 함께 커버한다.
_OWNER_FIELD_HINTS = ("posesn", "ownsh", "sownr", "소유")


def _walk(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _ownership_name(data: dict) -> str | None:
    """VWorld getPossessionAttr 응답에서 소유구분명(posesnSeCodeNm)을 찾는다."""
    # 1) 소유구분명 필드를 정확히 집는다(ownshipChgCauseCodeNm='소유권이전' 같은
    #    변동원인 필드를 소유구분으로 오인하지 않도록 정확한 키 우선).
    for key, value in _walk(data):
        if str(key) == "posesnSeCodeNm" and isinstance(value, str) and value.strip():
            return value.strip()
    # 2) 폴백: 값 자체가 소유구분 명칭 토큰을 포함하는 것을 찾는다(다른 표기 대비).
    for _key, value in _walk(data):
        if isinstance(value, str) and any(
            t in value for t in (*_PUBLIC_OWNER_TOKENS, *_PRIVATE_OWNER_TOKENS)
        ):
            return value.strip()
    return None


def _classify(name: str | None) -> str | None:
    if not name:
        return None
    if any(token in name for token in _PUBLIC_OWNER_TOKENS):
        return "국공유"
    if any(token in name for token in _PRIVATE_OWNER_TOKENS):
        return "사유"
    return None


@async_ttl_cache(ttl_seconds=600, maxsize=2048)
async def lookup_ownership(pnu: str, timeout: float = 15.0) -> dict:
    """PNU 필지의 소유구분을 조회한다. ownership 은 '국공유'/'사유'/None(미상)."""
    digits = "".join(ch for ch in (pnu or "") if ch.isdigit())
    if len(digits) != 19:
        return {"status": "UNAVAILABLE", "ownership": None, "detail": "19자리 PNU 필요"}
    if USE_MOCK or not VWORLD_KEY:
        return {
            "status": "NOT_CONFIGURED",
            "ownership": None,
            "detail": "VWorld 키(VWORLD_KEY)가 없습니다.",
        }
    params = {
        "key": VWORLD_KEY,
        "pnu": digits,
        "format": "json",
        "numOfRows": "5",
        "pageNo": "1",
        "domain": VWORLD_DOMAIN,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(LAND_OWNERSHIP_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        # 예외 문자열에 key 가 든 URL 이 노출될 수 있어 상태코드만 보존한다.
        return {
            "status": "UNAVAILABLE",
            "ownership": None,
            "detail": f"토지소유정보 API HTTP {exc.response.status_code}",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "UNAVAILABLE",
            "ownership": None,
            "detail": f"토지소유정보 조회 실패: {type(exc).__name__}",
        }

    name = _ownership_name(data)
    ownership = _classify(name)
    return {
        "status": "FOUND" if ownership else "CLEAR",
        "ownership": ownership,
        "detail": name,
        "source": "VWorld 국토정보(NED) 토지소유정보(소유구분)",
    }
