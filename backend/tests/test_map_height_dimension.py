from app.agents.map_control import _build_dimensions


def test_height_dimension_meets_horizontal_and_vertical_dimension_origin():
    parcel_geometry = {
        "type": "Polygon",
        "coordinates": [[[127.0, 35.0], [127.01, 35.0], [127.01, 35.01], [127.0, 35.01], [127.0, 35.0]]],
    }
    diagnosis = {
        "parcel": {"geometry": parcel_geometry, "area_m2": 22_169},
        "massing": {"mass_height_m": 16.5, "floors": 5, "building_area_m2": 4_434},
    }

    command = _build_dimensions(diagnosis, 127.0085, 35.0085)
    height_segment = next(s for s in command["segments"] if s.get("height_m"))
    horizontal, vertical = command["segments"][:2]
    origin = height_segment["positions"][0]

    assert horizontal["positions"][0] == origin
    assert vertical["positions"][0] == origin
    assert height_segment["positions"][1] == origin
    assert height_segment["label"] == "높이 약 16.5m · 5층"
