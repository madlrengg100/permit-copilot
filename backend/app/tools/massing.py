"""대지면적 + 건폐율/용적률 -> 건축 가능 규모 산출."""

from __future__ import annotations

import math

from ..config import FLOOR_HEIGHT_M


def calc_massing(
    area_m2: float,
    bcr_max_pct: float,
    far_max_pct: float,
    far_target_pct: float | None = None,
) -> dict:
    """대지면적으로부터 건축면적·연면적·층수·건물 높이를 계산한다.

    far_target_pct 를 주면 상한 대신 그 값으로 계산한다(사용자가 "용적률 200%로
    올려봐" 라고 지정한 경우).
    """
    if area_m2 <= 0:
        raise ValueError("대지면적이 0 이하입니다.")

    over_limit = far_target_pct is not None and far_target_pct > far_max_pct
    requested_far_pct = far_target_pct
    # 상한 초과 요청은 경고용으로만 보관한다. 불가능한 규모를 계산하거나
    # 지도에 입체화하지 않도록 실제 산정에는 조례상 상한을 적용한다.
    far_pct = far_max_pct if over_limit else (
        far_target_pct if far_target_pct is not None else far_max_pct
    )

    building_area = area_m2 * bcr_max_pct / 100.0   # 건축면적 (1층 바닥면적 상한)
    gross_floor_area = area_m2 * far_pct / 100.0    # 연면적

    # 층수 = 연면적 / 건축면적. 건폐율을 꽉 채워 지었을 때의 이론 층수.
    floors = gross_floor_area / building_area if building_area else 0
    # 소수 층이 남으면 그 면적을 수용할 최상층이 하나 더 필요하다.
    # 예: 3.33층은 '3층·13.2m'가 아니라 4층(최상층 약 33%)이다.
    floors_int = max(1, math.ceil(floors - 1e-9))
    full_floors = max(0, math.floor(floors + 1e-9))
    top_floor_ratio = 1.0 if floors_int == full_floors else floors - full_floors

    return {
        "site_area_m2": round(area_m2, 2),
        "bcr_applied_pct": bcr_max_pct,
        "far_applied_pct": far_pct,
        "requested_far_pct": requested_far_pct,
        "building_area_m2": round(building_area, 2),
        "gross_floor_area_m2": round(gross_floor_area, 2),
        "floors_theoretical": round(floors, 2),
        "floors": floors_int,
        "full_floors": full_floors,
        "top_floor_ratio": round(top_floor_ratio, 3),
        "mass_height_m": round(floors_int * FLOOR_HEIGHT_M, 1),
        "floor_height_m": FLOOR_HEIGHT_M,
        "exceeds_far_limit": over_limit,
        "note": (
            f"요청 용적률 {requested_far_pct}%는 조례상 상한 {far_max_pct}%를 초과합니다. "
            "층수와 입체 규모는 표시하지 않고 건폐율 기준 최대 건축면적만 표시합니다."
            if over_limit
            else "건폐율 상한까지 꽉 채운 이론상 규모입니다. 실제로는 일조·이격거리·주차로 줄어듭니다."
        ),
    }
