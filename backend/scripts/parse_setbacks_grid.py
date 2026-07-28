#!/usr/bin/env python
"""건축조례 '대지 안의 공지' 별표 HWP 표를 '격자 그대로' 결정적으로 파싱한다.

LLM 을 쓰지 않는다. hwp5proc 로 HWP 표의 행/셀(TableRow/TableCell)을 뽑아
3열 구조(대상 건축물 | 건축선거리 | 인접대지경계선거리)를 읽어 규칙으로 만든다.
값은 표 셀에 있는 숫자만 쓴다(지어내지 않음). 결과는 setback_rules 스키마.

  python parse_setbacks_grid.py --only 아산
  python parse_setbacks_grid.py                 # 전체(캐시된 HWP)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE.parent / "app" / "data"
HWP_CACHE = BASE / ".cache_hwp"
RAW = DATA / "setbacks_tables_raw.json"
OUT = DATA / "setbacks_parsed.json"

# 용도 정규화 — 셀 텍스트에 이 키워드가 있으면 그 표준 용도로 본다(긴 것부터).
_USE_KEYS: list[tuple[str, str]] = [
    ("공동주택", "공동주택"), ("아파트", "공동주택"), ("연립주택", "공동주택"), ("다세대", "공동주택"),
    ("단독주택", "단독주택"), ("전용주거지역에서 건축", "단독주택"),
    ("공장", "공장"), ("창고", "창고시설"),
    ("판매시설", "판매시설"), ("숙박시설", "숙박시설"),
    ("제1종근린생활시설", "제1종근린생활시설"), ("제2종근린생활시설", "제2종근린생활시설"),
    ("근린생활시설", "제2종근린생활시설"),
    ("업무시설", "업무시설"), ("의료시설", "의료시설"),
    ("교육연구시설", "교육연구시설"), ("위락시설", "위락시설"),
]

# 거리: 'N미터' 또는 'N m'(공백 허용). 제곱미터(면적)는 '제곱'이 앞에 있어 안 걸림.
_DIST = re.compile(r"(\d+(?:\.\d+)?)\s*(?:미터|m|M)(?![A-Za-z0-9])")
_GROSS = re.compile(r"(\d[\d,]*)\s*(?:제곱미터|㎡|m2|평방미터)")


def _dists(cell: str) -> list[float]:
    """셀에서 거리(미터) 목록. 셀이 '-' 뿐이면 0."""
    c = cell or ""
    out = [float(m.group(1)) for m in _DIST.finditer(c)]
    if not out and c.strip() in ("-", "－", "ㅡ"):
        out.append(0.0)
    return out


def _use_of(text: str) -> str | None:
    t = text or ""
    # '공동주택을 제외' 는 공동주택이 아니라 (전용주거지역) 단독주택 행이다.
    if re.search(r"공동주택[을은]?\s*제외", t):
        if "전용주거지역" in t or "단독주택" in t:
            return "단독주택"
        t = t.replace("공동주택", "")  # 오탐 방지
    for kw, use in _USE_KEYS:
        if kw in t:
            return use
    return None


def _gross_min(text: str) -> int | None:
    m = _GROSS.search(text or "")
    if m and "이상" in text:
        return int(m.group(1).replace(",", ""))
    return None


def _grid(hwp: Path) -> list[list[str]]:
    """HWP 표를 [행][열] 텍스트 격자로. (col 속성으로 열 정렬)"""
    try:
        xml = subprocess.run(["hwp5proc", "xml", str(hwp)],
                             capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    except Exception:
        return []
    rows: list[list[str]] = []
    for row in re.findall(r"<TableRow\b.*?</TableRow>", xml, re.S):
        cells: list[tuple[int, str]] = []
        for m in re.finditer(r'<TableCell\b([^>]*)>(.*?)</TableCell>', row, re.S):
            col = re.search(r'\bcol="(\d+)"', m.group(1))
            texts = re.findall(r"<Text[^>]*>(.*?)</Text>", m.group(2), re.S)
            txt = " ".join(re.sub(r"\s+", " ", t).strip() for t in texts).strip()
            cells.append((int(col.group(1)) if col else len(cells), txt))
        cells.sort(key=lambda c: c[0])
        rows.append([t for _, t in cells])
    return rows


def _parse_single_dist(rows: list[list[str]]) -> list[dict]:
    """형식(B): 용도 | 규모 | 거리(1열, 지역별 값 inline). 거리는 건축선·인접 공통값으로 본다."""
    rules: list[dict] = []
    ctx_use: str | None = None
    ctx_gross: int | None = None
    for r in rows:
        joined = " ".join(r)
        if ("띄어야" in joined and "거리" in joined and "용도" in joined) or "대상 건축물" in joined:
            continue  # 헤더
        # 거리셀 = 거리(N미터/N m) 포함 셀. 용도/규모는 나머지 셀에서.
        dist_cell = next((c for c in r if _DIST.search(c)), None)
        use = None
        gross = None
        for c in r:
            if c is dist_cell:
                continue
            use = use or _use_of(c)
            g = _gross_min(c)
            if g:
                gross = g
        # 규모가 별도 셀에 '• 500㎡ 이상'로 있을 수도, 용도셀에 붙어있을 수도.
        if use:
            ctx_use = use
            ctx_gross = gross
        elif gross:
            ctx_gross = gross
        if not dist_cell:
            continue
        u = use or ctx_use
        if not u:
            continue
        for lbl, d in _labeled_dists(dist_cell):
            if not (0 <= d <= 12):
                continue
            when: dict = {}
            if ctx_gross:
                when["min_gross"] = ctx_gross
            when.update(_zone_of(lbl))
            # 공동주택 세부유형(아파트/연립 등)은 규모조건 없이.
            rule = {"use": u, "front_m": d, "adjacent_m": d}
            if lbl and u == "공동주택":
                rule["note"] = f"{lbl} 기준"
                rule.pop("front_m", None)  # 세부유형은 값만 참고로
                rule["front_m"] = d
            if when:
                rule["when"] = when
            rules.append(rule)
    return rules


def _zone_of(label: str) -> dict:
    """거리셀 inline 라벨 → when 조건."""
    t = label or ""
    if "준공업지역" in t and "외" not in t:
        return {"zone": "준공업지역"}
    if "준공업지역 외" in t or "준공업지역외" in t:
        return {"not_zone_in": ["전용공업지역", "일반공업지역", "준공업지역"]}
    if "상업지역" in t:
        return {"zone_contains": "상업지역"}
    return {}


def _labeled_dists(cell: str) -> list[tuple[str, float]]:
    """거리셀에서 (라벨, 거리m). '준공업지역 : 1.5m', '아파트 3 m 이상', '3미터' 등.
    불릿(•·▪◦･)과 '- '(대시+공백)로 항목을 나눈다."""
    out: list[tuple[str, float]] = []
    parts = re.split(r"[•·▪◦･]|(?:^|\s)-\s+", cell or "")
    if len(parts) == 1:
        parts = re.split(r"(?<=미터)\s+(?=[가-힣(])|(?<=[mM])\s+(?=[가-힣(])", cell or "")
    for p in parts:
        m = _DIST.search(p)
        if not m:
            continue
        lbl = p[: m.start()].strip(" :：-·▪")
        out.append((lbl, float(m.group(1))))
    return out


def parse_grid(hwp: Path) -> list[dict]:
    """건축조례 대지공지 표를 규칙 목록으로. 두 형식 자동 처리:
      (A) 대상 | 건축선거리 | 인접경계거리  (거리 2열)
      (B) 용도 | 규모 | 거리(1열, 지역별 값 inline)
    """
    rows = _grid(hwp)
    if not rows:
        return []
    # 헤더로 형식 판별: '건축선'과 '인접'이 각각 다른 열에 있으면 (A) 2열.
    header = next((r for r in rows if any("거리" in c or "건축선" in c for c in r)), rows[0])
    two_col = any("건축선" in c for c in header) and any("인접" in c for c in header)
    if not two_col:
        return _parse_single_dist(rows)
    # 3열 이상인 행만 데이터로. 헤더(건축선/인접대지경계선 포함)는 건너뜀.
    rules: list[dict] = []
    ctx_use: str | None = None
    ctx_gross: int | None = None
    ctx_zone: str | None = None
    for r in rows:
        if len(r) < 3:
            continue
        c0, c1, c2 = r[0], r[1], r[2]
        if ("건축선" in c1 and "거리" in c1) or "인접대지경계선" in c2:
            continue  # 헤더
        front = _dists(c1)
        adj = _dists(c2)
        use = _use_of(c0)
        g = _gross_min(c0)
        # 용도가 새로 나오면 문맥 갱신
        if use:
            ctx_use = use
            ctx_gross = g
            ctx_zone = None
        elif g is not None:
            ctx_gross = g
        # 하위 지역 조건
        zone = None
        if "준공업지역" in c0 and "외" not in c0:
            zone = "준공업지역"
        elif "준공업지역 외" in c0 or "준공업지역외" in c0:
            zone = "__non_junindustrial"
        elif "상업지역" in c0:
            zone = "상업지역"

        if not (front or adj):
            # 거리 없는 카테고리 행(문맥만 설정)
            if zone:
                ctx_zone = zone
            continue
        u = use or ctx_use
        if not u:
            continue
        z = zone or ctx_zone
        # 하위 규모(1천㎡ 미만/이상) 다중값 처리
        subgross = re.findall(r"(\d[\d,]*)\s*(?:제곱미터|㎡)\s*(미만|이상)", c0)
        pairs = max(len(front), len(adj), 1)
        for i in range(pairs):
            f = front[i] if i < len(front) else (front[-1] if front else 0.0)
            a = adj[i] if i < len(adj) else (adj[-1] if adj else 0.0)
            when: dict = {}
            base_g = ctx_gross
            if base_g:
                when["min_gross"] = base_g
            if z == "준공업지역":
                when["zone"] = "준공업지역"
            elif z == "__non_junindustrial":
                when["not_zone_in"] = ["전용공업지역", "일반공업지역", "준공업지역"]
            elif z == "상업지역":
                when["zone_contains"] = "상업지역"
            if len(subgross) == pairs and pairs > 1:
                val, kind = subgross[i]
                v = int(val.replace(",", ""))
                if kind == "미만":
                    when["max_gross_excl"] = v
                else:
                    when["min_gross"] = v
            if u == "단독주택" and "전용주거지역" in c0:
                when["zone_contains"] = "전용주거지역"
            rule = {"use": u, "front_m": f, "adjacent_m": a}
            if when:
                rule["when"] = when
            if 0 <= f <= 12 and 0 <= a <= 12:
                rules.append(rule)
    return rules


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    # MST 매핑은 setbacks_raw.json(source_url)에서 가져온다.
    src_raw = json.loads((DATA / "setbacks_raw.json").read_text(encoding="utf-8"))
    mst_of: dict[str, str] = {}
    for k, v in src_raw.items():
        if k.startswith("_"):
            continue
        u = (v.get("_meta") or {}).get("source_url", "").replace("&amp;", "&")
        mm = re.search(r"MST=(\d+)", u)
        if mm:
            mst_of[k] = mm.group(1)
    targets = {
        k: v for k, v in raw.items()
        if not k.startswith("_") and v.get("status") == "extracted"
    }
    if args.only:
        targets = {k: v for k, v in targets.items() if args.only in k}

    out: dict = {}
    for i, (org, rec) in enumerate(targets.items(), 1):
        mst = mst_of.get(org)
        hwp = HWP_CACHE / f"{mst}.hwp" if mst else None
        if not hwp or not hwp.exists():
            out[org] = {"error": "hwp 캐시 없음"}
            print(f"[{i}/{len(targets)}] {org}: hwp 없음", flush=True)
            continue
        rules = parse_grid(hwp)
        out[org] = {
            "source": f"{rec.get('ordinance', org)} 별표(대지 안의 공지) (시행 {rec.get('effective_date','?')})",
            "review_status": "needs_review",
            "rules": rules,
        }
        print(f"[{i}/{len(targets)}] {org}: 규칙 {len(rules)}개", flush=True)

    payload = {"_meta": {"note": "별표 HWP 표 격자 결정적 파싱(LLM 미사용). 검수 후 setbacks.json 승격.",
                         "count": len(out)}, **out}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
