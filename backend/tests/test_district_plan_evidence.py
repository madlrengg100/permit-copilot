from app.tools.district_plan import evidence_for


def test_eumseong_district_plan_has_clickable_notice_source():
    items = evidence_for("충청북도 음성군", ["충북 혁신도시 지구단위계획구역"])
    assert items
    assert items[0]["notice_no"] == "음성군 고시 제2023-72호"
    assert items[0]["url"].startswith("https://www.eum.go.kr/")
    assert [doc["label"] for doc in items[0]["documents"]] == [
        "고시문", "군관리계획 총괄도", "지구단위계획 결정(변경)도", "지구단위계획 시행지침"
    ]
    assert all(doc["url"].startswith("https://www.eum.go.kr/") for doc in items[0]["documents"])


def test_district_plan_source_is_not_added_to_ordinary_parcel():
    assert evidence_for("충청북도 음성군", ["준보전산지"]) == []


def test_generic_district_plan_does_not_attach_an_unrelated_eumseong_plan():
    assert evidence_for(
        "충청북도 음성군",
        ["지구단위계획구역"],
        address="충청북도 음성군 대소면 삼정리 10",
    ) == []


def test_eumseong_plan_is_selected_by_its_name_or_notice_lot():
    by_name = evidence_for("충청북도 음성군", ["음성금석지구 지구단위계획구역"])
    assert [item["plan_name"] for item in by_name] == ["음성금석지구"]

    by_address = evidence_for(
        "충청북도 음성군",
        ["지구단위계획구역"],
        address="충청북도 음성군 생극면 신양리 445-3",
    )
    assert [item["plan_name"] for item in by_address] == ["생극신양지구"]
