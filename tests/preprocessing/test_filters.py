import csv
import json

import pytest

from preprocessing.preprocessing import (
    CardinalityTracker,
    IOTracker,
    OnlineVariance,
    apply_low_cardinality_filter_csv,
    apply_variance_filter_csv,
)


# CSV helper for the current pipeline format.
# If/when the preprocessing pipeline moves to Parquet, replace this helper with
# a small pandas DataFrame fixture plus DataFrame.to_parquet/read_parquet checks.
def write_csv(path, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# CSV helper for the current pipeline format.
# If/when the preprocessing pipeline moves to Parquet, replace this helper with
# pandas.read_parquet and assert against the resulting DataFrame columns/records.
def read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_online_variance_computes_normalized_variance_and_skips_bad_values():
    stats = OnlineVariance(excluded_columns=["zip", "time"])

    for row in [
        {"zip": "77002", "time": "2024-01-01 00:00:00", "constant": "5", "varying": "1", "text": "abc"},
        {"zip": "77002", "time": "2024-01-01 01:00:00", "constant": "5", "varying": "2", "text": "def"},
        {"zip": "77002", "time": "2024-01-01 02:00:00", "constant": "5", "varying": "3", "text": None},
    ]:
        stats.update_row(row)

    variances = stats.normalized_variances()

    assert "zip" not in variances
    assert "time" not in variances
    assert "text" not in variances
    assert variances["constant"] == pytest.approx(0.0)
    assert variances["varying"] == pytest.approx(1 / 6)


def test_cardinality_tracker_normalizes_blank_and_none_values():
    tracker = CardinalityTracker()

    tracker.update_row({"zip": "77002", "status": "", "category": "A"})
    tracker.update_row({"zip": "77002", "status": None, "category": "B"})
    tracker.update_row({"zip": "77003", "status": "   ", "category": "B"})

    assert tracker.summary() == {
        "column_cardinality": {
            "category": 2,
            "status": 1,
            "zip": 2,
        }
    }


def test_apply_variance_filter_csv_removes_low_variance_columns_and_writes_report(tmp_path):
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "variance_filtered.csv"
    report_json = tmp_path / "variance_report.json"
    rows = [
        {"zip": "77002", "time": "2024-01-01 00:00:00", "constant": "5", "varying": "1", "label": "A"},
        {"zip": "77002", "time": "2024-01-01 01:00:00", "constant": "5", "varying": "2", "label": "B"},
        {"zip": "77002", "time": "2024-01-01 02:00:00", "constant": "5", "varying": "3", "label": "C"},
    ]
    write_csv(input_csv, rows)

    stats = OnlineVariance(excluded_columns=["zip", "time"])
    for row in rows:
        stats.update_row(row)

    report = apply_variance_filter_csv(
        input_csv=str(input_csv),
        output_csv=str(output_csv),
        report_json=str(report_json),
        variance_stats=stats,
        variance_threshold=0.05,
        exclude_cols=["zip", "time"],
    )

    assert report["removed_columns"] == ["constant"]
    assert report["kept_columns"] == ["zip", "time", "varying", "label"]
    assert report["variances"]["constant"] == pytest.approx(0.0)
    assert report["variances"]["varying"] == pytest.approx(1 / 6)

    assert read_csv(output_csv) == [
        {"zip": "77002", "time": "2024-01-01 00:00:00", "varying": "1", "label": "A"},
        {"zip": "77002", "time": "2024-01-01 01:00:00", "varying": "2", "label": "B"},
        {"zip": "77002", "time": "2024-01-01 02:00:00", "varying": "3", "label": "C"},
    ]
    assert read_json(report_json) == report


def test_apply_variance_filter_csv_keeps_excluded_columns_even_when_marked_low_variance(tmp_path):
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "variance_filtered.csv"
    report_json = tmp_path / "variance_report.json"
    rows = [
        {"zip": "77002", "time": "2024-01-01 00:00:00", "constant": "1", "varying": "1"},
        {"zip": "77002", "time": "2024-01-01 01:00:00", "constant": "1", "varying": "2"},
        {"zip": "77002", "time": "2024-01-01 02:00:00", "constant": "1", "varying": "3"},
    ]
    write_csv(input_csv, rows)

    class FakeVarianceStats:
        def normalized_variances(self):
            return {
                "zip": 0.0,
                "time": 0.0,
                "constant": 0.0,
                "varying": 1.0,
            }

    report = apply_variance_filter_csv(
        input_csv=str(input_csv),
        output_csv=str(output_csv),
        report_json=str(report_json),
        variance_stats=FakeVarianceStats(),
        variance_threshold=0.05,
        exclude_cols=["zip", "time"],
    )

    assert report["kept_columns"] == ["zip", "time", "varying"]
    assert read_csv(output_csv)[0].keys() == {"zip", "time", "varying"}


def test_apply_low_cardinality_filter_csv_removes_low_cardinality_columns_and_writes_report(tmp_path):
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "cardinality_filtered.csv"
    report_json = tmp_path / "cardinality_report.json"
    rows = [
        {"zip": "77002", "time": "2024-01-01 00:00:00", "status": "same", "category": "A", "value": "1"},
        {"zip": "77002", "time": "2024-01-01 01:00:00", "status": "same", "category": "B", "value": "2"},
        {"zip": "77003", "time": "2024-01-01 02:00:00", "status": "same", "category": "B", "value": "3"},
    ]
    write_csv(input_csv, rows)

    tracker = CardinalityTracker()
    for row in rows:
        tracker.update_row(row)

    report = apply_low_cardinality_filter_csv(
        input_csv=str(input_csv),
        output_csv=str(output_csv),
        report_json=str(report_json),
        cardinality_summary=tracker.summary(),
        cardinality_threshold=2,
        exclude_cols=["zip", "time"],
    )

    assert report["removed_columns"] == ["status"]
    assert report["kept_columns"] == ["zip", "time", "category", "value"]
    assert report["column_cardinality"] == {
        "category": 2,
        "status": 1,
        "time": 3,
        "value": 3,
        "zip": 2,
    }
    assert read_csv(output_csv) == [
        {"zip": "77002", "time": "2024-01-01 00:00:00", "category": "A", "value": "1"},
        {"zip": "77002", "time": "2024-01-01 01:00:00", "category": "B", "value": "2"},
        {"zip": "77003", "time": "2024-01-01 02:00:00", "category": "B", "value": "3"},
    ]
    assert read_json(report_json) == report


def test_apply_low_cardinality_filter_csv_uses_strict_less_than_threshold(tmp_path):
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "cardinality_filtered.csv"
    report_json = tmp_path / "cardinality_report.json"
    rows = [
        {"zip": "77002", "binary_flag": "0", "constant": "x"},
        {"zip": "77003", "binary_flag": "1", "constant": "x"},
        {"zip": "77004", "binary_flag": "1", "constant": "x"},
    ]
    write_csv(input_csv, rows)

    report = apply_low_cardinality_filter_csv(
        input_csv=str(input_csv),
        output_csv=str(output_csv),
        report_json=str(report_json),
        cardinality_summary={
            "column_cardinality": {
                "zip": 3,
                "binary_flag": 2,
                "constant": 1,
            }
        },
        cardinality_threshold=2,
        exclude_cols=["zip"],
    )

    assert report["removed_columns"] == ["constant"]
    assert report["kept_columns"] == ["zip", "binary_flag"]
    assert read_csv(output_csv) == [
        {"zip": "77002", "binary_flag": "0"},
        {"zip": "77003", "binary_flag": "1"},
        {"zip": "77004", "binary_flag": "1"},
    ]


def test_filter_functions_record_io_tracker_operations(tmp_path):
    input_csv = tmp_path / "input.csv"
    variance_output_csv = tmp_path / "variance_filtered.csv"
    variance_report_json = tmp_path / "variance_report.json"
    cardinality_output_csv = tmp_path / "cardinality_filtered.csv"
    cardinality_report_json = tmp_path / "cardinality_report.json"
    rows = [
        {"zip": "77002", "constant": "1", "varying": "1"},
        {"zip": "77003", "constant": "1", "varying": "2"},
        {"zip": "77004", "constant": "1", "varying": "3"},
    ]
    write_csv(input_csv, rows)

    variance_stats = OnlineVariance(excluded_columns=["zip"])
    cardinality_tracker = CardinalityTracker()
    for row in rows:
        variance_stats.update_row(row)
        cardinality_tracker.update_row(row)

    io_tracker = IOTracker()

    apply_variance_filter_csv(
        input_csv=str(input_csv),
        output_csv=str(variance_output_csv),
        report_json=str(variance_report_json),
        variance_stats=variance_stats,
        variance_threshold=0.05,
        exclude_cols=["zip"],
        io_tracker=io_tracker,
    )
    apply_low_cardinality_filter_csv(
        input_csv=str(input_csv),
        output_csv=str(cardinality_output_csv),
        report_json=str(cardinality_report_json),
        cardinality_summary=cardinality_tracker.summary(),
        cardinality_threshold=2,
        exclude_cols=["zip"],
        io_tracker=io_tracker,
    )

    summary = io_tracker.summary()

    assert summary["read_breakdown"] == {
        "cardinality_input_read": 1,
        "variance_input_read": 1,
    }
    assert summary["write_breakdown"] == {
        "cardinality_output_write": 1,
        "json_write": 2,
        "variance_output_write": 1,
    }
    assert summary["total_read_ops"] == 2
    assert summary["total_write_ops"] == 4
