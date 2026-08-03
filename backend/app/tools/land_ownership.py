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
from ..config import DATA_GO_KR_SERVICE_KEY

# 공공데이터포털/NSDI '국토교통부_토지소유정보' 속성 API. 소유구분(posesnSeCodeNm)을 PNU로 준다.
# 활용신청 승인 후 상세문서의 실제 요청주소가 다르면 env 로 덮어쓴다(코드 수정 없이).
LAND_OWNERSHIP_API_URL = os.getenv(
    "LAND_OWNERSHIP_API_URL",
    os.getenv(  # 구 변수명 호환
        "LAND_CHARACTERISTICS_API_URL",
        "https://apis.data.go.kr/1611000/nsdi/LandOwnershipService/attr/getLandOwnership",
    ),
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
    """응답 구조가 기관마다 달라, 소유구분으로 보이는 문자열을 방어적으로 찾는다."""
    # 1) 필드명이 소유구분을 가리키는 문자열 값 우선.
    for key, value in _walk(data):
        if isinstance(value, str) and value.strip():
            if any(hint in str(key).lower() for hint in _OWNER_FIELD_HINTS):
                return value.strip()
    # 2) 못 찾으면 값 자체가 소유구분 명칭 토큰을 포함하는 것을 찾는다.
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
    if not DATA_GO_KR_SERVICE_KEY:
        return {
            "status": "NOT_CONFIGURED",
            "ownership": None,
            "detail": "토지특성정보 인증키(DATA_GO_KR_SERVICE_KEY)가 없습니다.",
        }
    params = {
        "serviceKey": DATA_GO_KR_SERVICE_KEY,
        "pnu": digits,
        "format": "json",
        "numOfRows": "10",
        "pageNo": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(LAND_OWNERSHIP_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        # 예외 문자열에 serviceKey 가 든 URL 이 노출될 수 있어 상태코드만 보존한다.
        return {
            "status": "UNAVAILABLE",
            "ownership": None,
            "detail": f"토지특성정보 API HTTP {exc.response.status_code}",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "UNAVAILABLE",
            "ownership": None,
            "detail": f"토지특성정보 조회 실패: {type(exc).__name__}",
        }

    name = _ownership_name(data)
    ownership = _classify(name)
    return {
        "status": "FOUND" if ownership else "CLEAR",
        "ownership": ownership,
        "detail": name,
        "source": "국토교통부 토지특성정보(소유구분)",
    }
