import csv
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "preprocessing" / "merge_data_into_master_file.py"
SPEC = importlib.util.spec_from_file_location("merge_data_into_master_file", MODULE_PATH)
merge_master = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(merge_master)


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    # PARQUET MIGRATION NOTE:
    # When this preprocessing script is converted to Parquet, replace this helper
    # with a pandas/pyarrow helper such as df.to_parquet(path, index=False).
    # The test assertions should still validate the same logical rows and columns.
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def read_csv(path: Path) -> list[list[str]]:
    # PARQUET MIGRATION NOTE:
    # For Parquet outputs, replace this with pandas.read_parquet(path), then compare
    # the DataFrame columns and records instead of raw CSV rows.
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.reader(file))


def empty_state() -> dict:
    return {
        "air_quality": {"files": [], "sort_keys": {}},
        "weather": {"files": [], "sort_keys": {}},
    }


def test_load_state_returns_default_when_missing(tmp_path):
    state_path = tmp_path / "merge_state.json"

    result = merge_master.load_state(state_path)

    assert result == empty_state()


def test_load_state_reads_existing_json(tmp_path):
    state_path = tmp_path / "merge_state.json"
    expected_state = empty_state()
    expected_state["air_quality"]["files"].append("/fake/path/file.csv")

    state_path.write_text(json.dumps(expected_state), encoding="utf-8")

    result = merge_master.load_state(state_path)

    assert result == expected_state


def test_save_state_writes_json(tmp_path):
    state_path = tmp_path / "merge_state.json"
    state = empty_state()
    state["air_quality"]["files"].append("example.csv")

    merge_master.save_state(state_path, state)

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved == state


def test_classify_file_identifies_air_quality_weather_and_ignores_other_files(tmp_path):
    air_file = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    weather_file = tmp_path / "march_1stweek_weather_hourly_20260315_120000.csv"
    unrelated_csv = tmp_path / "notes.csv"
    non_csv = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.txt"

    for path in [air_file, weather_file, unrelated_csv, non_csv]:
        path.write_text("header\n", encoding="utf-8")

    assert merge_master.classify_file(air_file) == "air_quality"
    assert merge_master.classify_file(weather_file) == "weather"
    assert merge_master.classify_file(unrelated_csv) is None
    assert merge_master.classify_file(non_csv) is None


def test_extract_sort_key_uses_month_day_and_filename_tiebreaker():
    result = merge_master.extract_sort_key(
        "march_10_air_quality_hourly_20260401_120609.csv"
    )

    assert result == (3, 10, "march_10_air_quality_hourly_20260401_120609.csv")


def test_extract_sort_key_rejects_unexpected_filename():
    with pytest.raises(ValueError, match="Unknown month token"):
        merge_master.extract_sort_key("bad_1stweek_air_quality_hourly.csv")

    with pytest.raises(ValueError, match="Could not extract numeric date"):
        merge_master.extract_sort_key("march_week_air_quality_hourly.csv")


def test_find_files_returns_sorted_air_quality_and_weather_files(tmp_path):
    later_air = tmp_path / "march_2ndweek_air_quality_hourly_20260322_120000.csv"
    earlier_air = tmp_path / "feb_4thweek_air_quality_hourly_20260312_120000.csv"
    weather = tmp_path / "march_1stweek_weather_hourly_20260315_120000.csv"
    ignored = tmp_path / "random.csv"

    for path in [later_air, earlier_air, weather, ignored]:
        path.write_text("zip,time,value\n", encoding="utf-8")

    found = merge_master.find_files(tmp_path)

    assert found["air_quality"] == [earlier_air, later_air]
    assert found["weather"] == [weather]


def test_append_csvs_writes_one_header_and_all_rows(tmp_path):
    # PARQUET MIGRATION NOTE:
    # This test currently checks raw CSV header behavior. For Parquet, there is no
    # repeated file header row, so change this to assert that concatenated Parquet
    # output has the expected columns and records.
    header = ["zip", "time", "us_aqi"]
    file_one = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    file_two = tmp_path / "march_2ndweek_air_quality_hourly_20260322_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"

    write_csv(file_one, header, [["77002", "2026-03-15 00:00:00", 42]])
    write_csv(file_two, header, [["77003", "2026-03-22 00:00:00", 55]])

    result_header = merge_master.append_csvs([file_one, file_two], output_csv, None)

    assert result_header == header
    assert read_csv(output_csv) == [
        header,
        ["77002", "2026-03-15 00:00:00", "42"],
        ["77003", "2026-03-22 00:00:00", "55"],
    ]


def test_append_csvs_rejects_header_mismatch(tmp_path):
    # PARQUET MIGRATION NOTE:
    # Keep this logical check after migration, but compare Parquet/DataFrame schemas
    # instead of CSV header rows.
    good_file = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    bad_file = tmp_path / "march_2ndweek_air_quality_hourly_20260322_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"

    write_csv(good_file, ["zip", "time", "us_aqi"], [["77002", "2026-03-15", 42]])
    write_csv(bad_file, ["zip", "time", "pm2_5"], [["77002", "2026-03-22", 8.5]])

    with pytest.raises(ValueError, match="Header mismatch"):
        merge_master.append_csvs([good_file, bad_file], output_csv, None)


def test_process_category_builds_master_file_and_updates_state(tmp_path):
    header = ["zip", "time", "us_aqi"]
    air_file = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"
    state = empty_state()

    write_csv(air_file, header, [["77002", "2026-03-15 00:00:00", 42]])

    merge_master.process_category("air_quality", [air_file], output_csv, state)

    assert read_csv(output_csv) == [
        header,
        ["77002", "2026-03-15 00:00:00", "42"],
    ]
    assert state["air_quality"]["files"] == [str(air_file.resolve())]
    assert state["air_quality"]["sort_keys"][str(air_file.resolve())] == [
        3,
        1,
        air_file.name,
    ]


def test_process_category_does_not_duplicate_existing_files_on_rerun(tmp_path):
    # PARQUET MIGRATION NOTE:
    # This duplicate-protection behavior should remain exactly the same for Parquet.
    # Only the fixture writer and output reader should change.
    header = ["zip", "time", "us_aqi"]
    air_file = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"
    state = empty_state()

    write_csv(air_file, header, [["77002", "2026-03-15 00:00:00", 42]])

    merge_master.process_category("air_quality", [air_file], output_csv, state)
    merge_master.process_category("air_quality", [air_file], output_csv, state)

    assert read_csv(output_csv) == [
        header,
        ["77002", "2026-03-15 00:00:00", "42"],
    ]
    assert len(state["air_quality"]["files"]) == 1


def test_process_category_appends_new_later_file(tmp_path):
    header = ["zip", "time", "us_aqi"]
    first_file = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    second_file = tmp_path / "march_2ndweek_air_quality_hourly_20260322_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"
    state = empty_state()

    write_csv(first_file, header, [["77002", "2026-03-15 00:00:00", 42]])
    write_csv(second_file, header, [["77003", "2026-03-22 00:00:00", 55]])

    merge_master.process_category("air_quality", [first_file], output_csv, state)
    merge_master.process_category(
        "air_quality", [first_file, second_file], output_csv, state
    )

    assert read_csv(output_csv) == [
        header,
        ["77002", "2026-03-15 00:00:00", "42"],
        ["77003", "2026-03-22 00:00:00", "55"],
    ]
    assert state["air_quality"]["files"] == [
        str(first_file.resolve()),
        str(second_file.resolve()),
    ]

def test_read_header_returns_first_csv_row(tmp_path):
    # PARQUET MIGRATION NOTE:
    # For Parquet, this should become a schema/column-name reader test.
    # Instead of reading the first CSV row, read the Parquet table schema or
    # DataFrame columns and assert they match the expected column names.
    csv_path = tmp_path / "air_quality_master.csv"
    write_csv(
        csv_path,
        ["zip", "time", "us_aqi"],
        [["77002", "2026-03-15 00:00:00", 42]],
    )

    result = merge_master.read_header(csv_path)

    assert result == ["zip", "time", "us_aqi"]


def test_read_header_rejects_empty_csv(tmp_path):
    # PARQUET MIGRATION NOTE:
    # For Parquet, replace this with a test that rejects an unreadable,
    # empty, or schema-less Parquet file if your IO layer supports that case.
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="CSV is empty"):
        merge_master.read_header(csv_path)


def test_rebuild_master_replaces_existing_output_file(tmp_path):
    # PARQUET MIGRATION NOTE:
    # For Parquet, this should verify that the old Parquet output is replaced
    # entirely, not appended to. Use pandas.read_parquet() and compare records.
    header = ["zip", "time", "us_aqi"]
    first_file = tmp_path / "march_1stweek_air_quality_hourly_20260315_120000.csv"
    second_file = tmp_path / "march_2ndweek_air_quality_hourly_20260322_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"

    write_csv(first_file, header, [["77002", "2026-03-15 00:00:00", 42]])
    write_csv(second_file, header, [["77003", "2026-03-22 00:00:00", 55]])

    output_csv.write_text(
        "zip,time,us_aqi\n99999,stale-row,999\n",
        encoding="utf-8",
    )

    merge_master.rebuild_master([first_file, second_file], output_csv)

    assert read_csv(output_csv) == [
        header,
        ["77002", "2026-03-15 00:00:00", "42"],
        ["77003", "2026-03-22 00:00:00", "55"],
    ]


def test_process_category_rebuilds_when_earlier_file_is_added_later(tmp_path):
    # PARQUET MIGRATION NOTE:
    # This currently proves chronological CSV row ordering. For Parquet, assert the
    # same logical ordering after reading the output table with pandas.read_parquet.
    header = ["zip", "time", "us_aqi"]
    later_file = tmp_path / "march_2ndweek_air_quality_hourly_20260322_120000.csv"
    earlier_file = tmp_path / "feb_4thweek_air_quality_hourly_20260312_120000.csv"
    output_csv = tmp_path / "air_quality_master.csv"
    state = empty_state()

    write_csv(later_file, header, [["77003", "2026-03-22 00:00:00", 55]])
    write_csv(earlier_file, header, [["77002", "2026-03-12 00:00:00", 42]])

    merge_master.process_category("air_quality", [later_file], output_csv, state)
    merge_master.process_category(
        "air_quality", [earlier_file, later_file], output_csv, state
    )

    assert read_csv(output_csv) == [
        header,
        ["77002", "2026-03-12 00:00:00", "42"],
        ["77003", "2026-03-22 00:00:00", "55"],
    ]
    assert state["air_quality"]["files"] == [
        str(earlier_file.resolve()),
        str(later_file.resolve()),
    ]
    
    
    