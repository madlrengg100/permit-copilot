"""정형 규칙으로 필지별 인허가 단계와 선행관계 그래프를 생성한다."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA = Path(__file__).resolve().parent.parent / "data"
_RULES_PATH = _DATA / "permit_rules.json"
_LEGAL_CATALOG_PATH = _DATA / "legal_rule_catalog.json"
_TEMPLATE = re.compile(r"\$\{([^}]+)}")


@lru_cache(maxsize=1)
def _ruleset() -> dict:
    return json.loads(_RULES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _legal_catalog() -> dict:
    return json.loads(_LEGAL_CATALOG_PATH.read_text(encoding="utf-8"))


def _get(context: dict, path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _condition(condition: dict | None, context: dict, sets: dict) -> bool:
    if not condition:
        return True
    if "all" in condition:
        return all(_condition(item, context, sets) for item in condition["all"])
    if "any" in condition:
        return any(_condition(item, context, sets) for item in condition["any"])
    if "not" in condition:
        return not _condition(condition["not"], context, sets)

    value = _get(context, condition["path"])
    operator = condition.get("op", "equals")
    expected = condition.get("value")
    if operator == "equals":
        return value == expected
    if operator == "not_equals":
        return value != expected
    if operator == "truthy":
        return bool(value)
    if operator == "nonempty":
        return isinstance(value, (list, tuple, set, dict, str)) and len(value) > 0
    if operator == "in":
        return value in (expected or [])
    if operator == "not_in":
        return value not in (expected or [])
    if operator == "in_set":
        return value in sets.get(condition.get("set"), [])
    if operator == "in_set_or_missing":
        return value is None or value in sets.get(condition.get("set"), [])
    raise ValueError(f"지원하지 않는 인허가 조건 연산자: {operator}")


def _render(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        matches = list(_TEMPLATE.finditer(value))
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            replacement = _get(context, matches[0].group(1))
            return "" if replacement is None else replacement
        return _TEMPLATE.sub(
            lambda match: str(_get(context, match.group(1)) or ""),
            value,
        )
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    return value


def _contexts(rule: dict, state: dict, authority: str) -> list[dict]:
    base = {**state, "authority": authority}
    path = rule.get("for_each")
    if not path:
        return [base]
    values = _get(base, path)
    if not isinstance(values, list):
        return []
    return [{**base, "item": item} for item in values if isinstance(item, dict)]


def _attach_legal_refs(item: dict, catalog: dict) -> None:
    references = catalog.get("references", {})
    ids = item.get("legal_ref_ids") or []
    item["legal_references"] = [
        {"ref_id": ref_id, **references[ref_id]}
        for ref_id in ids
        if ref_id in references
    ]


def _deduplicate(items: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        item_id = item.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _link_dependencies(items: list[dict]) -> list[dict]:
    ids = [item["id"] for item in items]
    existing = set(ids)
    prior_required: list[str] = []
    for item in items:
        dependencies = [
            dependency
            for dependency in item.pop("depends_on_if_present", [])
            if dependency in existing and dependency != item["id"]
        ]
        if item.pop("depends_on_all_previous", False):
            dependencies.extend(prior_required)
        item["depends_on"] = list(dict.fromkeys(dependencies))
        if item.get("required"):
            prior_required.append(item["id"])
    return items


def build(state: dict) -> dict:
    """현재 필지 상태에 맞는 규칙만 평가해 기존 UI 형식으로 반환한다."""
    ruleset = _ruleset()
    catalog = _legal_catalog()
    authority = state.get("jurisdiction") or "관할 시·군·구"
    sets = ruleset.get("sets", {})
    items: list[dict] = []
    evaluations: list[dict] = []

    for rule in ruleset.get("rules", []):
        matched_count = 0
        for context in _contexts(rule, state, authority):
            if not _condition(rule.get("when"), context, sets):
                continue
            item = _render(deepcopy(rule["emit"]), context)
            item["rule_id"] = rule["rule_id"]
            item["order"] = rule["order"]
            _attach_legal_refs(item, catalog)
            items.append(item)
            matched_count += 1
        evaluations.append({
            "rule_id": rule["rule_id"],
            "matched": matched_count > 0,
            "emitted_count": matched_count,
        })

    items = _deduplicate(sorted(items, key=lambda item: (item["order"], item["id"])))
    items = _link_dependencies(items)
    for index, item in enumerate(items, 1):
        item["sequence"] = index

    screen = state.get("regulatory_screen") or {}
    road = state.get("road_access") or {}
    category = (state.get("jimok_info") or {}).get("category")
    unknowns = list(dict.fromkeys(
        list(screen.get("unknowns") or [])
        + list(road.get("unknowns") or [])
        + (["경사도·표고", "입목축적"] if category == "forest" else [])
    ))
    edges = [
        {"from": dependency, "to": item["id"]}
        for item in items
        for dependency in item.get("depends_on", [])
    ]
    return {
        "authority": authority,
        "items": items,
        "unknowns": unknowns,
        "summary": f"예상 인허가·협의 {len(items)}단계",
        "caveat": "법정 의제·일괄협의 여부와 실제 처리기간은 사업 규모 및 보완 요구에 따라 달라집니다.",
        "parcel_pnu": (state.get("parcel") or {}).get("pnu", ""),
        "ruleset": {
            "schema_version": ruleset.get("_meta", {}).get("schema_version"),
            "version": ruleset.get("_meta", {}).get("ruleset_version"),
            "status": ruleset.get("_meta", {}).get("status"),
        },
        "evaluation_trace": evaluations,
        "workflow_graph": {
            "nodes": [
                {
                    "id": item["id"],
                    "phase": item.get("phase"),
                    "required": item.get("required", True),
                    "rule_id": item.get("rule_id"),
                }
                for item in items
            ],
            "edges": edges,
        },
    }


def clear_rule_cache() -> None:
    """규칙 파일 갱신 후 장기 실행 프로세스에서 명시적으로 다시 읽는다."""
    _ruleset.cache_clear()
    _legal_catalog.cache_clear()
