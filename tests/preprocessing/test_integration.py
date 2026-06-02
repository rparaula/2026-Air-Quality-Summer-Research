import csv

import pytest

from preprocessing.preprocessing import (
    CardinalityTracker,
    IOTracker,
    OnlineVariance,
    RunReader,
    SortedRunStream,
    make_sorted_runs_collect_keys,
    stream_merge_join_and_transform,
)


class FakeStream:
    def __init__(self, rows):
        self.rows = list(rows)

    def pop(self):
        if not self.rows:
            return None
        return self.rows.pop(0)


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def wind_fields():
    return {
        "wind_speed_10m": "10",
        "wind_direction_10m": "90",
        "wind_speed_100m": "20",
        "wind_direction_100m": "180",
    }


def test_make_sorted_runs_collect_keys_and_sorted_run_stream_order_rows(tmp_path):
    input_csv = tmp_path / "input.csv"
    temp_dir = tmp_path / "runs"
    temp_dir.mkdir()

    write_csv(
        input_csv,
        fieldnames=["zip", "time", "value", "drop_me"],
        rows=[
            {
                "zip": "77003",
                "time": "2024-01-01 02:00:00",
                "value": "third",
                "drop_me": "x",
            },
            {
                "zip": "77002",
                "time": "2024-01-01 01:00:00",
                "value": "second",
                "drop_me": "x",
            },
            {
                "zip": "77002",
                "time": "2024-01-01 00:00:00",
                "value": "first",
                "drop_me": "x",
            },
        ],
    )

    unique_times = set()
    unique_zips = set()
    io_tracker = IOTracker()

    run_paths, output_columns = make_sorted_runs_collect_keys(
        input_csv=str(input_csv),
        key_columns=["zip", "time"],
        chunk_rows=2,
        temp_dir=str(temp_dir),
        run_prefix="test",
        unique_times=unique_times,
        unique_zips=unique_zips,
        time_column="time",
        zip_column="zip",
        drop_columns=["drop_me"],
        io_tracker=io_tracker,
    )

    assert len(run_paths) == 2
    assert output_columns == ["zip", "time", "value"]
    assert unique_times == {
        "2024-01-01 00:00:00",
        "2024-01-01 01:00:00",
        "2024-01-01 02:00:00",
    }
    assert unique_zips == {"77002", "77003"}

    stream = SortedRunStream(run_paths, key_columns=["zip", "time"], io_tracker=io_tracker)
    try:
        streamed_rows = []
        while True:
            row = stream.pop()
            if row is None:
                break
            streamed_rows.append(row)
    finally:
        stream.close()

    assert [(row["zip"], row["time"], row["value"]) for row in streamed_rows] == [
        ("77002", "2024-01-01 00:00:00", "first"),
        ("77002", "2024-01-01 01:00:00", "second"),
        ("77003", "2024-01-01 02:00:00", "third"),
    ]

    io_summary = io_tracker.summary()
    assert io_summary["read_breakdown"]["csv_chunk_read"] == 2
    assert io_summary["write_breakdown"]["sorted_run_write"] == 2
    assert io_summary["read_breakdown"]["sorted_run_read"] == 2


def test_make_sorted_runs_collect_keys_raises_when_required_key_column_is_missing(tmp_path):
    input_csv = tmp_path / "input.csv"
    temp_dir = tmp_path / "runs"
    temp_dir.mkdir()

    write_csv(
        input_csv,
        fieldnames=["zip", "value"],
        rows=[
            {"zip": "77002", "value": "10"},
        ],
    )

    with pytest.raises(ValueError, match="Missing sort key columns"):
        make_sorted_runs_collect_keys(
            input_csv=str(input_csv),
            key_columns=["zip", "time"],
            chunk_rows=2,
            temp_dir=str(temp_dir),
            run_prefix="test",
            unique_times=set(),
            unique_zips=set(),
            time_column="time",
            zip_column="zip",
        )


def test_make_sorted_runs_collect_keys_raises_when_required_column_is_dropped(tmp_path):
    input_csv = tmp_path / "input.csv"
    temp_dir = tmp_path / "runs"
    temp_dir.mkdir()

    write_csv(
        input_csv,
        fieldnames=["zip", "time", "value"],
        rows=[
            {
                "zip": "77002",
                "time": "2024-01-01 00:00:00",
                "value": "10",
            },
        ],
    )

    with pytest.raises(ValueError, match="Cannot drop required columns"):
        make_sorted_runs_collect_keys(
            input_csv=str(input_csv),
            key_columns=["zip", "time"],
            chunk_rows=2,
            temp_dir=str(temp_dir),
            run_prefix="test",
            unique_times=set(),
            unique_zips=set(),
            time_column="time",
            zip_column="zip",
            drop_columns=["zip"],
        )


def test_stream_merge_join_and_transform_writes_outer_merge_with_features_and_metadata(tmp_path):
    output_csv = tmp_path / "merged.csv"

    left_stream = FakeStream(
        [
            {
                "zip": "77002",
                "time": "2024-01-01T00:00:00Z",
                "us_aqi": "10",
            },
            {
                "zip": "77002",
                "time": "2024-01-01T01:00:00Z",
                "us_aqi": "20",
            },
        ]
    )
    right_stream = FakeStream(
        [
            {
                "zip": "77002",
                "time": "2024-01-01T00:00:00Z",
                "temperature": "80",
                **wind_fields(),
            },
            {
                "zip": "77003",
                "time": "2024-01-01T00:00:00Z",
                "temperature": "75",
                **wind_fields(),
            },
        ]
    )

    spatial_lookup = {
        "77002": {
            "road_pairs": [
                {"dir_x": 0.0, "dir_y": 1.0, "decay": 0.5},
            ],
            "facility_pairs": [
                {
                    "dir_x": 0.0,
                    "dir_y": 1.0,
                    "decay": 0.4,
                    "severity": 0.25,
                },
            ],
        }
    }

    variance_stats = OnlineVariance(excluded_columns=["zip", "time"])
    cardinality_tracker = CardinalityTracker()
    io_tracker = IOTracker()

    meta = stream_merge_join_and_transform(
        left_stream=left_stream,
        right_stream=right_stream,
        output_csv=str(output_csv),
        key_columns=["zip", "time"],
        time_column="time",
        zip_column="zip",
        left_columns=["zip", "time", "us_aqi"],
        right_columns=[
            "zip",
            "time",
            "temperature",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_speed_100m",
            "wind_direction_100m",
        ],
        merge_how="outer",
        spatial_lookup=spatial_lookup,
        direction_columns=["wind_direction_10m", "wind_direction_100m"],
        drop_original_direction_columns=True,
        lag_feature_cols=["us_aqi"],
        num_past_feats=1,
        facility_wind_mode="10m",
        facility_wind_blend_100m=0.0,
        road_wind_mode="10m",
        road_wind_blend_100m=0.0,
        variance_stats=variance_stats,
        cardinality_tracker=cardinality_tracker,
        io_tracker=io_tracker,
    )

    rows = read_csv_rows(output_csv)

    assert meta["rows_written"] == 3
    assert meta["matched_rows"] == 1
    assert meta["left_only_rows"] == 1
    assert meta["right_only_rows"] == 1
    assert meta["output_csv"] == str(output_csv)

    assert len(rows) == 3

    matched = rows[0]
    left_only = rows[1]
    right_only = rows[2]

    assert matched["zip"] == "77002"
    assert matched["time"] == "2024-01-01 00:00:00"
    assert matched["us_aqi"] == "10"
    assert matched["temperature"] == "80"

    assert "wind_direction_10m" not in matched
    assert "wind_direction_10m_sin" in matched
    assert "wind_direction_10m_cos" in matched
    assert "month" in matched
    assert "hour" in matched
    assert "day_of_week" in matched
    assert "road_impact_score" in matched
    assert "facility_impact_score" in matched
    assert "us_aqi_past_1" in matched

    assert matched["us_aqi_past_1"] == "nan"

    assert left_only["zip"] == "77002"
    assert left_only["time"] == "2024-01-01 01:00:00"
    assert left_only["us_aqi"] == "20"
    assert left_only["temperature"] == "nan"
    assert left_only["us_aqi_past_1"] == "10"

    assert right_only["zip"] == "77003"
    assert right_only["time"] == "2024-01-01 00:00:00"
    assert right_only["us_aqi"] == "nan"
    assert right_only["temperature"] == "75"

    assert cardinality_tracker.summary()["column_cardinality"]["zip"] == 2
    assert "us_aqi" in variance_stats.normalized_variances()

    io_summary = io_tracker.summary()
    assert io_summary["write_breakdown"]["final_csv_write"] == 1
    
    
def test_run_reader_exposes_fieldnames_reads_rows_and_returns_none_at_eof(tmp_path):
    input_csv = tmp_path / "run.csv"
    write_csv(
        input_csv,
        fieldnames=["zip", "time", "value"],
        rows=[
            {
                "zip": "77002",
                "time": "2024-01-01 00:00:00",
                "value": "first",
            },
            {
                "zip": "77003",
                "time": "2024-01-01 01:00:00",
                "value": "second",
            },
        ],
    )

    io_tracker = IOTracker()
    reader = RunReader(str(input_csv), io_tracker=io_tracker)

    try:
        assert reader.fieldnames == ["zip", "time", "value"]
        assert reader.pop() == {
            "zip": "77002",
            "time": "2024-01-01 00:00:00",
            "value": "first",
        }
        assert reader.pop() == {
            "zip": "77003",
            "time": "2024-01-01 01:00:00",
            "value": "second",
        }
        assert reader.pop() is None
        assert reader.pop() is None
    finally:
        reader.close()

    assert io_tracker.summary()["read_breakdown"]["sorted_run_read"] == 1
    
    


def test_make_sorted_runs_collect_keys_handles_header_only_csv_as_empty_run(tmp_path):
    input_csv = tmp_path / "header_only.csv"
    temp_dir = tmp_path / "runs"
    temp_dir.mkdir()

    write_csv(
        input_csv,
        fieldnames=["zip", "time", "value", "drop_me"],
        rows=[],
    )

    unique_times = set()
    unique_zips = set()
    io_tracker = IOTracker()

    run_paths, output_columns = make_sorted_runs_collect_keys(
        input_csv=str(input_csv),
        key_columns=["zip", "time"],
        chunk_rows=2,
        temp_dir=str(temp_dir),
        run_prefix="empty",
        unique_times=unique_times,
        unique_zips=unique_zips,
        time_column="time",
        zip_column="zip",
        drop_columns=["drop_me"],
        io_tracker=io_tracker,
    )

    assert len(run_paths) == 1
    assert output_columns == ["zip", "time", "value"]
    assert unique_times == set()
    assert unique_zips == set()

    rows = read_csv_rows(run_paths[0])
    assert rows == []

    io_summary = io_tracker.summary()
    assert io_summary["read_breakdown"]["csv_chunk_read"] == 1
    assert io_summary["write_breakdown"]["sorted_run_write"] == 1
    assert "csv_header_read" not in io_summary["read_breakdown"]
    
    



def run_stream_merge(
    tmp_path,
    left_rows,
    right_rows,
    merge_how,
):
    output_csv = tmp_path / f"merged_{merge_how}.csv"

    meta = stream_merge_join_and_transform(
        left_stream=FakeStream(left_rows),
        right_stream=FakeStream(right_rows),
        output_csv=str(output_csv),
        key_columns=["zip", "time"],
        time_column="time",
        zip_column="zip",
        left_columns=["zip", "time", "us_aqi"],
        right_columns=["zip", "time", "temperature"],
        merge_how=merge_how,
        spatial_lookup={},
        direction_columns=[],
        drop_original_direction_columns=True,
        lag_feature_cols=[],
        num_past_feats=0,
        facility_wind_mode="10m",
        facility_wind_blend_100m=0.0,
        road_wind_mode="10m",
        road_wind_blend_100m=0.0,
        variance_stats=None,
        cardinality_tracker=None,
        io_tracker=None,
    )

    return meta, read_csv_rows(output_csv)



def test_stream_merge_join_and_transform_non_outer_mode_writes_only_matched_rows(tmp_path):
    left_rows = [
        {
            "zip": "77001",
            "time": "2024-01-01 00:00:00",
            "us_aqi": "left-only",
        },
        {
            "zip": "77002",
            "time": "2024-01-01 00:00:00",
            "us_aqi": "matched",
        },
    ]
    right_rows = [
        {
            "zip": "77002",
            "time": "2024-01-01 00:00:00",
            "temperature": "80",
        },
        {
            "zip": "77003",
            "time": "2024-01-01 00:00:00",
            "temperature": "right-only",
        },
    ]

    meta, rows = run_stream_merge(
        tmp_path=tmp_path,
        left_rows=left_rows,
        right_rows=right_rows,
        merge_how="inner",
    )

    assert meta["rows_written"] == 1
    assert meta["matched_rows"] == 1
    assert meta["left_only_rows"] == 0
    assert meta["right_only_rows"] == 0

    assert len(rows) == 1
    assert rows[0]["zip"] == "77002"
    assert rows[0]["us_aqi"] == "matched"
    assert rows[0]["temperature"] == "80"




def test_stream_merge_join_and_transform_non_outer_mode_skips_remaining_left_rows(tmp_path):
    left_rows = [
        {
            "zip": "77002",
            "time": "2024-01-01 00:00:00",
            "us_aqi": "left-only",
        },
    ]
    right_rows = []

    meta, rows = run_stream_merge(
        tmp_path=tmp_path,
        left_rows=left_rows,
        right_rows=right_rows,
        merge_how="inner",
    )

    assert meta["rows_written"] == 0
    assert meta["matched_rows"] == 0
    assert meta["left_only_rows"] == 0
    assert meta["right_only_rows"] == 0
    assert rows == []


def test_stream_merge_join_and_transform_non_outer_mode_skips_remaining_right_rows(tmp_path):
    left_rows = []
    right_rows = [
        {
            "zip": "77002",
            "time": "2024-01-01 00:00:00",
            "temperature": "right-only",
        },
    ]

    meta, rows = run_stream_merge(
        tmp_path=tmp_path,
        left_rows=left_rows,
        right_rows=right_rows,
        merge_how="inner",
    )

    assert meta["rows_written"] == 0
    assert meta["matched_rows"] == 0
    assert meta["left_only_rows"] == 0
    assert meta["right_only_rows"] == 0
    assert rows == []
