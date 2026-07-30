#!/usr/bin/env python3
"""전국 1:5,000 임상도 ZIP을 SQLite RTree 공간 DB로 변환한다."""

from __future__ import annotations

import argparse
import json
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


def source_crs(shp_path: Path) -> CRS:
    prj = shp_path.with_suffix(".prj")
    if not prj.exists():
        raise RuntimeError(f"좌표계 파일이 없습니다: {shp_path}")
    return CRS.from_wkt(prj.read_text(encoding="utf-8", errors="ignore"))


def open_reader(shp_path: Path) -> shapefile.Reader:
    last_error: Exception | None = None
    for encoding in ("cp949", "euc-kr", "utf-8"):
        try:
            reader = shapefile.Reader(str(shp_path), encoding=encoding)
            _ = reader.fields
            return reader
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"DBF를 읽을 수 없습니다: {shp_path}") from last_error


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        CREATE TABLE features (
            id INTEGER PRIMARY KEY,
            region_code TEXT NOT NULL,
            forest_type_code TEXT,
            forest_type_name TEXT,
            species_code TEXT,
            species_name TEXT,
            diameter_class_code TEXT,
            diameter_class_name TEXT,
            age_class_code TEXT,
            age_class_name TEXT,
            density_code TEXT,
            density_name TEXT,
            stand_height TEXT,
            stand_height_name TEXT,
            updated_year TEXT,
            properties TEXT NOT NULL,
            geom_wkb BLOB NOT NULL
        );
        CREATE VIRTUAL TABLE feature_bounds USING rtree(
            id, minx, maxx, miny, maxy
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX idx_inventory_region ON features(region_code);
        CREATE INDEX idx_inventory_species ON features(species_code);
        CREATE INDEX idx_inventory_age ON features(age_class_code);
        """
    )


def prop(props: dict, name: str) -> str:
    value = props.get(name)
    return "" if value is None else str(value).strip()


def import_zip(db: sqlite3.Connection, zip_path: Path, next_id: int) -> int:
    region_code = zip_path.stem
    with tempfile.TemporaryDirectory(prefix=f"forest-inventory-{region_code}-") as tmp:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(tmp)
        shp_files = sorted(Path(tmp).rglob("*.shp"))
        if not shp_files:
            raise RuntimeError(f"SHP가 없습니다: {zip_path.name}")

        for shp_path in shp_files:
            transformer = Transformer.from_crs(
                source_crs(shp_path), "EPSG:4326", always_xy=True
            )
            reader = open_reader(shp_path)
            fields = [item[0] for item in reader.fields[1:]]
            rows: list[tuple] = []
            bounds: list[tuple] = []
            for item in reader.iterShapeRecords():
                geom = shape(item.shape.__geo_interface__)
                if geom.is_empty:
                    continue
                geom = transform(transformer.transform, geom)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty:
                    continue
                props = dict(zip(fields, item.record))
                minx, miny, maxx, maxy = geom.bounds
                next_id += 1
                rows.append(
                    (
                        next_id,
                        region_code,
                        prop(props, "FRTP_CD"),
                        prop(props, "FRTP_NM"),
                        prop(props, "KOFTR_GROU"),
                        prop(props, "KOFTR_NM"),
                        prop(props, "DMCLS_CD"),
                        prop(props, "DMCLS_NM"),
                        prop(props, "AGCLS_CD"),
                        prop(props, "AGCLS_NM"),
                        prop(props, "DNST_CD"),
                        prop(props, "DNST_NM"),
                        prop(props, "HEIGHT"),
                        prop(props, "HEIGHT_NM"),
                        prop(props, "갱신년도"),
                        json.dumps(props, ensure_ascii=False, default=str),
                        sqlite3.Binary(wkb.dumps(geom)),
                    )
                )
                bounds.append((next_id, minx, maxx, miny, maxy))
                if len(rows) >= 1000:
                    db.executemany(
                        "INSERT INTO features VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        rows,
                    )
                    db.executemany(
                        "INSERT INTO feature_bounds VALUES (?,?,?,?,?)", bounds
                    )
                    rows.clear()
                    bounds.clear()
            if rows:
                db.executemany(
                    "INSERT INTO features VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                db.executemany(
                    "INSERT INTO feature_bounds VALUES (?,?,?,?,?)", bounds
                )
            db.commit()
    return next_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="data/source/forest_inventory",
        help="시도별 임상도 ZIP 폴더",
    )
    parser.add_argument(
        "--output",
        default="data/processed/forest_inventory/forest_inventory.sqlite",
    )
    parser.add_argument("--source-date", required=True)
    args = parser.parse_args()

    source = Path(args.source)
    files = sorted(source.glob("*.zip"))
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
            print(
                f"[{index}/{len(files)}] {zip_path.name} "
                f"({zip_path.stat().st_size / 1024 / 1024:,.1f}MB)",
                flush=True,
            )
            next_id = import_zip(db, zip_path, next_id)
            print(f"  누적 {next_id:,}건", flush=True)
        metadata = {
            "source": "산림청 1:5,000 임상도",
            "source_date": args.source_date,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "feature_count": str(next_id),
            "file_count": str(len(files)),
            "crs": "EPSG:4326",
            "usage": "사전 참고용; 인허가용 입목축적 현장조사를 대체하지 않음",
        }
        db.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
        db.commit()
        db.execute("PRAGMA optimize")
    temporary.replace(output)
    print(f"완료: {output} ({next_id:,}건)", flush=True)


if __name__ == "__main__":
    main()
