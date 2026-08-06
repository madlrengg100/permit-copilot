from app.tools.landuse import _parse_landuse_payload


def test_landuse_parser_accepts_double_encoded_json():
    result = _parse_landuse_payload(
        '{"landUses":{"resultCode":"OK","field":'
        '[{"prposAreaDstrcCodeNm":"지구단위계획구역",'
        '"prposAreaDstrcCode":"UQQ100","cnflcAt":"1","cnflcAtNm":"포함"}]}}'
    )
    assert result["status"] == "AVAILABLE"
    assert result["active_records"][0]["name"] == "지구단위계획구역"


def test_landuse_parser_does_not_crash_on_plain_string():
    result = _parse_landuse_payload("temporarily unavailable")
    assert result["status"] == "UNAVAILABLE"
    assert result["active_records"] == []


def test_landuse_parser_skips_non_object_fields():
    result = _parse_landuse_payload({
        "landUses": {"resultCode": "OK", "field": ["bad row", None]}
    })
    assert result["status"] == "AVAILABLE"
    assert result["records"] == []
