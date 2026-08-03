"""요청 시설의 농지법상 설치 가능 여부(결정식) — 진단과 지역추천이 공유하는 단일 원본.

지목 분류는 ``jimok.classify`` 를 그대로 재사용한다(하드코딩 지목 목록을 새로 만들지
않는다). 농지(전·답·과수원) 위 특정 시설의 허용 여부만 여기서 판단한다:

- 움막: 농지법상 농지에 설치할 수 있는 시설이 아니어서 불가('not_allowed').
- 농막: 신고 후 소규모(연면적 20㎡ 이하)로 설치 가능한 조건부('conditional').

수치나 용도지역 판정은 여기서 만들지 않는다 — 그건 zoning/ordinance 결정식이 한다.
"""

from __future__ import annotations

from . import jimok as jimok_tool


def farmland_facility_verdict(facility: str, jimok: str | None) -> str | None:
    """농지 위 특정 시설의 농지법 판정. 해당 없으면 None(=이 규칙과 무관).

    Args:
        facility: 사용자가 요청한 시설명(예: '움막', '농막').
        jimok: 필지 지목(예: '전', '답', '대').
    """
    facility = (facility or "").strip()
    if not facility:
        return None
    if jimok_tool.classify(jimok).get("category") != "farmland":
        return None
    if "움막" in facility:
        return "not_allowed"
    if "농막" in facility:
        return "conditional"
    return None
