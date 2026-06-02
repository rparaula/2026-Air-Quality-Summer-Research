import math

import pytest

from preprocessing.preprocessing import (
    add_direction_features_to_row,
    add_spatial_scores_to_row,
    add_time_features_to_row,
    compute_wind_vector,
    resolve_blend_weight,
)


def wind_row():
    return {
        "wind_speed_10m": "10",
        "wind_direction_10m_cos": "1",
        "wind_direction_10m_sin": "0",
        "wind_speed_100m": "20",
        "wind_direction_100m_cos": "0",
        "wind_direction_100m_sin": "1",
    }


def minimal_spatial_lookup():
    return {
        "77002": {
            "road_pairs": [],
            "facility_pairs": [],
        }
    }

@pytest.mark.parametrize(
    ("mode", "blend_100m", "expected"),
    [
        ("10m", 0.7, 0.0),
        ("100m", 0.7, 1.0),
        ("blend", 0.7, 0.7),
        ("BLEND", 0.25, 0.25),
    ],
)
def test_resolve_blend_weight_returns_expected_weight(mode, blend_100m, expected):
    assert resolve_blend_weight(mode, blend_100m) == pytest.approx(expected)


def test_resolve_blend_weight_raises_for_unsupported_mode():
    with pytest.raises(ValueError, match="Unsupported wind mode"):
        resolve_blend_weight("bad-mode", 0.5)


def test_compute_wind_vector_uses_10m_only_when_mode_is_10m():
    row = wind_row()

    wind_x, wind_y = compute_wind_vector(row, mode="10m", blend_100m=0.7)

    assert wind_x == pytest.approx(10.0)
    assert wind_y == pytest.approx(0.0)


def test_compute_wind_vector_uses_100m_only_when_mode_is_100m():
    row = wind_row()

    wind_x, wind_y = compute_wind_vector(row, mode="100m", blend_100m=0.0)

    assert wind_x == pytest.approx(0.0)
    assert wind_y == pytest.approx(20.0)


def test_compute_wind_vector_blends_10m_and_100m_components():
    row = wind_row()

    wind_x, wind_y = compute_wind_vector(row, mode="blend", blend_100m=0.25)

    # 75% of the 10m vector: 10 * (1, 0)
    # 25% of the 100m vector: 20 * (0, 1)
    assert wind_x == pytest.approx(7.5)
    assert wind_y == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("degrees", "expected_sin", "expected_cos"),
    [
        ("0", 0.0, 1.0),
        ("90", 1.0, 0.0),
        ("180", 0.0, -1.0),
        ("270", -1.0, 0.0),
    ],
)
def test_add_direction_features_to_row_adds_sin_and_cos_features(
    degrees,
    expected_sin,
    expected_cos,
):
    row = {"wind_direction_10m": degrees}

    add_direction_features_to_row(
        row,
        direction_columns=["wind_direction_10m"],
        drop_original=False,
    )

    assert row["wind_direction_10m_sin"] == pytest.approx(expected_sin)
    assert row["wind_direction_10m_cos"] == pytest.approx(expected_cos)
    assert row["wind_direction_10m"] == degrees


def test_add_direction_features_to_row_can_drop_original_column():
    row = {"wind_direction_10m": "90"}

    add_direction_features_to_row(
        row,
        direction_columns=["wind_direction_10m"],
        drop_original=True,
    )

    assert "wind_direction_10m" not in row
    assert row["wind_direction_10m_sin"] == pytest.approx(1.0)
    assert row["wind_direction_10m_cos"] == pytest.approx(0.0)


def test_add_direction_features_to_row_ignores_missing_direction_column():
    row = {"temperature": "85"}

    add_direction_features_to_row(
        row,
        direction_columns=["missing_direction_column"],
        drop_original=True,
    )

    assert row == {"temperature": "85"}


def test_add_time_features_to_row_adds_calendar_and_cyclic_features_for_valid_time():
    row = {"time": "2024-01-06 06:00:00"}  # Saturday

    add_time_features_to_row(row, time_col="time")

    assert row["month"] == 1
    assert row["day"] == 6
    assert row["hour"] == 6
    assert row["day_of_week"] == 5
    assert row["day_of_year"] == 6
    assert row["is_weekend"] == 1

    assert row["month_sin"] == pytest.approx(math.sin(2 * math.pi * 1 / 12))
    assert row["month_cos"] == pytest.approx(math.cos(2 * math.pi * 1 / 12))
    assert row["hour_sin"] == pytest.approx(math.sin(2 * math.pi * 6 / 24))
    assert row["hour_cos"] == pytest.approx(math.cos(2 * math.pi * 6 / 24))
    assert row["day_of_week_sin"] == pytest.approx(math.sin(2 * math.pi * 5 / 7))
    assert row["day_of_week_cos"] == pytest.approx(math.cos(2 * math.pi * 5 / 7))


def test_add_time_features_to_row_marks_weekday_as_not_weekend():
    row = {"time": "2024-01-03 12:00:00"}  # Wednesday

    add_time_features_to_row(row, time_col="time")

    assert row["day_of_week"] == 2
    assert row["is_weekend"] == 0


def test_add_time_features_to_row_adds_nan_features_for_invalid_time():
    row = {"time": "not a timestamp"}

    add_time_features_to_row(row, time_col="time")

    expected_nan_features = [
        "month",
        "month_sin",
        "month_cos",
        "day",
        "hour",
        "hour_sin",
        "hour_cos",
        "day_of_week",
        "day_of_week_sin",
        "day_of_week_cos",
        "day_of_year",
        "is_weekend",
    ]

    for feature in expected_nan_features:
        assert row[feature] == "nan"


def test_add_time_features_to_row_adds_nan_features_when_time_column_is_missing():
    row = {"zip": "77002"}

    add_time_features_to_row(row, time_col="time")

    assert row["month"] == "nan"
    assert row["hour"] == "nan"
    assert row["is_weekend"] == "nan"


def test_add_spatial_scores_to_row_adds_zero_scores_when_zip_is_not_in_lookup():
    row = {
        "zip": "77002",
        **wind_row(),
    }

    add_spatial_scores_to_row(
        row,
        spatial_lookup={},
        zip_col="zip",
        facility_wind_mode="100m",
        facility_wind_blend_100m=1.0,
        road_wind_mode="10m",
        road_wind_blend_100m=0.0,
    )

    assert row["road_impact_score"] == pytest.approx(0.0)
    assert row["facility_impact_score"] == pytest.approx(0.0)


def test_add_spatial_scores_to_row_computes_road_and_facility_scores_from_fake_lookup():
    row = {
        "zip": "77002",
        **wind_row(),
    }
    spatial_lookup = {
        "77002": {
            "road_pairs": [
                {"dir_x": 1.0, "dir_y": 0.0, "decay": 0.5},
                {"dir_x": -1.0, "dir_y": 0.0, "decay": 1.0},
            ],
            "facility_pairs": [
                {"dir_x": 0.0, "dir_y": 1.0, "decay": 0.4, "severity": 0.25},
                {"dir_x": 0.0, "dir_y": -1.0, "decay": 1.0, "severity": 1.0},
            ],
        }
    }

    add_spatial_scores_to_row(
        row,
        spatial_lookup=spatial_lookup,
        zip_col="zip",
        facility_wind_mode="100m",
        facility_wind_blend_100m=1.0,
        road_wind_mode="10m",
        road_wind_blend_100m=0.0,
    )

    # Road score:
    # 10m wind vector = (10, 0)
    # First road pair projection = 10, decay = 0.5 -> 5.0
    # Second road pair projection = -10, clipped to 0 -> 0.0
    assert row["road_impact_score"] == pytest.approx(5.0)

    # Facility score:
    # 100m wind vector = (0, 20)
    # First facility pair projection = 20, severity = 0.25, decay = 0.4 -> 2.0
    # Second facility pair projection = -20, clipped to 0 -> 0.0
    assert row["facility_impact_score"] == pytest.approx(2.0)


def test_add_spatial_scores_to_row_converts_zip_to_string_for_lookup():
    row = {
        "zip": 77002,
        **wind_row(),
    }
    spatial_lookup = {
        "77002": {
            "road_pairs": [
                {"dir_x": 1.0, "dir_y": 0.0, "decay": 1.0},
            ],
            "facility_pairs": [],
        }
    }

    add_spatial_scores_to_row(
        row,
        spatial_lookup=spatial_lookup,
        zip_col="zip",
        facility_wind_mode="100m",
        facility_wind_blend_100m=1.0,
        road_wind_mode="10m",
        road_wind_blend_100m=0.0,
    )

    assert row["road_impact_score"] == pytest.approx(10.0)
    assert row["facility_impact_score"] == pytest.approx(0.0)
    
def test_compute_wind_vector_raises_type_error_when_required_field_is_missing():
    row = wind_row()
    row.pop("wind_speed_10m")

    with pytest.raises(TypeError):
        compute_wind_vector(row, mode="10m", blend_100m=0.0)


def test_compute_wind_vector_raises_value_error_for_non_numeric_wind_field():
    row = wind_row()
    row["wind_speed_10m"] = "not-a-number"

    with pytest.raises(ValueError):
        compute_wind_vector(row, mode="10m", blend_100m=0.0)


def test_add_direction_features_to_row_raises_value_error_for_non_numeric_direction():
    row = {"wind_direction_10m": "north"}

    with pytest.raises(ValueError):
        add_direction_features_to_row(
            row,
            direction_columns=["wind_direction_10m"],
            drop_original=False,
        )
        



def test_add_spatial_scores_to_row_raises_value_error_for_bad_road_wind_mode():
    row = {
        "zip": "77002",
        **wind_row(),
    }

    with pytest.raises(ValueError, match="Unsupported wind mode"):
        add_spatial_scores_to_row(
            row,
            spatial_lookup=minimal_spatial_lookup(),
            zip_col="zip",
            facility_wind_mode="100m",
            facility_wind_blend_100m=1.0,
            road_wind_mode="bad-mode",
            road_wind_blend_100m=0.0,
        )


def test_add_spatial_scores_to_row_raises_value_error_for_bad_facility_wind_mode():
    row = {
        "zip": "77002",
        **wind_row(),
    }

    with pytest.raises(ValueError, match="Unsupported wind mode"):
        add_spatial_scores_to_row(
            row,
            spatial_lookup=minimal_spatial_lookup(),
            zip_col="zip",
            facility_wind_mode="bad-mode",
            facility_wind_blend_100m=1.0,
            road_wind_mode="10m",
            road_wind_blend_100m=0.0,
        )



def test_add_direction_features_to_row_marks_blank_direction_as_nan():
    row = {"wind_direction_10m": ""}

    add_direction_features_to_row(
        row,
        direction_columns=["wind_direction_10m"],
        drop_original=False,
    )

    assert row["wind_direction_10m_sin"] == "nan"
    assert row["wind_direction_10m_cos"] == "nan"
    assert row["wind_direction_10m"] == ""
    
    


def test_add_direction_features_to_row_marks_none_direction_as_nan():
    row = {"wind_direction_10m": None}

    add_direction_features_to_row(
        row,
        direction_columns=["wind_direction_10m"],
        drop_original=False,
    )

    assert row["wind_direction_10m_sin"] == "nan"
    assert row["wind_direction_10m_cos"] == "nan"
    assert row["wind_direction_10m"] is None