"""수집·정제 중인 지구단위계획 공식 원문 근거를 진단 결과에 연결한다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "data" / "district_plan_sources.json"


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _matches_source(source: dict, context: str) -> bool:
    """고시가 가리키는 지구명/대표 지번이 현재 필지와 확인된 때만 매칭한다."""
    terms = [str(term).strip() for term in source.get("match_any", []) if str(term).strip()]
    return bool(terms) and any(term in context for term in terms)


def evidence_for(
    jurisdiction: str | None,
    districts: list[str] | None,
    *,
    address: str | None = None,
    pnu: str | None = None,
) -> list[dict]:
    """지구단위계획 지정이 확인된 때만 관할 공식 자료 링크를 반환한다.

    아직 획지/PNU 매핑이 끝나지 않은 자료는 수치 판정 근거가 아니라 원문 확인
    링크로만 제공한다. 일반 필지에 지구단위계획 링크가 붙는 것을 막는다.
    """
    if not jurisdiction or not any("지구단위계획" in str(x) for x in (districts or [])):
        return []
    entries = _catalog().get("jurisdictions", {})
    matched = next(
        (value for name, value in entries.items() if jurisdiction.endswith(name) or name.endswith(jurisdiction)),
        None,
    )
    if not matched:
        return []
    # '지구단위계획구역'은 한 시군에 여러 곳이 존재한다. 관할만 같은 자료를
    # 모두 붙이면 다른 지구의 시행지침을 오인하게 되므로 지구명/대표 지번이
    # 주소·PNU·토지이용 지정명 중 하나와 일치하는 자료만 노출한다.
    context = " ".join(
        str(value) for value in [address or "", pnu or "", *(districts or [])]
    )
    result = []
    for source in matched.get("sources", []):
        url = source.get("source_page")
        if not url or not _matches_source(source, context):
            continue
        result.append({
            "kind": "지구단위계획 근거자료",
            "plan_name": source.get("plan_name"),
            "notice_no": source.get("notice_no"),
            "notice_date": source.get("notice_date"),
            "publisher": source.get("publisher") or jurisdiction,
            "document_types": source.get("document_types") or [],
            "documents": source.get("documents") or [],
            "url": url,
            "verification_status": (
                "LATEST_NOTICE_CHECK_REQUIRED"
                if source.get("needs_latest_notice_check")
                else "VERIFIED"
            ),
        })
    return result


def clear_cache() -> None:
    _catalog.cache_clear()
