import pytest

from preprocessing.preprocessing import (
    detect_column,
    detect_direction_columns,
    parse_csv_list,
    row_key,
    standardize_time_value,
)


def test_parse_csv_list_handles_none_and_empty_values():
    assert parse_csv_list(None) == []
    assert parse_csv_list("") == []
    assert parse_csv_list("   ") == []


def test_parse_csv_list_strips_spaces_and_ignores_empty_parts():
    assert parse_csv_list("city,state") == ["city", "state"]
    assert parse_csv_list(" city, state , zip ") == ["city", "state", "zip"]
    assert parse_csv_list("city,,state,") == ["city", "state"]


def test_detect_column_prefers_exact_case_insensitive_match():
    columns = ["ZIP", "Time", "wind_direction_10m"]

    assert detect_column(columns, ["zip"]) == "ZIP"
    assert detect_column(columns, ["time"]) == "Time"


def test_detect_column_falls_back_to_substring_match():
    columns = ["ZCTA5CE20", "weather_time", "wind_direction_10m"]

    assert detect_column(columns, ["zcta"]) == "ZCTA5CE20"
    assert detect_column(columns, ["direction"]) == "wind_direction_10m"


def test_detect_column_returns_none_when_no_match_exists():
    columns = ["zip", "time", "temperature"]

    assert detect_column(columns, ["humidity"]) is None


def test_detect_column_respects_candidate_priority_for_exact_matches():
    columns = ["GEOID20", "ZCTA5CE20", "zip"]

    assert detect_column(columns, ["zip", "GEOID20"]) == "zip"
    assert detect_column(columns, ["GEOID20", "zip"]) == "GEOID20"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-01-02T03:04:05", "2024-01-02 03:04:05"),
        ("2024-01-02 03:04:05", "2024-01-02 03:04:05"),
        ("2024-01-02T03:04:05Z", "2024-01-02 03:04:05"),
        ("2024-01-02 03:04:05-06:00", "2024-01-02 03:04:05"),
        (" 2024-01-02T03:04:05+00:00 ", "2024-01-02 03:04:05"),
    ],
)
def test_standardize_time_value_normalizes_valid_timestamps(raw, expected):
    assert standardize_time_value(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a timestamp", None])
def test_standardize_time_value_returns_empty_string_for_invalid_values(raw):
    assert standardize_time_value(raw) == ""


def test_row_key_returns_string_tuple_in_requested_column_order():
    row = {
        "zip": 77002,
        "time": "2024-01-01 00:00:00",
        "value": 42,
    }

    assert row_key(row, ["zip", "time"]) == ("77002", "2024-01-01 00:00:00")
    assert row_key(row, ["time", "zip"]) == ("2024-01-01 00:00:00", "77002")


def test_row_key_raises_key_error_when_required_key_is_missing():
    row = {"zip": "77002"}

    with pytest.raises(KeyError):
        row_key(row, ["zip", "time"])


def test_detect_direction_columns_auto_detects_direction_like_names():
    columns = [
        "zip",
        "time",
        "wind_direction_10m",
        "wind_dir_100m",
        "winddirection_surface",
        "temperature",
    ]

    assert detect_direction_columns(columns, explicit=[], auto_detect=True) == [
        "wind_direction_10m",
        "wind_dir_100m",
        "winddirection_surface",
    ]


def test_detect_direction_columns_adds_explicit_columns_after_auto_detected_columns():
    columns = [
        "zip",
        "time",
        "wind_direction_10m",
        "temperature",
        "custom_angle",
    ]

    assert detect_direction_columns(
        columns,
        explicit=["custom_angle"],
        auto_detect=True,
    ) == [
        "wind_direction_10m",
        "custom_angle",
    ]


def test_detect_direction_columns_avoids_duplicates():
    columns = ["zip", "time", "wind_direction_10m"]

    assert detect_direction_columns(
        columns,
        explicit=["wind_direction_10m"],
        auto_detect=True,
    ) == ["wind_direction_10m"]


def test_detect_direction_columns_can_disable_auto_detection():
    columns = [
        "zip",
        "time",
        "wind_direction_10m",
        "custom_angle",
    ]

    assert detect_direction_columns(
        columns,
        explicit=["custom_angle"],
        auto_detect=False,
    ) == ["custom_angle"]


def test_detect_direction_columns_ignores_explicit_columns_not_in_input_columns():
    columns = ["zip", "time", "wind_direction_10m"]

    assert detect_direction_columns(
        columns,
        explicit=["missing_direction_col"],
        auto_detect=False,
    ) == []
    
    
    
def test_detect_column_substring_match_uses_column_order_before_candidate_order():
    columns = ["weather_time", "air_quality_time"]

    assert detect_column(columns, ["air_quality", "weather"]) == "weather_time"