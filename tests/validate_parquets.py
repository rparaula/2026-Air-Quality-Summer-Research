import argparse
from pathlib import Path

import pandas as pd


EXPECTED_SCHEMAS = {
    "air_quality": {
        "file_pattern": "bronze/air/*air_quality_hourly_*.parquet",
        "required_columns": {
            "city",
            "state",
            "zip",
            "latitude",
            "longitude",
            "time",
            "us_aqi",
            "pm10",
            "pm2_5",
            "carbon_monoxide",
            "nitrogen_dioxide",
            "sulphur_dioxide",
            "ozone",
            "uv_index_clear_sky",
            "uv_index",
            "dust",
            "aerosol_optical_depth",
        },
        "numeric_columns": {
            "latitude",
            "longitude",
            "us_aqi",
            "pm10",
            "pm2_5",
            "carbon_monoxide",
            "nitrogen_dioxide",
            "sulphur_dioxide",
            "ozone",
            "uv_index_clear_sky",
            "uv_index",
            "dust",
            "aerosol_optical_depth",
        },
        "key_columns": ["zip", "time"],
    },
    "weather": {
        "file_pattern": "bronze/weather/*weather_hourly_*.parquet",
        "required_columns": {
            "city",
            "state",
            "zip",
            "latitude",
            "longitude",
            "time",
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "wind_speed_100m",
            "wind_direction_10m",
            "wind_direction_100m",
            "wind_gusts_10m",
            "shortwave_radiation",
            "diffuse_radiation",
            "cloud_cover",
        },
        "numeric_columns": {
            "latitude",
            "longitude",
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "wind_speed_100m",
            "wind_direction_10m",
            "wind_direction_100m",
            "wind_gusts_10m",
            "shortwave_radiation",
            "diffuse_radiation",
            "cloud_cover",
        },
        "key_columns": ["zip", "time"],
    },
}


def latest_file(data_dir: Path, pattern: str) -> Path:
    files = sorted(data_dir.glob(pattern), key=lambda p: p.stat().st_mtime)

    if not files:
        raise SystemExit(f"No files found matching pattern: {pattern}")

    return files[-1]


def validate_csv(file_path: Path, schema_name: str, schema: dict) -> None:
    print(f"Validating {schema_name}: {file_path}")

    df = pd.read_parquet(file_path)

    if df.empty:
        raise SystemExit(f"{file_path} is empty.")

    missing_columns = schema["required_columns"] - set(df.columns)
    if missing_columns:
        raise SystemExit(
            f"{file_path} is missing required columns: {sorted(missing_columns)}"
        )

    if df["zip"].isna().any():
        raise SystemExit(f"{file_path} has missing ZIP values.")

    if df["time"].isna().any():
        raise SystemExit(f"{file_path} has missing time values.")

    parsed_time = pd.to_datetime(df["time"], errors="coerce")
    if parsed_time.isna().any():
        raise SystemExit(f"{file_path} has invalid timestamps.")

    duplicate_count = df.duplicated(subset=schema["key_columns"]).sum()
    if duplicate_count > 0:
        raise SystemExit(
            f"{file_path} has {duplicate_count} duplicate rows by {schema['key_columns']}."
        )

    for col in schema["numeric_columns"]:
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.isna().any():
            raise SystemExit(f"{file_path} has non-numeric values in column: {col}")

    print(f"PASSED: {schema_name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    if not data_dir.exists():
        raise SystemExit(f"Data directory does not exist: {data_dir}")

    for schema_name, schema in EXPECTED_SCHEMAS.items():
        file_path = latest_file(data_dir, schema["file_pattern"])
        validate_csv(file_path, schema_name, schema)

    print("All ingestion output validation checks passed.")


if __name__ == "__main__":
    main()