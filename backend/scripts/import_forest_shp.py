#!/usr/bin/env python3
"""브이월드 전국 산지 SHP ZIP을 SQLite RTree 공간DB로 변환한다."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely import wkb
from shapely.geometry import shape
from shapely.ops import transform


NAME_FIELDS = ("uname", "scls_nm", "zone_name", "alias", "remark")
CODE_FIELDS = ("ucode", "mnum", "scls_cd", "zone_code")
ZONE_NAMES = {
    "UFM000": "보전준보전산지미분류",
    "UFM100": "보전산지",
    "UFM110": "임업용산지",
    "UFM120": "공익용산지",
    "UFM200": "준보전산지",
    "UFM999": "보전준보전산지기타",
}


def _field(props: dict, candidates: tuple[str, ...]) -> str:
    lowered = {str(key).lower(): value for key, value in props.items()}
    for name in candidates:
        value = lowered.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _zone(props: dict) -> tuple[str, str]:
    raw_code = _field(props, CODE_FIELDS)
    match = re.search(r"UFM\d{3}", raw_code.upper())
    code = match.group(0) if match else raw_code
    # 일부 원본 ALIAS가 비어 있거나 깨져 있으므로 공식 UFM 코드명을 우선한다.
    return ZONE_NAMES.get(code, _field(props, NAME_FIELDS)), code


def _source_crs(shp_path: Path) -> CRS:
    prj = shp_path.with_suffix(".prj")
    if prj.exists():
        try:
            return CRS.from_wkt(prj.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    # 파일명 UF801_5174의 공공데이터 기본 좌표계
    return CRS.from_epsg(5174)


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE features (
            id INTEGER PRIMARY KEY,
            region TEXT NOT NULL,
            zone_name TEXT,
            zone_code TEXT,
            properties TEXT NOT NULL,
            geom_wkb BLOB NOT NULL
        );
        CREATE VIRTUAL TABLE feature_bounds USING rtree(
            id, minx, maxx, miny, maxy
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX idx_features_region ON features(region);
        CREATE INDEX idx_features_zone_code ON features(zone_code);
        """
    )


def import_zip(db: sqlite3.Connection, zip_path: Path, next_id: int) -> int:
    region = zip_path.stem.rsplit("_", 1)[-1]
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp)
        shp_files = list(Path(tmp).rglob("*.shp"))
        if not shp_files:
            raise RuntimeError(f"SHP가 없습니다: {zip_path.name}")
        for shp_path in shp_files:
            source = _source_crs(shp_path)
            transformer = Transformer.from_crs(
                source, "EPSG:4326", always_xy=True
            )
            reader = None
            for encoding in ("cp949", "euc-kr", "utf-8"):
                try:
                    reader = shapefile.Reader(str(shp_path), encoding=encoding)
                    _ = reader.fields
                    break
                except Exception:
                    reader = None
            if reader is None:
                raise RuntimeError(f"DBF 인코딩을 읽을 수 없습니다: {shp_path}")
            fields = [item[0] for item in reader.fields[1:]]
            batch_features = []
            batch_bounds = []
            for item in reader.iterShapeRecords():
                geom = shape(item.shape.__geo_interface__)
                if geom.is_empty:
                    continue
                geom = transform(transformer.transform, geom).buffer(0)
                if geom.is_empty:
                    continue
                props = dict(zip(fields, item.record))
                zone_name, zone_code = _zone(props)
                minx, miny, maxx, maxy = geom.bounds
                next_id += 1
                batch_features.append((
                    next_id, region, zone_name, zone_code,
                    json.dumps(props, ensure_ascii=False, default=str),
                    sqlite3.Binary(wkb.dumps(geom)),
                ))
                batch_bounds.append((next_id, minx, maxx, miny, maxy))
                if len(batch_features) >= 1000:
                    db.executemany(
                        "INSERT INTO features VALUES (?,?,?,?,?,?)",
                        batch_features,
                    )
                    db.executemany(
                        "INSERT INTO feature_bounds VALUES (?,?,?,?,?)",
                        batch_bounds,
                    )
                    batch_features.clear()
                    batch_bounds.clear()
            if batch_features:
                db.executemany(
                    "INSERT INTO features VALUES (?,?,?,?,?,?)", batch_features
                )
                db.executemany(
                    "INSERT INTO feature_bounds VALUES (?,?,?,?,?)", batch_bounds
                )
            db.commit()
    return next_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="data/source/forest",
        help="시도별 LSMD_CONT_UF801_5174_*.zip 폴더",
    )
    parser.add_argument(
        "--output",
        default="data/processed/forest/forest_class.sqlite",
    )
    parser.add_argument("--source-date", required=True)
    args = parser.parse_args()

    source = Path(args.source)
    files = sorted(source.glob("LSMD_CONT_UF801_5174_*.zip"))
    # 2026년 행정구역 개편 전후 파일이 같은 배포 목록에 함께 있다. 통합본이
    # 있으면 구형 전남·광주 파일을 제외해 동일 경계의 이중 적재를 막는다.
    integrated = source / "LSMD_CONT_UF801_5174_전남광주통합특별시.zip"
    if integrated in files:
        legacy_names = {
            "LSMD_CONT_UF801_5174_전남.zip",
            "LSMD_CONT_UF801_5174_광주.zip",
        }
        skipped = [path.name for path in files if path.name in legacy_names]
        files = [path for path in files if path.name not in legacy_names]
        if skipped:
            print("통합 행정구역 파일 사용 — 중복 제외: " + ", ".join(skipped))
    if not files:
        raise SystemExit(f"변환할 ZIP이 없습니다: {source}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.sqlite")
    temporary.unlink(missing_ok=True)
    with sqlite3.connect(temporary) as db:
        create_schema(db)
        next_id = 0
        for index, zip_path in enumerate(files, 1):
            print(f"[{index}/{len(files)}] {zip_path.name}", flush=True)
            next_id = import_zip(db, zip_path, next_id)
        metadata = {
            "source": "브이월드 (연속주제)_산지관리/보전준보전산지",
            "source_date": args.source_date,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "feature_count": str(next_id),
            "file_count": str(len(files)),
            "crs": "EPSG:4326",
        }
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)", metadata.items()
        )
        db.commit()
    temporary.replace(output)
    print(f"완료: {output} ({next_id:,}건)")


if __name__ == "__main__":
    main()
