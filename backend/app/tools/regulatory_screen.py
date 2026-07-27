"""재해·환경·국가유산 공간규제 1차 스크리닝."""

from __future__ import annotations

from typing import Awaitable, Callable

from .ogc import inspect_layers

Inspect = Callable[[dict, list[str]], Awaitable[list[dict]]]


async def assess(
    parcel_geometry: dict,
    districts: list[str] | None = None,
    inspect: Inspect = inspect_layers,
) -> dict:
    districts = districts or []
    results = await inspect(parcel_geometry, ["disaster_risk_zone"])
    disaster = results[0] if results else {
        "status": "NOT_CONFIGURED", "overlaps": [], "title": "재해위험지구"
    }
    overlaps = [
        {k: v for k, v in item.items() if k != "geometry"}
        for item in disaster.get("overlaps", [])
    ]
    disaster = {**disaster, "overlaps": overlaps}

    heritage_names = [
        name for name in districts
        if "문화재" in name or "국가유산" in name or "역사문화" in name
    ]
    findings = []
    unknowns = []
    if disaster.get("status") == "OVERLAP":
        top = overlaps[0]
        findings.append({
            "category": "재해",
            "severity": "REVIEW",
            "label": top.get("name") or top.get("code") or "재해위험지구",
            "share_pct": top.get("share_pct"),
            "basis": "자연재해대책법에 따른 재해위험지구",
            "note": "개발행위·건축허가 전에 재해저감대책과 관할 방재부서 협의가 필요합니다.",
        })
    elif disaster.get("status") in {"UNAVAILABLE", "NOT_CONFIGURED"}:
        unknowns.append("재해위험지구")

    for name in heritage_names:
        findings.append({
            "category": "국가유산",
            "severity": "REVIEW",
            "label": name,
            "basis": "국가유산영향진단법 및 국가유산 관련 법령",
            "note": "현상변경 허용기준과 국가유산 영향진단·관할기관 협의 대상을 확인해야 합니다.",
        })

    # 생태·자연도 API는 별도 공공데이터포털 활용신청과 서비스별 호출 규격이
    # 필요하다. 연결 전에는 '해당 없음'으로 결론내리지 않는다.
    unknowns.append("생태·자연도 및 환경보전 등급")

    return {
        "status": "REVIEW" if findings else ("UNKNOWN" if unknowns else "CLEAR"),
        "findings": findings,
        "unknowns": list(dict.fromkeys(unknowns)),
        "disaster": disaster,
        "summary": (
            " / ".join(f"{item['category']}: {item['label']}" for item in findings)
            if findings
            else "현재 연결된 재해위험지구에서는 중첩이 확인되지 않았습니다."
        ),
        "caveat": "공간 중첩은 영향평가·영향진단의 대상 가능성을 선별하는 사전검토입니다.",
    }
