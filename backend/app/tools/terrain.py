"""전국 DEM에서 필지별 표고·경사도 참고값을 계산한다."""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import transform as window_transform
from shapely.geometry import box, mapping, shape


DEFAULT_DEM_PATH = (
    "/home/madlrengg100/permit-copilot/backend/data/processed/"
    "terrain/dem/cop30_korea.tif"
)


def _bands(slopes: np.ndarray) -> list[dict]:
    ranges = [
        ("5° 미만", 0, 5),
        ("5° 이상 10° 미만", 5, 10),
        ("10° 이상 15° 미만", 10, 15),
        ("15° 이상 20° 미만", 15, 20),
        ("20° 이상", 20, math.inf),
    ]
    total = slopes.size
    return [
        {
            "label": label,
            "share_pct": round(
                float(np.count_nonzero((slopes >= low) & (slopes < high)))
                / total
                * 100,
                1,
            ),
        }
        for label, low, high in ranges
    ]


def analyze_terrain(parcel_geometry: dict, path: str | None = None) -> dict:
    """필지 내부 DEM 셀을 이용해 표고와 경사도 참고 통계를 반환한다."""
    dem_path = Path(
        path
        or os.getenv("TERRAIN_DEM_PATH")
        or DEFAULT_DEM_PATH
    )
    if not dem_path.exists():
        return {
            "status": "NOT_COLLECTED",
            "message": f"DEM 파일이 없습니다: {dem_path}",
        }

    try:
        with rasterio.open(dem_path) as source:
            window = geometry_window(
                source,
                [parcel_geometry],
                pad_x=1,
                pad_y=1,
                boundless=False,
            )
            elevations = source.read(1, window=window, masked=True).astype("float64")
            transform = window_transform(window, source.transform)
            inside = geometry_mask(
                [parcel_geometry],
                out_shape=elevations.shape,
                transform=transform,
                invert=True,
                all_touched=True,
            )
            values = np.asarray(elevations.filled(np.nan), dtype="float64")
            valid = inside & np.isfinite(values)
            if not np.any(valid):
                return {
                    "status": "NO_COVERAGE",
                    "message": "필지 내부에서 유효한 DEM 셀을 찾지 못했습니다.",
                }

            center_lat = float(shape(parcel_geometry).centroid.y)
            cell_x_m = abs(source.res[0]) * 111_320 * math.cos(math.radians(center_lat))
            cell_y_m = abs(source.res[1]) * 110_574
            gradient_y, gradient_x = np.gradient(values, cell_y_m, cell_x_m)
            slope = np.degrees(np.arctan(np.hypot(gradient_x, gradient_y)))
            slope_values = slope[valid & np.isfinite(slope)]
            elevation_values = values[valid]
            if not slope_values.size:
                return {
                    "status": "NO_COVERAGE",
                    "message": "경사도를 계산할 유효한 DEM 셀이 부족합니다.",
                }

            # 지도 표시도 같은 DEM 셀을 사용한다. 큰 필지는 엔티티 과다 생성을
            # 막기 위해 최대 약 2,000개 셀로 균일 표본화하되 통계는 전체 셀로 계산한다.
            parcel_shape = shape(parcel_geometry).buffer(0)
            indices = np.argwhere(valid & np.isfinite(slope))
            stride = max(1, math.ceil(len(indices) / 2000))
            grid_cells: list[dict] = []
            for row, col in indices[::stride]:
                left, bottom, right, top = rasterio.windows.bounds(
                    rasterio.windows.Window(int(col), int(row), 1, 1),
                    transform,
                )
                clipped = box(left, bottom, right, top).intersection(parcel_shape)
                if clipped.is_empty:
                    continue
                grid_cells.append({
                    "geometry": mapping(clipped),
                    "elevation_m": round(float(values[row, col]), 1),
                    "slope_deg": round(float(slope[row, col]), 1),
                })

            return {
                "status": "REFERENCE_AVAILABLE",
                "source": "Copernicus DEM GLO-30",
                "resolution_m": 30,
                "cell_count": int(elevation_values.size),
                "elevation_min_m": round(float(np.min(elevation_values)), 1),
                "elevation_max_m": round(float(np.max(elevation_values)), 1),
                "elevation_mean_m": round(float(np.mean(elevation_values)), 1),
                "slope_mean_deg": round(float(np.mean(slope_values)), 1),
                "slope_max_deg": round(float(np.max(slope_values)), 1),
                "slope_bands": _bands(slope_values),
                "grid_cells": grid_cells,
                "caveat": (
                    "30m DEM 기반 사전 참고값이며, 산지전용 심사용 10m 격자 "
                    "평균경사도조사서·현황측량을 대체하지 않습니다."
                ),
            }
    except Exception as exc:
        return {"status": "UNAVAILABLE", "message": str(exc)}
