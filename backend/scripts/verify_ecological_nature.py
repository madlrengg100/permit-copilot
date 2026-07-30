#!/usr/bin/env python3
"""주소 한 곳으로 생태·자연도 로컬 DB 연결을 점검한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tools import regulatory_screen, vworld


async def verify(address: str) -> None:
    location = await vworld.geocode(address)
    parcel = await vworld.get_parcel(location["lon"], location["lat"])
    result = await regulatory_screen.assess(parcel["geometry"], [])
    print(json.dumps({
        "parcel": parcel.get("jibun"),
        "ecological_nature": result.get("ecological_nature"),
        "ecological_separate_management": result.get(
            "ecological_separate_management"
        ),
        "unknowns": result.get("unknowns"),
        "findings": [
            item for item in result.get("findings", [])
            if item.get("category") == "생태·환경"
        ],
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("address")
    args = parser.parse_args()
    asyncio.run(verify(args.address))


if __name__ == "__main__":
    main()
