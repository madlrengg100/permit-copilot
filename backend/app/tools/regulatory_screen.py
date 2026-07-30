"""재해·환경·국가유산 공간규제 1차 스크리닝."""

from __future__ import annotations

from typing import Awaitable, Callable

from .ogc import inspect_layers

Inspect = Callable[[dict, list[str]], Awaitable[list[dict]]]


async def assess(
    parcel_geometry: dict,
    districts: list[str] | None = None,
    inspect: Inspect = inspect_layers,
    designation_lookup: dict | None = None,
) -> dict:
    districts = districts or []
    designation_lookup = designation_lookup or {}
    designation_available = designation_lookup.get("status") == "AVAILABLE"
    results = await inspect(parcel_geometry, [
        "disaster_risk_zone",
        "ecological_nature",
        "ecological_separate_management",
    ])
    by_id = {item.get("layer_id"): item for item in results}
    disaster = by_id.get("disaster_risk_zone") or {
        "status": "NOT_CONFIGURED", "overlaps": [], "title": "재해위험지구"
    }
    overlaps = [
        {k: v for k, v in item.items() if k != "geometry"}
        for item in disaster.get("overlaps", [])
    ]
    disaster = {**disaster, "overlaps": overlaps}

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
        # 토지이용계획 지정정보는 확인 가능한 규제명을 보완할 뿐, 전국
        # 재해위험지구 공간 중첩 자료를 대신하지 않는다.
        unknowns.append("재해위험지구 전국 공간자료")

    designation_categories = (
        (
            "재해",
            ("자연재해위험", "재해위험", "방재지구", "붕괴위험", "침수위험"),
            "자연재해대책법 및 해당 재해 관련 법령",
            "개발행위·건축허가 전에 재해저감대책과 관할 방재부서 협의 여부를 확인해야 합니다.",
        ),
        (
            "생태·환경",
            ("생태·경관보전", "생태경관보전", "야생생물보호", "환경보전해역"),
            "자연환경보전법 및 환경 관련 법령",
            "보전등급과 허용행위, 환경부서 협의·평가 대상을 확인해야 합니다.",
        ),
        (
            "상수원·수질",
            ("상수원보호", "수변구역", "수질보전", "특별대책지역", "배출시설설치제한"),
            "수도법·물환경보전법 및 상수원 관련 법령",
            "입지·행위 제한과 관할 환경·수도 부서 협의 여부를 확인해야 합니다.",
        ),
        (
            "자연공원",
            ("자연공원", "공원구역", "국립공원", "도립공원", "군립공원"),
            "자연공원법",
            "공원구역의 허용행위와 공원관리청 허가·협의 여부를 확인해야 합니다.",
        ),
        (
            "습지",
            ("습지보호", "습지주변관리"),
            "습지보전법",
            "습지보호지역의 행위 제한과 환경부서 허가·협의 여부를 확인해야 합니다.",
        ),
        (
            "국가유산",
            ("문화재", "국가유산", "역사문화", "보존유적"),
            "국가유산영향진단법 및 국가유산 관련 법령",
            "현상변경 허용기준과 국가유산 영향진단·관할기관 협의 대상을 확인해야 합니다.",
        ),
    )
    seen_labels = {item["label"] for item in findings}
    for name in districts:
        for category, terms, basis, note in designation_categories:
            if name in seen_labels or not any(term in name for term in terms):
                continue
            findings.append({
                "category": category,
                "severity": "REVIEW",
                "label": name,
                "basis": basis,
                "note": note,
                "source": "토지이용계획 지정정보",
            })
            seen_labels.add(name)
            break

    ecological = by_id.get("ecological_nature") or {
        "status": "NOT_CONFIGURED", "overlaps": [], "title": "생태·자연도"
    }
    ecological_overlaps = [
        {k: v for k, v in item.items() if k != "geometry"}
        for item in ecological.get("overlaps", [])
        if float(item.get("share_pct") or 0) > 0
    ]
    ecological = {**ecological, "overlaps": ecological_overlaps}
    if ecological.get("status") == "OVERLAP":
        for item in ecological_overlaps:
            grade = str(item.get("code") or "").strip()
            findings.append({
                "category": "생태·환경",
                "severity": "REVIEW" if grade in {"1", "2"} else "INFO",
                "label": item.get("name") or f"생태·자연도 {grade}등급",
                "share_pct": item.get("share_pct"),
                "area_m2": item.get("area_m2"),
                "basis": "자연환경보전법에 따른 생태·자연도",
                "note": (
                    "1·2등급지는 개발계획 수립과 환경성 검토에서 보전가치를 "
                    "중점 확인해야 합니다. 등급만으로 건축 불가를 단정하지 않고 "
                    "사업 유형·규모별 환경평가 및 관계기관 협의 기준을 함께 적용합니다."
                    if grade in {"1", "2"}
                    else "3등급 중첩 현황을 환경성 검토의 기초자료로 반영합니다."
                ),
            })
    elif ecological.get("status") in {"UNAVAILABLE", "NOT_CONFIGURED"}:
        unknowns.append("생태·자연도 등급")

    separate = by_id.get("ecological_separate_management") or {
        "status": "NOT_CONFIGURED", "overlaps": [],
        "title": "생태·자연도 별도관리지역",
    }
    separate_overlaps = [
        {k: v for k, v in item.items() if k != "geometry"}
        for item in separate.get("overlaps", [])
        if float(item.get("share_pct") or 0) > 0
    ]
    separate = {**separate, "overlaps": separate_overlaps}
    if separate.get("status") == "OVERLAP":
        for item in separate_overlaps:
            findings.append({
                "category": "생태·환경",
                "severity": "REVIEW",
                "label": item.get("name") or "생태·자연도 별도관리지역",
                "share_pct": item.get("share_pct"),
                "area_m2": item.get("area_m2"),
                "basis": "생태·자연도 별도관리지역 공간자료와 개별 보호 법령",
                "note": (
                    "별도관리지역의 실제 유형에 해당하는 자연공원·습지·백두대간·"
                    "산림보호·국가유산 등 개별 법령의 허용행위와 협의·허가 요건을 "
                    "별도로 충족해야 합니다."
                ),
            })

    if findings:
        summary = " / ".join(
            f"{item['category']}: {item['label']}" for item in findings
        )
        if unknowns:
            summary += " / 미연계 공간자료는 추가 확인이 필요합니다."
    elif unknowns and designation_available:
        summary = "토지이용계획상 재해·환경·국가유산 관련 규제 없음"
    elif designation_available:
        summary = "토지이용계획상 재해·환경·국가유산 관련 규제 없음"
    else:
        summary = "재해·환경·국가유산 공간자료가 연결되지 않아 중첩 여부를 확인하지 못했습니다."

    needs_review = any(
        item.get("severity") == "REVIEW" for item in findings
    )
    return {
        "status": "REVIEW" if needs_review else ("UNKNOWN" if unknowns else "CLEAR"),
        "findings": findings,
        "unknowns": list(dict.fromkeys(unknowns)),
        "disaster": disaster,
        "ecological_nature": ecological,
        "ecological_separate_management": separate,
        "designation_lookup": {
            "status": designation_lookup.get("status", "NOT_CONFIGURED"),
            "source": designation_lookup.get("source"),
            "updated_at": designation_lookup.get("updated_at"),
        },
        "summary": summary,
        "caveat": "공간 중첩은 영향평가·영향진단의 대상 가능성을 선별하는 사전검토입니다.",
    }
