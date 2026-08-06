from app.agents.prediagnosis import _merge_permit_legal_evidence


def test_road_access_laws_are_not_dropped_by_semantic_search_ranking():
    permit_items = [{
        "name": "건축법상 도로·접도 확인",
        "legal_references": [
            {
                "ref_id": "law.building.article_2_road",
                "law": "건축법",
                "article": "제2조제1항제11호",
                "title": "도로의 정의",
                "jurisdiction": "전국",
                "source_url": "https://www.law.go.kr/법령/건축법",
            },
            {
                "ref_id": "law.building.article_44",
                "law": "건축법",
                "article": "제44조",
                "title": "대지와 도로의 관계",
                "jurisdiction": "전국",
                "source_url": "https://www.law.go.kr/법령/건축법",
            },
        ],
    }]
    semantic = [
        {"law": "농지법", "article": f"제{i}조", "url": "https://example.test"}
        for i in range(10)
    ]

    result = _merge_permit_legal_evidence(permit_items, semantic)

    assert [(item["law"], item["article"]) for item in result[:2]] == [
        ("건축법", "제2조제1항제11호"),
        ("건축법", "제44조"),
    ]
    assert all(item["jurisdiction"] == "전국" for item in result[:2])
