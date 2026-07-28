#!/usr/bin/env python
"""전국 건축조례 '대지 안의 공지' 별표(첨부 HWP 표)를 받아 표 셀 텍스트를 추출한다.

setbacks_raw.json 의 지자체별 건축조례 MST 로 본문을 받아, 제목에 '대지'+'공지'가
든 별표(공개공지 제외)의 첨부 HWP 다운로드 URL을 찾아 내려받고, hwp5proc 로 표
셀 텍스트를 뽑아 setbacks_tables_raw.json 에 저장한다. (파싱→규칙화는 다음 단계)

수치를 지어내지 않는다 — 여기서는 '표 셀 원문'만 안전하게 모은다.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE.parent / "app" / "data"
HWP_CACHE = BASE / ".cache_hwp"
XML_CACHE = BASE / ".cache_build"
RAW_IN = DATA / "setbacks_raw.json"
OUT = DATA / "setbacks_tables_raw.json"

SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"


def _oc() -> str:
    import os

    return os.environ.get("OC") or os.environ.get("LAW_OPEN_API_OC") or ""


def _get_bytes(url: str, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"다운로드 실패 {url}: {last}")


def _ordinance_xml(oc: str, mst: str) -> str:
    XML_CACHE.mkdir(exist_ok=True)
    c = XML_CACHE / f"{mst}.xml"
    if c.exists() and c.stat().st_size > 500:
        return c.read_text(encoding="utf-8")
    xml = _get_bytes(f"{SERVICE_URL}?OC={oc}&target=ordin&MST={mst}&type=XML").decode("utf-8", "replace")
    c.write_text(xml, encoding="utf-8")
    time.sleep(0.25)
    return xml


def _find_setback_appendix_url(xml: str) -> str | None:
    """제목에 '대지'+'공지'가 든 별표(공개공지 제외)의 HWP 첨부 URL."""
    for block in re.findall(r"<별표단위.*?</별표단위>", xml, re.S):
        title = re.search(r"<별표제목><!\[CDATA\[(.*?)\]\]></별표제목>", block, re.S)
        t = (title.group(1) if title else "")
        if "대지" in t and "공지" in t and "공개공지" not in t:
            url = re.search(r"<별표첨부파일명><!\[CDATA\[(https?://[^\]]+)\]\]", block)
            if url:
                return url.group(1)
    return None


def _hwp_table_cells(hwp_path: Path) -> list[str]:
    """hwp5proc xml → 표/문단 셀 텍스트 목록."""
    try:
        out = subprocess.run(
            ["hwp5proc", "xml", str(hwp_path)],
            capture_output=True, timeout=60,
        ).stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    cells = [re.sub(r"\s+", " ", t).strip()
             for t in re.findall(r"<Text[^>]*>(.*?)</Text>", out, re.S)]
    return [c for c in cells if c]


def main() -> None:
    oc = _oc()
    if not oc:
        raise SystemExit("OC(법령정보센터 키) 필요: 환경변수 OC/LAW_OPEN_API_OC")
    raw = json.loads(RAW_IN.read_text(encoding="utf-8"))
    jurisdictions = [k for k in raw if not k.startswith("_")]
    HWP_CACHE.mkdir(exist_ok=True)

    out: dict = {}
    for i, org in enumerate(jurisdictions, 1):
        meta = raw[org].get("_meta") or {}
        src_url = (meta.get("source_url") or "").replace("&amp;", "&")
        m = re.search(r"MST=(\d+)", src_url)
        if not m:
            out[org] = {"status": "no_mst"}
            print(f"[{i}/{len(jurisdictions)}] {org}: MST 없음", flush=True)
            continue
        mst = m.group(1)
        try:
            xml = _ordinance_xml(oc, mst)
            url = _find_setback_appendix_url(xml)
            if not url:
                out[org] = {"status": "no_appendix", "ordinance": meta.get("ordinance_name")}
                print(f"[{i}/{len(jurisdictions)}] {org}: 대지공지 별표 없음", flush=True)
                continue
            hwp = HWP_CACHE / f"{mst}.hwp"
            if not (hwp.exists() and hwp.stat().st_size > 2000):
                hwp.write_bytes(_get_bytes(url))
                time.sleep(0.2)
            cells = _hwp_table_cells(hwp)
            out[org] = {
                "status": "extracted" if cells else "empty",
                "ordinance": meta.get("ordinance_name"),
                "effective_date": meta.get("effective_date"),
                "appendix_url": url,
                "cells": cells,
            }
            print(f"[{i}/{len(jurisdictions)}] {org}: 셀 {len(cells)}개", flush=True)
        except Exception as e:  # noqa: BLE001
            out[org] = {"status": "error", "error": str(e)}
            print(f"[{i}/{len(jurisdictions)}] {org}: 실패 {e}", flush=True)

    payload = {
        "_meta": {
            "note": "건축조례 '대지 안의 공지' 별표 HWP에서 추출한 표 셀 원문. 파싱→규칙화 전 단계.",
            "extracted": sum(1 for v in out.values() if v.get("status") == "extracted"),
            "total": len(jurisdictions),
        },
        **out,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT} · 추출성공 {payload['_meta']['extracted']}/{len(jurisdictions)}")


if __name__ == "__main__":
    main()
