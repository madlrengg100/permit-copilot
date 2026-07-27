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

_ENDPOINT = "https://api.vworld.kr/ned/data/getLandUseAttr"


async def get_landuse_districts(pnu: str) -> list[str]:
    """PNU -> 용도지구·구역 목록(용도지역 제외). 키/데이터 없으면 []."""
    if USE_MOCK or not VWORLD_KEY or not pnu:
        return []

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
            r = await client.get(_ENDPOINT, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        # 네트워크·인증·스키마 문제로 실패해도 진단 전체를 막지 않는다.
        return []

    fields = (data.get("landUses") or {}).get("field") or []
    if isinstance(fields, dict):  # 단건이면 dict 로 올 수 있다
        fields = [fields]

    seen: list[str] = []
    for f in fields:
        name = (f.get("prposAreaDstrcCodeNm") or "").strip()
        if not name:
            continue
        # 용도지역('~지역': 일반상업지역·도시지역 등)은 VWorld 용도지역 조회로
        # 이미 받으므로 제외하고, 용도지구·구역만 남긴다.
        if name.endswith("지역"):
            continue
        if name not in seen:
            seen.append(name)
    return seen
