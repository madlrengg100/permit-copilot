"""용도지역별 최소 대지면적(건축법 시행령 제80조) 기반 협소 필지 판정.
법령 값은 data/min_lot_area.json 에서만 온다(하드코딩 금지 원칙).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "data" / "min_lot_area.json"


@lru_cache(maxsize=1)
def _categories() -> dict[str, int]:
    with _PATH.open(encoding="utf-8") as f:
        return json.load(f)["categories"]


def _category(zone: str) -> str:
    # 카테고리 목록은 데이터파일에서 온다(함수에 박지 않음). '기타'는 폴백.
    for key in _categories():
        if key != "기타" and key in (zone or ""):
            return key
    return "기타"


def minimum_area(zone: str) -> int:
    """용도지역의 법정 최소 대지면적(㎡). 필지 분할 가능성 판단 등에 쓴다."""
    return _categories()[_category(zone)]


def check(zone: str, area_m2: float | None) -> dict | None:
    """대지면적이 용도지역 법정 최소 대지면적 미만이면 협소 판정을 돌려준다(아니면 None)."""
    if not area_m2:
        return None
    minimum = _categories()[_category(zone)]
    if area_m2 >= minimum:
        return None
    return {
        "below_minimum": True,
        "minimum_m2": minimum,
        "area_m2": round(float(area_m2), 1),
        "legal_basis": "건축법 시행령 제80조",
        "note": (
            f"대지면적 {float(area_m2):,.1f}㎡가 {zone} 법정 최소 대지면적 {minimum}㎡ 미만입니다. "
            "용도지역상 건축은 조건부로 가능하나, 실제 건축물을 배치하기에는 협소해 건축이 "
            "제한될 수 있습니다(기존 소규모 필지 예외·합필 여부를 관할청·건축사무소로 확인)."
        ),
    }
