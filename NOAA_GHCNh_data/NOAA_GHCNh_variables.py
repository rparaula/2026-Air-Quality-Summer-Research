import argparse
import io
import math
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests


GHCNH_YEAR_FILE_TEMPLATES = [
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/{year}/psv/GHCNh_{station_id}_{year}.psv",
    "https://www.ncei.noaa.gov/data/global-historical-climate-network-hourly/access/by-year/{year}/psv/GHCNh_{station_id}_{year}.psv",
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/{year}/GHCNh_{station_id}_{year}.psv",
    "https://www.ncei.noaa.gov/data/global-historical-climate-network-hourly/access/by-year/{year}/GHCNh_{station_id}_{year}.psv",
]

WEATHER_VARIABLE_ORDER = [
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

WEATHER_VARIABLES = {
    "temperature_2m": {"measure_column": "measures_temperature_2m"},
    "relative_humidity_2m": {"measure_column": "measures_relative_humidity_2m"},
    "precipitation": {"measure_column": "measures_precipitation"},
    "wind_speed_10m": {"measure_column": "measures_wind_speed_10m"},
    "wind_speed_100m": {"measure_column": "measures_wind_speed_100m"},
    "wind_direction_10m": {"measure_column": "measures_wind_direction_10m"},
    "wind_direction_100m": {"measure_column": "measures_wind_direction_100m"},
    "wind_gusts_10m": {"measure_column": "measures_wind_gusts_10m"},
    "shortwave_radiation": {"measure_column": "measures_shortwave_radiation"},
    "diffuse_radiation": {"measure_column": "measures_diffuse_radiation"},
    "cloud_cover": {"measure_column": "measures_cloud_cover"},
}

GHCNH_COLUMN_CANDIDATES = {
    "station_id": ("station_id", "id", "station"),
    "date": ("date", "datetime", "timestamp", "time"),
    "temperature": ("temperature", "dry_bulb_temperature", "air_temperature"),
    "dew_point_temperature": ("dew_point_temperature", "dew_point", "dewpoint"),
    "relative_humidity": ("relative_humidity", "relative_humidity_pct"),
    "precipitation": ("precipitation", "precipitation_amount"),
    "wind_speed": ("wind_speed",),
    "wind_direction": ("wind_direction",),
    "wind_gust": ("wind_gust",),
    "sky_cover_1": ("sky_cover_1",),
    "sky_cover_2": ("sky_cover_2",),
    "sky_cover_3": ("sky_cover_3",),
}

STATION_COLUMNS = [
    "noaa_ghcnh_station_id",
    "station_name",
    "ctry",
    "state",
    "latitude",
    "longitude",
    "elevation_m",
    "county_code",
    "county_name",
    "wmo_id",
    "active",
    "available_ghcnh_variables",
]

MEASURE_COLUMNS = [WEATHER_VARIABLES[variable]["measure_column"] for variable in WEATHER_VARIABLE_ORDER]

OUTPUT_BASE_COLUMNS = [
    *STATION_COLUMNS,
    "time",
    "date_local",
    "time_local",
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


def parse_bool(value) -> bool:
    return clean_text(value).lower() in {"1", "true", "t", "yes", "y"}


def parse_station_ids(value: str) -> list[str] | None:
    if not value:
        return None
    return [token.strip() for token in value.split(",") if token.strip()]


def selected_variables(variable_text: str) -> list[str]:
    names = [name.strip() for name in variable_text.split(",") if name.strip()]
    unknown = sorted(set(names) - set(WEATHER_VARIABLES))
    if unknown:
        raise SystemExit(
            "Unknown weather variable(s): "
            + ", ".join(unknown)
            + ". Valid options: "
            + ", ".join(WEATHER_VARIABLE_ORDER)
        )
    return [name for name in WEATHER_VARIABLE_ORDER if name in names]


def latest_monitor_csv(monitor_dir: Path) -> Path:
    candidates = [
        path
        for path in monitor_dir.glob("*_noaa_ghcnh_monitor_locations_*.csv")
        if "_raw_" not in path.name.lower()
    ]
    if not candidates:
        raise SystemExit(
            f"No NOAA_GHCNh monitor inventory CSV found in {monitor_dir}. "
            "Pass --monitor-csv with a collect_NOAA_GHCNh_monitors.py output file."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_monitor_inventory(path: Path, station_ids: list[str] | None, active_only: bool) -> pd.DataFrame:
    dtype = {
        "noaa_ghcnh_station_id": "string",
        "county_code": "string",
        "wmo_id": "string",
        "available_ghcnh_variables": "string",
    }
    df = pd.read_csv(path, dtype=dtype)
    required = ["noaa_ghcnh_station_id", "latitude", "longitude"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise SystemExit(f"NOAA_GHCNh monitor inventory is missing required column(s): {', '.join(missing)}")

    df["noaa_ghcnh_station_id"] = df["noaa_ghcnh_station_id"].map(clean_text)
    for column in STATION_COLUMNS + MEASURE_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    if station_ids:
        wanted = set(station_ids)
        df = df[df["noaa_ghcnh_station_id"].isin(wanted)].copy()
        missing_ids = sorted(wanted - set(df["noaa_ghcnh_station_id"]))
        if missing_ids:
            print(f"WARNING: {len(missing_ids)} requested station id(s) were not found: {', '.join(missing_ids)}")

    if active_only:
        df = df[df["active"].map(parse_bool)].copy()

    df = df.drop_duplicates(subset=["noaa_ghcnh_station_id"]).reset_index(drop=True)
    if df.empty:
        raise SystemExit("No NOAA_GHCNh stations remain after applying filters.")
    return df[STATION_COLUMNS + MEASURE_COLUMNS].copy()


def station_can_measure(station: pd.Series, variable: str) -> bool:
    return parse_bool(station.get(WEATHER_VARIABLES[variable]["measure_column"]))


def station_variables_to_fetch(station: pd.Series, variables: list[str]) -> list[str]:
    return [variable for variable in variables if station_can_measure(station, variable)]


def year_file_urls(station_id: str, year: int, templates: list[str]) -> list[str]:
    return [template.format(station_id=station_id, year=year) for template in templates]


def request_station_year_psv(
    session: requests.Session,
    station_id: str,
    year: int,
    templates: list[str],
    timeout_seconds: int,
    retries: int,
    request_delay: float,
) -> pd.DataFrame:
    last_exc = None
    for url in year_file_urls(station_id, year, templates):
        for attempt in range(1, retries + 1):
            try:
                if request_delay > 0:
                    time.sleep(request_delay)
                response = session.get(url, timeout=timeout_seconds)
                if response.status_code == 404:
                    break
                response.raise_for_status()
                first_line = response.text.splitlines()[0] if response.text else ""
                separator = "|" if "|" in first_line else ","
                return pd.read_csv(io.StringIO(response.text), sep=separator, dtype=str, keep_default_na=False)
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_exc = exc
                if attempt >= retries:
                    break
                time.sleep(min(30, 2**attempt))
    if last_exc:
        print(f"[NOAA_GHCNh] WARNING: Failed to fetch {station_id} {year}: {last_exc}")
    return pd.DataFrame()


def detect_column(columns: list[str], logical_name: str) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for candidate in GHCNH_COLUMN_CANDIDATES[logical_name]:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def numeric_series(df: pd.DataFrame, logical_name: str):
    column = detect_column(list(df.columns), logical_name)
    if column is None:
        return pd.Series([pd.NA] * len(df), index=df.index)
    return pd.to_numeric(df[column], errors="coerce")


def parse_relative_humidity(temp_c, dew_c):
    if pd.isna(temp_c) or pd.isna(dew_c):
        return pd.NA
    if temp_c <= -243.04 or dew_c <= -243.04:
        return pd.NA
    exponent = (17.625 * dew_c) / (243.04 + dew_c) - (17.625 * temp_c) / (243.04 + temp_c)
    return max(0.0, min(100.0, 100.0 * math.exp(exponent)))


def ghcnh_wind_ms_to_kmh(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce") * 3.6


def ghcnh_cloud_to_percent(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.dropna().empty:
        return numeric
    if numeric.dropna().max() <= 8:
        return (numeric / 8.0) * 100.0
    return numeric


def add_parsed_variable_columns(raw: pd.DataFrame, station: pd.Series, variables: list[str]) -> pd.DataFrame:
    parsed = raw.copy()
    temp = numeric_series(parsed, "temperature")
    dew = numeric_series(parsed, "dew_point_temperature")
    rh = numeric_series(parsed, "relative_humidity")

    for variable in variables:
        if not station_can_measure(station, variable):
            parsed[variable] = pd.NA
            continue

        if variable == "temperature_2m":
            parsed[variable] = temp
        elif variable == "relative_humidity_2m":
            if rh.notna().any():
                parsed[variable] = rh
            else:
                parsed[variable] = [parse_relative_humidity(t, d) for t, d in zip(temp, dew)]
        elif variable == "precipitation":
            parsed[variable] = numeric_series(parsed, "precipitation")
        elif variable == "wind_speed_10m":
            parsed[variable] = ghcnh_wind_ms_to_kmh(numeric_series(parsed, "wind_speed"))
        elif variable == "wind_direction_10m":
            parsed[variable] = numeric_series(parsed, "wind_direction")
        elif variable == "wind_gusts_10m":
            parsed[variable] = ghcnh_wind_ms_to_kmh(numeric_series(parsed, "wind_gust"))
        elif variable == "cloud_cover":
            cover = numeric_series(parsed, "sky_cover_1")
            for logical in ("sky_cover_2", "sky_cover_3"):
                alternate = numeric_series(parsed, logical)
                cover = cover.combine_first(alternate)
            parsed[variable] = ghcnh_cloud_to_percent(cover)
        else:
            parsed[variable] = pd.NA

    return parsed


def align_observations_to_hours(
    raw: pd.DataFrame,
    station: pd.Series,
    variables: list[str],
    timezone: str,
    local_start: pd.Timestamp,
    local_end: pd.Timestamp,
    hour_binning: str,
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["noaa_ghcnh_station_id", "time", *variables])

    date_column = detect_column(list(raw.columns), "date")
    if date_column is None:
        return pd.DataFrame(columns=["noaa_ghcnh_station_id", "time", *variables])

    obs = raw.copy()
    obs["_observed_time"] = pd.to_datetime(obs[date_column], utc=True, errors="coerce")
    obs = obs.dropna(subset=["_observed_time"])
    if obs.empty:
        return pd.DataFrame(columns=["noaa_ghcnh_station_id", "time", *variables])

    if hour_binning == "floor":
        obs["_binned_time_utc"] = obs["_observed_time"].dt.floor("h")
    elif hour_binning == "ceil":
        obs["_binned_time_utc"] = obs["_observed_time"].dt.ceil("h")
    else:
        obs["_binned_time_utc"] = obs["_observed_time"].dt.round("h")

    obs["_observed_time_local"] = obs["_observed_time"].dt.tz_convert(timezone)
    obs["time"] = obs["_binned_time_utc"].dt.tz_convert(timezone)
    obs = obs[(obs["time"] >= local_start) & (obs["time"] <= local_end)].copy()
    if obs.empty:
        return pd.DataFrame(columns=["noaa_ghcnh_station_id", "time", *variables])

    obs["_distance_to_hour_seconds"] = (obs["_observed_time"] - obs["_binned_time_utc"]).dt.total_seconds().abs()
    obs = add_parsed_variable_columns(obs, station, variables)
    obs["noaa_ghcnh_station_id"] = station["noaa_ghcnh_station_id"]
    obs = obs.sort_values(["time", "_distance_to_hour_seconds"], kind="stable")

    def first_valid(series):
        for value in series:
            if pd.notna(value):
                return value
        return pd.NA

    aggregation = {variable: first_valid for variable in variables}
    hourly = obs.groupby(["noaa_ghcnh_station_id", "time"], as_index=False).agg(aggregation)
    return hourly[["noaa_ghcnh_station_id", "time", *variables]]


def build_local_hourly_grid(stations: pd.DataFrame, start_date: date, end_date: date, timezone: str) -> pd.DataFrame:
    local_start = pd.Timestamp(start_date).tz_localize(timezone)
    local_end = pd.Timestamp(end_date).tz_localize(timezone) + pd.Timedelta(hours=23)
    times = pd.date_range(start=local_start, end=local_end, freq="h")

    time_df = pd.DataFrame({"time": times})
    time_df["date_local"] = time_df["time"].dt.strftime("%Y-%m-%d")
    time_df["time_local"] = time_df["time"].dt.strftime("%H:%M")

    stations = stations.copy()
    stations["_join_key"] = 1
    time_df["_join_key"] = 1
    return stations.merge(time_df, on="_join_key", how="outer").drop(columns="_join_key")


def years_needed_for_grid(local_start: pd.Timestamp, local_end: pd.Timestamp) -> list[int]:
    utc_start = local_start.tz_convert("UTC")
    utc_end = local_end.tz_convert("UTC")
    return list(range(utc_start.year, utc_end.year + 1))


def fetch_station_measurements(
    session: requests.Session,
    stations: pd.DataFrame,
    variables: list[str],
    start_date: date,
    end_date: date,
    timezone: str,
    hour_binning: str,
    timeout_seconds: int,
    retries: int,
    request_delay: float,
) -> pd.DataFrame:
    local_start = pd.Timestamp(start_date).tz_localize(timezone)
    local_end = pd.Timestamp(end_date).tz_localize(timezone) + pd.Timedelta(hours=23)
    years = years_needed_for_grid(local_start, local_end)

    frames = []
    for index, station in stations.iterrows():
        station_id = clean_text(station["noaa_ghcnh_station_id"])
        fetch_variables = station_variables_to_fetch(station, variables)
        if not fetch_variables:
            print(f"[NOAA_GHCNh] Skipping {station_id}: no selected measurable variables")
            continue

        print(
            "[NOAA_GHCNh] Fetching station "
            f"{index + 1}/{len(stations)} station={station_id} years={','.join(str(year) for year in years)}"
        )
        station_frames = []
        for year in years:
            raw = request_station_year_psv(
                session=session,
                station_id=station_id,
                year=year,
                templates=GHCNH_YEAR_FILE_TEMPLATES,
                timeout_seconds=timeout_seconds,
                retries=retries,
                request_delay=request_delay,
            )
            aligned = align_observations_to_hours(
                raw=raw,
                station=station,
                variables=variables,
                timezone=timezone,
                local_start=local_start,
                local_end=local_end,
                hour_binning=hour_binning,
            )
            if not aligned.empty:
                station_frames.append(aligned)
        if station_frames:
            frames.append(pd.concat(station_frames, ignore_index=True))

    if not frames:
        return pd.DataFrame(columns=["noaa_ghcnh_station_id", "time", *variables])
    measurements = pd.concat(frames, ignore_index=True)
    measurements = measurements.sort_values(["noaa_ghcnh_station_id", "time"], kind="stable")
    measurements = measurements.drop_duplicates(subset=["noaa_ghcnh_station_id", "time"], keep="first")
    return measurements[["noaa_ghcnh_station_id", "time", *variables]]


def build_final_output(
    stations: pd.DataFrame,
    measurements: pd.DataFrame,
    variables: list[str],
    start_date: date,
    end_date: date,
    timezone: str,
) -> pd.DataFrame:
    grid = build_local_hourly_grid(stations, start_date, end_date, timezone)
    final = grid.merge(measurements, on=["noaa_ghcnh_station_id", "time"], how="left")
    for variable in variables:
        if variable not in final.columns:
            final[variable] = pd.NA

    final = final.sort_values(["noaa_ghcnh_station_id", "time"], kind="stable").reset_index(drop=True)
    final["date_local"] = final["time"].dt.strftime("%Y-%m-%d")
    final["time_local"] = final["time"].dt.strftime("%H:%M")
    final["time"] = final["time"].astype(str)
    return final[[*OUTPUT_BASE_COLUMNS, *variables]]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect hourly NOAA GHCNh weather variables for Greater Houston station inventory rows."
    )
    parser.add_argument("--start-date", type=parse_iso_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=parse_iso_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--monitor-csv", default=None)
    parser.add_argument("--monitor-dir", default=str(Path("NOAA_GHCNh_data") / "monitor_locations"))
    parser.add_argument("--out-dir", default=str(Path("NOAA_GHCNh_data") / "weather_data"))
    parser.add_argument("--out-prefix", default="greater_houston")
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--variables", default=",".join(WEATHER_VARIABLE_ORDER))
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--station-ids", default="")
    parser.add_argument("--max-stations", type=int, default=None)
    parser.add_argument("--hour-binning", choices=["round", "floor", "ceil"], default="round")
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")

    variables = selected_variables(args.variables)
    station_ids = parse_station_ids(args.station_ids)
    monitor_csv = Path(args.monitor_csv) if args.monitor_csv else latest_monitor_csv(Path(args.monitor_dir))
    stations = load_monitor_inventory(monitor_csv, station_ids, args.active_only)
    if args.max_stations:
        stations = stations.head(args.max_stations).copy()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = (
            out_dir
            / f"{args.out_prefix}_noaa_ghcnh_weather_hourly_"
            f"{args.start_date:%Y%m%d}_{args.end_date:%Y%m%d}_{timestamp}.csv"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[NOAA_GHCNh] Monitor inventory: {monitor_csv}")
    print(f"[NOAA_GHCNh] Stations in output grid: {len(stations)}")
    print(f"[NOAA_GHCNh] Variables: {', '.join(variables)}")

    session = requests.Session()
    session.headers.update({"User-Agent": "2026-Air-Quality-Summer-Research NOAA_GHCNh weather collector"})
    measurements = fetch_station_measurements(
        session=session,
        stations=stations,
        variables=variables,
        start_date=args.start_date,
        end_date=args.end_date,
        timezone=args.timezone,
        hour_binning=args.hour_binning,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        request_delay=args.request_delay,
    )
    print(f"[NOAA_GHCNh] Station-hour observations with at least one parsed value: {len(measurements)}")
    final = build_final_output(stations, measurements, variables, args.start_date, args.end_date, args.timezone)
    final.to_csv(output_file, index=False)
    print(f"[NOAA_GHCNh] Saved hourly GHCNh weather variables -> {output_file}")
    print(f"[NOAA_GHCNh] Output rows: {len(final)}")


if __name__ == "__main__":
    main()
