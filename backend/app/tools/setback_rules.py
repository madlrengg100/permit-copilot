"""지자체 건축조례 '대지 안의 공지'(건축선·인접대지경계선 이격) 조회.

값은 코드에 하드코딩하지 않는다. data/setbacks.json 의 지자체별 규칙(rules)을
읽어 first-match 로 평가한다. 지자체 항목이 없으면 NOT_COLLECTED 로 돌려준다.
지자체 추가 = setbacks.json 에 규칙을 넣는 것으로 끝난다(코드 변경 없음).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "setbacks.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def jurisdictions() -> list[str]:
    return [k for k in _load() if not k.startswith("_")]


def _when_text(when: dict) -> str:
    """규칙의 when 조건을 사람이 읽는 한국어로. (하드코딩 아님 — 값에서 조립)"""
    if not when:
        return ""
    bits: list[str] = []
    if "min_gross" in when and "max_gross_excl" in when:
        bits.append(f"연면적 {when['min_gross']:g}~{when['max_gross_excl']:g}㎡")
    elif "min_gross" in when:
        bits.append(f"연면적 {float(when['min_gross']):g}㎡ 이상")
    elif "max_gross_excl" in when:
        bits.append(f"연면적 {float(when['max_gross_excl']):g}㎡ 미만")
    if "zone" in when:
        bits.append(str(when["zone"]))
    if "zone_contains" in when:
        bits.append(str(when["zone_contains"]))
    if "zone_in" in when:
        bits.append("·".join(when["zone_in"]))
    return " ".join(bits)


def _dist_text(front: float, adjacent: float) -> str:
    parts = []
    if front > 0:
        parts.append(f"전면(건축선) {front:g}m")
    if adjacent > 0:
        parts.append(f"인접경계 {adjacent:g}m")
    return " · ".join(parts)


def describe_rules(jurisdiction: str, exclude_use: str | None = None) -> list[str]:
    """이 지자체 조례에서 이격이 발생하는 용도·조건·수치를 사람이 읽을 목록으로.

    카드에서 '이 용도는 0m지만 공장·창고는 값이 있다'는 걸 실제 데이터로 보여주기
    위한 것. 값을 지어내지 않고 setbacks.json 규칙을 그대로 요약한다.
    """
    entry = _load().get(jurisdiction)
    if not entry:
        return []
    lines: list[str] = []
    for rule in entry.get("rules", []):
        use = rule.get("use")
        if not use or use == exclude_use:
            continue
        if rule.get("needs_subtype"):
            note = rule.get("note")
            if note:
                lines.append(f"{use}: {note}")
            continue
        front = float(rule.get("front_m") or 0)
        adjacent = float(rule.get("adjacent_m") or 0)
        if front <= 0 and adjacent <= 0:
            continue
        cond = _when_text(rule.get("when") or {})
        dist = _dist_text(front, adjacent)
        lines.append(f"{use}{f'({cond})' if cond else ''}: {dist}")
    # 같은 문장 중복 제거(순서 유지)
    seen: set[str] = set()
    uniq = []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            uniq.append(ln)
    return uniq


def _matches(when: dict, zone: str, gross: float) -> bool:
    """규칙의 when 조건을 모두 만족하는지."""
    if not when:
        return True
    z = zone or ""
    if "min_gross" in when and gross < float(when["min_gross"]):
        return False
    if "max_gross_excl" in when and gross >= float(when["max_gross_excl"]):
        return False
    if "zone" in when and z != when["zone"]:
        return False
    if "zone_contains" in when and when["zone_contains"] not in z:
        return False
    if "zone_not_contains" in when and when["zone_not_contains"] in z:
        return False
    if "not_zone_in" in when and z in (when["not_zone_in"] or []):
        return False
    if "zone_in" in when and z not in (when["zone_in"] or []):
        return False
    return True


def setback_uses(jurisdiction: str, exclude_use: str | None = None) -> list[str]:
    """이 지자체 조례에서 이격이 적용되는(수치가 있거나 세부유형별로 있는) 용도 목록.

    '이 용도는 0m지만 다른 용도는 수치가 있다'를 데이터로 보여주기 위한 것.
    """
    entry = _load().get(jurisdiction)
    if not entry:
        return []
    uses: list[str] = []
    for rule in entry.get("rules", []):
        use = rule.get("use")
        if not use or use == exclude_use or use in uses:
            continue
        if rule.get("needs_subtype") or float(rule.get("front_m") or 0) > 0 or float(
            rule.get("adjacent_m") or 0
        ) > 0:
            uses.append(use)
    return uses


def applicable_setbacks(
    jurisdiction: str,
    zone: str,
    gross_floor_area_m2: float,
    exclude_use: str | None = None,
) -> list[dict]:
    """이 필지의 용도지역·연면적에서 '실제로' 이격이 생기는 용도와 수치.

    일반론이 아니라 first-match lookup 을 각 용도에 적용해, 이 필지 규모 기준
    실제 front/adjacent 값을 돌려준다(0m·미해당 용도는 제외). 세부유형이 필요한
    용도(공동주택·숙박 등)는 needs_subtype 로 표시한다.
    """
    entry = _load().get(jurisdiction)
    if not entry:
        return []
    uses: list[str] = []
    for rule in entry.get("rules", []):
        u = rule.get("use")
        if u and u != exclude_use and u not in uses:
            uses.append(u)
    out: list[dict] = []
    for u in uses:
        r = lookup(jurisdiction, u, zone, gross_floor_area_m2)
        if r["status"] == "NEEDS_SUBTYPE":
            out.append({"use": u, "needs_subtype": True, "note": r.get("note")})
        elif r["status"] == "APPLIED" and (
            float(r["front_m"] or 0) > 0 or float(r["adjacent_m"] or 0) > 0
        ):
            out.append(
                {"use": u, "front_m": float(r["front_m"]), "adjacent_m": float(r["adjacent_m"])}
            )
    return out


def lookup(
    jurisdiction: str,
    building_use: str,
    zone: str,
    gross_floor_area_m2: float,
) -> dict:
    data = _load()
    entry = data.get(jurisdiction)
    if not entry:
        return {
            "status": "NOT_COLLECTED",
            "front_m": None,
            "adjacent_m": None,
            "source": None,
            "note": (
                f"{jurisdiction or '해당 지자체'} 건축조례 별표의 대지 공지 기준을 "
                "아직 수집하지 않았습니다."
            ),
        }

    source = entry.get("source")
    gross = float(gross_floor_area_m2 or 0)
    for rule in entry.get("rules", []):
        if rule.get("use") != building_use:
            continue
        if not _matches(rule.get("when") or {}, zone, gross):
            continue
        if rule.get("needs_subtype"):
            return {
                "status": "NEEDS_SUBTYPE",
                "front_m": None,
                "adjacent_m": None,
                "source": source,
                "note": rule.get("note", "세부 유형에 따라 이격이 달라집니다."),
            }
        return {
            "status": "APPLIED",
            "front_m": float(rule.get("front_m") or 0),
            "adjacent_m": float(rule.get("adjacent_m") or 0),
            "source": source,
            "note": rule.get("note", "해당 건축조례 별표 기준을 적용했습니다."),
        }

    # 지자체는 수집됐으나 이 용도·규모는 대지 안의 공지 대상이 아님 → 0m.
    return {
        "status": "APPLIED",
        "front_m": 0.0,
        "adjacent_m": 0.0,
        "source": source,
        "note": "해당 건축조례 별표에서 이 용도·규모에 대한 대지 공지 기준이 없어 0m로 적용했습니다.",
    }
