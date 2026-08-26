#!/usr/bin/env python
"""지구단위계획 공식 원문(고시문·결정조서·시행지침)을 수집한다.

`app/data/district_plan_sources.json` 의 `source_page` 를 열어 첨부파일을 받아
`backend/data/source/district_plans/<시군구>/<계획명>/` 에 저장한다. 이후
`parse_district_plan_documents.py` 가 페이지 단위로 정제하고
`build_district_plan_chunks.py` 가 청킹한다.

수집한 원문은 원시 증거다. 획지·PNU 검증 전에는 수치 판정에 쓰지 않는다.

지원 원본:
  - 토지이음(eum.go.kr) 고시 상세 — 폼 POST 다운로드. **euc-kr 로 인코딩해야
    한다.** UTF-8 로 보내면 서버가 "첨부파일이 없습니다" 를 돌려준다.
  - 직접 파일 URL(.pdf/.hwp/.hwpx)
  - 그 밖의 지자체 CMS — 페이지에서 첨부 링크를 찾아 받는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

import time

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
SOURCES = ROOT / "app" / "data" / "district_plan_sources.json"
OUT_ROOT = ROOT / "data" / "source" / "district_plans"
# 일부 지자체 CMS 는 비브라우저 UA 에 응답 없이 연결을 끊는다.
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
DOC_SUFFIX = (".pdf", ".hwp", ".hwpx", ".zip")
_UNSAFE = re.compile(r'[\\/:*?"<>|]+')


def _get(client: httpx.Client, url: str, *, referer: str = "", tries: int = 3) -> httpx.Response:
    """끊긴 연결을 재시도한다. 지자체 CMS 는 첫 요청을 자주 흘린다."""
    headers = {**UA, **({"Referer": referer} if referer else {})}
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError(url)


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", name).strip().strip(".") or "unnamed"


def _save(target: Path, content: bytes) -> bool:
    """받은 바이트가 실제 문서면 저장한다.

    실패해도 HTTP 200 에 HTML 경고문을 주는 사이트가 있어 매직 넘버로 거른다.
    """
    if len(content) < 1024 or content[:5] in (b"<scri", b"<!DOC", b"<html"):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return True


def _form_fields(html: str, name: str) -> dict[str, str]:
    form = re.search(rf'<form[^>]+name="{name}".*?</form>', html, re.S)
    fields: dict[str, str] = {}
    if not form:
        return fields
    for tag in re.findall(r"<input[^>]+>", form.group(0)):
        key = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if key:
            fields[key.group(1)] = value.group(1) if value else ""
    return fields


def _eum_attachments(client: httpx.Client, page: str) -> list[tuple[str, bytes]]:
    """토지이음 고시 상세에서 첨부파일을 받는다.

    두 가지를 맞춰야 파일이 온다. 어느 하나라도 틀리면 서버는 HTTP 200 에
    "첨부파일이 없습니다" 경고 스크립트를 돌려주므로 빈 고시와 구분되지 않는다.
      - 폼의 hidden 필드를 그대로 보낸다. 특히 `gosi=Y` 가 없으면 실패한다.
      - 본문을 euc-kr 로 인코딩한다. UTF-8 은 파일명을 못 찾는다.
    """
    html = _get(client, page).text
    calls = re.findall(r"download\('([^']+)',\s*'([^']+)'\)", html)
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(page))
    fields = _form_fields(html, "frm")
    result: list[tuple[str, bytes]] = []
    for path, remote in calls:
        body = urllib.parse.urlencode(
            {**fields, "file": remote},
            encoding="euc-kr",
            errors="replace",
        )
        response = client.post(
            origin + path,
            content=body,
            headers={
                **UA,
                "Referer": page,
                "Content-Type": "application/x-www-form-urlencoded; charset=euc-kr",
            },
        )
        result.append((Path(remote).name, response.content))
    return result


def _asan_attachments(client: httpx.Client, page: str) -> list[tuple[str, bytes]]:
    """아산시 CMS 게시글의 첨부파일을 받는다.

    첨부 링크는 확장자가 없는 `download.php?...&uid=` 이고, 실제 파일명은 바로
    앞의 `preview.php?file_nm=` 에 들어 있다. 확장자만 보고 긁으면 본문과 무관한
    사이드바 문서를 가져오게 된다.
    """
    response = _get(client, page)
    html = response.text
    # preview(파일명) 와 download(실제 링크) 가 쌍으로 붙어 있다.
    pairs = re.findall(
        r'preview\.php\?file_nm=([^"&]+)[^"]*"[^>]*>.*?'
        r'href\s*=\s*"(download\.php\?[^"]+)"',
        html,
        re.S,
    )
    result: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for stored, link in pairs:
        url = urllib.parse.urljoin(str(response.url), link)
        if url in seen:
            continue
        seen.add(url)
        item = _get(client, url, referer=page)
        name = _filename_from(item) or stored
        result.append((name, item.content))
    return result


def _filename_from(response: httpx.Response) -> str:
    """Content-Disposition 의 원래 파일명. 없으면 빈 문자열."""
    raw = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", raw)
    if not match:
        return ""
    name = urllib.parse.unquote(match.group(1))
    try:
        # euc-kr 헤더를 latin-1 로 잘못 디코딩해 오는 경우를 되돌린다.
        name = name.encode("latin-1").decode("euc-kr")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return _safe(name)


def _linked_attachments(client: httpx.Client, page: str) -> list[tuple[str, bytes]]:
    """일반 CMS 페이지에서 문서 확장자 링크를 찾아 받는다."""
    response = _get(client, page)
    html = response.text
    found: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for href in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html):
        if not href.lower().split("?")[0].endswith(DOC_SUFFIX):
            continue
        url = urllib.parse.urljoin(str(response.url), href)
        if url in seen:
            continue
        seen.add(url)
        try:
            item = _get(client, url, referer=page)
        except httpx.HTTPError:
            continue
        found.append((_safe(Path(urllib.parse.urlsplit(url).path).name), item.content))
    return found


def collect_source(client: httpx.Client, sigungu: str, source: dict) -> list[Path]:
    page = source.get("source_page") or ""
    plan = source.get("plan_name") or "unnamed"
    if not page:
        return []
    target_dir = OUT_ROOT / _safe(sigungu) / _safe(plan)

    if page.lower().split("?")[0].endswith(DOC_SUFFIX):
        name = _safe(Path(urllib.parse.urlsplit(page).path).name)
        items = [(name, _get(client, page).content)]
    elif "eum.go.kr" in page:
        items = _eum_attachments(client, page)
    elif "asan.go.kr" in page:
        items = _asan_attachments(client, page)
    else:
        items = _linked_attachments(client, page)

    saved: list[Path] = []
    for name, content in items:
        path = target_dir / _safe(name)
        if _save(path, content):
            saved.append(path)
    return saved


EUM_LIST = "https://www.eum.go.kr/web/gs/gv/gvGosiList.jsp"
_PLAN_TITLE = re.compile(r"지구단위계획")
# 계획 자체가 아닌 고시는 원문에 지구단위계획 내용이 없다.
_SKIP_TITLE = re.compile(r"열람|공고\s*$|폐지|실효|취소|재열람")
_PLAN_NAME = re.compile(r"[(（]\s*([^)）]*지구단위계획[^)）]*)\s*[)）]")


def discover_eum(client: httpx.Client, org: str, pages: int, limit: int) -> list[dict]:
    """토지이음 고시 목록에서 그 지자체의 지구단위계획 결정고시를 찾는다.

    source_page 가 등록돼 있지 않은 지자체를 위한 자동 탐색이다. 결과는
    district_plan_sources.json 과 같은 모양으로 돌려준다.

    질의 문자열은 euc-kr 로 인코딩해야 한다(사이트 인코딩).
    """
    found: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        query = urllib.parse.urlencode(
            {
                "listSize": "50", "pageNo": str(page), "zonenm": "",
                "startdt": "", "enddt": "", "chrgorg": org, "selSggCd": "",
                "select2": "", "select_3": "", "gosino": "", "gosichrg": "",
                "prj_nm": "", "prj_cat_cd": "", "geul_yn": "",
                "gihyung_yn": "", "silsi_yn": "", "mobile_yn": "",
            },
            encoding="euc-kr",
        )
        html = _get(client, f"{EUM_LIST}?{query}").text
        rows = re.findall(
            r"gvGosiDet\.jsp\?seq=(\d+)[^>]*>(.*?)</a>", html, re.S
        )
        if not rows:
            break
        for seq, raw in rows:
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip()
            if seq in seen or not _PLAN_TITLE.search(title):
                continue
            if _SKIP_TITLE.search(title):
                continue
            seen.add(seq)
            inner = _PLAN_NAME.search(title)
            name = inner.group(1).strip() if inner else title
            found.append({
                "plan_name": _safe(name)[:60],
                "source_page": f"https://www.eum.go.kr/web/gs/gv/gvGosiDet.jsp?seq={seq}",
                "publisher": org,
                "notice_title": title,
                "discovered": True,
            })
            if len(found) >= limit:
                return found
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="시군구명 부분일치만")
    parser.add_argument(
        "--discover",
        nargs="*",
        default=None,
        metavar="시군구",
        help="source_page 가 없는 지자체를 토지이음 고시 목록에서 찾아 수집한다. "
             "이름을 주지 않으면 sources 가 비어 있는 지자체 전부.",
    )
    parser.add_argument("--discover-pages", type=int, default=8)
    parser.add_argument("--discover-limit", type=int, default=12)
    args = parser.parse_args()

    catalog = json.loads(SOURCES.read_text(encoding="utf-8"))
    jurisdictions = catalog.setdefault("jurisdictions", {})
    total = 0
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        if args.discover is not None:
            targets = args.discover or [
                name for name, entry in jurisdictions.items()
                if not (entry.get("sources") or [])
            ]
            for org in targets:
                entry = jurisdictions.setdefault(org, {"status": "collecting"})
                existing = {
                    item.get("source_page") for item in entry.get("sources") or []
                }
                discovered = discover_eum(
                    client, org, args.discover_pages, args.discover_limit
                )
                fresh = [
                    item for item in discovered
                    if item["source_page"] not in existing
                ]
                entry.setdefault("sources", []).extend(fresh)
                print(f"[{org}] 고시 탐색: {len(fresh)}건 추가", flush=True)
            SOURCES.write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        for sigungu, entry in jurisdictions.items():
            if args.only and args.only not in sigungu:
                continue
            for source in entry.get("sources") or []:
                plan = source.get("plan_name")
                try:
                    saved = collect_source(client, sigungu, source)
                except Exception as exc:  # noqa: BLE001
                    print(f"[{sigungu}] {plan}: 실패 {type(exc).__name__} {exc}", flush=True)
                    continue
                total += len(saved)
                if saved:
                    for path in saved:
                        print(f"[{sigungu}] {plan}: {path.name} "
                              f"({path.stat().st_size:,}B)", flush=True)
                else:
                    print(f"[{sigungu}] {plan}: 첨부 없음", flush=True)
    print(f"\n저장 {total}건 -> {OUT_ROOT}")
    if not total:
        sys.exit(1)


if __name__ == "__main__":
    main()
