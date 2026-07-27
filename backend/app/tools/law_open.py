"""국가법령정보센터 공동활용 API로 진단 근거의 현행 법령을 검증한다."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import httpx

from ..config import LAW_OPEN_API_OC


SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
_LAW_NAME = re.compile(
    r"([가-힣·\s]+?(?:법률|법|시행령|시행규칙))"
    r"(?=\s*제?\d|\s*(?:및|,|·|$))"
)


def extract_law_names(state: dict) -> list[str]:
    """진단 데이터의 근거 문자열에서 법령명만 중복 없이 뽑는다."""
    texts: list[str] = []
    regulation = state.get("regulation") or {}
    texts.append(str(regulation.get("legal_basis") or ""))
    for key in ("conversion_charge", "development_charge", "road_access"):
        texts.append(str((state.get(key) or {}).get("legal_basis") or ""))
    texts.append(str((state.get("land_conversion") or {}).get("legal_basis") or ""))
    for item in (state.get("permit_requirements") or {}).get("items", []):
        texts.append(str(item.get("basis") or ""))

    names: list[str] = []
    for basis in texts:
        same_law_parts = re.findall(r"같은\s+법\s+(시행령|시행규칙)", basis)
        cleaned = re.sub(r"(?:및|,|·)?\s*같은\s+법\s+(?:시행령|시행규칙)", "", basis)
        basis_names: list[str] = []
        for match in _LAW_NAME.finditer(cleaned):
            name = " ".join(match.group(1).split()).strip(" ·,")
            if name and name not in names:
                names.append(name)
            if name:
                basis_names.append(name)
        if same_law_parts and basis_names:
            base = re.sub(r"\s+(?:시행령|시행규칙)$", "", basis_names[0])
            for suffix in same_law_parts:
                expanded = f"{base} {suffix}"
                if expanded not in names:
                    names.append(expanded)
    return names[:10]


async def _search_one(client: httpx.AsyncClient, name: str) -> dict:
    params = {
        "OC": LAW_OPEN_API_OC,
        "target": "law",
        "type": "JSON",
        "search": 1,
        "query": name,
        "display": 10,
    }
    response = await client.get(SEARCH_URL, params=params)
    response.raise_for_status()
    payload = response.json()
    root = payload.get("LawSearch", payload)
    items = root.get("law") or []
    if isinstance(items, dict):
        items = [items]
    exact = next(
        (item for item in items if item.get("법령명한글") == name),
        items[0] if items else None,
    )
    if not exact:
        return {"query": name, "status": "NOT_FOUND"}
    title = exact.get("법령명한글") or name
    return {
        "query": name,
        "status": "VERIFIED",
        "title": title,
        "effective_date": exact.get("시행일자") or "",
        "promulgation_date": exact.get("공포일자") or "",
        "revision_type": exact.get("제개정구분명") or "",
        "ministry": exact.get("소관부처명") or "",
        # API 상세링크에는 OC가 포함되므로 인증값 없는 공개 원문 링크를 쓴다.
        "url": f"https://www.law.go.kr/법령/{quote(title)}",
    }


async def verify_legal_sources(state: dict) -> dict:
    names = extract_law_names(state)
    if not LAW_OPEN_API_OC:
        return {
            "status": "NOT_CONFIGURED",
            "sources": [],
            "message": "LAW_OPEN_API_OC가 실제 서비스 환경에 설정되어 있지 않습니다.",
        }
    if not names:
        return {
            "status": "NO_BASIS",
            "sources": [],
            "message": "검증할 근거 법령명이 없습니다.",
        }

    async with httpx.AsyncClient(
        timeout=12,
        headers={"User-Agent": "permit-copilot/1.0"},
    ) as client:
        results = await asyncio.gather(
            *(_search_one(client, name) for name in names),
            return_exceptions=True,
        )
    sources: list[dict] = []
    errors: list[str] = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            errors.append(name)
        elif result.get("status") == "VERIFIED":
            sources.append(result)

    status = "VERIFIED" if sources and not errors else "PARTIAL" if sources else "UNAVAILABLE"
    return {
        "status": status,
        "sources": sources,
        "failed_queries": errors,
        "message": (
            f"국가법령정보센터 현행 법령 {len(sources)}건 검증"
            if sources
            else "국가법령정보센터 법령 원문을 확인하지 못했습니다."
        ),
    }
