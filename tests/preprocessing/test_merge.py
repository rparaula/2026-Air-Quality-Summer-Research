import pytest


from preprocessing.preprocessing import (
    build_output_fieldnames,
    merge_rows_full_outer,
)


STANDARD_ENGINEERED_COLUMNS = [
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
    "road_impact_score",
    "facility_impact_score",
]


def test_merge_rows_full_outer_combines_matching_left_and_right_rows():
    key = ("77002", "2024-01-01 00:00:00")
    left_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "us_aqi": "42",
        "ozone": "0.031",
    }
    right_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "temperature": "85",
        "humidity": "60",
    }

    out = merge_rows_full_outer(
        key=key,
        left_row=left_row,
        right_row=right_row,
        key_columns=["zip", "time"],
        left_columns=["zip", "time", "us_aqi", "ozone"],
        right_columns=["zip", "time", "temperature", "humidity"],
    )

    assert out == {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "us_aqi": "42",
        "ozone": "0.031",
        "temperature": "85",
        "humidity": "60",
    }


def test_merge_rows_full_outer_fills_right_columns_with_nan_for_left_only_row():
    key = ("77002", "2024-01-01 00:00:00")
    left_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "us_aqi": "42",
    }

    out = merge_rows_full_outer(
        key=key,
        left_row=left_row,
        right_row=None,
        key_columns=["zip", "time"],
        left_columns=["zip", "time", "us_aqi"],
        right_columns=["zip", "time", "temperature", "humidity"],
    )

    assert out == {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "us_aqi": "42",
        "temperature": "nan",
        "humidity": "nan",
    }


def test_merge_rows_full_outer_fills_left_columns_with_nan_for_right_only_row():
    key = ("77002", "2024-01-01 00:00:00")
    right_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "temperature": "85",
    }

    out = merge_rows_full_outer(
        key=key,
        left_row=None,
        right_row=right_row,
        key_columns=["zip", "time"],
        left_columns=["zip", "time", "us_aqi", "ozone"],
        right_columns=["zip", "time", "temperature"],
    )

    assert out == {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "us_aqi": "nan",
        "ozone": "nan",
        "temperature": "85",
    }


def test_merge_rows_full_outer_uses_key_tuple_for_key_columns():
    key = ("77002", "2024-01-01 00:00:00")
    left_row = {
        "zip": "WRONG_ZIP",
        "time": "WRONG_TIME",
        "us_aqi": "42",
    }
    right_row = {
        "zip": "ALSO_WRONG_ZIP",
        "time": "ALSO_WRONG_TIME",
        "temperature": "85",
    }

    out = merge_rows_full_outer(
        key=key,
        left_row=left_row,
        right_row=right_row,
        key_columns=["zip", "time"],
        left_columns=["zip", "time", "us_aqi"],
        right_columns=["zip", "time", "temperature"],
    )

    assert out["zip"] == "77002"
    assert out["time"] == "2024-01-01 00:00:00"
    assert out["us_aqi"] == "42"
    assert out["temperature"] == "85"


def test_build_output_fieldnames_places_keys_first_then_left_and_right_non_keys():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "us_aqi", "ozone"],
        right_columns=["zip", "time", "temperature", "humidity"],
        key_columns=["zip", "time"],
        direction_columns=[],
        drop_original_direction_columns=False,
        lag_feature_cols=[],
        num_past_feats=0,
    )

    assert fieldnames[:6] == [
        "zip",
        "time",
        "us_aqi",
        "ozone",
        "temperature",
        "humidity",
    ]

    for col in STANDARD_ENGINEERED_COLUMNS:
        assert col in fieldnames


def test_build_output_fieldnames_does_not_duplicate_shared_non_key_columns():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "latitude", "longitude", "us_aqi"],
        right_columns=["zip", "time", "latitude", "longitude", "temperature"],
        key_columns=["zip", "time"],
        direction_columns=[],
        drop_original_direction_columns=False,
        lag_feature_cols=[],
        num_past_feats=0,
    )

    assert fieldnames.count("latitude") == 1
    assert fieldnames.count("longitude") == 1
    assert fieldnames[:7] == [
        "zip",
        "time",
        "latitude",
        "longitude",
        "us_aqi",
        "temperature",
        "month",
    ]


def test_build_output_fieldnames_keeps_original_direction_column_when_requested():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "wind_direction_10m", "us_aqi"],
        right_columns=["zip", "time", "temperature"],
        key_columns=["zip", "time"],
        direction_columns=["wind_direction_10m"],
        drop_original_direction_columns=False,
        lag_feature_cols=[],
        num_past_feats=0,
    )

    assert "wind_direction_10m" in fieldnames
    assert "wind_direction_10m_sin" in fieldnames
    assert "wind_direction_10m_cos" in fieldnames
    assert fieldnames.index("wind_direction_10m") < fieldnames.index("wind_direction_10m_sin")
    assert fieldnames.index("wind_direction_10m") < fieldnames.index("wind_direction_10m_cos")


def test_build_output_fieldnames_drops_original_direction_column_when_requested():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "wind_direction_10m", "us_aqi"],
        right_columns=["zip", "time", "temperature"],
        key_columns=["zip", "time"],
        direction_columns=["wind_direction_10m"],
        drop_original_direction_columns=True,
        lag_feature_cols=[],
        num_past_feats=0,
    )

    assert "wind_direction_10m" not in fieldnames
    assert "wind_direction_10m_sin" in fieldnames
    assert "wind_direction_10m_cos" in fieldnames


def test_build_output_fieldnames_adds_lag_feature_columns_at_the_end():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "us_aqi", "ozone"],
        right_columns=["zip", "time", "temperature"],
        key_columns=["zip", "time"],
        direction_columns=[],
        drop_original_direction_columns=False,
        lag_feature_cols=["us_aqi", "ozone"],
        num_past_feats=2,
    )

    assert fieldnames[-4:] == [
        "us_aqi_past_1",
        "us_aqi_past_2",
        "ozone_past_1",
        "ozone_past_2",
    ]


def test_build_output_fieldnames_adds_no_lag_columns_when_num_past_feats_is_zero():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "us_aqi"],
        right_columns=["zip", "time", "temperature"],
        key_columns=["zip", "time"],
        direction_columns=[],
        drop_original_direction_columns=False,
        lag_feature_cols=["us_aqi"],
        num_past_feats=0,
    )

    assert "us_aqi_past_1" not in fieldnames


def test_build_output_fieldnames_does_not_duplicate_existing_engineered_columns():
    fieldnames = build_output_fieldnames(
        left_columns=["zip", "time", "month", "road_impact_score", "us_aqi"],
        right_columns=["zip", "time", "temperature"],
        key_columns=["zip", "time"],
        direction_columns=[],
        drop_original_direction_columns=False,
        lag_feature_cols=[],
        num_past_feats=0,
    )

    assert fieldnames.count("month") == 1
    assert fieldnames.count("road_impact_score") == 1
    assert fieldnames.count("facility_impact_score") == 1
    
def test_merge_rows_full_outer_right_non_key_value_overwrites_left_on_collision():
    key = ("77002", "2024-01-01 00:00:00")
    left_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "latitude": "left-lat",
        "us_aqi": "42",
    }
    right_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "latitude": "right-lat",
        "temperature": "85",
    }

    out = merge_rows_full_outer(
        key=key,
        left_row=left_row,
        right_row=right_row,
        key_columns=["zip", "time"],
        left_columns=["zip", "time", "latitude", "us_aqi"],
        right_columns=["zip", "time", "latitude", "temperature"],
    )

    assert out["latitude"] == "right-lat"
    assert out["us_aqi"] == "42"
    assert out["temperature"] == "85"
    


def test_merge_rows_full_outer_raises_key_error_when_left_row_is_missing_declared_column():
    key = ("77002", "2024-01-01 00:00:00")
    left_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
    }
    right_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "temperature": "85",
    }

    with pytest.raises(KeyError):
        merge_rows_full_outer(
            key=key,
            left_row=left_row,
            right_row=right_row,
            key_columns=["zip", "time"],
            left_columns=["zip", "time", "us_aqi"],
            right_columns=["zip", "time", "temperature"],
        )


def test_merge_rows_full_outer_raises_key_error_when_right_row_is_missing_declared_column():
    key = ("77002", "2024-01-01 00:00:00")
    left_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "us_aqi": "42",
    }
    right_row = {
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
    }

    with pytest.raises(KeyError):
        merge_rows_full_outer(
            key=key,
            left_row=left_row,
            right_row=right_row,
            key_columns=["zip", "time"],
            left_columns=["zip", "time", "us_aqi"],
            right_columns=["zip", "time", "temperature"],
        )