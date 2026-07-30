#!/usr/bin/env python
"""국가법령정보센터에서 토지·개발·건축 인허가 법령을 조문 단위로 수집한다.

원문 청크는 설명·근거 검색용이다. 허용 여부와 수치는 permit_rules.json 등
검수된 정형 규칙이 계산하며, 이 스크립트가 규칙 파일을 자동으로 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


BASE = Path(__file__).resolve().parent
CACHE = BASE / ".cache_law"
OUTPUT = BASE.parent / "app" / "data" / "legal_corpus_chunks.json"
SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"

CORE_LAWS = [
    "국토의 계획 및 이용에 관한 법률",
    "국토의 계획 및 이용에 관한 법률 시행령",
    "국토의 계획 및 이용에 관한 법률 시행규칙",
    "건축법",
    "건축법 시행령",
    "건축법 시행규칙",
    "농지법",
    "농지법 시행령",
    "농지법 시행규칙",
    "산지관리법",
    "산지관리법 시행령",
    "산지관리법 시행규칙",
    "개발이익 환수에 관한 법률",
    "개발이익 환수에 관한 법률 시행령",
    "자연재해대책법",
    "환경영향평가법",
    "자연환경보전법",
    "물환경보전법",
    "수도법",
    "자연공원법",
    "습지보전법",
    "국가유산영향진단법",
    "매장유산 보호 및 조사에 관한 법률",
    "도로법",
    "사도법",
    "주차장법",
    "주차장법 시행령",
    "건축물관리법",
]

KEYWORDS = re.compile(
    r"토지|대지|필지|개발행위|건축|용도지역|용도지구|용도구역|건폐율|용적률|"
    r"농지|산지|전용|도로|접도|건축선|주차|재해|환경|생태|상수원|수질|"
    r"공원|습지|국가유산|매장유산|허가|신고|협의|심의|부담금|복구|준공"
)


def _oc() -> str:
    value = os.getenv("LAW_OPEN_API_OC") or os.getenv("OC") or ""
    if not value:
        raise SystemExit("LAW_OPEN_API_OC 또는 OC 환경변수가 필요합니다.")
    return value


def _get(url: str, tries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "permit-copilot/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"국가법령정보센터 요청 실패: {last}")


def _clean(value: str) -> str:
    value = re.sub(r"<!\[CDATA\[|\]\]>", "", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tag(block: str, name: str) -> str:
    match = re.search(
        rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>",
        block,
        re.S,
    )
    return _clean(match.group(1)) if match else ""


def search_law(oc: str, name: str) -> dict:
    query = urllib.parse.quote(name)
    xml = _get(
        f"{SEARCH_URL}?OC={urllib.parse.quote(oc)}&target=law&type=XML"
        f"&search=1&query={query}&display=20"
    )
    blocks = re.findall(r"<law(?:\s[^>]*)?>.*?</law>", xml, re.S)
    candidates = []
    for block in blocks:
        title = _tag(block, "법령명한글")
        if not title:
            continue
        candidates.append({
            "title": title,
            "mst": _tag(block, "법령일련번호") or _tag(block, "법령ID"),
            "law_id": _tag(block, "법령ID"),
            "effective_date": _tag(block, "시행일자"),
            "promulgation_date": _tag(block, "공포일자"),
        })
    exact = next((item for item in candidates if item["title"] == name), None)
    if not exact:
        raise RuntimeError(f"정확한 법령명을 찾지 못했습니다: {name}")
    return exact


def fetch_body(oc: str, law: dict) -> str:
    CACHE.mkdir(exist_ok=True)
    key = law["mst"] or law["law_id"]
    cache = CACHE / f"{key}.xml"
    if cache.exists() and cache.stat().st_size > 500:
        return cache.read_text(encoding="utf-8")
    identifier = f"MST={urllib.parse.quote(law['mst'])}" if law["mst"] else (
        f"ID={urllib.parse.quote(law['law_id'])}"
    )
    xml = _get(
        f"{SERVICE_URL}?OC={urllib.parse.quote(oc)}&target=law&{identifier}&type=XML"
    )
    cache.write_text(xml, encoding="utf-8")
    time.sleep(0.2)
    return xml


def chunks(law: dict, xml: str) -> list[dict]:
    result = []
    source_url = f"https://www.law.go.kr/법령/{urllib.parse.quote(law['title'])}"
    units = re.findall(r"<조문단위(?:\s[^>]*)?>.*?</조문단위>", xml, re.S)
    for unit in units:
        article = _tag(unit, "조문번호")
        title = _tag(unit, "조문제목")
        text = _tag(unit, "조문내용") or _clean(unit)
        if not text or not KEYWORDS.search(f"{title} {text}"):
            continue
        identifier = hashlib.sha256(
            f"{law['title']}|{article}|{text}".encode()
        ).hexdigest()[:20]
        result.append({
            "chunk_id": f"law-{identifier}",
            "jurisdiction": "전국",
            "law": law["title"],
            "ordinance": law["title"],
            "article": article,
            "title": title,
            "text": text,
            "effective_date": law["effective_date"],
            "promulgation_date": law["promulgation_date"],
            "url": source_url,
            "kind": "법령-조문",
            "source": "국가법령정보센터 공동활용 API",
        })
    for index, unit in enumerate(
        re.findall(r"<별표단위(?:\s[^>]*)?>.*?</별표단위>", xml, re.S),
        start=1,
    ):
        text = _clean(unit)
        if len(text) < 20 or not KEYWORDS.search(text):
            continue
        identifier = hashlib.sha256(
            f"{law['title']}|별표{index}|{text}".encode()
        ).hexdigest()[:20]
        result.append({
            "chunk_id": f"law-{identifier}",
            "jurisdiction": "전국",
            "law": law["title"],
            "ordinance": law["title"],
            "article": f"별표 {index}",
            "title": "별표",
            "text": text,
            "effective_date": law["effective_date"],
            "promulgation_date": law["promulgation_date"],
            "url": source_url,
            "kind": "법령-별표",
            "source": "국가법령정보센터 공동활용 API",
        })
    return result


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--laws", nargs="*", default=CORE_LAWS)
    argument_parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = argument_parser.parse_args()
    oc = _oc()
    all_chunks = []
    failures = []
    for name in arguments.laws:
        try:
            law = search_law(oc, name)
            law_chunks = chunks(law, fetch_body(oc, law))
            all_chunks.extend(law_chunks)
            print(f"{name}: {len(law_chunks)}청크", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append({"law": name, "error": str(exc)})
            print(f"{name}: 실패", file=sys.stderr, flush=True)
    payload = {
        "_meta": {
            "schema_version": 1,
            "source": "국가법령정보센터 공동활용 API",
            "law_count": len({item["law"] for item in all_chunks}),
            "chunk_count": len(all_chunks),
            "failures": failures,
        },
        "chunks": all_chunks,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"저장: {arguments.output} ({len(all_chunks)}청크)")


if __name__ == "__main__":
    main()
