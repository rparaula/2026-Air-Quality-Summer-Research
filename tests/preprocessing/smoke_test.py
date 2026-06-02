import csv
import json
import sys

from preprocessing import preprocessing as pp


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_preprocessing_main_smoke_runs_tiny_pipeline(tmp_path, monkeypatch, capsys):
    air_quality_csv = tmp_path / "air_quality.csv"
    weather_csv = tmp_path / "weather.csv"
    output_dir = tmp_path / "pipeline_output"
    temp_dir = tmp_path / "sort_runs"

    tri_facilities_csv = tmp_path / "tri_facilities.csv"
    tri_chemicals_csv = tmp_path / "tri_chemicals.csv"
    zip_shapefile = tmp_path / "dummy_zips.shp"
    roads_shapefile = tmp_path / "dummy_roads.shp"

    # These dummy files exist only because main() requires paths.
    # build_spatial_zip_lookup() is mocked below, so the files are not actually read.
    tri_facilities_csv.write_text("dummy\n", encoding="utf-8")
    tri_chemicals_csv.write_text("dummy\n", encoding="utf-8")
    zip_shapefile.write_text("dummy\n", encoding="utf-8")
    roads_shapefile.write_text("dummy\n", encoding="utf-8")

    write_csv(
        air_quality_csv,
        fieldnames=["zip", "time", "us_aqi", "city", "state"],
        rows=[
            {
                "zip": "77002",
                "time": "2024-01-01T00:00:00Z",
                "us_aqi": "10",
                "city": "Houston",
                "state": "TX",
            },
            {
                "zip": "77002",
                "time": "2024-01-01T01:00:00Z",
                "us_aqi": "20",
                "city": "Houston",
                "state": "TX",
            },
        ],
    )

    write_csv(
        weather_csv,
        fieldnames=[
            "zip",
            "time",
            "temperature",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_speed_100m",
            "wind_direction_100m",
            "city",
            "state",
        ],
        rows=[
            {
                "zip": "77002",
                "time": "2024-01-01T00:00:00Z",
                "temperature": "80",
                "wind_speed_10m": "10",
                "wind_direction_10m": "90",
                "wind_speed_100m": "20",
                "wind_direction_100m": "180",
                "city": "Houston",
                "state": "TX",
            },
            {
                "zip": "77003",
                "time": "2024-01-01T00:00:00Z",
                "temperature": "75",
                "wind_speed_10m": "8",
                "wind_direction_10m": "0",
                "wind_speed_100m": "15",
                "wind_direction_100m": "90",
                "city": "Houston",
                "state": "TX",
            },
        ],
    )

    def fake_build_spatial_zip_lookup(
        tri_facilities_csv,
        tri_chemicals_csv,
        zip_shapefile,
        roads_shapefile,
        zips_needed,
        road_radius_km,
        facility_radius_km,
        logger,
        io_tracker=None,
    ):
        logger.section("Fake spatial precompute")

        lookup = {
            str(zip_code): {
                "road_pairs": [],
                "facility_pairs": [],
                "road_count_nearby": 0,
                "facility_count_nearby": 0,
            }
            for zip_code in zips_needed
        }

        meta = {
            "zip_codes_scored": len(lookup),
            "fake_spatial_lookup": True,
        }

        return lookup, meta

    monkeypatch.setattr(pp, "build_spatial_zip_lookup", fake_build_spatial_zip_lookup)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "preprocessing.py",
            "--air-quality",
            str(air_quality_csv),
            "--weather",
            str(weather_csv),
            "--tri-facilities",
            str(tri_facilities_csv),
            "--tri-chemicals",
            str(tri_chemicals_csv),
            "--zip-shapefile",
            str(zip_shapefile),
            "--roads-shapefile",
            str(roads_shapefile),
            "--output-dir",
            str(output_dir),
            "--temp-dir",
            str(temp_dir),
            "--chunk-rows",
            "1",
            "--left-drop-columns",
            "city",
            "state",
            "--right-drop-columns",
            "city",
            "state",
            "--feats-for-past",
            "us_aqi",
            "--num-past-feats",
            "1",
        ],
    )

    pp.main()

    captured = capsys.readouterr()
    assert "Pipeline complete." in captured.out

    final_csv = output_dir / "all_features.csv"
    summary_json = output_dir / "metadata" / "pipeline_summary.json"
    spatial_meta_json = output_dir / "metadata" / "spatial_lookup.json"
    log_file = output_dir / "logs" / "pipeline_steps.log"

    assert final_csv.exists()
    assert summary_json.exists()
    assert spatial_meta_json.exists()
    assert log_file.exists()

    rows = read_csv_rows(final_csv)

    assert len(rows) == 3

    first = rows[0]
    second = rows[1]
    third = rows[2]

    assert first["zip"] == "77002"
    assert first["time"] == "2024-01-01 00:00:00"
    assert first["us_aqi"] == "10"
    assert first["temperature"] == "80"
    assert first["us_aqi_past_1"] == "nan"

    assert second["zip"] == "77002"
    assert second["time"] == "2024-01-01 01:00:00"
    assert second["us_aqi"] == "20"
    assert second["temperature"] == "nan"
    assert second["us_aqi_past_1"] == "10"

    assert third["zip"] == "77003"
    assert third["time"] == "2024-01-01 00:00:00"
    assert third["us_aqi"] == "nan"
    assert third["temperature"] == "75"

    assert "wind_direction_10m" not in first
    assert "wind_direction_10m_sin" in first
    assert "wind_direction_10m_cos" in first
    assert "month" in first
    assert "hour" in first
    assert "road_impact_score" in first
    assert "facility_impact_score" in first

    summary = json.loads(summary_json.read_text(encoding="utf-8"))

    assert summary["outputs"]["final_csv"] == str(final_csv)
    assert summary["parameters"]["chunk_rows"] == 1
    assert summary["parameters"]["feats_for_past"] == ["us_aqi"]
    assert summary["parameters"]["num_past_feats"] == 1
    assert summary["cardinality_summary"]["column_cardinality"]["zip"] == 2

    spatial_meta = json.loads(spatial_meta_json.read_text(encoding="utf-8"))
    assert spatial_meta["fake_spatial_lookup"] is True