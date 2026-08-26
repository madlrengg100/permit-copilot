#!/usr/bin/env python
"""정제한 지구단위계획 원문을 근거 검색용 청크로 만든다.

입력: backend/data/processed/district_plans/<시군구>/<계획명>/*.json
      (parse_district_plan_documents.py 산출)
출력: backend/app/data/district_plan_chunks.json

청크 스키마는 조례 청크와 같다. `build_ordinance_index.py` 가 같은 TF-IDF 색인에
넣어 한 번의 검색으로 조례·법령·지구단위계획을 함께 회수한다.

**이 청크는 근거 검색용이다.** 건폐율·용적률·이격 수치를 만들지 않는다. 획지별
수치는 획지·PNU 매핑이 끝난 뒤에야 판정에 쓸 수 있고, 그 전까지 원문 확인 링크와
검색 근거로만 쓴다(district_plan_sources.json 의 needs_latest_notice_check).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
PROCESSED = ROOT / "data" / "processed" / "district_plans"
SOURCES = ROOT / "app" / "data" / "district_plan_sources.json"
OUTPUT = ROOT / "app" / "data" / "district_plan_chunks.json"

# 시행지침은 '제1장/제3조/1./가.' 체계를 쓴다. 조 단위를 기본 청크로 삼는다.
_ARTICLE = re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?)\s*[（(]?\s*([^)）\n]{0,40})?")
_HEADING = re.compile(r"^\s*(제\s*[ⅠⅡⅢⅣⅤ\d]+\s*[편장절]|제\s*\d+\s*조(?:의\s*\d+)?)", re.M)
# 판정 근거가 될 만한 내용이 있는 청크만 남긴다.
_KEYWORDS = re.compile(
    r"건폐율|용적률|높이|층수|용도|획지|가구|건축한계선|건축지정선|벽면|이격|"
    r"대지|주차|경관|색채|담장|조경|허용|불허|권장|기반시설|도로|공동개발|"
    r"결정조서|지구단위계획"
)
_MIN_TEXT = 40
_MAX_TEXT = 1600


def _clean(value: str) -> str:
    value = value.replace("\x00", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _source_meta() -> dict[tuple[str, str], dict]:
    """(시군구, 계획명) -> 고시 메타. 파일 경로로 되찾는다."""
    catalog = json.loads(SOURCES.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict] = {}
    for sigungu, entry in catalog.get("jurisdictions", {}).items():
        for source in entry.get("sources") or []:
            out[(sigungu, str(source.get("plan_name")))] = source
    return out


def _split(text: str) -> list[tuple[str, str]]:
    """조 단위로 나눈다. 조 체계가 없으면 통째로 하나.

    반환: [(조문 라벨, 본문)]
    """
    positions = [m.start() for m in _HEADING.finditer(text)]
    if len(positions) < 2:
        return [("", text)]
    bounds = positions + [len(text)]
    pieces: list[tuple[str, str]] = []
    for index in range(len(positions)):
        body = text[bounds[index]:bounds[index + 1]].strip()
        if not body:
            continue
        match = _ARTICLE.match(body)
        label = re.sub(r"\s+", "", match.group(1)) if match else ""
        pieces.append((label, body))
    return pieces


def _windows(body: str) -> list[str]:
    """긴 조문을 문단 경계로 잘라 색인 단위를 고르게 한다."""
    if len(body) <= _MAX_TEXT:
        return [body]
    out: list[str] = []
    current = ""
    for paragraph in body.split("\n"):
        if len(current) + len(paragraph) + 1 > _MAX_TEXT and current:
            out.append(current.strip())
            current = ""
        current += paragraph + "\n"
    if current.strip():
        out.append(current.strip())
    return out


def _table_text(table: list[list[str]]) -> str:
    """표를 행 단위 텍스트로 편다.

    결정조서·시행지침의 획지별 건폐율·용적률·높이는 대부분 표에만 있다.
    hwp5txt 본문은 그 자리에 `<표>` 표시만 남기므로 표를 따로 청킹하지 않으면
    핵심 수치가 통째로 색인에서 빠진다.
    """
    lines = []
    for row in table:
        cells = [re.sub(r"\s+", " ", str(cell)).strip() for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def build() -> list[dict]:
    meta = _source_meta()
    chunks: list[dict] = []
    for path in sorted(PROCESSED.rglob("*.json")):
        relative = path.relative_to(PROCESSED)
        if len(relative.parts) < 3:
            continue
        sigungu, plan = relative.parts[0], relative.parts[1]
        source = meta.get((sigungu, plan), {})
        document = json.loads(path.read_text(encoding="utf-8"))
        for page in document.get("pages") or []:
            text = _clean(
                (page.get("embedded_text") or "")
                + "\n"
                + (page.get("ocr_text") or "")
            )
            if len(re.sub(r"\s", "", text)) < _MIN_TEXT:
                continue
            for label, body in _split(text):
                for piece in _windows(body):
                    compact = re.sub(r"\s", "", piece)
                    if len(compact) < _MIN_TEXT or not _KEYWORDS.search(piece):
                        continue
                    identifier = hashlib.sha256(
                        f"{sigungu}|{plan}|{path.name}|{piece}".encode()
                    ).hexdigest()[:20]
                    chunks.append({
                        "chunk_id": f"dp-{identifier}",
                        "jurisdiction": sigungu,
                        "content": "text",
                        # 조례 청크와 같은 키를 쓴다. 검색 결과 표시가 갈리지 않는다.
                        "ordinance": plan,
                        "plan_name": plan,
                        "article": label or f"{path.stem} p.{page.get('page')}",
                        "title": source.get("plan_name") or plan,
                        "text": piece[:_MAX_TEXT],
                        "effective_date": source.get("notice_date"),
                        "notice_no": source.get("notice_no"),
                        "url": source.get("source_page"),
                        "document": path.stem,
                        "page": page.get("page"),
                        "kind": "지구단위계획",
                        # 획지·PNU 매핑 전이라 수치 판정 근거가 아니다.
                        "status": "EVIDENCE_ONLY",
                    })

        for index, table in enumerate(document.get("tables") or [], start=1):
            body = _table_text(table)
            if len(re.sub(r"\s", "", body)) < _MIN_TEXT or not _KEYWORDS.search(body):
                continue
            for piece in _windows(body):
                identifier = hashlib.sha256(
                    f"{sigungu}|{plan}|{path.name}|표{index}|{piece}".encode()
                ).hexdigest()[:20]
                chunks.append({
                    "chunk_id": f"dp-{identifier}",
                    "jurisdiction": sigungu,
                    "content": "table",
                    "ordinance": plan,
                    "plan_name": plan,
                    "article": f"{path.stem} 표{index}",
                    "title": source.get("plan_name") or plan,
                    "text": piece[:_MAX_TEXT],
                    "effective_date": source.get("notice_date"),
                    "notice_no": source.get("notice_no"),
                    "url": source.get("source_page"),
                    "document": path.stem,
                    "page": None,
                    "kind": "지구단위계획",
                    "status": "EVIDENCE_ONLY",
                })
    return chunks


def main() -> None:
    chunks = build()
    payload = {
        "_meta": {
            "schema_version": 1,
            "name": "지구단위계획 근거 검색용 청크",
            "status": "evidence_only",
            "notice": (
                "획지·PNU 매핑 전이므로 건폐율·용적률·이격 수치 판정에 쓰지 않는다. "
                "수치는 ordinances*.json·setbacks.json 과 결정적 조건식에서만 온다."
            ),
            "chunk_count": len(chunks),
            "plan_count": len({(c["jurisdiction"], c["plan_name"]) for c in chunks}),
        },
        "chunks": chunks,
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"청크 {len(chunks)}개 / 지구 {payload['_meta']['plan_count']}곳 -> {OUTPUT}")


if __name__ == "__main__":
    main()
