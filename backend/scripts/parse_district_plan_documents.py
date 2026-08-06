#!/usr/bin/env python3
"""지구단위계획 PDF/HWP/HWPX를 페이지 단위로 추출하고 스캔 페이지만 OCR한다.

원문은 backend/data/source/district_plans/<시군구>/<계획명>/ 아래에 둔다.
결과는 backend/data/processed/district_plans/<시군구>/<계획명>/document.json 이다.
OCR은 기존 OpenAI 호환 비전 모델(Gemini 포함)을 사용하며, 본문 텍스트가 충분한
페이지는 외부 전송하지 않는다. 이 단계 결과는 원시 증거이며 검증 전 판정에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import fitz
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import LLM_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL  # noqa: E402

SOURCE_ROOT = ROOT / "data/source/district_plans"
OUTPUT_ROOT = ROOT / "data/processed/district_plans"
LOT_RE = re.compile(r"(?:(?P<dong>[가-힣]{1,12}(?:동|리|가))\s*)?(?P<san>산\s*)?(?P<main>\d{1,4})(?:\s*[-－]\s*(?P<sub>\d{1,4}))?")


def _hwpx_text(path: Path) -> str:
    chunks = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith("Contents/section") or not name.endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(name))
            chunks.extend(node.text for node in root.iter() if node.text)
    return "\n".join(chunks)


def _hwp_text(path: Path) -> str:
    proc = subprocess.run([str(ROOT / ".venv/bin/hwp5txt"), str(path)], capture_output=True, text=True, check=True)
    return proc.stdout


class _TableReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table" and self.table is None:
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if any(self.row):
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.table:
                self.tables.append(self.table)
            self.table = None


def _hwp_tables(path: Path) -> list[list[list[str]]]:
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "document.html"
        subprocess.run(
            [str(ROOT / ".venv/bin/hwp5html"), "--output", str(html), "--html", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        reader = _TableReader()
        reader.feed(html.read_text(encoding="utf-8", errors="replace"))
        return reader.tables


def _vision_ocr(png: bytes) -> dict:
    if not OPENAI_API_KEY:
        return {"text": "", "status": "OCR_NOT_CONFIGURED"}
    base = (OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "한국 지구단위계획 도면이다. 보이는 모든 한글과 숫자를 위치 관계가 유지되도록 읽어라. 특히 지번, 획지번호, 건폐율, 용적률, 층수, 높이, 건축한계선 거리와 범례를 빠뜨리지 마라. 추측하지 말고 판독 불가는 [불명]으로 표시하라."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        ]}],
        "max_tokens": 5000,
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(base + "/chat/completions", headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, json=payload)
        response.raise_for_status()
        data = response.json()
    return {"text": data["choices"][0]["message"].get("content", ""), "status": "OCR_COMPLETE"}


def _lot_mentions(text: str) -> list[dict]:
    out, seen = [], set()
    for match in LOT_RE.finditer(text):
        # 비율·면적 같은 숫자 오인을 줄이기 위해 동/리/가 또는 산 표기가 있는 것만 자동 주소 후보로 둔다.
        if not match.group("dong") and not match.group("san"):
            continue
        label = "".join(filter(None, [match.group("dong"), " 산 " if match.group("san") else " ", match.group("main"), "-" + match.group("sub") if match.group("sub") else ""])).strip()
        if label in seen:
            continue
        seen.add(label)
        out.append({"label": label, "start": match.start(), "end": match.end(), "pnu": None, "mapping_status": "PENDING"})
    return out


def parse_file(path: Path, use_ocr: bool) -> dict:
    path = path.resolve()
    suffix = path.suffix.lower()
    pages = []
    if suffix == ".pdf":
        doc = fitz.open(path)
        for index, page in enumerate(doc):
            embedded = page.get_text("text").strip()
            record = {"page": index + 1, "embedded_text": embedded, "ocr_text": "", "ocr_status": "NOT_NEEDED"}
            if use_ocr and len(re.sub(r"\s", "", embedded)) < 40:
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                result = _vision_ocr(pix.tobytes("png"))
                record["ocr_text"], record["ocr_status"] = result["text"], result["status"]
            combined = (embedded + "\n" + record["ocr_text"]).strip()
            record["lot_mentions"] = _lot_mentions(combined)
            pages.append(record)
    elif suffix == ".hwp":
        text = _hwp_text(path)
        pages = [{"page": 1, "embedded_text": text, "ocr_text": "", "ocr_status": "NOT_NEEDED", "lot_mentions": _lot_mentions(text)}]
    elif suffix == ".hwpx":
        text = _hwpx_text(path)
        pages = [{"page": 1, "embedded_text": text, "ocr_text": "", "ocr_status": "NOT_NEEDED", "lot_mentions": _lot_mentions(text)}]
    else:
        raise ValueError(f"지원하지 않는 문서 형식: {suffix}")
    return {
        "source_file": str(path),
        "pages": pages,
        "tables": _hwp_tables(path) if suffix == ".hwp" else [],
        "status": "EXTRACTED_UNVERIFIED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    paths = args.paths or [p for p in SOURCE_ROOT.rglob("*") if p.suffix.lower() in {".pdf", ".hwp", ".hwpx"}]
    for path in paths:
        path = path.resolve()
        result = parse_file(path, args.ocr)
        try:
            rel = path.relative_to(SOURCE_ROOT)
        except ValueError:
            rel = Path("manual") / path.name
        target = OUTPUT_ROOT / rel.parent / (path.stem + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
