"""지자체 도시계획조례 로더.

data/ordinances.json 은 두 층위를 담는다:
  _meta.statutory_reference.limits  국토계획법 시행령 제84·85조 법정 상한
  <지자체명>.<용도지역>              해당 지자체 조례가 정한 실제 적용값

조례는 법정 상한 이내에서 더 강하게 정할 수 있으므로, 지자체가 특정되면
조례값이 우선한다. 조례에 규정이 없으면(null) 법정값으로 폴백한다.

수치는 전부 조례·시행령 원문에서 확인한 값이다. 값이 없으면 지어내지 않고
null 로 두고, 사유를 함께 기록한다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "ordinances.json"
# 전국 자동 수집본(collect_ordinances.py 산출). 수작업 검증본이 없는 지자체만
# 보완적으로 병합한다. review_status=="auto_extracted" 인 것만 신뢰해 쓴다.
_AUTO_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "ordinances_auto.json"

# 도시지역 밖에서는 시·군 도시·군계획조례가 실제 적용 밀도를 정하므로,
# 조례를 확인하지 못한 상태에서 시행령 상한으로 자동 계산하지 않는다.
NON_URBAN_ZONES = {
    "계획관리지역",
    "생산관리지역",
    "보전관리지역",
    "농림지역",
    "자연환경보전지역",
}


def _merge_auto(data: dict) -> None:
    """자동 수집본을 수작업 검증본 위에 '보완'으로 병합한다(수작업 우선).

    - 수작업본에 이미 있는 지자체는 건드리지 않는다(검증값 우선).
    - review_status=="auto_extracted" 인 지자체만 신뢰해 쓴다(needs_review 제외).
    - 자동본 스키마(zones/_meta)를 수작업본 스키마(zone별 dict + _source)로 변환.
    """
    if not _AUTO_DATA_PATH.exists():
        return
    try:
        with _AUTO_DATA_PATH.open(encoding="utf-8") as f:
            auto = json.load(f)
    except (OSError, ValueError):
        return
    for org, rec in auto.items():
        if org.startswith("_") or org in data:
            continue
        meta = rec.get("_meta") or {}
        if meta.get("review_status") != "auto_extracted":
            continue
        zones = rec.get("zones") or {}
        if not zones:
            continue
        entry: dict = {
            "_source": {
                "ordinance": meta.get("ordinance_name") or f"{org} 도시계획조례",
                "ordinance_no": meta.get("ordinance_no"),
                "articles": meta.get("articles") or "",
                "effective_date": meta.get("effective_date"),
                "url": meta.get("source_url"),
                "collection": "auto",  # 자동 수집본임을 표시(감사 추적)
                "confidence": meta.get("extraction_confidence"),
            }
        }
        for zone, vals in zones.items():
            entry[zone] = {
                "bcr_max_pct": vals.get("bcr_max_pct"),
                "far_max_pct": vals.get("far_max_pct"),
                "far_min_pct": vals.get("far_min_pct"),
                "note": f"{meta.get('ordinance_name') or org} 자동 수집값(국가법령정보센터 API).",
            }
        data[org] = entry


@lru_cache(maxsize=1)
def _load() -> dict:
    with _DATA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    _merge_auto(data)
    return data


# 수집된 지자체 목록 (_meta 제외)
def jurisdictions() -> list[str]:
    return [k for k in _load() if not k.startswith("_")]


def statutory_limits() -> dict[str, dict]:
    """시행령 법정 상한 전체."""
    return _load()["_meta"]["statutory_reference"]["limits"]


def statutory_meta() -> dict:
    ref = _load()["_meta"]["statutory_reference"]
    return {k: v for k, v in ref.items() if k != "limits"}


# ------------------------------------------------------- 주소 -> 지자체 매핑

# 주소 문자열에서 지자체를 알아내기 위한 별칭. 긴 것부터 매칭한다.
#
# 주의: 광역시 별칭("인천")은 자치구까지 포괄한다. 인천 계양구처럼 자치구가
# 별도 조례를 두지 않는 곳은 이걸로 맞지만, 부산은 영도·동래·금정·사상·기장이
# 별도 조례를 갖는다. 그 자치구가 나오면 시 조례를 쓰지 말라고 경고해야 한다.
_ALIASES: list[tuple[str, str]] = [
    ("서울특별시", "서울특별시"), ("서울시", "서울특별시"), ("서울", "서울특별시"),
    ("부산광역시", "부산광역시"), ("부산시", "부산광역시"), ("부산", "부산광역시"),
    ("인천광역시", "인천광역시"), ("인천시", "인천광역시"), ("인천", "인천광역시"),
    ("대구광역시", "대구광역시"), ("대구시", "대구광역시"), ("대구", "대구광역시"),
    ("경기도 성남시", "경기도 성남시"), ("성남시", "경기도 성남시"), ("성남", "경기도 성남시"),
    # 비도시 지자체 — 개발행위허가 기준이 실질적 관문인 지역
    ("충청북도 청주시", "충청북도 청주시"), ("청주시", "충청북도 청주시"), ("청주", "충청북도 청주시"),
    ("충청북도 음성군", "충청북도 음성군"), ("음성군", "충청북도 음성군"), ("음성", "충청북도 음성군"),
    ("경상북도 경산시", "경상북도 경산시"), ("경산시", "경상북도 경산시"), ("경산", "경상북도 경산시"),
    ("경상북도 영천시", "경상북도 영천시"), ("영천시", "경상북도 영천시"), ("영천", "경상북도 영천시"),
    ("충청남도 아산시", "충청남도 아산시"), ("아산시", "충청남도 아산시"), ("아산", "충청남도 아산시"),
    ("충청남도 예산군", "충청남도 예산군"), ("예산군", "충청남도 예산군"),
    # 계양구는 별도 도시계획조례가 아니라 인천광역시 도시계획조례를 적용한다.
    ("인천광역시 계양구", "인천광역시"), ("계양구", "인천광역시"), ("계양", "인천광역시"),
]

# 시 조례로 판단하면 안 되는 자치구 — 별도 도시계획조례를 둔다.
# 수치를 수집하지 않았으므로 값을 추정하지 않고 경고만 띄운다.
SEPARATE_ORDINANCE_DISTRICTS: dict[str, list[str]] = {
    "부산광역시": ["영도구", "동래구", "금정구", "사상구", "기장군"],
}


def separate_ordinance_warning(address: str, jurisdiction: str | None) -> str | None:
    """주소가 별도 조례를 둔 자치구에 해당하면 경고 문구를 돌려준다."""
    if not jurisdiction or not address:
        return None
    normalized = re.sub(r"\s+", "", address)
    for district in SEPARATE_ORDINANCE_DISTRICTS.get(jurisdiction, []):
        if district in normalized:
            return (
                f"{jurisdiction} {district}는 별도 도시계획조례를 둡니다. "
                f"여기 표시된 값은 {jurisdiction} 조례 기준이므로 실제와 다를 수 있습니다. "
                f"{district} 조례를 직접 확인하세요."
            )
    return None


@lru_cache(maxsize=1)
def _dynamic_aliases() -> list[tuple[str, str]]:
    """수집된 전체 지자체명에서 주소 매칭용 별칭을 자동 생성한다.

    - 전체명(공백 제거)은 항상 별칭.
    - 마지막 토큰(시/군/구)이 전국에서 유일하면 짧은 별칭(예: '부천시','부천')도 허용.
    - 동명(예: '고성군'이 강원·경남에 모두)일 때는 짧은 별칭을 만들지 않고
      광역명이 포함된 전체명으로만 매칭해 오매핑을 막는다.
    """
    keys = [k for k in _load() if not k.startswith("_")]
    last_counts: dict[str, int] = {}
    for k in keys:
        last_counts[k.split()[-1]] = last_counts.get(k.split()[-1], 0) + 1
    out: list[tuple[str, str]] = []
    for k in keys:
        out.append((re.sub(r"\s+", "", k), k))  # 전체명
        last = k.split()[-1]
        if last_counts[last] == 1:  # 전국 유일 지명만 짧은 별칭
            out.append((last, k))
            stem = re.sub(r"(특별자치시|특별자치도|광역시|특별시|시|군|구)$", "", last)
            if len(stem) >= 2:
                out.append((stem, k))
    return out


def detect_jurisdiction(address: str) -> str | None:
    """주소에서 조례 데이터가 있는 지자체를 찾는다. 없으면 None."""
    if not address:
        return None
    available = set(jurisdictions())
    normalized = re.sub(r"\s+", "", address)
    # 1) 수작업 별칭 우선(계양구→인천 등 예외 처리 포함).
    for alias, canonical in sorted(_ALIASES, key=lambda p: -len(p[0])):
        if canonical in available and re.sub(r"\s+", "", alias) in normalized:
            return canonical
    # 2) 전국 자동 별칭 — 긴 별칭부터(전체명 > 시·군명) 매칭.
    for alias, canonical in sorted(_dynamic_aliases(), key=lambda p: -len(p[0])):
        if alias in normalized:
            return canonical
    return None


# ------------------------------------------------------------- 규제값 조회


def resolve_limits(zone: str, jurisdiction: str | None = None) -> dict:
    """용도지역의 적용 건폐율·용적률을 돌려준다.

    지자체가 지정되고 그 조례에 해당 용도지역 규정이 있으면 조례값을,
    아니면 법정 상한을 쓴다. 어느 쪽을 썼는지 source 로 밝힌다.
    """
    data = _load()
    statutory = statutory_limits().get(zone)

    if statutory is None:
        return {
            "found": False,
            "zone": zone,
            "reason": f"'{zone}'은(는) 시행령 용도지역 목록에 없습니다.",
        }

    result = {
        "found": True,
        "zone": zone,
        "bcr_max_pct": statutory["bcr_max_pct"],
        "far_min_pct": statutory["far_min_pct"],
        "far_max_pct": statutory["far_max_pct"],
        "source": "statutory",
        "source_label": "국토계획법 시행령 제84·85조 (법정 상한)",
        "jurisdiction": None,
        "statutory": dict(statutory),
        "ordinance_note": None,
    }

    if not jurisdiction or jurisdiction not in data:
        if zone in NON_URBAN_ZONES:
            return {
                "found": False,
                "zone": zone,
                "reason": (
                    f"{zone}은 비도시지역이므로 관할 시·군 조례 확인이 필요합니다. "
                    "현재 주소에서 수집된 조례 관할을 확인하지 못해 법정 상한으로 "
                    "대체 계산하지 않았습니다."
                ),
                "requires_ordinance": True,
                "statutory": dict(statutory),
            }
        return result

    entry = (data[jurisdiction] or {}).get(zone)

    # 항목이 있어도 건폐율·용적률이 모두 null 이면 '조례에 규정 없음'이다.
    # (데이터셋은 미규정 항목도 사유를 담은 placeholder 로 넣어 둔다)
    # 이걸 조례 적용으로 취급하면 법정값에 없는 조문을 근거로 붙이게 된다.
    regulated = bool(entry) and any(
        entry.get(k) is not None for k in ("bcr_max_pct", "far_max_pct")
    )

    if not regulated:
        if zone in NON_URBAN_ZONES:
            return {
                "found": False,
                "zone": zone,
                "reason": (
                    (entry or {}).get("note")
                    or f"{jurisdiction} 조례에서 '{zone}'의 건폐율·용적률을 확인하지 못했습니다."
                ),
                "requires_ordinance": True,
                "jurisdiction": jurisdiction,
                "statutory": dict(statutory),
            }
        result["jurisdiction"] = jurisdiction
        result["ordinance_note"] = (
            (entry or {}).get("note")
            or f"{jurisdiction} 조례에 '{zone}' 규정이 없어 법정 상한을 적용했습니다."
        )
        return result

    src = data[jurisdiction].get("_source", {})
    result.update(
        {
            "jurisdiction": jurisdiction,
            "source": "ordinance",
            "source_label": (
                f"{src.get('ordinance', jurisdiction + ' 도시계획조례')} "
                f"{src.get('articles', '')} (시행 {src.get('effective_date', '?')})"
            ).strip(),
            "ordinance_note": entry.get("note"),
        }
    )

    # 조례가 값을 정한 항목만 덮어쓴다. null 이면 법정값 유지.
    for key in ("bcr_max_pct", "far_max_pct", "far_min_pct"):
        if entry.get(key) is not None:
            result[key] = entry[key]

    # 용적률 하한은 조례가 규정하지 않으므로 사실상 항상 법정값이다
    result["far_min_note"] = entry.get("far_min_reason")

    return result


def compare(zone: str) -> list[dict]:
    """한 용도지역에 대해 법정 상한과 전 지자체 조례값을 나란히 비교한다."""
    statutory = statutory_limits().get(zone)
    if not statutory:
        return []

    rows = [
        {
            "jurisdiction": "법정 상한",
            "bcr_max_pct": statutory["bcr_max_pct"],
            "far_max_pct": statutory["far_max_pct"],
            "bcr_gap": 0,
            "far_gap": 0,
            "regulated": True,
            "note": None,
        }
    ]

    for j in jurisdictions():
        r = resolve_limits(zone, j)
        regulated = r["source"] == "ordinance"
        rows.append(
            {
                "jurisdiction": j,
                "bcr_max_pct": r["bcr_max_pct"] if regulated else None,
                "far_max_pct": r["far_max_pct"] if regulated else None,
                "bcr_gap": statutory["bcr_max_pct"] - r["bcr_max_pct"] if regulated else None,
                "far_gap": statutory["far_max_pct"] - r["far_max_pct"] if regulated else None,
                "regulated": regulated,
                "note": r.get("ordinance_note"),
            }
        )

    return rows


def largest_gaps(limit: int = 10) -> list[dict]:
    """법정 상한 대비 조례가 가장 크게 조인 항목들."""
    gaps = []
    for zone in statutory_limits():
        for row in compare(zone):
            if row["jurisdiction"] == "법정 상한" or not row["regulated"]:
                continue
            if row["far_gap"]:
                gaps.append(
                    {
                        "zone": zone,
                        "jurisdiction": row["jurisdiction"],
                        "metric": "용적률",
                        "statutory": statutory_limits()[zone]["far_max_pct"],
                        "applied": row["far_max_pct"],
                        "gap": row["far_gap"],
                    }
                )
            if row["bcr_gap"]:
                gaps.append(
                    {
                        "zone": zone,
                        "jurisdiction": row["jurisdiction"],
                        "metric": "건폐율",
                        "statutory": statutory_limits()[zone]["bcr_max_pct"],
                        "applied": row["bcr_max_pct"],
                        "gap": row["bcr_gap"],
                    }
                )
    return sorted(gaps, key=lambda g: -g["gap"])[:limit]
