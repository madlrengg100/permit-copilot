"""토지이용계획 속성조회 — 필지 PNU -> 지역지구 지정 목록.

VWorld 국토정보(NED) getLandUseAttr 를 호출한다. 한 필지의 '지역지구 등 지정여부'
전체(용도지역·용도지구·구역·도시계획시설 등)를 돌려주므로, VWorld 용도지역 조회로는
안 잡히던 경관지구·고도지구·지구단위계획구역·과밀억제권역 등을 여기서 보강한다.

기존 VWorld 키(VWORLD_KEY)로 바로 되며(별도 data.go.kr 키 불필요), 키가 없거나
mock 모드면 조용히 빈 목록을 돌려준다.
"""

from __future__ import annotations

import httpx

from ..config import USE_MOCK, VWORLD_DOMAIN, VWORLD_KEY
from ..cache import async_ttl_cache

_ENDPOINT = "https://api.vworld.kr/ned/data/getLandUseAttr"

# 국토계획법상 용도지역 코드. UQQ(지구단위계획구역)처럼 이름이 비슷한 다른
# 규제를 제외하지 않도록 코드의 세 번째 문자까지만 뭉뚱그려 판단하지 않는다.
_ZONING_CODE_PREFIXES = ("UQA", "UQB", "UQC", "UQD")


def _is_zoning_record(code: str) -> bool:
    return len(code) == 6 and code.startswith(_ZONING_CODE_PREFIXES)


def _parse_landuse_payload(data: dict) -> dict:
    payload = data.get("landUses") or {}
    if payload.get("resultCode") not in (None, "", "OK"):
        return {
            "status": "UNAVAILABLE",
            "source": "VWorld NED 토지이용계획정보",
            "records": [],
            "active_records": [],
            "error": payload.get("resultCode"),
        }
    fields = payload.get("field") or []
    if isinstance(fields, dict):
        fields = [fields]

    records: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for field in fields:
        name = str(field.get("prposAreaDstrcCodeNm") or "").strip()
        code = str(field.get("prposAreaDstrcCode") or "").strip()
        relation_code = str(field.get("cnflcAt") or "").strip()
        relation = str(field.get("cnflcAtNm") or "").strip()
        if not name:
            continue
        key = (code, name, relation_code)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "name": name,
            "code": code,
            "relation": relation,
            "relation_code": relation_code,
            "active": relation_code in {"1", "2"} or relation in {"포함", "저촉"},
            "is_zoning": _is_zoning_record(code),
            "updated_at": field.get("lastUpdtDt"),
            "registered_at": field.get("registDt"),
        })

    return {
        "status": "AVAILABLE",
        "source": "VWorld NED 토지이용계획정보",
        "records": records,
        "active_records": [record for record in records if record["active"]],
        "updated_at": max(
            (record["updated_at"] for record in records if record.get("updated_at")),
            default=None,
        ),
    }


@async_ttl_cache(ttl_seconds=900, maxsize=2048)
async def get_landuse_designations(pnu: str) -> dict:
    """PNU의 토지이용계획 지정정보와 조회 상태를 반환한다.

    ``포함``·``저촉``만 현재 필지 규제로 사용한다. ``접함``은 주변 경계 정보일
    뿐 현재 필지 중첩이 아니므로 판정 목록에 넣지 않는다.
    """
    if USE_MOCK or not VWORLD_KEY or not pnu:
        return {
            "status": "NOT_CONFIGURED",
            "source": "VWorld NED 토지이용계획정보",
            "records": [],
            "active_records": [],
        }

    params = {
        "key": VWORLD_KEY,
        "pnu": pnu,
        "format": "json",
        "numOfRows": "100",
        "pageNo": "1",
        "domain": VWORLD_DOMAIN,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(_ENDPOINT, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "source": "VWorld NED 토지이용계획정보",
            "records": [],
            "active_records": [],
            "error": type(exc).__name__,
        }

    return _parse_landuse_payload(data)


async def get_landuse_districts(pnu: str) -> list[str]:
    """PNU -> 현재 필지에 포함·저촉된 용도지구·구역(용도지역 제외)."""
    result = await get_landuse_designations(pnu)
    return list(dict.fromkeys(
        record["name"]
        for record in result.get("active_records", [])
        if not record.get("is_zoning")
    ))
