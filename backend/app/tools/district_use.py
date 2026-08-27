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
    """시설명이 맞으면 'facility', 건축물 용도만 맞으면 'building_use'.

    `match: "facility"` 인 허용행위는 건축법 용도만으로는 매칭하지 않는다.
    농지법 제32조와 산지관리법 제12조는 "농업인이 자기가 생산한 농산물을
    건조·보관하기 위하여 설치하는 시설"처럼 **시설의 성격**으로 열거한다.
    조문에 '창고시설'이라는 건축법 용도는 나오지 않으므로, 일반 창고시설
    질문을 농업용 시설로 가정해 예외를 열어 주면 안 된다.

    국토계획법 시행령 별표 21·22 는 건축법 용도로 열거하므로 용도 매칭이
    성립한다(예: 별표 21 제1호라목 "제18호가목의 창고").
    """
    compact = _compact(facility)
    if compact:
        for keyword in allowance.get("facilities") or []:
            if _compact(keyword) and _compact(keyword) in compact:
                return "facility"
    if allowance.get("match") == "facility":
        return None
    if building_use and building_use in (allowance.get("building_uses") or []):
        return "building_use"
    return None


def narrower_paths(districts: list[str], building_use: str) -> list[dict]:
    """그 용도와 관련은 있으나 시설명이 있어야 열리는 허용행위.

    "일반 창고시설은 불가" 로 끝내지 않고 "농업인이 자기 생산 농산물을
    건조·보관하는 농업용 시설이면 제32조제1항제3호로 가능" 이라는 경로를
    함께 안내하기 위한 것이다. 이것 자체가 허용 판정은 아니다.
    """
    if not building_use:
        return []
    out: list[dict] = []
    for district in districts:
        if district not in _ruleset()["districts"]:
            continue
        for allowance in _allowances(district):
            if allowance.get("match") != "facility":
                continue
            if building_use not in (allowance.get("building_uses") or []):
                continue
            out.append({**allowance, "checked_district": district})
    return out


def resolve_governing(districts: list[str]) -> dict:
    """1단계 — 어느 법이 적용되는지 먼저 정한다.

    용도지역과 개별법 용도구역이 겹칠 때 무조건 누적이 아니다. 국토계획법
    제76조제5항제3호는 "농림지역 중 농업진흥지역, 보전산지 또는 초지인 경우에는
    **제1항부터 제4항까지의 규정에도 불구하고** 각각 농지법·산지관리법·초지법에서
    정하는 바에 따른다" 고 정한다. 별표 21 은 제1항 위임이므로 이때는 적용되지
    않는다 — 누적이 아니라 **대체**다.

    그래서 농림지역 + 농업진흥구역은 별표 21 의 "단독주택 1천㎡ 미만" 이 아니라
    농지법 제32조의 "농업인 주택" 이 판정 기준이 된다.

    반환:
      applied     실제로 적용할 구역
      superseded  대체돼 적용하지 않는 구역
      basis       대체 근거 조문(대체가 없으면 빈 목록)
    """
    known = [d for d in districts if d in _ruleset()["districts"]]
    superseded: list[str] = []
    basis: list[str] = []
    for district in known:
        spec = _ruleset()["districts"][district]
        for target in spec.get("supersedes") or []:
            if target in known and target not in superseded:
                superseded.append(target)
                cite = "국토의 계획 및 이용에 관한 법률 제76조제5항제3호"
                if cite not in basis:
                    basis.append(cite)
    return {
        "applied": [d for d in known if d not in superseded],
        "superseded": superseded,
        "basis": basis,
    }


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
    governing = resolve_governing(districts)
    known = governing["applied"]
    if not known:
        return {
            "verdict": "unknown",
            "districts": [],
            "matched": [],
            "legal_references": [],
            "conditions": [],
            "reason": "",
            "governing": governing,
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
        # 같은 용도라도 시설의 성격이 맞으면 열리는 경로가 있으면 함께 알린다.
        # "창고시설 불가" 로 끝내면 농업용 건조·보관 시설 경로를 놓치게 된다.
        paths = narrower_paths(blocking, building_use)
        suffix = ""
        if paths:
            cites = " · ".join(
                f"{hit['name']}({hit['clause']})" for hit in paths[:3]
            )
            suffix = (
                f" 다만 {cites}에 해당하면 허용될 수 있으므로, 해당 시설로 "
                "검토할지 지정해 주세요."
            )
        return {
            "verdict": "not_allowed",
            "districts": known,
            "matched": matched,
            "legal_references": _references(blocking),
            "conditions": [],
            "narrower_paths": paths,
            "governing": governing,
            "reason": (
                _superseded_note(governing)
                + f"{names}은 원칙적으로 해당 구역 목적 외의 토지이용행위를 금지하며, "
                f"{target}은 허용행위 열거에 해당하지 않습니다." + suffix
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
        "governing": governing,
        "reason": (
            _superseded_note(governing)
            + f"{' · '.join(known)}의 행위제한에서 {names}에 해당해 "
            f"{clauses}로 허용될 수 있습니다. 아래 요건 충족 여부를 확인해야 합니다."
        ),
    }


def _superseded_note(governing: dict) -> str:
    """어느 법으로 판정했는지 먼저 밝힌다. 판정만 보이면 근거를 알 수 없다."""
    if not governing.get("superseded"):
        return ""
    return (
        f"{' · '.join(governing['superseded'])}이지만 "
        f"{' · '.join(governing['applied'])}이므로 "
        f"{' · '.join(governing['basis'])}에 따라 개별법이 적용됩니다. "
    )


def use_overview(districts: list[str], building_uses: list[str]) -> dict | None:
    """용도구역 행위제한으로 본 용도별 허용 현황.

    `building_use_rules.json` 의 용도지역 판정표를 대체한다. 그 표는 국토계획법
    용도지역만 알아서, 농림지역의 창고시설을 조건 없는 '조건부'로 내보내고
    별표 21 이 허용하는 단독주택을 불가로 내보낸다. 그대로 LLM 에 넘기면
    "창고시설이나 교육연구시설 등 예외적으로 허용되는 시설" 같은 문장이 나온다.

    조건부 항목은 **허용행위 이름**으로 표기한다("창고시설"이 아니라
    "창고(농업·임업·축산업·수산업용)"). 뭉뚱그린 용도명이 근거처럼 읽히면 안 된다.

    아는 구역이 하나도 없으면 None — 호출부가 기존 판정표를 쓴다.
    """
    known = resolve_governing(districts)["applied"]
    if not known:
        return None
    result: dict[str, list[str]] = {
        "allowed": [], "conditional": [], "not_allowed": [],
        # 건축법 용도만으로는 안 되고 시설의 성격이 맞아야 열리는 항목.
        # 농업진흥구역처럼 모든 일반 용도가 불가인 구역에서 "그럼 뭐가 되나" 를
        # 답하는 유일한 목록이다.
        "facility_specific": [],
    }
    for use in building_uses:
        verdict = evaluate(known, "", use)
        if verdict["verdict"] == "conditional":
            names = [hit["name"] for hit in verdict["matched"]]
            # 여러 구역이 겹치면 가장 좁은(마지막) 이름을 쓴다.
            result["conditional"].append(names[-1] if names else use)
        else:
            result["not_allowed"].append(use)
    seen: set[str] = set()
    for district in known:
        for allowance in _allowances(district):
            if allowance.get("match") != "facility":
                continue
            name = allowance.get("name") or ""
            if name and name not in seen:
                seen.add(name)
                result["facility_specific"].append(name)
    return result


def _references(districts: list[str]) -> list[str]:
    out: list[str] = []
    for district in districts:
        for ref_id in _ruleset()["districts"][district].get("legal_ref_ids") or []:
            if ref_id not in out:
                out.append(ref_id)
    return out
