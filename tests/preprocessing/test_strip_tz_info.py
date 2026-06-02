import csv

import pytest

from preprocessing.strip_tz_info import (
    strip_after_second,
    find_time_col_index,
    process_csv,
)


def read_csv(path, delimiter=","):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f, delimiter=delimiter))

@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-03-05 12:34:56-06:00", "2026-03-05 12:34:56"),
        ("2026-03-05 12:34:56Z", "2026-03-05 12:34:56"),
        ("2026-03-05 12:34:56", "2026-03-05 12:34:56"),
        ("2026/03/05 12:34:56-06:00", "2026/03/05 12:34:56-06:00"),
        ("not a timestamp", "not a timestamp"),
        ("", ""),
        (None, None),
    ],
)
def test_strip_after_second(value, expected):
    assert strip_after_second(value) == expected
    
    
# Test cases for find_time_col_index
def test_find_time_col_index_exact_match():
    header = ["station_id", "time", "pm25"]
    assert find_time_col_index(header, "time") == 1


def test_find_time_col_index_strips_header_whitespace():
    header = ["station_id", " time ", "pm25"]
    assert find_time_col_index(header, "time") == 1


def test_find_time_col_index_strips_requested_column_whitespace():
    header = ["station_id", "time", "pm25"]
    assert find_time_col_index(header, " time ") == 1


def test_find_time_col_index_missing_column_exits(capsys):
    header = ["station_id", "datetime", "pm25"]

    with pytest.raises(SystemExit):
        find_time_col_index(header, "time")

    captured = capsys.readouterr()
    assert "Error: time column 'time' not found." in captured.err
    assert "Available columns:" in captured.err
    assert "datetime" in captured.err
    

def test_process_csv_strips_timezone_from_time_column(tmp_path, capsys):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"

    input_path.write_text(
        "station_id,time,pm25\n"
        "A1,2026-03-05 12:34:56-06:00,10.5\n"
        "A2,2026-03-05 13:00:00Z,12.0\n"
        "A3,not a timestamp,8.2\n",
        encoding="utf-8",
    )

    process_csv(
        input_path=str(input_path),
        output_path=str(output_path),
        time_col="time",
    )

    rows = read_csv(output_path)

    assert rows == [
        ["station_id", "time", "pm25"],
        ["A1", "2026-03-05 12:34:56", "10.5"],
        ["A2", "2026-03-05 13:00:00", "12.0"],
        ["A3", "not a timestamp", "8.2"],
    ]

    captured = capsys.readouterr()
    assert "Rows processed: 3" in captured.out
    assert "Time values modified: 2" in captured.out


# Tests for show row padding
def test_process_csv_pads_short_rows(tmp_path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"

    input_path.write_text(
        "station_id,time,pm25\n"
        "A1,2026-03-05 12:34:56-06:00\n",
        encoding="utf-8",
    )

    process_csv(
        input_path=str(input_path),
        output_path=str(output_path),
        time_col="time",
    )

    rows = read_csv(output_path)

    assert rows == [
        ["station_id", "time", "pm25"],
        ["A1", "2026-03-05 12:34:56", ""],
    ]
    

# Testing custom time column
def test_process_csv_custom_time_column(tmp_path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"

    input_path.write_text(
        "station_id,datetime,pm25\n"
        "A1,2026-03-05 12:34:56-06:00,10.5\n",
        encoding="utf-8",
    )

    process_csv(
        input_path=str(input_path),
        output_path=str(output_path),
        time_col="datetime",
    )

    rows = read_csv(output_path)

    assert rows == [
        ["station_id", "datetime", "pm25"],
        ["A1", "2026-03-05 12:34:56", "10.5"],
    ]




# Testing custom delimiter
def test_process_csv_custom_delimiter(tmp_path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"

    input_path.write_text(
        "station_id|time|pm25\n"
        "A1|2026-03-05 12:34:56-06:00|10.5\n",
        encoding="utf-8",
    )

    process_csv(
        input_path=str(input_path),
        output_path=str(output_path),
        time_col="time",
        delimiter="|",
    )

    rows = read_csv(output_path, delimiter="|")

    assert rows == [
        ["station_id", "time", "pm25"],
        ["A1", "2026-03-05 12:34:56", "10.5"],
    ]
    
    
#Testing empty CSV error
def test_process_csv_empty_file_exits(tmp_path, capsys):
    input_path = tmp_path / "empty.csv"
    output_path = tmp_path / "output.csv"

    input_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit):
        process_csv(
            input_path=str(input_path),
            output_path=str(output_path),
            time_col="time",
        )

    captured = capsys.readouterr()
    assert "Error: CSV is empty." in captured.err