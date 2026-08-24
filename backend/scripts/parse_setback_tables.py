#!/usr/bin/env python
"""건축조례 '대지 안의 공지' 별표 표 셀 → 규칙 JSON 파싱(LLM).

setbacks_tables_raw.json 의 표 셀을 읽어, LLM 으로 setbacks.json 규칙 스키마로
변환한다. 값은 표에 있는 것만 쓰도록 강하게 지시하고, 결과는 검수 상태로 둔다.
아산(정답 아는 지자체)으로 먼저 검증한다.

  python parse_setback_tables.py --only 아산     # 아산만(검증)
  python parse_setback_tables.py                 # 전체(추출성공분)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
RAW = DATA / "setbacks_tables_raw.json"
OUT = DATA / "setbacks_parsed.json"

# 조례 표의 계층·조건 로직을 정확히 옮기려면 강한 모델이 필요하다(flash-lite 는
# 조건을 자주 틀림). Gemini pro 로 파싱한다(같은 GEMINI_API_KEY).
_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
_MODEL = os.getenv("SETBACK_PARSE_MODEL", "gemini-2.5-pro")
if not _KEY:
    raise SystemExit("GEMINI_API_KEY 가 없습니다.")
_client = OpenAI(base_url=_BASE, api_key=_KEY)

PROMPT = """다음은 한 지자체 건축조례 '대지 안의 공지' 별표의 표 셀 텍스트를 순서대로 나열한 것이다.
이 표를 아래 규칙 JSON 배열로 정확히 변환하라. 표에 실제로 있는 값만 쓰고, 없는 값은 절대 지어내지 마라.

각 규칙 객체:
- "use": 건축물 용도. 반드시 다음 중 하나로 정규화: 단독주택, 공동주택, 공장, 창고시설, 판매시설, 숙박시설, 제1종근린생활시설, 제2종근린생활시설, 업무시설, 의료시설, 교육연구시설, 위락시설. 표의 용도가 이 목록에 없으면 그 행은 건너뛴다.
- "front_m": 건축선으로부터 건축물까지 띄어야 하는 거리(미터, 숫자). 표에 "-"면 0.
- "adjacent_m": 인접대지경계선으로부터 거리(미터, 숫자). "-"면 0.
- "when": (선택) 조건 객체. 규모 조건이 있으면 {"min_gross": 500} (연면적 500㎡ 이상), {"max_gross_excl": 1000}(1천㎡ 미만). 특정 용도지역이면 {"zone":"준공업지역"} 또는 {"zone_contains":"상업지역"} 또는 {"not_zone_in":["전용공업지역","일반공업지역"]}. 조건 없으면 when 생략.
- 세부유형이 필요해 수치를 특정 못하면 {"use":..., "needs_subtype": true} 로만.

규칙 순서는 구체적 조건(준공업지역, 규모 큰 것)을 먼저 오도록 배열한다.
JSON 배열만 출력하고 다른 말은 하지 마라.

표 셀:
{cells}
"""



def _kdate(value) -> str:
    """시행일자 YYYYMMDD -> YYYY-MM-DD (app/tools/textfmt.kdate 와 같은 규칙).
    실재하지 않는 날짜나 8자리가 아니면 원문 그대로 둔다."""
    import datetime
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if not m:
        return text
    try:
        datetime.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return text
    return f"{m[1]}-{m[2]}-{m[3]}"

def parse_cells(cells: list[str]) -> list[dict]:
    text = " | ".join(cells)[:8000]
    resp = _client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": PROMPT.replace("{cells}", text)}],
        temperature=0,
        max_tokens=2000,
    )
    out = resp.choices[0].message.content or ""
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return []
    try:
        rules = json.loads(m.group(0))
    except ValueError:
        return []
    # 안전 검증: 거리는 0~10m 범위만 허용(파싱 오류·환각 차단).
    clean = []
    for r in rules:
        if not isinstance(r, dict) or not r.get("use"):
            continue
        if r.get("needs_subtype"):
            clean.append({"use": r["use"], "needs_subtype": True})
            continue
        f = r.get("front_m")
        a = r.get("adjacent_m")
        try:
            f = float(f if f is not None else 0)
            a = float(a if a is not None else 0)
        except (TypeError, ValueError):
            continue
        if not (0 <= f <= 10 and 0 <= a <= 10):
            continue
        obj = {"use": r["use"], "front_m": f, "adjacent_m": a}
        if isinstance(r.get("when"), dict) and r["when"]:
            obj["when"] = r["when"]
        clean.append(obj)
    return clean


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    targets = [
        k for k, v in raw.items()
        if not k.startswith("_") and v.get("status") == "extracted" and v.get("cells")
    ]
    if args.only:
        targets = [k for k in targets if args.only in k]
    if args.limit:
        targets = targets[: args.limit]

    out: dict = {}
    for i, org in enumerate(targets, 1):
        rec = raw[org]
        try:
            rules = parse_cells(rec["cells"])
            out[org] = {
                "source": f"{rec.get('ordinance', org)} 별표(대지 안의 공지) "
                          f"(시행 {_kdate(rec.get('effective_date')) or '?'})",
                "review_status": "needs_review",
                "rules": rules,
            }
            print(f"[{i}/{len(targets)}] {org}: 규칙 {len(rules)}개", flush=True)
        except Exception as e:  # noqa: BLE001
            out[org] = {"error": str(e)}
            print(f"[{i}/{len(targets)}] {org}: 실패 {e}", flush=True)

    payload = {"_meta": {"note": "별표 표 → LLM 파싱 규칙(검수 필요). setback_rules 스키마.",
                         "count": len(out)}, **out}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
