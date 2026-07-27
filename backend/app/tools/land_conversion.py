"""농지·산지 전용 사전검토.

공간레이어 중첩을 허가의 최종 결론으로 오인하지 않도록, 이 모듈은
`전용 절차의 검토 강도`만 결정한다. 소유권, 현황 농지 여부, 경사도,
입목축적과 관할청 재량은 별도 확인 사항으로 남긴다.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from .ogc import inspect_layers

Inspect = Callable[[dict, list[str]], Awaitable[list[dict]]]

AGRICULTURE_NAMES = {
    "UEA100": "농업진흥지역",
    "UEA110": "농업진흥구역",
    "UEA120": "농업보호구역",
}


def _compact_layer(result: dict) -> dict:
    """지도 표시용 교차 geometry를 제거하고 판정 근거만 보존한다."""
    return {
        **{k: v for k, v in result.items() if k != "overlaps"},
        "overlaps": [
            {k: v for k, v in overlap.items() if k != "geometry"}
            for overlap in result.get("overlaps", [])
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


async def assess(
    parcel_geometry: dict,
    jimok_info: dict,
    inspect: Inspect = inspect_layers,
) -> dict:
    """필지와 농업진흥지역·산지구분을 대조해 전용 사전검토 결과를 만든다."""
    raw = await inspect(
        parcel_geometry, ["agricultural_promotion", "forest_class"]
    )
    results = [_compact_layer(item) for item in raw]
    agriculture = _layer_by_id(results, "agricultural_promotion")
    forest = _layer_by_id(results, "forest_class")
    category = jimok_info.get("category")

    status = "CLEAR"
    label = "전용 규제 중첩 없음"
    summary = "농업진흥지역 및 보전산지 중첩은 확인되지 않았습니다."
    basis: list[str] = []
    unknowns: list[str] = []

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
                    f"{_overlap_text(forest)}입니다. 산지전용 허용행위, "
                    "경사도·표고·입목축적을 추가 확인해야 합니다."
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
        unknowns.extend(["경사도·표고", "입목축적"])
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
        "legal_basis": basis,
        "unknowns": list(dict.fromkeys(unknowns)),
        "limitations": [
            "공간 중첩은 사전검토 결과이며 허가·불허의 최종 처분이 아닙니다.",
            "소유권, 기존 건축물 철거 가능 여부와 현황 토지 이용은 별도 확인이 필요합니다.",
        ],
    }
