from scripts.parse_district_plan_documents import _TableReader, _lot_mentions


def test_extracts_only_address_like_lot_mentions():
    result = _lot_mentions("백천동 산 32, 금호읍 교대리 308-1, 용적률 200% 높이 15m")
    assert [item["label"] for item in result] == ["백천동 산 32", "교대리 308-1"]
    assert all(item["mapping_status"] == "PENDING" for item in result)


def test_preserves_hwp_html_table_cells():
    reader = _TableReader()
    reader.feed("<table><tr><th>획지</th><th>건폐율</th></tr><tr><td>A1</td><td>60%</td></tr></table>")
    assert reader.tables == [[["획지", "건폐율"], ["A1", "60%"]]]
