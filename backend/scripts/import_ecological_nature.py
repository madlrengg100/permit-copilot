#!/usr/bin/env python3
"""2026 생태·자연도 FileGDB를 SQLite RTree 공간DB로 변환한다."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyogrio
from pyogrio.raw import read
import shapely


ECO_FIELDS = (
    "생태자연도", "보전등급", "판정기준", "대분류", "식물군락명",
    "Year", "고시번호",
)
SPECIAL_FIELDS = (
    "산림보호", "국립공원", "도립공원", "군립공원", "천연기념물",
    "야생동식물", "수산자원", "습지보호", "백두대간", "생태경관", "비고",
)


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=NORMAL;
        PRAGMA temp_store=MEMORY;
        CREATE TABLE features (
            id INTEGER PRIMARY KEY,
            zone_name TEXT,
            zone_code TEXT,
            properties TEXT NOT NULL,
            geom_wkb BLOB NOT NULL
        );
        CREATE VIRTUAL TABLE feature_bounds USING rtree(
            id, minx, maxx, miny, maxy
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX idx_features_zone_code ON features(zone_code);
        """
    )


def _clean(value):
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    text = str(value).strip()
    return text or None


def _label(layer: str, props: dict) -> tuple[str, str]:
    if layer == "F_생태자연도_A":
        grade = _clean(props.get("생태자연도"))
        return (f"생태·자연도 {grade}등급" if grade else "생태·자연도", grade or "")
    labels = [
        f"{field}: {value}" for field in SPECIAL_FIELDS
        if (value := _clean(props.get(field)))
    ]
    return (" / ".join(labels) or "별도관리지역", "SPECIAL")


def import_layer(
    source_uri: str,
    layer: str,
    output: Path,
    source_date: str,
    batch_size: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.sqlite")
    temporary.unlink(missing_ok=True)
    fields = ECO_FIELDS if layer == "F_생태자연도_A" else SPECIAL_FIELDS
    total = int(pyogrio.read_info(source_uri, layer=layer)["features"])
    with sqlite3.connect(temporary) as db:
        create_schema(db)
        next_id = 0
        for offset in range(0, total, batch_size):
            meta, _fids, geometries, values = read(
                source_uri,
                layer=layer,
                columns=list(fields),
                skip_features=offset,
                max_features=batch_size,
                force_2d=True,
            )
            if geometries is None or not len(geometries):
                continue
            names = list(meta["fields"])
            arrays = dict(zip(names, values))
            shapes = shapely.from_wkb(geometries)
            bounds = shapely.bounds(shapes)
            feature_rows = []
            bound_rows = []
            for index, geom_wkb in enumerate(geometries):
                if geom_wkb is None or np.isnan(bounds[index]).any():
                    continue
                props = {
                    name: cleaned
                    for name in names
                    if (cleaned := _clean(arrays[name][index])) is not None
                }
                zone_name, zone_code = _label(layer, props)
                next_id += 1
                minx, miny, maxx, maxy = map(float, bounds[index])
                feature_rows.append((
                    next_id,
                    zone_name,
                    zone_code,
                    json.dumps(props, ensure_ascii=False),
                    sqlite3.Binary(bytes(geom_wkb)),
                ))
                bound_rows.append((next_id, minx, maxx, miny, maxy))
            db.executemany(
                "INSERT INTO features VALUES (?,?,?,?,?)", feature_rows
            )
            db.executemany(
                "INSERT INTO feature_bounds VALUES (?,?,?,?,?)", bound_rows
            )
            db.commit()
            print(
                f"{layer}: {min(offset + batch_size, total):,}/{total:,}",
                flush=True,
            )
        metadata = {
            "source": "국립생태원 2026년 생태·자연도 정기고시 공간자료",
            "source_date": source_date,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "feature_count": str(next_id),
            "layer": layer,
            "crs": "EPSG:5186",
        }
        db.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
        db.commit()
    temporary.replace(output)
    print(f"완료: {output} ({next_id:,}건)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-date", default="2026-05-26")
    parser.add_argument("--batch-size", type=int, default=20000)
    parser.add_argument(
        "--output-dir", default="data/processed/ecological_nature_map"
    )
    args = parser.parse_args()
    source = Path(args.source).resolve()
    uri = f"/vsizip/{source}"
    output = Path(args.output_dir)
    import_layer(
        uri, "F_생태자연도_A", output / "ecological_nature.sqlite",
        args.source_date, args.batch_size,
    )
    import_layer(
        uri, "F_별도관리지역_A", output / "separate_management.sqlite",
        args.source_date, args.batch_size,
    )


if __name__ == "__main__":
    main()
