#!/usr/bin/env python
"""전국 건축조례 '대지 안의 공지(이격거리)' 별표·조문 원문 수집기.

이격거리는 용도·용도지역·규모별 매트릭스(별표)라 숫자를 자동으로 뽑아 판정에
쓰면 오류를 주입한다. 그래서 이 수집기는 '숫자를 만들지 않고' 관련 조문·별표
원문을 지자체별로 수집·저장한다(원문 저장소 + 메타). 이 corpus 는:
  - 벡터 근거 검색(ordinance_index)에서 이격 근거 조문을 찾는 데 쓰고,
  - 사람 검수를 거쳐 setback_rules 에 수치를 확정 입력할 때 원문 근거로 쓴다.

출력: backend/app/data/setbacks_raw.json
      backend/scripts/out/setbacks_report.json

사용: OC=<key> python collect_setbacks.py [--limit N] [--only 지자체]
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
DATA = BASE.parent / "app" / "data"
CACHE = BASE / ".cache_build"   # 건축조례 본문 캐시(도시계획조례 캐시와 분리)
OUT = BASE / "out"
RAW_PATH = DATA / "setbacks_raw.json"

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"

# 대지 안의 공지 / 이격 관련 조문·별표 판별 키워드
_SETBACK_KW = re.compile(r"대지.{0,6}공지|인접.{0,4}대지경계|건축선.{0,6}후퇴|이격|공지.{0,4}기준")


def _oc() -> str:
    oc = os.environ.get("OC") or os.environ.get("LAW_OPEN_API_OC") or ""
    if not oc:
        sys.exit("OC(법령정보센터 공동활용 키)를 환경변수로 넣어주세요.")
    return oc


def _get(url: str, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"요청 실패: {url} ({last})")


def _clean(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _meta(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", xml, re.S)
    return (m.group(1).strip() if m else "")


def enumerate_building_ordinances(oc: str) -> list[dict]:
    q = urllib.parse.quote("건축 조례")
    first = _get(f"{SEARCH_URL}?OC={oc}&target=ordin&type=XML&query={q}&display=1&page=1")
    total = int(re.search(r"<totalCnt>(\d+)</totalCnt>", first).group(1))
    rows: list[dict] = []
    per = 100
    for page in range((total // per) + 2):
        xml = _get(f"{SEARCH_URL}?OC={oc}&target=ordin&type=XML&query={q}&display={per}&page={page + 1}")
        for block in re.findall(r"<law id=.*?</law>", xml, re.S):
            def g(tag: str) -> str:
                m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
                return (m.group(1).strip() if m else "")
            name, kind = g("자치법규명"), g("자치법규종류")
            if kind != "조례" or not name.endswith("건축 조례") and not name.endswith("건축조례"):
                continue
            rows.append({
                "org": g("지자체기관명"), "name": name,
                "mst": g("자치법규일련번호"), "effective": g("시행일자"),
                "link": g("자치법규상세링크"),
            })
        time.sleep(0.2)
    best: dict[str, dict] = {}
    for r in rows:
        if r["org"] not in best or r["effective"] > best[r["org"]]["effective"]:
            best[r["org"]] = r
    return sorted(best.values(), key=lambda r: r["org"])


def fetch_body(oc: str, mst: str) -> str:
    CACHE.mkdir(exist_ok=True)
    c = CACHE / f"{mst}.xml"
    if c.exists() and c.stat().st_size > 200:
        return c.read_text(encoding="utf-8")
    xml = _get(f"{SERVICE_URL}?OC={oc}&target=ordin&MST={mst}&type=XML")
    c.write_text(xml, encoding="utf-8")
    time.sleep(0.25)
    return xml


def extract_setback_passages(xml: str) -> list[dict]:
    """대지공지·이격 관련 조문·별표 원문을 뽑는다(숫자 판정 없이 원문 저장)."""
    passages: list[dict] = []
    for title, body in re.findall(r"<조제목>(.*?)</조제목>.*?<조내용>(.*?)</조내용>", xml, re.S):
        b = _clean(body)
        if _SETBACK_KW.search(b):
            art = re.search(r"제\d+조(?:의\d+)?", b)
            paren = re.search(r"\(([^)]*)\)", _clean(title) or b[:40])
            passages.append({
                "article": art.group(0) if art else None,
                "title": paren.group(1) if paren else "대지 안의 공지",
                "text": b[:1500],
            })
    for bt in re.findall(r"<별표내용>(.*?)</별표내용>", xml, re.S):
        b = _clean(bt)
        if _SETBACK_KW.search(b):
            passages.append({"article": "별표", "title": "대지 안의 공지(별표)", "text": b[:1500]})
    return passages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    oc = _oc()

    print("전국 건축조례 목록 수집 중...", flush=True)
    listing = enumerate_building_ordinances(oc)
    if args.only:
        listing = [r for r in listing if args.only in r["org"]]
    if args.limit:
        listing = listing[: args.limit]
    print(f"대상 지자체: {len(listing)}곳", flush=True)

    out: dict[str, dict] = {}
    found = 0
    for i, row in enumerate(listing, 1):
        try:
            xml = fetch_body(oc, row["mst"])
            passages = extract_setback_passages(xml)
            out[row["org"]] = {
                "_meta": {
                    "org": row["org"],
                    "ordinance_name": _meta(xml, "자치법규명") or row["name"],
                    "ordinance_no": _meta(xml, "공포번호"),
                    "effective_date": _meta(xml, "시행일자") or row["effective"],
                    "source_url": "https://www.law.go.kr" + row["link"] if row["link"] else None,
                    "review_status": "needs_review",  # 이격 수치는 사람 검수 후 확정
                    "passages_found": len(passages),
                },
                "passages": passages,
            }
            if passages:
                found += 1
            if i % 20 == 0 or i == len(listing):
                print(f"[{i}/{len(listing)}] {row['org']}: 관련조문 {len(passages)}개", flush=True)
        except Exception as e:  # noqa: BLE001
            out[row["org"]] = {"_meta": {"org": row["org"], "review_status": "fetch_failed",
                                          "error": str(e)}, "passages": []}

    OUT.mkdir(exist_ok=True)
    payload = {
        "_meta": {
            "note": "건축조례 '대지 안의 공지(이격)' 원문 corpus. 수치 자동판정 금지, 검수 후 확정.",
            "source": "국가법령정보센터 공동활용 API (target=ordin)",
            "jurisdictions_with_passages": found,
            "total_targets": len(listing),
        },
        **out,
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    payload["_meta"]["content_hash"] = hashlib.sha256(body).hexdigest()[:16]
    RAW_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "setbacks_report.json").write_text(json.dumps(
        {"total": len(listing), "with_passages": found}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n대지공지 조문 확보 지자체: {found}/{len(listing)}")
    print("원문 corpus:", RAW_PATH)


if __name__ == "__main__":
    main()
