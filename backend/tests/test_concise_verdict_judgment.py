from app.orchestrator import _concise_verdict_judgment


def test_specific_use_review_is_two_short_sentences_without_repeating_details():
    diagnosis = {
        "verdict": "conditional",
        "parcel": {"jibun": "충청북도 음성군 금왕읍 내송리 65"},
        "request": {"building_use": "단독주택"},
        "permit_requirements": {"items": [
            {"name": "농지전용허가·협의"},
            {"name": "건축법상 도로·접도 확인"},
            {"name": "상수원·수질 관련 협의·영향진단"},
            {"name": "개발행위허가"},
            {"name": "건축허가 또는 건축신고"},
        ]},
        "regulation": {"constraints": [
            {"name": "중점경관관리구역", "note": "높이·형태·색채 제한"},
            {"name": "배출시설설치제한지역", "note": "오수처리계획 확인"},
        ]},
    }

    text = _concise_verdict_judgment(diagnosis)

    assert text.count(".") == 2
    assert len(text) < 220
    assert "조건부로 건축 가능합니다" in text
    assert "농지전용허가·협의" in text
    assert "건축법상 도로·접도 확인" in text
    assert "높이·형태·색채 제한" not in text
    assert "오수처리계획" not in text
