#!/usr/bin/env python
"""법정 상한 vs 지자체 조례 비교표.

    python compare_ordinances.py            # 전체 용도지역 비교
    python compare_ordinances.py 일반상업지역   # 특정 용도지역 상세
    python compare_ordinances.py --gaps      # 격차 큰 순
"""

from __future__ import annotations

import sys

from app.tools import massing, ordinance

SAMPLE_AREA = 660.0  # 비교용 표준 대지면적


def table_all() -> None:
    juris = ordinance.jurisdictions()
    header = f"{'용도지역':<18}{'법정':>12}" + "".join(f"{j[:4]:>12}" for j in juris)
    print("건폐율(%) / 용적률(%) — 법정 상한 대비 지자체 조례")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for zone in ordinance.statutory_limits():
        rows = {r["jurisdiction"]: r for r in ordinance.compare(zone)}
        stat = rows["법정 상한"]
        line = f"{zone:<18}{stat['bcr_max_pct']:>4}/{stat['far_max_pct']:>6}  "
        for j in juris:
            r = rows[j]
            cell = "—" if not r["regulated"] else f"{r['bcr_max_pct']}/{r['far_max_pct']}"
            line += f"{cell:>12}"
        print(line)

    print()
    print("— = 해당 조례에 규정 없음 (도시지역 외 용도지역은 조례에 없는 경우가 있음)")
    print("용적률 하한은 조례가 규정하지 않으므로 항상 시행령 제85조 값을 쓴다.")


def table_zone(zone: str) -> None:
    rows = ordinance.compare(zone)
    if not rows:
        print(f"'{zone}' 은(는) 시행령 용도지역 목록에 없습니다.")
        print("가능한 값:", ", ".join(ordinance.statutory_limits()))
        return

    print(f"[{zone}] 표준 대지면적 {SAMPLE_AREA:,.0f}㎡ 기준")
    print("=" * 78)
    print(f"{'적용 기준':<16}{'건폐율':>8}{'용적률':>9}{'건축면적':>11}{'연면적':>12}{'층수':>7}")
    print("-" * 78)

    for r in rows:
        if not r["regulated"]:
            print(f"{r['jurisdiction']:<16}{'— 조례에 규정 없음':>30}")
            continue
        m = massing.calc_massing(SAMPLE_AREA, r["bcr_max_pct"], r["far_max_pct"])
        gap = ""
        if r["jurisdiction"] != "법정 상한" and r["far_gap"]:
            gap = f"  (법정 대비 -{r['far_gap']}%p)"
        print(
            f"{r['jurisdiction']:<16}{r['bcr_max_pct']:>7}%{r['far_max_pct']:>8}%"
            f"{m['building_area_m2']:>10,.0f}㎡{m['gross_floor_area_m2']:>11,.0f}㎡"
            f"{m['floors']:>6}층{gap}"
        )

    notes = [(r["jurisdiction"], r["note"]) for r in rows if r.get("note")]
    if notes:
        print()
        print("조례 단서:")
        for j, n in notes:
            print(f"  · {j}: {n}")


def table_gaps() -> None:
    print("법정 상한 대비 조례가 가장 크게 조인 항목")
    print("=" * 78)
    print(f"{'용도지역':<18}{'지자체':<14}{'항목':<8}{'법정':>7}{'조례':>7}{'격차':>8}")
    print("-" * 78)
    for g in ordinance.largest_gaps(15):
        unit = "%"
        print(
            f"{g['zone']:<18}{g['jurisdiction']:<14}{g['metric']:<8}"
            f"{g['statutory']:>6}{unit}{g['applied']:>6}{unit}{g['gap']:>6}%p"
        )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if not args:
        table_all()
    elif args[0] == "--gaps":
        table_gaps()
    else:
        table_zone(args[0])
