#!/usr/bin/env python3
"""대형 가공 공간데이터의 manifest·tar 패키지를 생성하고 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


INCLUDED_FILES = (
    "forest/forest_class.sqlite",
    "forest_inventory/forest_inventory.sqlite",
    "ecological_nature_map/ecological_nature.sqlite",
    "ecological_nature_map/separate_management.sqlite",
    "terrain/dem/cop30_korea.tif",
)
MANIFEST_NAME = "spatial-data-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(source: Path, version: str) -> dict:
    files = []
    missing = []
    for relative in INCLUDED_FILES:
        path = source / relative
        if not path.is_file():
            missing.append(relative)
            continue
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if missing:
        raise SystemExit("필수 공간데이터가 없습니다: " + ", ".join(missing))
    return {
        "schema_version": 1,
        "data_version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": "processed",
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "files": files,
    }


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def package(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    output = args.output.resolve()
    manifest = build_manifest(source, args.version)
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "w:gz" if output.name.endswith((".tar.gz", ".tgz")) else "w"
    with tempfile.TemporaryDirectory() as temporary:
        manifest_path = Path(temporary) / MANIFEST_NAME
        write_json(manifest_path, manifest)
        with tarfile.open(output, mode) as archive:
            archive.add(manifest_path, arcname=MANIFEST_NAME)
            for item in manifest["files"]:
                relative = item["path"]
                archive.add(source / relative, arcname=f"processed/{relative}")
    if args.manifest:
        write_json(args.manifest.resolve(), manifest)
    print(f"패키지 생성: {output}")
    print(f"파일 {len(manifest['files'])}개, {manifest['total_size_bytes']:,} bytes")


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_tree(root: Path, manifest: dict) -> None:
    base = root / manifest.get("root", "processed")
    errors = []
    for item in manifest.get("files", []):
        path = base / item["path"]
        if not path.is_file():
            errors.append(f"누락: {item['path']}")
        elif path.stat().st_size != item["size_bytes"]:
            errors.append(f"크기 불일치: {item['path']}")
        elif sha256(path) != item["sha256"]:
            errors.append(f"체크섬 불일치: {item['path']}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"검증 완료: {len(manifest['files'])}개 파일")


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        relative = PurePosixPath(member.name)
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit(f"안전하지 않은 tar 경로: {member.name}")
        target = (root / Path(*relative.parts)).resolve()
        if root != target and root not in target.parents:
            raise SystemExit(f"대상 폴더 밖의 tar 경로: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"링크 항목은 허용하지 않습니다: {member.name}")
    archive.extractall(root)


def extract(args: argparse.Namespace) -> None:
    package_path = args.package.resolve()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(package_path, "r:*") as archive:
        safe_extract(archive, destination)
    manifest_path = destination / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SystemExit(f"{MANIFEST_NAME}가 패키지에 없습니다.")
    verify_tree(destination, load_manifest(manifest_path))
    print(f"압축 해제: {destination}")


def verify(args: argparse.Namespace) -> None:
    manifest_path = args.manifest.resolve()
    verify_tree(args.root.resolve(), load_manifest(manifest_path))


def manifest(args: argparse.Namespace) -> None:
    value = build_manifest(args.source.resolve(), args.version)
    write_json(args.output.resolve(), value)
    print(f"manifest 생성: {args.output.resolve()}")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser()
    subcommands = command.add_subparsers(dest="command", required=True)

    manifest_parser = subcommands.add_parser("manifest")
    manifest_parser.add_argument("--source", type=Path, default=Path("data/processed"))
    manifest_parser.add_argument("--output", type=Path, required=True)
    manifest_parser.add_argument("--version", required=True)
    manifest_parser.set_defaults(handler=manifest)

    package_parser = subcommands.add_parser("pack")
    package_parser.add_argument("--source", type=Path, default=Path("data/processed"))
    package_parser.add_argument("--output", type=Path, required=True)
    package_parser.add_argument("--manifest", type=Path)
    package_parser.add_argument("--version", required=True)
    package_parser.set_defaults(handler=package)

    extract_parser = subcommands.add_parser("extract")
    extract_parser.add_argument("--package", type=Path, required=True)
    extract_parser.add_argument("--destination", type=Path, required=True)
    extract_parser.set_defaults(handler=extract)

    verify_parser = subcommands.add_parser("verify")
    verify_parser.add_argument("--root", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.set_defaults(handler=verify)
    return command


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.handler(arguments)
