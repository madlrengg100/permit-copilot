"""조례 근거 검색(벡터 유사) 레이어.

외부 벡터DB·임베딩 라이브러리 없이 동작하도록 numpy TF-IDF 코사인 유사도로
구현한다. 조례 조문·별표를 청크로 나눠 색인해 두고, 질의(용도+지역+지자체)에
가장 관련 있는 조문을 근거로 돌려준다.

설계 원칙(사용자 요구 반영):
  - 벡터 검색 결과로 '숫자를 계산'하지 않는다. 근거 조문을 '찾아 보여줄' 뿐이다.
  - 각 청크에 지자체·조문·시행일·원문 URL·해시 메타를 붙여 판정 근거를 추적한다.
  - 지자체·시행일로 필터해 관할·시점이 다른 조례를 섞지 않는다.

임베딩으로 교체하려면 _vectorize()/search()의 벡터화만 바꾸면 된다.
색인 파일은 build_ordinance_index.py 가 생성한다.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

_DATA = Path(__file__).resolve().parent.parent / "data"
_CHUNKS_PATH = _DATA / "ordinance_index_chunks.json"
_INDEX_PATH = _DATA / "ordinance_index.npz"
_VOCAB_PATH = _DATA / "ordinance_index_vocab.json"


# ------------------------------------------------------------- 토큰화(색인·질의 공통)
_HANGUL_RUN = re.compile(r"[가-힣]{2,}")
_WORD = re.compile(r"[가-힣A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """한국어 조례 텍스트용 토큰: 단어 + 한글 2·3-그램(형태소기 없이도 매칭되게)."""
    text = text.replace("퍼센트", "%")
    toks: list[str] = _WORD.findall(text)
    for run in _HANGUL_RUN.findall(text):
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                toks.append(run[i : i + n])
    return toks


def _tf(tokens: list[str]) -> dict[str, int]:
    d: dict[str, int] = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


# ------------------------------------------------------------------ 색인 로드
@lru_cache(maxsize=1)
def _load_index():
    if not (_CHUNKS_PATH.exists() and _INDEX_PATH.exists() and _VOCAB_PATH.exists()):
        return None
    chunks = json.loads(_CHUNKS_PATH.read_text(encoding="utf-8"))
    vocab = json.loads(_VOCAB_PATH.read_text(encoding="utf-8"))  # term -> id
    npz = np.load(_INDEX_PATH)
    return {
        "chunks": chunks,
        "vocab": vocab,
        "idf": npz["idf"].astype(np.float32),
        "indptr": npz["indptr"].astype(np.int64),
        "indices": npz["indices"].astype(np.int64),
        "data": npz["data"].astype(np.float32),  # 정규화된 tf-idf (L2)
    }


def available() -> bool:
    return _load_index() is not None


def _same_jurisdiction(requested: str, indexed: str | None) -> bool:
    """`아산시`와 수집 원문 메타의 `충청남도 아산시`를 같은 관할로 본다."""
    indexed = " ".join(str(indexed or "").split())
    requested = " ".join(str(requested or "").split())
    return bool(
        indexed
        and requested
        and (
            indexed == requested
            or indexed.endswith(f" {requested}")
            or requested.endswith(f" {indexed}")
        )
    )


def _query_vector(query: str, vocab: dict, idf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tf = _tf(tokenize(query))
    idx, val = [], []
    for term, f in tf.items():
        tid = vocab.get(term)
        if tid is None:
            continue
        idx.append(tid)
        val.append((1.0 + math.log(f)) * idf[tid])
    if not idx:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
    v = np.array(val, dtype=np.float32)
    n = np.linalg.norm(v)
    if n > 0:
        v = v / n
    return np.array(idx, dtype=np.int64), v


def search(
    query: str,
    jurisdiction: str | None = None,
    effective_on: str | None = None,
    top_k: int = 5,
    scope: str = "all",
) -> list[dict]:
    """질의와 가장 관련 있는 조례 조문/별표 청크를 근거로 돌려준다.

    jurisdiction  지정 시 그 관할 조례만(관할 혼동 방지).
    effective_on  'YYYYMMDD' 지정 시 그 시점에 시행 중이던 조례만(시점 혼동 방지).
    반환: [{score, jurisdiction, ordinance, article, title, snippet, effective_date, url}]
    """
    ix = _load_index()
    if ix is None:
        return []
    q_idx, q_val = _query_vector(query, ix["vocab"], ix["idf"])
    if q_idx.size == 0:
        return []
    q_map = dict(zip(q_idx.tolist(), q_val.tolist()))

    chunks = ix["chunks"]
    indptr, indices, data = ix["indptr"], ix["indices"], ix["data"]
    scored: list[tuple[float, int]] = []
    for i, ch in enumerate(chunks):
        is_law = str(ch.get("kind") or "").startswith("법령-")
        if scope == "law" and not is_law:
            continue
        if scope == "ordinance" and is_law:
            continue
        # 국가 법령은 모든 관할에 공통 적용한다. 관할 조례만 선택 지자체로 격리한다.
        if (
            jurisdiction
            and ch.get("jurisdiction") != "전국"
            and not _same_jurisdiction(jurisdiction, ch.get("jurisdiction"))
        ):
            continue
        if effective_on and ch.get("effective_date") and ch["effective_date"] > effective_on:
            continue
        lo, hi = indptr[i], indptr[i + 1]
        s = 0.0
        for k in range(lo, hi):
            qv = q_map.get(int(indices[k]))
            if qv is not None:
                s += qv * float(data[k])
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    out = []
    for s, i in scored[:top_k]:
        ch = chunks[i]
        text = ch.get("text", "")
        out.append({
            "score": round(float(s), 4),
            "jurisdiction": ch.get("jurisdiction"),
            "ordinance": ch.get("ordinance"),
            "law": ch.get("law"),
            "chunk_id": ch.get("chunk_id"),
            "article": ch.get("article"),
            "title": ch.get("title"),
            "kind": ch.get("kind"),
            "snippet": text[:280],
            "effective_date": ch.get("effective_date"),
            "url": (ch.get("url") or "").replace("&amp;", "&") or None,
        })
    return out
