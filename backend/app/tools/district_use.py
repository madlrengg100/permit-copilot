"""개별법 용도구역의 행위제한 판정.

국토계획법 용도지역 판정(`zoning.lookup_zoning_rules`)과 축이 다르다. 용도지역이
"이 지역에서 이 용도가 되나"를 본다면, 여기는 농지법·산지관리법이 **용도구역**에
건 원칙 금지와 예외 열거를 본다. 농업진흥구역의 농업인 주택처럼, 용도지역만으로는
불가인데 개별법 예외로 열리는 경로가 이 축에 있다.

여기서 만드는 것은 허용 여부와 **근거 조문**까지다. 예외 열거에 해당해도 대부분
대통령령·부령의 면적·자격 요건이 걸려 있어 판정은 `conditional` 이 상한이다.
건폐율·용적률·이격 수치는 만들지 않는다 — 그건 `ordinances*.json` 과 결정적
조건식의 몫이다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "district_use_rules.json"


@lru_cache(maxsize=1)
def _ruleset() -> dict:
    return json.loads(_RULES_PATH.read_text(encoding="utf-8"))


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or ""))


@lru_cache(maxsize=1)
def _by_code() -> dict[str, str]:
    """공간레이어 코드(UEA110 등) -> 구역명."""
    out: dict[str, str] = {}
    for name, spec in _ruleset()["districts"].items():
        for code in spec.get("codes") or []:
            out[code] = name
    return out


def districts_for(
    names: list[str] | None,
    codes: list[str] | None = None,
    zone: str | None = None,
) -> list[str]:
    """용도지역과 레이어 중첩 결과에서 이 룰셋이 아는 구역만 골라낸다.

    두 축이 함께 걸린다. 국토계획법 용도지역(농림지역 등)은 `zone` 으로,
    개별법 용도구역(농업진흥구역 등)은 공간레이어의 이름·코드로 들어온다.
    코드를 이름보다 먼저 본다 — 고시·연도에 따라 이름 표기가 흔들린다.
    """
    known = _ruleset()["districts"]
    found: list[str] = []
    if zone:
        compact_zone = _compact(zone)
        for name, spec in known.items():
            if compact_zone in [_compact(z) for z in spec.get("zones") or []]:
                if name not in found:
                    found.append(name)
    for code in codes or []:
        name = _by_code().get(_compact(code).upper())
        if name and name not in found:
            found.append(name)
    for raw in names or []:
        compact = _compact(raw)
        for name, spec in known.items():
            # 용도지역 축은 zone 인자로만 넣는다. 지역지구 이름 목록에 '농림지역'이
            # 섞여 들어와 같은 구역이 두 번 잡히지 않게 한다.
            if spec.get("zones"):
                continue
            if name in compact and name not in found:
                found.append(name)
    return found


def _allowances(district: str, seen: set[str] | None = None) -> list[dict]:
    """구역의 허용행위. inherits 가 있으면 상위 구역 것을 앞에 붙인다.

    농업보호구역은 농지법 제32조제2항제1호가 "제1항에 따라 허용되는 토지이용행위"를
    그대로 끌어오므로, 진흥구역 허용행위가 보호구역에서도 살아 있어야 한다.
    """
    seen = seen if seen is not None else set()
    if district in seen:
        return []
    seen.add(district)
    spec = _ruleset()["districts"].get(district)
    if not spec:
        return []
    result: list[dict] = []
    for parent in spec.get("inherits") or []:
        result.extend(_allowances(parent, seen))
    for item in spec.get("allowances") or []:
        result.append({**item, "district": district})
    return result


def _matches(allowance: dict, facility: str, building_use: str) -> str | None:
    """시설명이 맞으면 'facility', 건축물 용도만 맞으면 'building_use'."""
    compact = _compact(facility)
    if compact:
        for keyword in allowance.get("facilities") or []:
            if _compact(keyword) and _compact(keyword) in compact:
                return "facility"
    if building_use and building_use in (allowance.get("building_uses") or []):
        return "building_use"
    return None


def evaluate(
    districts: list[str],
    facility: str = "",
    building_use: str = "",
) -> dict:
    """용도구역 목록과 검토 용도로 행위제한을 판정한다.

    반환의 verdict:
      conditional  — 예외 열거에 해당. 조문상 열려 있으나 요건 확인이 남았다.
      not_allowed  — 원칙 금지에 걸리고 해당하는 예외가 없다.
      unknown      — 아는 용도구역이 없다(이 축이 판정을 만들지 않는다).
    """
    known = [d for d in districts if d in _ruleset()["districts"]]
    if not known:
        return {
            "verdict": "unknown",
            "districts": [],
            "matched": [],
            "legal_references": [],
            "conditions": [],
            "reason": "",
        }

    matched: list[dict] = []
    blocking: list[str] = []
    for district in known:
        spec = _ruleset()["districts"][district]
        hit = None
        for allowance in _allowances(district):
            kind = _matches(allowance, facility, building_use)
            if kind:
                hit = {**allowance, "match": kind, "checked_district": district}
                break
        if hit:
            matched.append(hit)
        elif spec.get("principle") == "prohibited":
            blocking.append(district)

    # 여러 구역이 겹치면 가장 엄격한 쪽을 따른다. 한 곳이라도 금지면 금지다.
    if blocking:
        names = " · ".join(blocking)
        target = facility or building_use or "요청 용도"
        return {
            "verdict": "not_allowed",
            "districts": known,
            "matched": matched,
            "legal_references": _references(blocking),
            "conditions": [],
            "reason": (
                f"{names}은 원칙적으로 해당 구역 목적 외의 토지이용행위를 금지하며, "
                f"{target}은 허용행위 열거에 해당하지 않습니다."
            ),
        }

    conditions: list[str] = []
    for hit in matched:
        for condition in hit.get("conditions") or []:
            if condition not in conditions:
                conditions.append(condition)
    clauses = " · ".join(
        f"{_ruleset()['districts'][hit['checked_district']]['law']} {hit['clause']}"
        + (f"({hit['delegated']} 위임)" if hit.get("delegated") else "")
        for hit in matched
    )
    names = " · ".join(hit["name"] for hit in matched)
    return {
        "verdict": "conditional",
        "districts": known,
        "matched": matched,
        "legal_references": _references([hit["checked_district"] for hit in matched]),
        "conditions": conditions,
        "reason": (
            f"{' · '.join(known)}의 행위제한에서 {names}에 해당해 "
            f"{clauses}로 허용될 수 있습니다. 아래 요건 충족 여부를 확인해야 합니다."
        ),
    }


def _references(districts: list[str]) -> list[str]:
    out: list[str] = []
    for district in districts:
        for ref_id in _ruleset()["districts"][district].get("legal_ref_ids") or []:
            if ref_id not in out:
                out.append(ref_id)
    return out
