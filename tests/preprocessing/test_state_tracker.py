import pytest

from preprocessing.preprocessing import (
    CardinalityTracker,
    LagState,
    OnlineVariance,
    IOTracker,
)


def test_lag_state_adds_nan_lags_for_first_row_for_zip():
    lag_state = LagState(feature_cols=["us_aqi"], num_past_feats=2)
    row = {"zip": "77002", "us_aqi": "42"}

    lag_state.apply(row, zip_col="zip")

    assert row["us_aqi_past_1"] == "nan"
    assert row["us_aqi_past_2"] == "nan"


def test_lag_state_uses_previous_values_for_later_rows():
    lag_state = LagState(feature_cols=["us_aqi"], num_past_feats=2)

    row1 = {"zip": "77002", "us_aqi": "10"}
    row2 = {"zip": "77002", "us_aqi": "20"}
    row3 = {"zip": "77002", "us_aqi": "30"}

    lag_state.apply(row1, zip_col="zip")
    lag_state.apply(row2, zip_col="zip")
    lag_state.apply(row3, zip_col="zip")

    assert row1["us_aqi_past_1"] == "nan"
    assert row1["us_aqi_past_2"] == "nan"

    assert row2["us_aqi_past_1"] == "10"
    assert row2["us_aqi_past_2"] == "nan"

    assert row3["us_aqi_past_1"] == "20"
    assert row3["us_aqi_past_2"] == "10"


def test_lag_state_keeps_separate_buffers_per_zip_code():
    lag_state = LagState(feature_cols=["us_aqi"], num_past_feats=1)

    row1 = {"zip": "77002", "us_aqi": "10"}
    row2 = {"zip": "77003", "us_aqi": "99"}
    row3 = {"zip": "77002", "us_aqi": "20"}

    lag_state.apply(row1, zip_col="zip")
    lag_state.apply(row2, zip_col="zip")
    lag_state.apply(row3, zip_col="zip")

    assert row1["us_aqi_past_1"] == "nan"
    assert row2["us_aqi_past_1"] == "nan"
    assert row3["us_aqi_past_1"] == "10"


def test_lag_state_tracks_multiple_features_independently():
    lag_state = LagState(feature_cols=["us_aqi", "ozone"], num_past_feats=1)

    row1 = {"zip": "77002", "us_aqi": "10", "ozone": "0.010"}
    row2 = {"zip": "77002", "us_aqi": "20", "ozone": "0.020"}

    lag_state.apply(row1, zip_col="zip")
    lag_state.apply(row2, zip_col="zip")

    assert row2["us_aqi_past_1"] == "10"
    assert row2["ozone_past_1"] == "0.010"


def test_lag_state_stores_nan_when_feature_is_missing_from_row():
    lag_state = LagState(feature_cols=["us_aqi"], num_past_feats=1)

    row1 = {"zip": "77002"}
    row2 = {"zip": "77002", "us_aqi": "50"}

    lag_state.apply(row1, zip_col="zip")
    lag_state.apply(row2, zip_col="zip")

    assert row1["us_aqi_past_1"] == "nan"
    assert row2["us_aqi_past_1"] == "nan"


def test_lag_state_does_nothing_when_num_past_feats_is_zero():
    lag_state = LagState(feature_cols=["us_aqi"], num_past_feats=0)
    row = {"zip": "77002", "us_aqi": "42"}

    lag_state.apply(row, zip_col="zip")

    assert row == {"zip": "77002", "us_aqi": "42"}


def test_lag_state_does_nothing_when_no_feature_columns_are_configured():
    lag_state = LagState(feature_cols=[], num_past_feats=2)
    row = {"zip": "77002", "us_aqi": "42"}

    lag_state.apply(row, zip_col="zip")

    assert row == {"zip": "77002", "us_aqi": "42"}


def test_online_variance_tracks_normalized_variance_for_numeric_column():
    stats = OnlineVariance(excluded_columns=[])

    stats.update_row({"value": "1"})
    stats.update_row({"value": "2"})
    stats.update_row({"value": "3"})

    variances = stats.normalized_variances()

    assert variances["value"] == pytest.approx(1 / 6)


def test_online_variance_returns_zero_for_constant_numeric_column():
    stats = OnlineVariance(excluded_columns=[])

    stats.update_row({"constant": "5"})
    stats.update_row({"constant": "5"})
    stats.update_row({"constant": "5"})

    variances = stats.normalized_variances()

    assert variances["constant"] == pytest.approx(0.0)


def test_online_variance_ignores_excluded_columns():
    stats = OnlineVariance(excluded_columns=["zip", "time"])

    stats.update_row({
        "zip": "77002",
        "time": "2024-01-01 00:00:00",
        "value": "1",
    })
    stats.update_row({
        "zip": "77003",
        "time": "2024-01-01 01:00:00",
        "value": "3",
    })

    variances = stats.normalized_variances()

    assert "zip" not in variances
    assert "time" not in variances
    assert "value" in variances


def test_online_variance_ignores_none_and_non_numeric_values():
    stats = OnlineVariance(excluded_columns=[])

    stats.update_row({"value": None})
    stats.update_row({"value": "not-a-number"})
    stats.update_row({"value": "1"})
    stats.update_row({"value": "3"})

    variances = stats.normalized_variances()

    # Values actually used are [1, 3].
    # mean = 2, population variance = 1, range = 2,
    # normalized variance = 1 / 4.
    assert variances["value"] == pytest.approx(0.25)


def test_online_variance_returns_empty_dict_when_no_numeric_values_were_seen():
    stats = OnlineVariance(excluded_columns=[])

    stats.update_row({"value": None})
    stats.update_row({"value": "not-a-number"})

    assert stats.normalized_variances() == {}


def test_online_variance_tracks_multiple_numeric_columns_independently():
    stats = OnlineVariance(excluded_columns=[])

    stats.update_row({"a": "1", "b": "10"})
    stats.update_row({"a": "2", "b": "10"})
    stats.update_row({"a": "3", "b": "10"})

    variances = stats.normalized_variances()

    assert variances["a"] == pytest.approx(1 / 6)
    assert variances["b"] == pytest.approx(0.0)


def test_cardinality_tracker_counts_unique_values_per_column():
    tracker = CardinalityTracker()

    tracker.update_row({"zip": "77002", "condition": "clear"})
    tracker.update_row({"zip": "77002", "condition": "cloudy"})
    tracker.update_row({"zip": "77003", "condition": "clear"})

    summary = tracker.summary()

    assert summary["column_cardinality"] == {
        "condition": 2,
        "zip": 2,
    }


def test_cardinality_tracker_normalizes_none_blank_and_whitespace_values():
    tracker = CardinalityTracker()

    tracker.update_row({"value": None})
    tracker.update_row({"value": ""})
    tracker.update_row({"value": "   "})
    tracker.update_row({"value": " nan "})
    tracker.update_row({"value": "real-value"})
    tracker.update_row({"value": " real-value "})

    summary = tracker.summary()

    assert summary["column_cardinality"]["value"] == 2


def test_cardinality_tracker_summary_sorts_column_names():
    tracker = CardinalityTracker()

    tracker.update_row({"z_col": "1", "a_col": "1", "m_col": "1"})

    summary = tracker.summary()

    assert list(summary["column_cardinality"].keys()) == [
        "a_col",
        "m_col",
        "z_col",
    ]
    
    
    
def test_io_tracker_starts_with_zero_counts():
    tracker = IOTracker()

    assert tracker.summary() == {
        "total_read_ops": 0,
        "total_write_ops": 0,
        "read_breakdown": {},
        "write_breakdown": {},
    }


def test_io_tracker_tracks_read_and_write_counts_by_label():
    tracker = IOTracker()

    tracker.add_read("csv_chunk_read")
    tracker.add_read("csv_chunk_read", count=2)
    tracker.add_read("shapefile_read")

    tracker.add_write("sorted_run_write")
    tracker.add_write("sorted_run_write", count=3)
    tracker.add_write("json_write")

    assert tracker.summary() == {
        "total_read_ops": 4,
        "total_write_ops": 5,
        "read_breakdown": {
            "csv_chunk_read": 3,
            "shapefile_read": 1,
        },
        "write_breakdown": {
            "json_write": 1,
            "sorted_run_write": 4,
        },
    }