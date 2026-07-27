"""VWorld 오픈API 클라이언트.

세 가지 공간 조회를 담당한다:
  1. 주소 -> 좌표      (지오코더)
  2. 좌표 -> 필지      (연속지적도: PNU, 지목, 면적, 경계 폴리곤)
  3. PNU  -> 용도지역   (용도지역지구도)

VWORLD_KEY 가 없으면 목 데이터를 돌려주므로 키 없이도 파이프라인 전체가 돈다.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from pyproj import Geod

from ..config import (
    LAYER_PARCEL,
    LAYERS_ZONING,
    USE_MOCK,
    VWORLD_BASE,
    VWORLD_DOMAIN,
    VWORLD_KEY,
)


class VWorldError(RuntimeError):
    pass


# --------------------------------------------------------------- 기하 계산

_GEOD = Geod(ellps="WGS84")


def outer_rings(geometry: dict) -> list[list[list[float]]]:
    """Polygon / MultiPolygon 을 외곽 링 목록으로 통일한다.

    VWorld 연속지적도는 MultiPolygon 으로 온다. 한 필지가 여러 조각으로
    나뉘어 있을 수 있으므로 링을 전부 돌려준다.
    """
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        return [coords[0]] if coords else []
    if gtype == "MultiPolygon":
        return [poly[0] for poly in coords if poly]
    return []


def geodesic_area_m2(geometry: dict) -> float:
    """WGS84 경위도 폴리곤의 측지 면적(m²). 투영 없이 타원체상에서 계산한다."""
    total = 0.0
    for ring in outer_rings(geometry):
        if len(ring) < 3:
            continue
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        area, _perimeter = _GEOD.polygon_area_perimeter(lons, lats)
        total += abs(area)
    return total


async def _get(path: str, params: dict) -> dict:
    # domain 은 등록 도메인 검증용 — 없으면 data 서비스가 INCORRECT_KEY 로 거부한다.
    params = {**params, "key": VWORLD_KEY, "format": "json", "domain": VWORLD_DOMAIN}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{VWORLD_BASE}/{path}", params=params)
        r.raise_for_status()
        data = r.json()

    # VWorld 는 오류도 HTTP 200 으로 준다. status 를 직접 확인해야 한다.
    resp = data.get("response", {})
    if resp.get("status") == "ERROR":
        err = resp.get("error", {})
        code = err.get("code", "UNKNOWN")
        text = err.get("text", "")
        if code == "INCORRECT_KEY":
            raise VWorldError(
                f"VWorld 인증키가 거부되었습니다({code}). VWORLD_KEY 값과, "
                f"VWORLD_DOMAIN({VWORLD_DOMAIN})이 인증키에 등록한 서비스URL과 "
                f"일치하는지 확인하세요."
            )
        raise VWorldError(f"VWorld 오류 [{code}] {text}")

    return data


async def get_individual_land_price(pnu: str) -> dict | None:
    """PNU로 최신 개별공시지가(원/㎡)를 조회한다.

    연속지적도 레이어는 최근 응답에서 ``jiga`` 속성을 주지 않으므로,
    VWorld NED 개별공시지가 전용 API를 별도로 사용한다.
    """
    if not pnu:
        return None
    if USE_MOCK:
        return {"price_won_per_m2": 1_000_000, "year": "2026", "date": "2026-04-30"}
    try:
        params = {
            "pnu": pnu,
            "format": "json",
            "numOfRows": "20",
            "pageNo": "1",
            "key": VWORLD_KEY,
            "domain": VWORLD_DOMAIN,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.vworld.kr/ned/data/getIndvdLandPriceAttr",
                params=params,
            )
            response.raise_for_status()
            fields = (
                response.json().get("indvdLandPrices", {}).get("field", [])
                or []
            )
        valid = [item for item in fields if _to_int(item.get("pblntfPclnd"))]
        if not valid:
            return None
        latest = max(
            valid,
            key=lambda item: (
                int(item.get("stdrYear") or 0),
                int(item.get("stdrMt") or 0),
                item.get("pblntfDe") or "",
            ),
        )
        return {
            "price_won_per_m2": _to_int(latest.get("pblntfPclnd")),
            "year": latest.get("stdrYear"),
            "date": latest.get("pblntfDe"),
        }
    except Exception:
        # 공시지가 부가 조회 실패가 필지·건축 진단 전체를 막아서는 안 된다.
        return None


# ---------------------------------------------------------------- 지오코딩


async def geocode(address: str) -> dict:
    """주소 문자열 -> {lon, lat, matched_address}.

    도로명(ROAD)·지번(PARCEL)을 '순차'로 시도하면, 한쪽 요청이 느릴 때 최악
    30초(15초×2)까지 멈춘다. 그래서 둘을 '동시에' 던지고 먼저 성공하는 쪽을
    즉시 채택한다(나머지는 취소). 지번 주소면 PARCEL 이 바로 성공해, ROAD 의
    느린 응답을 기다리지 않는다.
    """
    if USE_MOCK:
        return {"lon": 127.0286, "lat": 37.4979, "matched_address": f"{address} (mock)"}

    async def _try(addr_type: str) -> dict | None:
        try:
            data = await _get(
                "address",
                {
                    "service": "address",
                    "request": "getcoord",
                    "version": "2.0",
                    "crs": "EPSG:4326",
                    "type": addr_type,
                    "address": address,
                },
            )
        except Exception:
            return None
        resp = data.get("response", {})
        if resp.get("status") == "OK":
            point = resp["result"]["point"]
            return {
                "lon": float(point["x"]),
                "lat": float(point["y"]),
                "matched_address": resp.get("refined", {}).get("text", address),
            }
        return None

    # 읍·면·동·리와 번지가 있는 입력은 지번 주소다. ROAD/PARCEL을 경쟁시켜
    # 먼저 끝난 결과를 쓰면 ROAD가 동명이번지의 다른 지역을 반환할 수 있으므로
    # 지번 조회를 우선 확정하고, 실패할 때만 도로명 조회로 보완한다.
    is_parcel_address = bool(
        re.search(r"(?:읍|면|동|리)\s+(?:산\s*)?\d+(?:-\d+)?(?:\D|$)", address)
    )
    if is_parcel_address:
        parcel_result = await _try("PARCEL")
        if parcel_result:
            return parcel_result
        road_result = await _try("ROAD")
        if road_result:
            return road_result
        raise VWorldError(f"주소를 찾을 수 없습니다: {address}")

    tasks = [asyncio.create_task(_try(t)) for t in ("ROAD", "PARCEL")]
    result: dict | None = None
    pending = set(tasks)
    try:
        while pending and result is None:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for d in done:
                if d.result():
                    result = d.result()
                    break
    finally:
        for t in pending:
            t.cancel()

    if result:
        return result
    raise VWorldError(f"주소를 찾을 수 없습니다: {address}")


async def search_addresses(query: str, size: int = 8) -> list[dict]:
    """불완전한 도로명·지번으로 클릭 가능한 주소 후보를 찾는다."""
    if USE_MOCK:
        return [{
            "title": query,
            "road": query,
            "parcel": "",
            "lon": 127.0286,
            "lat": 37.4979,
        }]

    results: list[dict] = []
    seen: set[str] = set()
    for category in ("road", "parcel"):
        data = await _get(
            "search",
            {
                "service": "search",
                "request": "search",
                "version": "2.0",
                "crs": "EPSG:4326",
                "query": query,
                "type": "address",
                "category": category,
                "size": str(size),
                "page": "1",
            },
        )
        items = data.get("response", {}).get("result", {}).get("items", []) or []
        for item in items:
            address = item.get("address", {}) or {}
            road = address.get("road", "")
            parcel = address.get("parcel", "")
            label = road or parcel or re.sub(r"<[^>]+>", "", item.get("title", ""))
            if not label or label in seen:
                continue
            point = item.get("point", {}) or {}
            try:
                lon, lat = float(point["x"]), float(point["y"])
            except (KeyError, TypeError, ValueError):
                continue
            seen.add(label)
            results.append({
                "title": re.sub(r"<[^>]+>", "", item.get("title", label)),
                "road": road,
                "parcel": parcel,
                "address": label,
                "lon": lon,
                "lat": lat,
            })
            if len(results) >= size:
                return results
    return results


async def search_places(query: str, size: int = 8) -> list[dict]:
    """학교·관공서·상호 같은 장소명(POI)을 좌표와 주소로 검색한다."""
    if USE_MOCK:
        return [{
            "title": query,
            "category": "장소",
            "road": "",
            "parcel": "",
            "address": query,
            "lon": 127.0286,
            "lat": 37.4979,
        }]

    data = await _get(
        "search",
        {
            "service": "search",
            "request": "search",
            "version": "2.0",
            "crs": "EPSG:4326",
            "query": query,
            "type": "PLACE",
            "size": str(size),
            "page": "1",
        },
    )
    items = data.get("response", {}).get("result", {}).get("items", []) or []
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        address = item.get("address", {}) or {}
        road = address.get("road", "")
        parcel = address.get("parcel", "")
        title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
        label = road or parcel
        point = item.get("point", {}) or {}
        try:
            lon, lat = float(point["x"]), float(point["y"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (title, label)
        if not title or key in seen:
            continue
        seen.add(key)
        results.append({
            "title": title,
            "category": item.get("category", ""),
            "road": road,
            "parcel": parcel,
            "address": label,
            "lon": lon,
            "lat": lat,
        })
    return results


# ------------------------------------------------------------------- 필지


async def get_parcel(lon: float, lat: float) -> dict:
    """좌표가 포함된 필지의 PNU / 지번 / 지목 / 면적(m²) / 경계 폴리곤."""
    if USE_MOCK:
        # 강남구 역삼동 인근 가상 필지 (약 660m²)
        d = 0.00045
        return {
            "pnu": "1168010100100000000",
            "jibun": "역삼동 123-4 (mock)",
            "jimok": "대",
            "area_m2": 660.0,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [lon - d, lat - d], [lon + d, lat - d],
                    [lon + d, lat + d], [lon - d, lat + d],
                    [lon - d, lat - d],
                ]],
            },
        }

    data = await _get(
        "data",
        {
            "service": "data",
            "request": "GetFeature",
            "version": "2.0",
            "data": LAYER_PARCEL,
            "geomFilter": f"POINT({lon} {lat})",
            "geometry": "true",
            "crs": "EPSG:4326",
            "size": "1",
        },
    )
    features = (
        data.get("response", {}).get("result", {}).get("featureCollection", {}).get("features")
    )
    if not features:
        raise VWorldError("해당 좌표에서 필지를 찾지 못했습니다.")

    f = features[0]
    props = f["properties"]
    geometry = f["geometry"]

    # 연속지적도에는 면적 필드가 없다. 경계 폴리곤에서 측지 면적을 계산한다.
    # 공부(토지대장)상 면적과는 소폭 차이가 날 수 있고, 법적으로는 대장 면적이
    # 우선이므로 area_source 로 출처를 밝힌다.
    area = geodesic_area_m2(geometry)

    pnu = props.get("pnu", "")
    price = _to_int(props.get("jiga"))
    price_info = None if price else await get_individual_land_price(pnu)

    return {
        "pnu": pnu,
        "jibun": props.get("addr", ""),
        # 지목은 jibun 끝에 한글 코드로 붙어 온다. 띄어쓰기가 일정하지 않다:
        #   '737 대'(공백 있음) / '100-10 도' / '1유'(공백 없음)
        # 그래서 공백 분리가 아니라 '끝에 오는 한글'을 뽑는다.
        "jimok": _trailing_hangul(props.get("jibun", "")),
        "area_m2": round(area, 1),
        "area_source": "지적도 경계 기하계산(측지면적) — 토지대장 공부면적과 다를 수 있음",
        "jiga_won_per_m2": price or (
            price_info.get("price_won_per_m2") if price_info else None
        ),
        "jiga_year": price_info.get("year") if price_info else None,
        "jiga_date": price_info.get("date") if price_info else None,
        "geometry": geometry,
    }


async def get_parcels_bbox(west: float, south: float, east: float, north: float) -> list[dict]:
    """2D 선택 화면에 표시할 연속지적도 경계를 bbox로 조회한다."""
    if USE_MOCK:
        return [(await get_parcel((west + east) / 2, (south + north) / 2))["geometry"]]
    data = await _get(
        "data",
        {
            "service": "data",
            "request": "GetFeature",
            "version": "2.0",
            "data": LAYER_PARCEL,
            "geomFilter": f"BOX({west},{south},{east},{north})",
            "geometry": "true",
            "crs": "EPSG:4326",
            "size": "1000",
        },
    )
    features = (
        data.get("response", {}).get("result", {}).get("featureCollection", {}).get("features", [])
    )
    return [f["geometry"] for f in features if f.get("geometry")]


async def get_parcel_features_bbox(
    west: float, south: float, east: float, north: float
) -> list[dict]:
    """접도 등 공간판정용 연속지적도 객체(경계+PNU+지목)를 조회한다."""
    if USE_MOCK:
        parcel = await get_parcel((west + east) / 2, (south + north) / 2)
        return [
            {
                "pnu": parcel.get("pnu", ""),
                "address": parcel.get("jibun", ""),
                "jimok": parcel.get("jimok", ""),
                "geometry": parcel.get("geometry"),
            }
        ]
    data = await _get(
        "data",
        {
            "service": "data",
            "request": "GetFeature",
            "version": "2.0",
            "data": LAYER_PARCEL,
            "geomFilter": f"BOX({west},{south},{east},{north})",
            "geometry": "true",
            "crs": "EPSG:4326",
            "size": "1000",
        },
    )
    features = (
        data.get("response", {})
        .get("result", {})
        .get("featureCollection", {})
        .get("features", [])
    )
    return [
        {
            "pnu": (f.get("properties") or {}).get("pnu", ""),
            "address": (f.get("properties") or {}).get("addr", ""),
            "jimok": _trailing_hangul((f.get("properties") or {}).get("jibun", "")),
            "geometry": f.get("geometry"),
        }
        for f in features
        if f.get("geometry")
    ]


def _trailing_hangul(text: str) -> str:
    """문자열 끝에 붙은 한글만 뽑는다. '100-10 도' -> '도', '1유' -> '유'."""
    m = re.search(r"([가-힣]+)\s*$", (text or "").strip())
    return m.group(1) if m else ""


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- 용도지역


async def get_land_use(lon: float, lat: float) -> dict:
    """좌표 지점에 지정된 용도지역·지구 목록.

    용도지역 4개 대분류 레이어(도시 UQ111 / 관리 UQ112 / 농림 UQ113 /
    자연환경보전 UQ114)를 모두 조회한다. 일부만 보면 나머지 대분류 지역에서
    전부 '정보 없음'이 된다 — 농림지역 필지가 통째로 조회 실패하던 원인이었다.
    """
    if USE_MOCK:
        return {
            "zones": ["제2종일반주거지역"],
            "districts": ["지구단위계획구역"],
            "source": "mock",
        }

    names: list[str] = []
    used: list[str] = []

    for layer in LAYERS_ZONING:
        try:
            data = await _get(
                "data",
                {
                    "service": "data",
                    "request": "GetFeature",
                    "version": "2.0",
                    "data": layer,
                    "geomFilter": f"POINT({lon} {lat})",
                    "geometry": "false",
                    "crs": "EPSG:4326",
                    "size": "10",
                },
            )
        except VWorldError:
            # 해당 레이어에 자료가 없는 좌표는 오류로 오기도 한다 — 다른 레이어로 계속
            continue

        features = (
            data.get("response", {})
            .get("result", {})
            .get("featureCollection", {})
            .get("features", [])
        )
        found = [f["properties"].get("uname", "").strip() for f in features]
        found = [n for n in found if n]
        if found:
            names.extend(found)
            used.append(layer)

    names = list(dict.fromkeys(names))  # 중복 제거, 순서 유지

    # 용도"지역"과 용도"지구/구역"을 구분 — 건폐율·용적률은 지역에서 나온다
    zones = [n for n in names if n.endswith("지역")]
    districts = [n for n in names if not n.endswith("지역")]

    if not zones:
        raise VWorldError(
            "용도지역 정보를 확인할 수 없습니다. "
            "도시·관리·농림·자연환경보전 용도지역 레이어 모두에서 자료가 조회되지 않았습니다."
        )

    return {"zones": zones, "districts": districts, "source": ",".join(used)}


async def get_zoning_polygons_bbox(
    west: float, south: float, east: float, north: float
) -> list[dict]:
    """범위 안 용도지역 폴리곤 목록 [{zone, geometry}]. 주제도 오버레이용.

    도시·관리·농림·자연환경보전 4개 레이어를 모두 조회한다.
    """
    if USE_MOCK:
        return []

    async def _one(layer: str):
        return await _get(
            "data",
            {
                "service": "data",
                "request": "GetFeature",
                "version": "2.0",
                "data": layer,
                "geomFilter": f"BOX({west},{south},{east},{north})",
                "geometry": "true",
                "crs": "EPSG:4326",
                "size": "100",
            },
        )

    # 4개 용도지역 레이어를 순차가 아니라 병렬로 조회한다(지연 단축).
    responses = await asyncio.gather(
        *[_one(layer) for layer in LAYERS_ZONING], return_exceptions=True
    )

    out: list[dict] = []
    for data in responses:
        if isinstance(data, BaseException):
            continue
        features = (
            data.get("response", {})
            .get("result", {})
            .get("featureCollection", {})
            .get("features", [])
        )
        for f in features:
            name = (f.get("properties", {}).get("uname") or "").strip()
            if name and f.get("geometry"):
                out.append({"zone": name, "geometry": f["geometry"]})
    return out


async def get_zone_shares(geometry: dict | None) -> list[dict]:
    """필지 폴리곤과 용도지역 폴리곤의 교차 면적 비율.

    점 조회(get_land_use)는 필지가 용도지역 경계에 걸쳐 있으면 점 위치에 따라
    답이 달라진다 — 실무에서 실제로 문제가 되는 케이스라 국토계획법 제84조가
    걸침 필지의 적용 방법을 따로 규정한다. 여기서는 필지 전체와 겹치는 지역을
    모두 찾아 면적 비율로 돌려준다.

    geomFilter 에 필지 폴리곤을 그대로 넣으면 꼭짓점 수백 개짜리 필지에서 URL 이
    한도를 넘는다. 그래서 서버에는 짧은 BOX(외접 사각형)로 후보만 받아오고,
    정밀한 교차 계산은 shapely 로 여기서 한다.

    반환: [{zone, area_m2, share_pct}] — 면적 큰 순. 실패·mock 이면 [].
    """
    if USE_MOCK or not geometry:
        return []

    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    try:
        parcel = shape(geometry).buffer(0)  # buffer(0): 자기교차 등 오류 기하 보정
    except Exception:
        return []
    if parcel.is_empty:
        return []

    minx, miny, maxx, maxy = parcel.bounds
    parcel_area = geodesic_area_m2(geometry)
    if parcel_area <= 0:
        return []

    area_by_zone: dict[str, float] = {}
    # 지도에 조각을 색으로 깔기 위한 교차 기하 (지역별로 합집합)
    geoms_by_zone: dict[str, list] = {}

    for layer in LAYERS_ZONING:
        try:
            data = await _get(
                "data",
                {
                    "service": "data",
                    "request": "GetFeature",
                    "version": "2.0",
                    "data": layer,
                    "geomFilter": f"BOX({minx},{miny},{maxx},{maxy})",
                    "geometry": "true",
                    "crs": "EPSG:4326",
                    "size": "30",
                },
            )
        except VWorldError:
            continue

        features = (
            data.get("response", {})
            .get("result", {})
            .get("featureCollection", {})
            .get("features", [])
        )
        for f in features:
            name = (f.get("properties", {}).get("uname") or "").strip()
            # 건폐율·용적률이 나오는 용도"지역"만 면적 안분 대상이다
            if not name.endswith("지역"):
                continue
            try:
                inter = shape(f["geometry"]).buffer(0).intersection(parcel)
            except Exception:
                continue
            if inter.is_empty or inter.area == 0:
                continue
            area_by_zone[name] = area_by_zone.get(name, 0.0) + geodesic_area_m2(
                mapping(inter)
            )
            geoms_by_zone.setdefault(name, []).append(inter)

    shares = [
        {
            "zone": zone,
            "area_m2": round(area, 1),
            "share_pct": round(area / parcel_area * 100, 1),
            # 필지 위에 색으로 깔 교차 조각 (GeoJSON)
            "geometry": mapping(unary_union(geoms_by_zone[zone])),
        }
        for zone, area in area_by_zone.items()
    ]
    # 경계선 스침(1% 미만)은 데이터 오차일 가능성이 높아 버린다
    shares = [s for s in shares if s["share_pct"] >= 1.0]
    shares.sort(key=lambda s: -s["area_m2"])
    return shares
