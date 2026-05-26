import argparse
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd


POLLUTANT_COLUMNS = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
]

OPEN_METEO_AIR_PLACEHOLDERS = [
    "us_aqi",
    "uv_index_clear_sky",
    "uv_index",
    "dust",
    "aerosol_optical_depth",
]

OUTPUT_COLUMNS = [
    "city",
    "state",
    "zip",
    "latitude",
    "longitude",
    "time",
    "grid_id",
    "grid_row",
    "grid_col",
    "cell_size_miles",
    "assigned_monitor_site_id",
    "assigned_monitor_site_name",
    "assigned_monitor_distance_miles",
    "assigned_monitor_latitude",
    "assigned_monitor_longitude",
    "assigned_monitor_county_code",
    "assigned_monitor_county_name",
    "assigned_monitor_site_number",
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
]


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD date, got {value!r}") from exc


def clean_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def normalize_zip(value) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        text = str(int(float(text)))
    except ValueError:
        pass
    return text.zfill(5)


def normalize_code(value, width: int) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        text = str(int(float(text)))
    except ValueError:
        pass
    return text.zfill(width)


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    radius_miles = 3958.7613
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * radius_miles * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def latest_concentration_csv(aqs_dir: Path) -> Path:
    candidates = [
        path
        for path in aqs_dir.glob("*_aqs_concentrations_hourly_*.csv")
        if "_raw_" not in path.name.lower()
    ]
    if not candidates:
        raise SystemExit(
            f"No AQS concentration CSV found in {aqs_dir}. "
            "Pass --aqs-concentrations with an AQS_concentrations.py output file."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_grid_centroids(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype={
            "grid_id": "string",
            "zip": "string",
            "containing_zip": "string",
        },
    )

    rename_map = {}
    if "containing_zip" in df.columns and "zip" not in df.columns:
        rename_map["containing_zip"] = "zip"
    if "lat" in df.columns and "latitude" not in df.columns:
        rename_map["lat"] = "latitude"
    if "lng" in df.columns and "longitude" not in df.columns:
        rename_map["lng"] = "longitude"
    if "row" in df.columns and "grid_row" not in df.columns:
        rename_map["row"] = "grid_row"
    if "col" in df.columns and "grid_col" not in df.columns:
        rename_map["col"] = "grid_col"
    if rename_map:
        df = df.rename(columns=rename_map)

    required_columns = ["city", "state", "zip", "latitude", "longitude"]
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise SystemExit(f"Grid centroid CSV is missing required column(s): {', '.join(missing)}")

    if "grid_id" not in df.columns:
        df["grid_id"] = [f"GRID-{i:06d}" for i in range(1, len(df) + 1)]
    if "grid_row" not in df.columns:
        df["grid_row"] = pd.NA
    if "grid_col" not in df.columns:
        df["grid_col"] = pd.NA
    if "cell_size_miles" not in df.columns:
        df["cell_size_miles"] = pd.NA

    centroids = df[
        [
            "city",
            "state",
            "zip",
            "latitude",
            "longitude",
            "grid_id",
            "grid_row",
            "grid_col",
            "cell_size_miles",
        ]
    ].copy()
    centroids["city"] = centroids["city"].astype(str).str.strip()
    centroids["state"] = centroids["state"].astype(str).str.strip().str.upper()
    centroids["zip"] = centroids["zip"].map(normalize_zip)
    centroids["latitude"] = pd.to_numeric(centroids["latitude"], errors="coerce")
    centroids["longitude"] = pd.to_numeric(centroids["longitude"], errors="coerce")

    before = len(centroids)
    centroids = centroids.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    dropped = before - len(centroids)
    if dropped:
        print(f"WARNING: Dropped {dropped} centroid row(s) with missing coordinates.")
    if centroids.empty:
        raise SystemExit("No usable centroid rows found.")
    return centroids


def load_aqs_concentrations(path: Path, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    dtype = {
        "monitor_site_id": "string",
        "state_code": "string",
        "county_code": "string",
        "site_number": "string",
        "cbsa_code": "string",
        "available_parameter_codes": "string",
    }
    df = pd.read_csv(path, dtype=dtype)

    required = ["monitor_site_id", "time", "latitude", "longitude"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise SystemExit(f"AQS concentration CSV is missing required column(s): {', '.join(missing)}")

    df["monitor_site_id"] = df["monitor_site_id"].astype(str).str.strip()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["monitor_site_id", "time", "latitude", "longitude"]).copy()

    if start_date is not None:
        df = df[df["time"] >= pd.Timestamp(start_date)].copy()
    if end_date is not None:
        df = df[df["time"] <= pd.Timestamp(end_date) + pd.Timedelta(hours=23, minutes=59, seconds=59)].copy()

    for pollutant in POLLUTANT_COLUMNS:
        if pollutant not in df.columns:
            df[pollutant] = pd.NA
        df[pollutant] = pd.to_numeric(df[pollutant], errors="coerce")

    for column, width in [("state_code", 2), ("county_code", 3), ("site_number", 4)]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].map(lambda value: normalize_code(value, width))

    optional_text_columns = ["site_name", "county_name"]
    for column in optional_text_columns:
        if column not in df.columns:
            df[column] = ""

    if df.empty:
        raise SystemExit("No AQS concentration rows remain after filtering.")
    return df


def build_station_table(aqs_df: pd.DataFrame) -> pd.DataFrame:
    station_cols = [
        "monitor_site_id",
        "site_name",
        "latitude",
        "longitude",
        "county_code",
        "county_name",
        "site_number",
    ]
    stations = aqs_df[station_cols].drop_duplicates(subset=["monitor_site_id"]).copy()
    stations = stations.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    if stations.empty:
        raise SystemExit("No monitor stations with usable coordinates were found.")
    return stations.rename(
        columns={
            "site_name": "assigned_monitor_site_name",
            "latitude": "assigned_monitor_latitude",
            "longitude": "assigned_monitor_longitude",
            "county_code": "assigned_monitor_county_code",
            "county_name": "assigned_monitor_county_name",
            "site_number": "assigned_monitor_site_number",
        }
    )


def assign_nearest_monitors(centroids: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    station_records = stations.to_dict("records")
    assignments = []

    for centroid in centroids.to_dict("records"):
        nearest = None
        nearest_distance = float("inf")
        for station in station_records:
            distance = haversine_miles(
                centroid["latitude"],
                centroid["longitude"],
                station["assigned_monitor_latitude"],
                station["assigned_monitor_longitude"],
            )
            if distance < nearest_distance:
                nearest_distance = distance
                nearest = station

        row = dict(centroid)
        row["assigned_monitor_site_id"] = nearest["monitor_site_id"]
        row["assigned_monitor_site_name"] = nearest["assigned_monitor_site_name"]
        row["assigned_monitor_distance_miles"] = round(float(nearest_distance), 4)
        row["assigned_monitor_latitude"] = nearest["assigned_monitor_latitude"]
        row["assigned_monitor_longitude"] = nearest["assigned_monitor_longitude"]
        row["assigned_monitor_county_code"] = nearest["assigned_monitor_county_code"]
        row["assigned_monitor_county_name"] = nearest["assigned_monitor_county_name"]
        row["assigned_monitor_site_number"] = nearest["assigned_monitor_site_number"]
        assignments.append(row)

    return pd.DataFrame(assignments)


def build_mapped_timeseries(assignments: pd.DataFrame, aqs_df: pd.DataFrame) -> pd.DataFrame:
    times = pd.DataFrame({"time": sorted(aqs_df["time"].dropna().unique())})
    assignments = assignments.copy()
    assignments["_join_key"] = 1
    times["_join_key"] = 1
    centroid_time = assignments.merge(times, on="_join_key", how="outer").drop(columns="_join_key")

    values = aqs_df[["monitor_site_id", "time", *POLLUTANT_COLUMNS]].copy()
    values = values.groupby(["monitor_site_id", "time"], as_index=False)[POLLUTANT_COLUMNS].mean()
    values = values.rename(columns={"monitor_site_id": "assigned_monitor_site_id"})

    mapped = centroid_time.merge(
        values,
        on=["assigned_monitor_site_id", "time"],
        how="left",
    )

    mapped["time"] = pd.to_datetime(mapped["time"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    for column in OPEN_METEO_AIR_PLACEHOLDERS:
        if column not in mapped.columns:
            mapped[column] = pd.NA
    for column in OUTPUT_COLUMNS:
        if column not in mapped.columns:
            mapped[column] = pd.NA
    return mapped[OUTPUT_COLUMNS].sort_values(["grid_id", "time"], kind="stable").reset_index(drop=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Map 2x2 Houston grid centroids to their nearest AQS monitor station and "
            "emit centroid-hour pollutant concentrations from the assigned station."
        )
    )
    parser.add_argument(
        "--grid-centroids",
        default=str(Path("static data") / "houston_grid_centroids_2x2.csv"),
        help="Path to houston_grid_centroids_2x2.csv.",
    )
    parser.add_argument(
        "--aqs-concentrations",
        default=None,
        help=(
            "Path to AQS_concentrations.py output CSV. Defaults to the newest "
            "AQS_data/air_data/*_aqs_concentrations_hourly_*.csv file."
        ),
    )
    parser.add_argument(
        "--aqs-dir",
        default=str(Path("AQS_data") / "air_data"),
        help="Directory searched when --aqs-concentrations is omitted.",
    )
    parser.add_argument("--start-date", type=parse_iso_date, default=None, help="Optional YYYY-MM-DD filter.")
    parser.add_argument("--end-date", type=parse_iso_date, default=None, help="Optional YYYY-MM-DD filter.")
    parser.add_argument("--out-dir", default="new_data", help="Directory for mapped output CSVs.")
    parser.add_argument("--out-prefix", default="greater_houston_2x2")
    parser.add_argument("--output-file", default=None, help="Optional explicit output CSV path.")
    parser.add_argument(
        "--max-centroids",
        type=int,
        default=None,
        help="Optional first-N centroid limit for smoke tests.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.start_date and args.end_date and args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.max_centroids is not None and args.max_centroids < 1:
        raise SystemExit("--max-centroids must be at least 1")

    grid_path = Path(args.grid_centroids)
    aqs_path = Path(args.aqs_concentrations) if args.aqs_concentrations else latest_concentration_csv(Path(args.aqs_dir))

    centroids = load_grid_centroids(grid_path)
    if args.max_centroids is not None:
        centroids = centroids.head(args.max_centroids).copy()

    aqs_df = load_aqs_concentrations(aqs_path, start_date=args.start_date, end_date=args.end_date)
    stations = build_station_table(aqs_df)
    assignments = assign_nearest_monitors(centroids, stations)
    mapped = build_mapped_timeseries(assignments, aqs_df)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = out_dir / f"{args.out_prefix}_aqs_mapped_centroid_hourly_{timestamp}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)

    mapped.to_csv(output_file, index=False)
    print(f"[AQS] Grid centroids: {grid_path}")
    print(f"[AQS] AQS concentrations: {aqs_path}")
    print(f"[AQS] Centroids mapped: {len(centroids)}")
    print(f"[AQS] Monitor stations available: {len(stations)}")
    print(f"[AQS] Unique timestamps: {mapped['time'].nunique()}")
    print(f"[AQS] Output rows: {len(mapped)}")
    print(f"[AQS] Saved mapped centroid hourly CSV -> {output_file}")


if __name__ == "__main__":
    main()
