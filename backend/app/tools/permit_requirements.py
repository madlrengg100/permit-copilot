"""진단 결과에서 필지별 인허가 순서·구비서류 체크리스트를 만든다."""

from __future__ import annotations


NON_URBAN = {
    "계획관리지역", "생산관리지역", "보전관리지역", "농림지역", "자연환경보전지역"
}


def build(state: dict) -> dict:
    parcel = state.get("parcel", {})
    category = state.get("jimok_info", {}).get("category")
    zone = state.get("regulation", {}).get("zone", "")
    road = state.get("road_access", {})
    existing = state.get("existing_buildings", {})
    screen = state.get("regulatory_screen", {})
    authority = state.get("jurisdiction") or "관할 시·군·구"

    items: list[dict] = []

    if existing.get("has_buildings"):
        items.append({
            "order": 10,
            "id": "demolition",
            "name": "기존 건축물 해체허가·신고 및 멸실 정리",
            "required": True,
            "department": f"{authority} 건축 담당부서",
            "processing_days": None,
            "documents": ["해체계획서", "소유권·사용권 증빙", "해체공사 안전관리 자료"],
            "basis": "건축물관리법 및 건축물대장 관련 규정",
            "note": "소유자가 아닌 경우 임의 철거할 수 없으며 권리관계를 먼저 확인해야 합니다.",
        })

    if category == "farmland":
        items.append({
            "order": 20,
            "id": "farmland_conversion",
            "name": "농지전용허가·협의",
            "required": True,
            "department": f"{authority} 농지 담당부서",
            "processing_days": None,
            "documents": [
                "농지전용허가 신청서", "사업계획서", "소유권 또는 사용권 증빙",
                "지적도·지형도", "피해방지계획 및 복구계획",
            ],
            "basis": "농지법 제34조",
            "note": "농업진흥지역 여부, 시설 용도와 감면조건에 따라 협의 범위가 달라집니다.",
        })
    elif category == "forest":
        items.append({
            "order": 20,
            "id": "forest_conversion",
            "name": "산지전용허가",
            "required": True,
            "department": f"{authority} 산림 담당부서",
            "processing_days": 30,
            "documents": [
                "산지전용허가 신청서", "사업계획서", "산지전용 예정지 실측도",
                "산림조사서·입목축적조사서", "복구계획서", "재해위험성 검토자료(해당 시)",
            ],
            "basis": "산지관리법 제14조 및 같은 법 시행규칙",
            "note": "평균경사도·표고·입목축적과 지자체 조례 기준을 추가 확인해야 합니다.",
        })

    if zone in NON_URBAN or category in {"farmland", "forest"}:
        items.append({
            "order": 30,
            "id": "development_activity",
            "name": "개발행위허가",
            "required": True,
            "department": f"{authority} 도시계획·개발행위 담당부서",
            "processing_days": None,
            "documents": [
                "개발행위허가 신청서", "토지이용계획서", "배치도·공사계획도",
                "배수·재해방지계획", "소유권 또는 사용권 증빙",
            ],
            "basis": "국토의 계획 및 이용에 관한 법률 제56조",
            "note": "진입도로, 경사도, 배수와 주변 토지 피해방지 기준을 함께 심사합니다.",
        })

    if road.get("status") != "CADASTRAL_CONTACT" or road.get("unknowns"):
        items.append({
            "order": 40,
            "id": "road_review",
            "name": "건축법상 도로·접도 확인",
            "required": True,
            "department": f"{authority} 건축·도로 담당부서",
            "processing_days": None,
            "documents": ["도로대장", "현황측량도", "도로 지정자료 또는 통행권 증빙(해당 시)"],
            "basis": "건축법 제2조제1항제11호 및 제44조",
            "note": "지적도상 도로 접촉만으로 건축법상 접도요건 충족이 확정되지는 않습니다.",
        })

    for finding in screen.get("findings", []):
        items.append({
            "order": 50,
            "id": f"special_{finding.get('category', 'review')}",
            "name": f"{finding.get('category')} 관련 협의·영향진단",
            "required": True,
            "department": f"{authority} 관련 전문부서",
            "processing_days": None,
            "documents": ["사업계획 및 배치도", "해당 구역 중첩도", "저감·보존대책"],
            "basis": finding.get("basis", ""),
            "note": finding.get("note", ""),
        })

    items.append({
        "order": 90,
        "id": "building_permission",
        "name": "건축허가 또는 건축신고",
        "required": True,
        "department": f"{authority} 건축 담당부서",
        "processing_days": None,
        "documents": [
            "건축허가·신고 신청서", "대지 권리관계 증빙", "배치도·평면도·입면도·단면도",
            "구조·설비 관련 도서", "의제 처리할 개별 인허가 신청서류",
        ],
        "basis": "건축법 제11조·제14조",
        "note": "허가·신고 구분과 처리기간은 건축물 규모·용도 및 관계기관 협의에 따라 달라집니다.",
    })

    items.sort(key=lambda item: item["order"])
    for index, item in enumerate(items, 1):
        item["sequence"] = index

    unknowns = list(dict.fromkeys(
        screen.get("unknowns", [])
        + road.get("unknowns", [])
        + (["경사도·표고", "입목축적"] if category == "forest" else [])
    ))
    return {
        "authority": authority,
        "items": items,
        "unknowns": unknowns,
        "summary": f"예상 인허가·협의 {len(items)}단계",
        "caveat": "법정 의제·일괄협의 여부와 실제 처리기간은 사업 규모 및 보완 요구에 따라 달라집니다.",
        "parcel_pnu": parcel.get("pnu", ""),
    }
