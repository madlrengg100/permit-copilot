#!/usr/bin/env python
"""조례 근거 검색용 TF-IDF 색인 빌더.

입력:
  - backend/scripts/.cache_ordin/*.xml  (collect_ordinances.py 가 받아둔 도시계획조례 원문)
  - backend/app/data/ordinances_auto.json (지자체명·URL 메타 매칭용)
  - backend/app/data/setbacks_raw.json    (있으면 건축조례 대지공지 별표 corpus)
출력:
  - backend/app/data/ordinance_index_chunks.json  (청크 메타+본문)
  - backend/app/data/ordinance_index_vocab.json   (term -> id)
  - backend/app/data/ordinance_index.npz          (idf + L2정규화 tf-idf CSR)

규제 판정에 쓰이는 조문만 색인해 관련성·용량을 높인다(건폐율·용적률·높이·이격·
용도지역·별표·건축선·일조·경관·지구단위 등).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.tools.ordinance_index import tokenize  # noqa: E402

BASE = Path(__file__).resolve().parent
CACHE = BASE / ".cache_ordin"
DATA = BASE.parent / "app" / "data"
AUTO = DATA / "ordinances_auto.json"
SETBACKS = DATA / "setbacks_raw.json"
LEGAL_CORPUS = DATA / "legal_corpus_chunks.json"
DISTRICT_PLANS = DATA / "district_plan_chunks.json"

_KEYWORDS = re.compile(
    r"건폐율|용적률|용도지역|높이|이격|공지|층수|용도|별표|건축선|일조|경관|지구단위|"
    r"녹지|관리지역|주거지역|상업지역|공업지역|취락|개발진흥"
)


def _clean(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _meta(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", xml, re.S)
    return (m.group(1).strip() if m else "")


def build_chunks() -> list[dict]:
    # MST -> (지자체, URL, 시행일) 매핑 (자동본 메타에서)
    mst_meta: dict[str, dict] = {}
    if AUTO.exists():
        auto = json.loads(AUTO.read_text(encoding="utf-8"))
        for org, rec in auto.items():
            if org.startswith("_"):
                continue
            url = (rec.get("_meta") or {}).get("source_url") or ""
            m = re.search(r"MST=(\d+)", url)
            if m:
                mst_meta[m.group(1)] = {
                    "jurisdiction": org,
                    "url": url,
                    "effective_date": (rec.get("_meta") or {}).get("effective_date"),
                    "ordinance": (rec.get("_meta") or {}).get("ordinance_name"),
                }

    chunks: list[dict] = []
    for xml_path in sorted(CACHE.glob("*.xml")):
        xml = xml_path.read_text(encoding="utf-8")
        if "<조내용>" not in xml:
            continue
        mst = xml_path.stem
        meta = mst_meta.get(mst, {})
        org = meta.get("jurisdiction") or _meta(xml, "지자체기관명")
        ordinance = meta.get("ordinance") or _meta(xml, "자치법규명")
        eff = meta.get("effective_date") or _meta(xml, "시행일자")
        url = meta.get("url")
        for title, body in re.findall(r"<조제목>(.*?)</조제목>.*?<조내용>(.*?)</조내용>", xml, re.S):
            t = _clean(title)
            b = _clean(body)
            core = t or b[:40]
            if not _KEYWORDS.search(core + " " + b[:200]):
                continue
            art = re.search(r"제\d+조(?:의\d+)?", b)
            paren = re.search(r"\(([^)]*)\)", core)
            title = paren.group(1) if paren else core
            chunks.append({
                "jurisdiction": org,
                "ordinance": ordinance,
                "article": art.group(0) if art else None,
                "title": title,
                "text": b[:1200],
                "effective_date": eff,
                "url": url,
                "kind": "도시계획조례",
            })
        # 별표내용도 색인
        for bt in re.findall(r"<별표내용>(.*?)</별표내용>", xml, re.S):
            b = _clean(bt)
            if len(b) < 20 or not _KEYWORDS.search(b[:300]):
                continue
            chunks.append({
                "jurisdiction": org, "ordinance": ordinance, "article": "별표",
                "title": "별표", "text": b[:1200], "effective_date": eff,
                "url": url, "kind": "도시계획조례-별표",
            })

    # 건축조례 대지공지 corpus(있으면)
    if SETBACKS.exists():
        sb = json.loads(SETBACKS.read_text(encoding="utf-8"))
        for org, rec in sb.items():
            if org.startswith("_"):
                continue
            for item in rec.get("passages", []):
                chunks.append({
                    "jurisdiction": org,
                    "ordinance": rec.get("_meta", {}).get("ordinance_name"),
                    "article": item.get("article", "별표"),
                    "title": item.get("title", "대지 안의 공지"),
                    "text": item.get("text", "")[:1200],
                    "effective_date": rec.get("_meta", {}).get("effective_date"),
                    "url": rec.get("_meta", {}).get("source_url"),
                    "kind": "건축조례-이격",
                })
    # 토지·개발·건축 관련 국가 법령 조문·별표 corpus. 정형 판정 수치를
    # 만들지 않고, permit_rules.json이 선택한 절차의 원문 근거 검색에만 쓴다.
    if LEGAL_CORPUS.exists():
        corpus = json.loads(LEGAL_CORPUS.read_text(encoding="utf-8"))
        for item in corpus.get("chunks", []):
            text = item.get("text", "")
            if not text:
                continue
            chunks.append({
                "chunk_id": item.get("chunk_id"),
                "jurisdiction": "전국",
                "ordinance": item.get("law"),
                "law": item.get("law"),
                "article": item.get("article"),
                "title": item.get("title"),
                "text": text,
                "effective_date": item.get("effective_date"),
                "url": item.get("url"),
                "kind": item.get("kind", "법령-조문"),
            })

    # 지구단위계획 청크(있으면). 조례·법령과 같은 색인에 넣어 한 번의 검색으로
    # 함께 회수한다. 획지·PNU 매핑 전이라 근거 검색 전용이며 수치를 만들지 않는다.
    if DISTRICT_PLANS.exists():
        plans = json.loads(DISTRICT_PLANS.read_text(encoding="utf-8"))
        for item in plans.get("chunks", []):
            text = item.get("text", "")
            if not text:
                continue
            chunks.append({
                "chunk_id": item.get("chunk_id"),
                "jurisdiction": item.get("jurisdiction"),
                "ordinance": item.get("ordinance"),
                "plan_name": item.get("plan_name"),
                "article": item.get("article"),
                "title": item.get("title"),
                "text": text,
                "effective_date": item.get("effective_date"),
                "url": item.get("url"),
                "kind": item.get("kind", "지구단위계획"),
            })
    return chunks


def build_index(chunks: list[dict]):
    # 어휘·문서빈도
    df: dict[str, int] = {}
    tfs: list[dict[str, int]] = []
    for ch in chunks:
        tf: dict[str, int] = {}
        for tok in tokenize(ch["text"]):
            tf[tok] = tf.get(tok, 0) + 1
        tfs.append(tf)
        for term in tf:
            df[term] = df.get(term, 0) + 1
    N = len(chunks)
    # 너무 드물거나(1회) 너무 흔한(>60%) 항은 제외해 잡음·용량 감소
    vocab: dict[str, int] = {}
    for term, d in df.items():
        if d < 2 or d > 0.6 * N:
            continue
        vocab[term] = len(vocab)
    idf = np.zeros(len(vocab), dtype=np.float32)
    for term, tid in vocab.items():
        idf[tid] = math.log((1 + N) / (1 + df[term])) + 1.0

    indptr = [0]
    indices: list[int] = []
    data: list[float] = []
    for tf in tfs:
        row_i, row_v = [], []
        for term, f in tf.items():
            tid = vocab.get(term)
            if tid is None:
                continue
            row_i.append(tid)
            row_v.append((1.0 + math.log(f)) * idf[tid])
        v = np.array(row_v, dtype=np.float32)
        norm = np.linalg.norm(v)
        if norm > 0:
            v = v / norm
        indices.extend(row_i)
        data.extend(v.tolist())
        indptr.append(len(indices))
    return vocab, idf, np.array(indptr), np.array(indices), np.array(data, dtype=np.float32)


def main() -> None:
    print("청크 생성 중...", flush=True)
    chunks = build_chunks()
    print(f"청크 {len(chunks)}개 ({len({c['jurisdiction'] for c in chunks})}개 지자체)")
    vocab, idf, indptr, indices, data = build_index(chunks)
    print(f"어휘 {len(vocab)}개, 비영요소 {len(data)}개")
    (DATA / "ordinance_index_chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    (DATA / "ordinance_index_vocab.json").write_text(
        json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
    np.savez_compressed(DATA / "ordinance_index.npz",
                        idf=idf, indptr=indptr, indices=indices, data=data)
    print("색인 저장 완료:", DATA / "ordinance_index.npz")


if __name__ == "__main__":
    main()
