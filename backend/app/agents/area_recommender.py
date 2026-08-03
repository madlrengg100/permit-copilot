"""지역 추천 — "○○ 비도시 지역에서 농막 지을 데 찾아줘" 류 탐색형 질의.

지금 진단 파이프라인은 '주소 1건 -> 진단' 구조라 "조건 맞는 곳을 찾아줘"에
바로 답하지 못한다. 여기서는 VWorld 용도지역 폴리곤을 스캔해 '비도시 용도지역'을
확정하고(사용자가 고른 방식), 그 구역 안에 실제로 들어오는 필지를 지번·지목과 함께
리스트로 만든다. 농막처럼 농지 위 시설을 물으면 지목 전·답·과수원을 골라준다.

'클릭하면 그 지점으로 이동 + 자동 진단'하도록, 각 후보는 실제 지번 주소를 갖는다.

정확도 한계(솔직히 밝힐 것):
- 표본이다. VWorld bbox 조회는 상한이 있어 넓은 시·군을 전수하지 못한다.
- 최종 판정(도로 접함·소유·전용 등)은 클릭 후 개별 진단에서 확정된다.
"""

from __future__ import annotations

import asyncio
import logging
import re

from ..tools import vworld

logger = logging.getLogger(__name__)

# 비도시(도시지역 외) 성격의 용도지역. 관리·농림·자연환경보전이 핵심이고,
# 녹지지역은 도시지역이지만 사실상 비시가화라 함께 후보로 본다.
_NON_URBAN = {
    "계획관리지역", "생산관리지역", "보전관리지역",
    "농림지역", "자연환경보전지역",
    "생산녹지지역", "보전녹지지역", "자연녹지지역",
}

# 농막 등 '농지 위 시설'을 물었을 때 고르는 지목(전·답·과수원).
_FARM_JIMOK = {"전", "답", "과수원"}

# 농막류가 아닌 일반 건물이면 지을 만한 지목만(임야·도로·하천·구거·묘지 등 제외).
_BUILDABLE_JIMOK = {"대", "잡종지", "공장용지", "창고용지", "전", "답", "과수원", "목장용지"}

# 농막류 질의 감지 키워드.
_FARM_HINTS = ("농막", "농지", "밭", "논", "텃밭", "농사", "영농", "과수")

# 산지전용 질의 -> 지목 '임'(임야)를 고른다. 실제 개발 가능성(준보전산지 여부·
# 경사도·입목축적·접도)은 클릭 후 개별 진단에서 확정한다.
_FOREST_JIMOK = {"임"}
_FOREST_HINTS = ("산지전용", "산지", "임야", "임업", "산 지", "산림")


def _is_farm_query(building_use: str, query: str) -> bool:
    blob = f"{building_use} {query}"
    return any(h in blob for h in _FARM_HINTS)


def _is_forest_query(building_use: str, query: str) -> bool:
    blob = f"{building_use} {query}"
    return any(h in blob for h in _FOREST_HINTS)


def _region_core(region: str) -> str:
    """'경기도 양평군' -> '양평'. 주소 필터링에 쓸 핵심 시·군·구 이름."""
    for part in reversed(region.split()):
        if len(part) > 1 and part.endswith(("시", "군", "구")):
            return part[:-1]
    parts = region.split()
    return parts[-1] if parts else region


def _region_address_terms(region: str) -> list[str]:
    """후보 주소가 반드시 포함해야 할 사용자의 명시적 행정구역 토큰.

    도/광역시는 주소 표기 방식이 달라질 수 있어 제외하고, 시·군·구 및
    읍·면·동·리를 유지한다. 예를 들어 ``경기도 의왕시 초평동``이면
    의왕시와 초평동을 모두 요구해 인접 수원시 후보가 섞이지 않게 한다.
    """
    terms: list[str] = []
    for raw in region.split():
        term = re.sub(r"[^가-힣0-9]", "", raw)
        if len(term) > 1 and term.endswith(("시", "군", "구", "읍", "면", "동", "리")):
            terms.append(term)
    return terms


async def recommend_areas(
    region: str,
    building_use: str = "",
    query: str = "",
    limit: int = 6,
) -> dict:
    """지역명 -> 비도시 용도지역 안의 후보 필지 리스트."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    # 1) 지역 중심 좌표 — 지역명 자체가 지오코딩 안 되면 '군청/시청'으로 재시도.
    center = None
    for cand in (region, f"{region}청", f"{region} 청사"):
        try:
            center = await vworld.geocode(cand)
            break
        except Exception:
            logger.debug("지역 중심 지오코딩 실패: %s", cand, exc_info=True)
            continue
    if not center:
        return {
            "region": region, "matched": None, "items": [],
            "note": f"'{region}'의 위치를 찾지 못했습니다. 시·군·구 이름을 확인해 주세요.",
        }

    lon0, lat0 = center["lon"], center["lat"]
    forest = _is_forest_query(building_use, query)
    farm = _is_farm_query(building_use, query) and not forest
    core = _region_core(region)
    address_terms = _region_address_terms(region)

    # 2) 중심 주변에서 비도시 용도지역 폴리곤을 모은다(넓게 ±0.1°≈11km).
    dz = 0.1
    polys = await vworld.get_zoning_polygons_bbox(lon0 - dz, lat0 - dz, lon0 + dz, lat0 + dz)
    zone_shapes: list[tuple] = []  # (geom, zone_name)
    for p in polys:
        if p.get("zone") not in _NON_URBAN:
            continue
        try:
            g = shape(p["geometry"]).buffer(0)
        except Exception:
            logger.debug("용도지역 폴리곤 기하 파싱 실패(스킵)", exc_info=True)
            continue
        if not g.is_empty:
            zone_shapes.append((g, p["zone"]))

    if not zone_shapes:
        return {
            "region": region, "matched": center["matched_address"],
            "center": {"lon": lon0, "lat": lat0}, "farm_query": farm, "items": [],
            "note": f"{center['matched_address']} 주변에서 비도시 용도지역을 찾지 못했습니다.",
        }

    non_urban_union = unary_union([g for g, _ in zone_shapes])

    def _zone_at(point) -> str:
        for g, name in zone_shapes:
            if g.contains(point):
                return name
        return "비도시지역"

    # 3) 필지(연속지적도)를 받아 비도시 구역 '안'에 들고 지목이 맞는 것만 남긴다.
    #    ★ VWorld 연속지적도 bbox 는 10km² 상한이라, 넓은 박스 한 번이 아니라
    #      비도시 폴리곤마다 '작은 박스(<10km²)'로 나눠 조회한다.
    target = _FOREST_JIMOK if forest else (_FARM_JIMOK if farm else _BUILDABLE_JIMOK)

    def _bbox_km2(minx, miny, maxx, maxy) -> float:
        # 위도 ~37° 근사: 경도 1°≈88km, 위도 1°≈111km.
        return (maxx - minx) * 88.0 * (maxy - miny) * 111.0

    # 요청 지역(중심점)에 가까운 폴리곤부터 조회해야 인접 시·군이 아니라
    # 해당 시·군 안의 필지가 먼저 잡힌다. 거리 우선, 그다음 작은 폴리곤.
    def _poly_sort_key(zs):
        g, _ = zs
        p = g.representative_point()
        dist = ((p.x - lon0) * 88.0) ** 2 + ((p.y - lat0) * 111.0) ** 2
        return (dist, g.area)

    # 조회할 박스를 먼저 정하고(가까운 폴리곤 우선, 상한 MAX_QUERIES),
    # 지적도 조회는 순차가 아니라 '한꺼번에 병렬로' 던진다 — 이게 속도의 핵심.
    # (예전엔 14번을 하나씩 기다려 10초 넘게 걸렸다.)
    MAX_QUERIES = 12  # 동시 조회 상한(지연·부하 균형)
    boxes: list[tuple] = []
    for g, _zone in sorted(zone_shapes, key=_poly_sort_key):
        if len(boxes) >= MAX_QUERIES:
            break
        minx, miny, maxx, maxy = g.bounds
        if _bbox_km2(minx, miny, maxx, maxy) > 9.0:
            # 폴리곤이 너무 크면(산지 등) 대표점 주변 ~2.4km 박스만 표본 조회.
            rp0 = g.representative_point()
            hlon, hlat = 0.013, 0.011  # 약 2.3km × 2.4km ≈ 5.6km²
            boxes.append((rp0.x - hlon, rp0.y - hlat, rp0.x + hlon, rp0.y + hlat))
        else:
            boxes.append((minx, miny, maxx, maxy))

    results = await asyncio.gather(
        *[vworld.get_parcel_features_bbox(*b) for b in boxes],
        return_exceptions=True,
    )

    # 조밀한 시·군은 target 지목 필지가 수천 개라, 필지마다 큰 union 에
    # contains() 를 돌리면 CPU 가 폭발한다(양평 78초의 원인). prepared geometry 로
    # 포함 판정을 빠르게 하고, 필요한 만큼만 모으면 바로 멈춘다(조기 종료).
    from shapely.prepared import prep

    prepared = prep(non_urban_union)
    enough = limit * 3

    cands: list[dict] = []
    seen: set[tuple] = set()
    for feats in results:
        if isinstance(feats, BaseException) or not feats:
            continue
        for f in feats:
            if f.get("jimok") not in target:
                continue
            try:
                rp = shape(f["geometry"]).representative_point()
            except Exception:
                logger.debug("후보 필지 대표점 계산 실패(스킵)", exc_info=True)
                continue
            if not prepared.contains(rp):
                continue
            key = (round(rp.x, 4), round(rp.y, 4))
            if key in seen:
                continue
            seen.add(key)
            cands.append({
                "address": f.get("address") or "",
                "lon": rp.x, "lat": rp.y,
                "zone": _zone_at(rp),
                "jimok": f.get("jimok") or "",
                "area_m2": round(vworld.geodesic_area_m2(f["geometry"]), 1),
                "pnu": f.get("pnu", ""),
                "geometry": f["geometry"],  # 전용 제한(보전산지 등) 후보 검증용
            })
        if len(cands) >= enough:
            break  # 충분히 모았으면 나머지 박스는 처리하지 않는다

    # 요청한 행정구역 밖의 후보는 절대 표시하지 않는다. 예전에는 지역 일치
    # 후보를 앞에 '정렬'만 해서, 의왕 요청에 인접 수원 필지가 노출될 수 있었다.
    cands = [c for c in cands if c["address"]]
    if address_terms:
        cands = [
            c for c in cands
            if all(term in c["address"] for term in address_terms)
        ]
    else:
        cands = [c for c in cands if core in c["address"]]

    # 요청 시설이 그 필지에서 법령상 불가면(예: 움막+농지) 후보에서 뺀다 — '다 안 되는
    # 지역만 추천'하는 누수를 막는다. 판정은 진단과 동일한 결정식(facility_rules)을
    # 재사용한다: 하드코딩 지목 목록이 아니라 실제 지목 분류 + 농지법.
    from ..tools.facility_rules import farmland_facility_verdict

    restricted = sum(
        1 for c in cands
        if farmland_facility_verdict(building_use, c["jimok"]) == "not_allowed"
    )
    cands = [
        c for c in cands
        if farmland_facility_verdict(building_use, c["jimok"]) != "not_allowed"
    ]
    cands.sort(key=lambda c: -c["area_m2"])

    # 후보마다 '실제 공간 규제'를 진단과 같은 로직(land_conversion.assess)으로 확인해,
    # 보전산지·농업진흥지역 전용 제한(RESTRICTED_REVIEW)처럼 그 시설이 건축 불가한
    # 필지는 추천에서 뺀다 — 지목만 보고 추천해 '건축 불가'를 안내하던 누수를 막는다.
    # 상위 후보부터 검사해 limit 개를 채우면 멈춘다(불필요한 공간조회 최소화).
    from ..tools import jimok as jimok_tool, land_conversion

    async def _buildable(cand: dict) -> bool:
        try:
            res = await land_conversion.assess(
                cand["geometry"], jimok_tool.classify(cand["jimok"])
            )
        except Exception:
            logger.debug("후보 전용 규제 검증 실패(보수적으로 제외)", exc_info=True)
            return False
        # 전용이 크게 제한(보전산지·농업진흥지역 등)돼 예외 입증 전엔 건축 불가인 필지 제외.
        return res.get("status") != "RESTRICTED_REVIEW"

    conversion_restricted = 0
    checked: list[dict] = []
    for i in range(0, len(cands), 8):  # 8개씩 병렬 검사, limit 채우면 중단
        batch = cands[i:i + 8]
        oks = await asyncio.gather(*[_buildable(c) for c in batch])
        for c, ok in zip(batch, oks):
            if ok:
                checked.append(c)
            else:
                conversion_restricted += 1
        if len(checked) >= limit:
            break
    cands = checked
    items = cands[:limit]
    for it in items:
        it.pop("geometry", None)  # 무거운 폴리곤은 세션·응답에 남기지 않는다

    if not items and conversion_restricted:
        # 후보가 있었으나 보전산지·농업진흥지역 전용 제한으로 전부 건축 불가라 제외된 경우.
        note = (
            f"{center['matched_address']} 주변 비도시 후보는 보전산지·농업진흥지역 등 "
            f"전용 제한이 중첩돼 {(building_use or '해당 시설')} 설치가 어렵습니다. "
            "다른 시·군이나 구체적인 동·리를 지정해 다시 찾아보시겠어요?"
        )
    elif not items and restricted:
        # 후보가 있었으나 요청 시설이 그 필지(농지)에서 법령상 불가라 전부 제외된 경우.
        note = (
            f"{center['matched_address']} 주변 비도시 후보는 대부분 농지(전·답)여서 "
            f"{(building_use or '해당 시설')}은(는) 농지법상 설치할 수 없습니다. "
            "농막(신고 후 20㎡)이나 다른 용도로 다시 찾아보시겠어요?"
        )
    elif not items:
        kind = "임야(산지)" if forest else ("농지(전·답·과수원)" if farm else "건축 가능 지목")
        note = (
            f"{center['matched_address']} 주변 비도시 용도지역 안에서 "
            f"{kind} 필지를 찾지 못했습니다. "
            "범위를 넓히거나 다른 시·군으로 시도해 보세요."
        )
    elif forest:
        note = (
            "비도시 용도지역 안의 임야(산지) 필지 표본입니다. 산지전용이 실질적으로 "
            "수월한 곳은 '준보전산지'이며, 보전산지·공익용산지는 허용 시설이 크게 "
            "제한됩니다. 준보전산지 여부·경사도·입목축적·접도 기준과 대체산림자원조성비는 "
            "지점을 눌러 개별 진단에서 확인하세요."
        )
    elif farm:
        note = (
            "비도시(관리·농림 등) 용도지역 안의 농지 필지 표본입니다. "
            "농막 설치·소유·도로 접함 여부는 지점을 눌러 개별 진단에서 확인하세요."
        )
    else:
        note = (
            "비도시 용도지역 안의 건축 가능 지목 필지 표본입니다. "
            "지점을 누르면 그 위치로 이동해 개별 진단을 실행합니다."
        )

    return {
        "region": region,
        "matched": center["matched_address"],
        "center": {"lon": lon0, "lat": lat0},
        "farm_query": farm,
        "forest_query": forest,
        "items": items,
        "note": note,
    }
