"""필지 분할(토지분할) 성립 사전판정.

정확한 분할선(지오메트리)이 아니라, 분할이 '성립 가능한지'와 '분할 후 건축 가능한
유효 대지면적'을 이미 있는 진단 데이터로 추정한다(사전검토용). 정밀 분할선·배치는
분할측량 확정 후(2단계) 다룬다.

성립 조건(단일 원본):
  ① 분할 후 각 필지 ≥ 용도지역 법정 최소 대지면적(min_lot_area)
  ② 분할 후에도 접도 유지 — 원래 맹지면 분할해도 맹지라 성립 불가
  ③ 녹지·관리·농림·자연환경보전지역은 개발행위허가(분할) 대상
분할 뒤 그 대지에 개발행위허가·건축허가(필요 시 농지·산지 전용)가 이어진다.
"""

from __future__ import annotations

from . import min_lot_area


def recompute_massing(
    *, area_m2: float, geometry: dict, regulation: dict, building_use: str,
    zone: str, jurisdiction: str | None, road_access: dict | None,
) -> tuple[dict, dict]:
    """분할된 대지(부분 지오메트리·면적)로 매스·이격을 다시 계산한다.
    prediagnosis 의 매스 산출과 같은 순서(밀도 산정 → 이격·주차 반영)로 맞춘다."""
    from . import massing as massing_tool
    from . import site_constraints

    mass = massing_tool.calc_massing(
        area_m2=area_m2,
        bcr_max_pct=regulation["bcr_max_pct"],
        far_max_pct=regulation["far_max_pct"],
        far_target_pct=None,
    )
    sc = site_constraints.apply(
        parcel_geometry=geometry,
        massing=mass,
        building_use=building_use,
        zone=zone,
        jurisdiction=jurisdiction,
        road_access=road_access,
        parking_strategy="unspecified",
    )
    achievable = sc.get("achievable_gross_floor_area_m2", mass["gross_floor_area_m2"])
    adj = sc["adjusted_building_area_m2"]
    mass.update({
        "density_building_area_m2": mass["building_area_m2"],
        "building_area_m2": adj,
        "gross_floor_area_m2": achievable,
        "floors_theoretical": round(achievable / adj, 2) if adj else 0,
        "floors": sc["floors"],
        "full_floors": sc["full_floors"],
        "top_floor_ratio": sc["top_floor_ratio"],
        "mass_height_m": sc["mass_height_m"],
        "layout_feasible": sc.get("layout_feasible", True),
    })
    return mass, sc

# 국토계획법 시행령상 토지분할이 개발행위허가 대상인 용도지역(도시지역 주거·상업·공업의
# 단순 분할은 대개 제외되나, 아래는 허가 대상이므로 임의 분할 불가).
_DEV_PERMIT_ZONES = ("녹지", "관리지역", "농림", "자연환경보전")
_MAENJI_STATUS = {"NO_CADASTRAL_ROAD", "NO_ROAD", "UNAVAILABLE"}


def assess(diagnosis: dict | None) -> dict:
    """진단 데이터로 필지 분할 성립 여부·방법·유효 대지면적을 판정한다."""
    diagnosis = diagnosis or {}
    parcel = diagnosis.get("parcel") or {}
    regulation = diagnosis.get("regulation") or {}
    land_use = diagnosis.get("land_use") or {}
    road_access = diagnosis.get("road_access") or {}
    conversion = diagnosis.get("land_conversion") or {}

    zone = regulation.get("zone") or (land_use.get("zones") or [""])[0]
    area = float(parcel.get("area_m2") or 0)
    min_area = min_lot_area.minimum_area(zone) if zone else 0
    maenji = road_access.get("status") in _MAENJI_STATUS
    needs_dev_permit = any(k in (zone or "") for k in _DEV_PERMIT_ZONES)

    methods: list[dict] = []

    # ① 규제 분리 — 용도지역 걸침: 한 용도지역 부분만 떼어내면 그 면적이 최소면적 이상인지.
    shares = [
        s for s in (land_use.get("zone_shares") or [])
        if s.get("zone") and float(s.get("area_m2") or 0) > 0
    ]
    if len(shares) >= 2:
        for s in shares:
            m2 = round(float(s["area_m2"]), 1)
            z_min = min_lot_area.minimum_area(s["zone"])
            if m2 >= z_min:
                methods.append({
                    "method": "규제 분리(용도지역 걸침)",
                    "buildable_area_m2": m2,
                    "note": (
                        f"{s['zone']} 부분 약 {m2:,.0f}㎡만 분할하면 그 용도지역 최소 "
                        f"대지면적({z_min}㎡)을 충족합니다."
                    ),
                })

    # ① 규제 분리 — 농지·산지 등 규제구역 부분 걸침: 규제 없는 부분 면적.
    for layer in (conversion.get("agriculture") or {}, conversion.get("forest") or {}):
        for ov in (layer.get("overlaps") or []):
            share = float(ov.get("share_pct") or 0)
            if 0 < share < 100 and area > 0:
                free = round(area * (1 - share / 100), 1)
                if free >= min_area:
                    name = ov.get("name") or layer.get("title") or "규제구역"
                    methods.append({
                        "method": "규제 분리(규제구역 걸침)",
                        "buildable_area_m2": free,
                        "note": (
                            f"{name} 걸침({share:g}%)을 뺀 부분 약 {free:,.0f}㎡가 최소 "
                            f"대지면적({min_area}㎡) 이상이라 그 부분만 분할해 건축할 수 있습니다."
                        ),
                    })

    # ② 도로 후퇴 — 미달도로(소요너비 4m 미만) 접한 필지: 중심선 후퇴분을 뺀 유효 대지.
    for road in (road_access.get("roads") or []):
        w = road.get("cadastral_width_estimate_m")
        clen = float(road.get("contact_length_m") or 0)
        if w and float(w) < 4 and clen > 0:
            setback = (4 - float(w)) / 2
            strip = round(clen * setback, 1)
            remaining = round(area - strip, 1)
            methods.append({
                "method": "도로 후퇴(미달도로 편입)",
                "buildable_area_m2": max(remaining, 0),
                "note": (
                    f"접한 도로 추정폭 {float(w):g}m(<4m)라 중심선에서 약 {setback:.1f}m "
                    f"후퇴선이 잡혀 약 {strip:,.0f}㎡가 도로로 편입되고, 남는 대지는 약 "
                    f"{remaining:,.0f}㎡입니다(유효 도로폭은 현황측량 확정)."
                ),
            })

    # ③ 일반 분할(소유권·개발) — 걸침이 없어도 대지가 최소면적의 2배 이상이면 나눌 수 있다.
    if area >= 2 * min_area and min_area > 0:
        methods.append({
            "method": "일반 분할(소유권·개발)",
            "buildable_area_m2": round(area - min_area, 1),
            "note": (
                f"대지 약 {area:,.0f}㎡가 최소 대지면적({min_area}㎡)의 2배 이상이라 여러 "
                "획지로 분할할 수 있습니다(분할 경계는 소유·계획에 따라 정합니다)."
            ),
        })

    # 성립 판정: 방법이 있고 맹지가 아니어야 실질적으로 건축 가능한 분할이 성립한다.
    if maenji:
        status = "NOT_FEASIBLE"
    elif methods:
        status = "FEASIBLE"
    else:
        status = "NOT_APPLICABLE"

    return {
        "status": status,
        "methods": methods,
        "zone": zone,
        "min_area_m2": min_area,
        "parcel_area_m2": round(area, 1),
        "maenji": maenji,
        "needs_dev_permit": needs_dev_permit,
        # 분할 뒤 그 대지에 이어지는 후속 인허가(분할은 끝이 아니라 시작). 녹지·관리·농림
        # 등은 분할 자체도 개발행위허가 대상이라 그 점을 함께 밝힌다.
        "followups": (
            ["개발행위허가(분할 포함)", "건축허가"] if needs_dev_permit
            else ["개발행위허가", "건축허가"]
        ),
    }
