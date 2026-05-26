import argparse
import math
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd


WEATHER_COLUMNS = [
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
    "assigned_monitor_noaa_ghcnh_station_id",
    "assigned_monitor_station_name",
    "assigned_monitor_distance_miles",
    "assigned_monitor_latitude",
    "assigned_monitor_longitude",
    "assigned_monitor_county_code",
    "assigned_monitor_county_name",
    *WEATHER_COLUMNS,
]

TIMEZONE_SUFFIX_RE = re.compile(r"([+-]\d{2}:\d{2}|Z)$")


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


def strip_timezone_suffix(value) -> str:
    raw = clean_text(value).replace("T", " ")
    return TIMEZONE_SUFFIX_RE.sub("", raw).strip()


def local_naive_timestamp(value):
    return pd.to_datetime(strip_timezone_suffix(value), errors="coerce")


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


def latest_noaa_ghcnh_weather_csv(weather_dir: Path) -> Path:
    candidates = [
        path
        for path in weather_dir.glob("*_noaa_ghcnh_weather_hourly_*.csv")
        if "_raw_" not in path.name.lower()
    ]
    if not candidates:
        raise SystemExit(
            f"No NOAA_GHCNh hourly weather CSV found in {weather_dir}. "
            "Pass --noaa-ghcnh-weather with a NOAA_GHCNh_variables.py output file."
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
        ["city", "state", "zip", "latitude", "longitude", "grid_id", "grid_row", "grid_col", "cell_size_miles"]
    ].copy()
    centroids["city"] = centroids["city"].astype(str).str.strip()
    centroids["state"] = centroids["state"].astype(str).str.strip().str.upper()
    centroids["zip"] = centroids["zip"].map(normalize_zip)
    centroids["latitude"] = pd.to_numeric(centroids["latitude"], errors="coerce")
    centroids["longitude"] = pd.to_numeric(centroids["longitude"], errors="coerce")
    centroids = centroids.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    if centroids.empty:
        raise SystemExit("No usable centroid rows found.")
    return centroids


def load_noaa_ghcnh_weather(path: Path, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    dtype = {
        "noaa_ghcnh_station_id": "string",
        "county_code": "string",
        "wmo_id": "string",
        "available_ghcnh_variables": "string",
    }
    df = pd.read_csv(path, dtype=dtype)
    required = ["noaa_ghcnh_station_id", "time", "latitude", "longitude"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise SystemExit(f"NOAA_GHCNh weather CSV is missing required column(s): {', '.join(missing)}")

    df["noaa_ghcnh_station_id"] = df["noaa_ghcnh_station_id"].map(clean_text)
    df["time"] = df["time"].map(clean_text)
    df["_time_local_naive"] = df["time"].map(local_naive_timestamp)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["noaa_ghcnh_station_id", "time", "_time_local_naive", "latitude", "longitude"]).copy()

    if start_date is not None:
        df = df[df["_time_local_naive"] >= pd.Timestamp(start_date)].copy()
    if end_date is not None:
        df = df[df["_time_local_naive"] <= pd.Timestamp(end_date) + pd.Timedelta(hours=23, minutes=59, seconds=59)].copy()

    for column in WEATHER_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")

    for column, width in [("county_code", 3)]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].map(lambda value: normalize_code(value, width))
    for column in ["station_name", "county_name"]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].map(clean_text)

    if df.empty:
        raise SystemExit("No NOAA_GHCNh weather rows remain after filtering.")
    if int(df[WEATHER_COLUMNS].notna().sum().sum()) == 0:
        print("WARNING: NOAA_GHCNh weather rows were found, but all mapped weather values are blank/NaN.")
    return df


def build_station_table(noaa_df: pd.DataFrame) -> pd.DataFrame:
    station_cols = [
        "noaa_ghcnh_station_id",
        "station_name",
        "latitude",
        "longitude",
        "county_code",
        "county_name",
    ]
    stations = noaa_df[station_cols].drop_duplicates(subset=["noaa_ghcnh_station_id"]).copy()
    stations = stations.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    if stations.empty:
        raise SystemExit("No NOAA_GHCNh stations with usable coordinates were found.")
    return stations.rename(
        columns={
            "noaa_ghcnh_station_id": "assigned_monitor_noaa_ghcnh_station_id",
            "station_name": "assigned_monitor_station_name",
            "latitude": "assigned_monitor_latitude",
            "longitude": "assigned_monitor_longitude",
            "county_code": "assigned_monitor_county_code",
            "county_name": "assigned_monitor_county_name",
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
        row.update(nearest)
        row["assigned_monitor_distance_miles"] = round(float(nearest_distance), 4)
        assignments.append(row)
    return pd.DataFrame(assignments)


def build_mapped_timeseries(assignments: pd.DataFrame, noaa_df: pd.DataFrame) -> pd.DataFrame:
    times = noaa_df[["time", "_time_local_naive"]].drop_duplicates().copy()
    times = times.sort_values("_time_local_naive", kind="stable").reset_index(drop=True)

    assignments = assignments.copy()
    assignments["_join_key"] = 1
    times["_join_key"] = 1
    centroid_time = assignments.merge(times[["time", "_join_key"]], on="_join_key", how="outer").drop(columns="_join_key")

    values = noaa_df[["noaa_ghcnh_station_id", "time", *WEATHER_COLUMNS]].copy()
    values = values.groupby(["noaa_ghcnh_station_id", "time"], as_index=False)[WEATHER_COLUMNS].mean()
    values = values.rename(columns={"noaa_ghcnh_station_id": "assigned_monitor_noaa_ghcnh_station_id"})
    mapped = centroid_time.merge(values, on=["assigned_monitor_noaa_ghcnh_station_id", "time"], how="left")

    for column in OUTPUT_COLUMNS:
        if column not in mapped.columns:
            mapped[column] = pd.NA
    return mapped[OUTPUT_COLUMNS].sort_values(["grid_id", "time"], kind="stable").reset_index(drop=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Map 2x2 Houston grid centroids to nearest NOAA_GHCNh station weather observations."
    )
    parser.add_argument("--grid-centroids", default=str(Path("static data") / "houston_grid_centroids_2x2.csv"))
    parser.add_argument("--noaa-ghcnh-weather", default=None)
    parser.add_argument("--weather-dir", default=str(Path("NOAA_GHCNh_data") / "weather_data"))
    parser.add_argument("--start-date", type=parse_iso_date, default=None)
    parser.add_argument("--end-date", type=parse_iso_date, default=None)
    parser.add_argument("--out-dir", default="new_data")
    parser.add_argument("--out-prefix", default="greater_houston_2x2")
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--max-centroids", type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.start_date and args.end_date and args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.max_centroids is not None and args.max_centroids < 1:
        raise SystemExit("--max-centroids must be at least 1")

    grid_path = Path(args.grid_centroids)
    noaa_path = Path(args.noaa_ghcnh_weather) if args.noaa_ghcnh_weather else latest_noaa_ghcnh_weather_csv(Path(args.weather_dir))

    centroids = load_grid_centroids(grid_path)
    if args.max_centroids is not None:
        centroids = centroids.head(args.max_centroids).copy()

    noaa_df = load_noaa_ghcnh_weather(noaa_path, args.start_date, args.end_date)
    stations = build_station_table(noaa_df)
    assignments = assign_nearest_monitors(centroids, stations)
    mapped = build_mapped_timeseries(assignments, noaa_df)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = out_dir / f"{args.out_prefix}_noaa_ghcnh_mapped_centroid_hourly_{timestamp}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    mapped.to_csv(output_file, index=False)

    print(f"[NOAA_GHCNh] Grid centroids: {grid_path}")
    print(f"[NOAA_GHCNh] NOAA_GHCNh weather: {noaa_path}")
    print(f"[NOAA_GHCNh] Centroids mapped: {len(centroids)}")
    print(f"[NOAA_GHCNh] Monitor stations available: {len(stations)}")
    print(f"[NOAA_GHCNh] Unique timestamps: {mapped['time'].nunique()}")
    print(f"[NOAA_GHCNh] Output rows: {len(mapped)}")
    print(f"[NOAA_GHCNh] Saved mapped centroid hourly CSV -> {output_file}")


if __name__ == "__main__":
    main()
