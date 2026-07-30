"""농지·산지 전용 사전검토.

공간레이어 중첩을 허가의 최종 결론으로 오인하지 않도록, 이 모듈은
`전용 절차의 검토 강도`만 결정한다. 소유권, 현황 농지 여부, 경사도,
입목축적과 관할청 재량은 별도 확인 사항으로 남긴다.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from .ogc import inspect_layers
from .terrain import analyze_terrain

Inspect = Callable[[dict, list[str]], Awaitable[list[dict]]]

AGRICULTURE_NAMES = {
    "UEA100": "농업진흥지역",
    "UEA110": "농업진흥구역",
    "UEA120": "농업보호구역",
}


def _compact_layer(result: dict) -> dict:
    """지도 표시용 교차 geometry를 제거하고 판정 근거만 보존한다."""
    # 경계 좌표 정밀도 때문에 실제 면적은 거의 0인데 교차 도형만 생기는 경우가
    # 있다. 비율이 0.0%로 반올림되거나 면적이 0㎡인 조각은 규제 중첩으로
    # 해석하지 않는다.
    overlaps = [
        overlap for overlap in result.get("overlaps", [])
        if float(overlap.get("share_pct") or 0) > 0
        and (
            overlap.get("area_m2") is None
            or float(overlap.get("area_m2") or 0) > 0
        )
    ]
    status = result.get("status")
    if status == "OVERLAP" and not overlaps:
        status = "CLEAR"
    return {
        **{k: v for k, v in result.items() if k != "overlaps"},
        "status": status,
        "overlaps": [
            {k: v for k, v in overlap.items() if k != "geometry"}
            for overlap in overlaps
        ],
    }


def _layer_by_id(results: list[dict], layer_id: str) -> dict:
    return next(
        (item for item in results if item.get("layer_id") == layer_id),
        {"layer_id": layer_id, "status": "NOT_CONFIGURED", "overlaps": []},
    )


def _overlap_text(layer: dict) -> str:
    overlaps = layer.get("overlaps") or []
    if not overlaps:
        return ""
    top = overlaps[0]
    code = top.get("code") or ""
    name = top.get("name") or AGRICULTURE_NAMES.get(code) or code or "규제구역"
    return f"{name} {top.get('share_pct', 0):.1f}% 중첩"


def _forest_inventory_summary(layer: dict) -> list[dict]:
    """임상도 조각을 같은 임상 속성별로 합쳐 필지 기준 참고값을 만든다."""
    grouped: dict[tuple[str, ...], dict] = {}
    for overlap in layer.get("overlaps") or []:
        props = overlap.get("properties") or {}
        values = (
            str(props.get("FRTP_NM") or ""),
            str(props.get("KOFTR_NM") or overlap.get("name") or ""),
            str(props.get("AGCLS_NM") or ""),
            str(props.get("DMCLS_NM") or ""),
            str(props.get("DNST_NM") or ""),
            str(props.get("HEIGHT_NM") or ""),
            str(props.get("갱신년도") or ""),
        )
        item = grouped.setdefault(
            values,
            {
                "forest_type": values[0],
                "species": values[1],
                "age_class": values[2],
                "diameter_class": values[3],
                "density": values[4],
                "stand_height": values[5],
                "updated_year": values[6],
                "area_m2": 0.0,
                "share_pct": 0.0,
            },
        )
        item["area_m2"] += float(overlap.get("area_m2") or 0)
        item["share_pct"] += float(overlap.get("share_pct") or 0)
    result = list(grouped.values())
    for item in result:
        item["area_m2"] = round(item["area_m2"], 1)
        item["share_pct"] = round(min(item["share_pct"], 100.0), 1)
    return sorted(result, key=lambda item: -item["area_m2"])


async def assess(
    parcel_geometry: dict,
    jimok_info: dict,
    inspect: Inspect = inspect_layers,
) -> dict:
    """필지와 농업진흥지역·산지구분을 대조해 전용 사전검토 결과를 만든다."""
    raw = await inspect(
        parcel_geometry,
        ["agricultural_promotion", "forest_class", "forest_inventory"],
    )
    raw_forest = _layer_by_id(raw, "forest_class")
    results = [_compact_layer(item) for item in raw]
    agriculture = _layer_by_id(results, "agricultural_promotion")
    forest = _layer_by_id(results, "forest_class")
    forest_inventory_layer = _layer_by_id(results, "forest_inventory")
    forest_inventory = _forest_inventory_summary(forest_inventory_layer)
    terrain = (
        analyze_terrain(parcel_geometry)
        if inspect is inspect_layers
        else {"status": "NOT_TESTED"}
    )
    category = jimok_info.get("category")

    status = "CLEAR"
    label = "전용 규제 중첩 없음"
    summary = "농업진흥지역 및 보전산지 중첩은 확인되지 않았습니다."
    basis: list[str] = []
    unknowns: list[str] = []
    data_gaps: list[dict] = []

    unavailable = [
        layer.get("title") or layer.get("layer_id", "")
        for layer in (agriculture, forest)
        if layer.get("status") in {"UNAVAILABLE", "NOT_CONFIGURED"}
    ]
    if unavailable:
        unknowns.append(f"{', '.join(unavailable)} 공간조회")

    if category == "farmland":
        basis = ["농지법 제32조(농업진흥지역의 행위 제한)", "농지법 제34조(농지전용허가)"]
        if agriculture.get("status") == "OVERLAP":
            status = "RESTRICTED_REVIEW"
            label = "농지전용 제한 검토"
            summary = (
                f"{_overlap_text(agriculture)}으로 농지전용이 크게 제한될 수 있습니다. "
                "허용시설 해당 여부와 관할청 협의가 필요합니다."
            )
        elif agriculture.get("status") == "CLEAR":
            status = "PERMIT_REQUIRED"
            label = "농지전용 절차 필요"
            summary = (
                "농업진흥지역 중첩은 확인되지 않았지만 농지전용허가·협의와 "
                "농지보전부담금 검토가 필요합니다."
            )
    elif category == "forest":
        basis = ["산지관리법 제4조(산지의 구분)", "산지관리법 제14조(산지전용허가)"]
        if forest.get("status") == "OVERLAP":
            top = (forest.get("overlaps") or [{}])[0]
            code = top.get("code", "")
            name = top.get("name") or code
            if code in {"UFM100", "UFM110", "UFM120"} or name in {
                "보전산지", "임업용산지", "공익용산지"
            }:
                status = "RESTRICTED_REVIEW"
                label = "보전산지 전용 제한 검토"
                summary = (
                    f"{_overlap_text(forest)}입니다. 해당 계획이 산지전용 "
                    "허용행위에 해당하는지 확인해야 합니다."
                )
            else:
                status = "PERMIT_REQUIRED"
                label = "산지전용 절차 필요"
                summary = (
                    f"{_overlap_text(forest)}입니다. 산지전용허가와 "
                    "대체산림자원조성비 검토가 필요합니다."
                )
        elif forest.get("status") == "CLEAR":
            status = "MANUAL_REVIEW"
            label = "산지 여부 추가 확인"
            summary = (
                "산지구분 경계 중첩은 확인되지 않았습니다. 다만 지목이 임야이므로 "
                "현황 산지 여부와 산지전용 대상 여부를 관할청에 확인해야 합니다."
            )
        if terrain.get("status") == "REFERENCE_AVAILABLE":
            data_gaps.append({
                "item": "산지전용 심사용 경사도·표고 확정값",
                "status": "FIELD_SURVEY_REQUIRED",
                "reason": "COP30 30m DEM 사전 참고값은 연결됐으나 심사용 10m 격자 조사값은 아님",
                "required_source": "자격자가 작성한 평균경사도·표고조사서 및 현황측량",
            })
        else:
            data_gaps.append({
                "item": "산지전용 심사용 경사도·표고",
                "status": "NOT_COLLECTED",
                "reason": "현재 연결된 DEM에서 필지 분석값을 얻지 못함",
                "required_source": "수치표고모형(DEM) 기반 경사도 분석 및 현황측량",
            })
        if forest_inventory:
            data_gaps.append({
                "item": "입목축적 확정값",
                "status": "FIELD_SURVEY_REQUIRED",
                "reason": "1:5,000 임상도 속성은 연결됐으나 필지별 확정 입목축적은 제공하지 않음",
                "required_source": "산림경영기술자가 작성한 산림조사서",
            })
        else:
            data_gaps.append({
                "item": "입목축적",
                "status": "NOT_COLLECTED",
                "reason": "해당 필지와 중첩되는 1:5,000 임상도 속성을 찾지 못함",
                "required_source": "산림조사 또는 입목축적 조사자료",
            })
    else:
        overlaps = [
            text for text in (_overlap_text(agriculture), _overlap_text(forest)) if text
        ]
        if overlaps:
            status = "MANUAL_REVIEW"
            label = "현황·공부 불일치 검토"
            summary = (
                f"지목상 전용 대상은 아니지만 {' / '.join(overlaps)}이 확인됐습니다. "
                "최신 공부와 현황을 확인해야 합니다."
            )

    if unavailable and status == "CLEAR":
        status = "UNKNOWN"
        label = "공간규제 확인 불가"
        summary = f"{', '.join(unavailable)} 조회에 실패해 전용 규제 여부를 확정할 수 없습니다."

    return {
        "status": status,
        "label": label,
        "summary": summary,
        "agriculture": agriculture,
        "forest": forest,
        "forest_inventory": forest_inventory,
        "terrain": terrain,
        # 지도에서 산지구분 중첩 영역을 직접 표시하기 위한 필지 교차 형상.
        # 법령 판단용 forest 데이터와 분리해 LLM 축약본에서는 제외한다.
        "forest_map_overlaps": [
            overlap for overlap in raw_forest.get("overlaps", [])
            if float(overlap.get("share_pct") or 0) > 0
            and (
                overlap.get("area_m2") is None
                or float(overlap.get("area_m2") or 0) > 0
            )
        ],
        "legal_basis": basis,
        "unknowns": list(dict.fromkeys(unknowns)),
        "data_gaps": data_gaps,
        "limitations": [
            "공간 중첩은 사전검토 결과이며 허가·불허의 최종 처분이 아닙니다.",
            "소유권, 기존 건축물 철거 가능 여부와 현황 토지 이용은 별도 확인이 필요합니다.",
        ],
    }
