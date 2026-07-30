"""법률의 금지·예외·누적 적용 관계를 정형 규칙으로 평가한다."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PATH = Path(__file__).resolve().parent.parent / "data" / "legal_conflict_rules.json"
@lru_cache(maxsize=1)
def _rules():
    return json.loads(_PATH.read_text(encoding="utf-8"))
def _get(state: dict, path: str) -> Any:
    value: Any = state
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value
def evaluate(state: dict) -> dict:
    matched = [
        {k: v for k, v in rule.items() if k not in {"path", "equals"}}
        for rule in _rules()["rules"]
        if _get(state, rule["path"]) == rule["equals"]
    ]
    matched.sort(key=lambda item: -item["priority"])
    blocking = [item for item in matched if item["blocks_final_approval"]]
    return {
        "status": blocking[0]["status"] if blocking else ("CUMULATIVE_REQUIREMENTS" if matched else "CLEAR"),
        "ruleset_version": _rules()["_meta"]["ruleset_version"],
        "evaluations": matched,
        "blocks_final_approval": bool(blocking),
        "requires_consultation": any(item["requires_consultation"] for item in matched),
        "summary": " / ".join(dict.fromkeys(item["effect"] for item in matched)),
    }
