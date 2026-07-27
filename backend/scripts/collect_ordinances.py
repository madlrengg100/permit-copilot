#!/usr/bin/env python
"""전국 지자체 도시계획조례 건폐율·용적률 자동 수집·파싱기.

국가법령정보센터 공동활용 API(DRF, target=ordin)로 전국 "○○ 도시계획 조례"
(군은 "○○ 군계획 조례") 본문을 받아, '용도지역 안에서의 건폐율/용적률' 조문에서
21개 표준 용도지역별 상한값을 추출한다.

원칙(기존 수작업 데이터와 동일):
  - 조례에 숫자가 없으면(시행령 위임) 지어내지 않고 null.
  - 추출값이 국토계획법 시행령 법정 상한을 초과하면 파싱 오류로 보고 검수 대기.
  - 예외·특례·완화 조문(경관지구·취락지구·방화지구 등)은 기본값으로 쓰지 않는다.
    (조제목이 정확히 '용도지역 안에서의 건폐율/용적률'인 조문만 사용)

출력:
  - backend/app/data/ordinances_auto.json   (자동 수집본 — 수작업본과 별도)
  - backend/scripts/out/accuracy_report.json (ground truth 대조 정확도)
기존 backend/app/data/ordinances.json(수작업 검증본)은 절대 덮어쓰지 않는다.

사용:
  OC=<key> python collect_ordinances.py --limit 15    # 표본 수집·검증
  OC=<key> python collect_ordinances.py                # 전국 전체
  OC=<key> python collect_ordinances.py --validate-only # 수집 없이 캐시로 정확도만
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
DATA_DIR = BASE.parent / "app" / "data"
CACHE_DIR = BASE / ".cache_ordin"
OUT_DIR = BASE / "out"
MANUAL_PATH = DATA_DIR / "ordinances.json"
AUTO_PATH = DATA_DIR / "ordinances_auto.json"

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"

# 국토계획법 시행령 21개 표준 용도지역 (statutory_reference 키와 동일)
STANDARD_ZONES = [
    "제1종전용주거지역", "제2종전용주거지역",
    "제1종일반주거지역", "제2종일반주거지역", "제3종일반주거지역", "준주거지역",
    "중심상업지역", "일반상업지역", "근린상업지역", "유통상업지역",
    "전용공업지역", "일반공업지역", "준공업지역",
    "보전녹지지역", "생산녹지지역", "자연녹지지역",
    "보전관리지역", "생산관리지역", "계획관리지역",
    "농림지역", "자연환경보전지역",
]
# 긴 이름부터 매칭해야 '제1종전용주거지역'을 '주거지역'으로 오인하지 않는다.
_ZONES_BY_LEN = sorted(STANDARD_ZONES, key=len, reverse=True)


def _oc() -> str:
    oc = os.environ.get("OC") or os.environ.get("LAW_OPEN_API_OC") or ""
    if not oc:
        sys.exit("OC(법령정보센터 공동활용 키)를 환경변수 OC 또는 LAW_OPEN_API_OC 로 넣어주세요.")
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


# --------------------------------------------------------------- 시행령 상한
def statutory_limits() -> dict:
    data = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    return data["_meta"]["statutory_reference"]["limits"]


# ------------------------------------------------------------------ 전국 열거
def enumerate_ordinances(oc: str) -> list[dict]:
    """전국 도시계획/군계획 조례 목록. 시행규칙·세칙 제외, 지자체별 최신 1건."""
    q = urllib.parse.quote("도시계획 조례")
    first = _get(f"{SEARCH_URL}?OC={oc}&target=ordin&type=XML&query={q}&display=1&page=1")
    total = int(re.search(r"<totalCnt>(\d+)</totalCnt>", first).group(1))
    rows: list[dict] = []
    per = 100
    for page in range((total // per) + 2):
        xml = _get(
            f"{SEARCH_URL}?OC={oc}&target=ordin&type=XML&query={q}"
            f"&display={per}&page={page + 1}"
        )
        for block in re.findall(r"<law id=.*?</law>", xml, re.S):
            def g(tag: str) -> str:
                m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
                return (m.group(1).strip() if m else "")
            name = g("자치법규명")
            kind = g("자치법규종류")
            org = g("지자체기관명")
            mst = g("자치법규일련번호")
            eff = g("시행일자")
            link = g("자치법규상세링크")
            # 도시계획/군계획 '조례' 본체만. 시행규칙·시행세칙·특별회계 등 제외.
            if kind != "조례":
                continue
            if not (name.endswith("도시계획 조례") or name.endswith("군계획 조례")
                    or name.endswith("도시계획조례") or name.endswith("군계획조례")):
                continue
            rows.append({
                "org": org, "name": name, "mst": mst,
                "effective": eff, "link": link,
            })
        time.sleep(0.2)
    # 지자체별 최신(시행일자 큰 것) 1건만
    best: dict[str, dict] = {}
    for r in rows:
        key = r["org"]
        if key not in best or r["effective"] > best[key]["effective"]:
            best[key] = r
    return sorted(best.values(), key=lambda r: r["org"])


# ------------------------------------------------------------------ 본문 취득
def fetch_body(oc: str, mst: str) -> str:
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{mst}.xml"
    if cache.exists() and cache.stat().st_size > 200:
        return cache.read_text(encoding="utf-8")
    xml = _get(f"{SERVICE_URL}?OC={oc}&target=ordin&MST={mst}&type=XML")
    cache.write_text(xml, encoding="utf-8")
    time.sleep(0.25)
    return xml


# -------------------------------------------------------------------- 파서
def _clean(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _articles(xml: str) -> list[tuple[str, str]]:
    return [
        (_clean(t), _clean(b))
        for t, b in re.findall(r"<조제목>(.*?)</조제목>.*?<조내용>(.*?)</조내용>", xml, re.S)
    ]


def _zone_hits(body: str) -> int:
    return sum(
        1 for z in STANDARD_ZONES
        if re.search(re.escape(z) + r"\s*(?::|은|는)?\s*\d{1,4}\s*(?:퍼센트|%)", body)
    )


def _find_zone_article(articles: list[tuple[str, str]], kind: str) -> str | None:
    """용도지역별 '기본' 건폐율/용적률 조문을 찾는다.

    조제목이 있으면 정확 제목으로, 조제목이 비어 있는(제목이 조내용에 인라인)
    지자체는 폴백으로 찾는다. 판별 기준:
      - 기본표는 시행령 위임조항 '영 제84조제1항'(건폐율)/'제85조제1항'(용적률)을
        인용한다. 예외·완화 조문은 제84조 제4~9항 등을 인용하므로 자연히 걸러진다.
      - 그중 표준 용도지역이 가장 많이 매칭되는 조문을 채택한다.
    """
    inline = "용도지역안에서의건폐율" if kind == "bcr" else "용도지역안에서의용적률"
    base_ref = "제84조제1항" if kind == "bcr" else "제85조제1항"
    best: str | None = None
    best_score = 0
    for title, body in articles:
        core = title
        m = re.search(r"\(([^)]*)\)", title)
        if m:
            core = m.group(1)
        head = (core + " " + body[:60]).replace(" ", "")
        is_base = (inline in head) or (base_ref in body.replace(" ", ""))
        if not is_base:
            continue
        score = _zone_hits(body)
        if score > best_score:
            best_score, best = score, body
    return best if best_score > 0 else None


def _parse_zone_values(body: str) -> dict[str, int]:
    """'... 제1종전용주거지역 : 50퍼센트 이하 ...'에서 용도지역별 첫 수치를 뽑는다.
    괄호 안 특례(시장정비사업구역 70퍼센트 등)는 콜론 직후 첫 숫자만 취해 배제한다."""
    out: dict[str, int] = {}
    for zone in _ZONES_BY_LEN:
        if zone in out:
            continue
        # 용도지역명 뒤 콜론, 그 직후 첫 '숫자 퍼센트/%'.
        m = re.search(
            re.escape(zone) + r"\s*(?::|은|는)?\s*(\d{1,4})\s*(?:퍼센트|%)",
            body,
        )
        if m:
            out[zone] = int(m.group(1))
    return out


def parse_ordinance(xml: str) -> dict:
    arts = _articles(xml)

    def meta(tag: str) -> str:
        m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", xml, re.S)
        return (m.group(1).strip() if m else "")

    bcr_body = _find_zone_article(arts, "bcr")
    far_body = _find_zone_article(arts, "far")
    bcr = _parse_zone_values(bcr_body) if bcr_body else {}
    far = _parse_zone_values(far_body) if far_body else {}
    return {
        "ordinance_name": meta("자치법규명"),
        "ordinance_no": meta("공포번호"),
        "effective_date": meta("시행일자"),
        "promulgated": meta("공포일자"),
        "bcr_articles_found": bool(bcr_body),
        "far_articles_found": bool(far_body),
        "bcr": bcr,
        "far": far,
    }


# ------------------------------------------------------------- 검증 + 레코드
def validate_and_build(parsed: dict, org: str, link: str, stat: dict) -> dict:
    zones: dict[str, dict] = {}
    flags: list[str] = []
    for zone in STANDARD_ZONES:
        b = parsed["bcr"].get(zone)
        f = parsed["far"].get(zone)
        lim = stat.get(zone) or {}
        # 법정 상한 초과 = 파싱 오류로 간주하고 값 폐기 + 플래그
        if b is not None and lim.get("bcr_max_pct") is not None and b > lim["bcr_max_pct"]:
            flags.append(f"{zone} 건폐율 {b}>{lim['bcr_max_pct']}(법정초과) 폐기")
            b = None
        if f is not None and lim.get("far_max_pct") is not None and f > lim["far_max_pct"]:
            flags.append(f"{zone} 용적률 {f}>{lim['far_max_pct']}(법정초과) 폐기")
            f = None
        if b is None and f is None:
            continue
        zones[zone] = {"bcr_max_pct": b, "far_max_pct": f, "far_min_pct": None}

    filled = len(zones)
    confidence = "high" if filled >= 15 else "medium" if filled >= 5 else "low"
    review = "needs_review" if (flags or confidence == "low") else "auto_extracted"
    return {
        "_meta": {
            "org": org,
            "ordinance_name": parsed["ordinance_name"],
            "ordinance_no": parsed["ordinance_no"],
            "effective_date": parsed["effective_date"],
            "source_url": "https://www.law.go.kr" + link if link else None,
            "articles": "제(용도지역 안에서의 건폐율)·(용도지역 안에서의 용적률)",
            "extraction_confidence": confidence,
            "review_status": review,
            "validation_flags": flags,
            "zones_filled": filled,
        },
        "zones": zones,
    }


# ---------------------------------------------------------- ground truth 대조
def accuracy_report(auto: dict) -> dict:
    manual = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    gt_names = [k for k in manual if not k.startswith("_")]
    report = {"compared": [], "summary": {}}
    total = agree = disagree = missing = 0
    for j in gt_names:
        rec = auto.get(j)
        if not rec:
            report["compared"].append({"jurisdiction": j, "status": "auto_missing"})
            continue
        auto_zones = rec["zones"]
        for zone, mv in manual[j].items():
            if zone.startswith("_"):
                continue
            for metric in ("bcr_max_pct", "far_max_pct"):
                man = mv.get(metric)
                if man is None:
                    continue
                total += 1
                av = (auto_zones.get(zone) or {}).get(metric)
                if av is None:
                    missing += 1
                elif av == man:
                    agree += 1
                else:
                    disagree += 1
                    report["compared"].append({
                        "jurisdiction": j, "zone": zone, "metric": metric,
                        "manual": man, "auto": av,
                    })
    report["summary"] = {
        "ground_truth_values": total,
        "agree": agree,
        "disagree": disagree,
        "auto_missing": missing,
        "accuracy_pct": round(100 * agree / total, 1) if total else None,
    }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리 지자체 수 제한(표본)")
    ap.add_argument("--only", default="", help="특정 지자체명 부분일치만")
    args = ap.parse_args()
    oc = _oc()
    stat = statutory_limits()

    print("전국 도시계획/군계획 조례 목록 수집 중...", flush=True)
    listing = enumerate_ordinances(oc)
    if args.only:
        listing = [r for r in listing if args.only in r["org"]]
    if args.limit:
        listing = listing[: args.limit]
    print(f"대상 지자체: {len(listing)}곳", flush=True)

    auto: dict[str, dict] = {}
    for i, row in enumerate(listing, 1):
        try:
            xml = fetch_body(oc, row["mst"])
            parsed = parse_ordinance(xml)
            rec = validate_and_build(parsed, row["org"], row["link"], stat)
            auto[row["org"]] = rec
            m = rec["_meta"]
            print(f"[{i}/{len(listing)}] {row['org']}: "
                  f"{m['zones_filled']}개 zone, {m['extraction_confidence']}"
                  f"{' ⚠' + str(len(m['validation_flags'])) if m['validation_flags'] else ''}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(listing)}] {row['org']}: 실패 {e}", flush=True)
            auto[row["org"]] = {"_meta": {"org": row["org"], "review_status": "fetch_failed",
                                          "error": str(e)}, "zones": {}}

    OUT_DIR.mkdir(exist_ok=True)
    payload = {
        "_meta": {
            "note": "DRF 자치법규 API 자동 수집·파싱본. 수작업 검증본(ordinances.json)과 별도.",
            "source": "국가법령정보센터 공동활용 API (target=ordin)",
            "jurisdiction_count": len([k for k in auto if auto[k]["zones"]]),
            "total_targets": len(listing),
        },
        **auto,
    }
    # content_hash (재현·변경감지용)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload["_meta"]["content_hash"] = hashlib.sha256(body).hexdigest()[:16]
    AUTO_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rep = accuracy_report(auto)
    (OUT_DIR / "accuracy_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 정확도(ground truth 11곳 대조) ===")
    print(json.dumps(rep["summary"], ensure_ascii=False, indent=2))
    print(f"\n자동본: {AUTO_PATH}")
    print(f"리포트: {OUT_DIR / 'accuracy_report.json'}")


if __name__ == "__main__":
    main()
