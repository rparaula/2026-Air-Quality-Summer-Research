import argparse
import io
import math
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests


NOAA_GLOBAL_HOURLY_BASE_URL = "https://noaa-global-hourly-pds.s3.amazonaws.com"

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
    "temperature_2m": {
        "measure_column": "measures_temperature_2m",
        "fields": ("TMP",),
    },
    "relative_humidity_2m": {
        "measure_column": "measures_relative_humidity_2m",
        "fields": ("TMP", "DEW"),
    },
    "precipitation": {
        "measure_column": "measures_precipitation",
        "fields": ("AA1", "AA2", "AA3", "AA4"),
    },
    "wind_speed_10m": {
        "measure_column": "measures_wind_speed_10m",
        "fields": ("WND",),
    },
    "wind_speed_100m": {
        "measure_column": "measures_wind_speed_100m",
        "fields": (),
    },
    "wind_direction_10m": {
        "measure_column": "measures_wind_direction_10m",
        "fields": ("WND",),
    },
    "wind_direction_100m": {
        "measure_column": "measures_wind_direction_100m",
        "fields": (),
    },
    "wind_gusts_10m": {
        "measure_column": "measures_wind_gusts_10m",
        "fields": ("OC1",),
    },
    "shortwave_radiation": {
        "measure_column": "measures_shortwave_radiation",
        "fields": ("GH1",),
    },
    "diffuse_radiation": {
        "measure_column": "measures_diffuse_radiation",
        "fields": (),
    },
    "cloud_cover": {
        "measure_column": "measures_cloud_cover",
        "fields": ("GD1", "GD2", "GD3", "GD4", "GD5", "GD6"),
    },
}

STATION_COLUMNS = [
    "noaa_station_id",
    "global_hourly_station_id",
    "usaf",
    "wban",
    "station_name",
    "ctry",
    "state",
    "icao",
    "latitude",
    "longitude",
    "elevation_m",
    "county_code",
    "county_name",
    "begin_date",
    "end_date",
    "active",
    "available_isd_parameter_codes",
]

MEASURE_COLUMNS = [
    WEATHER_VARIABLES[variable]["measure_column"]
    for variable in WEATHER_VARIABLE_ORDER
]

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


def parse_site_ids(value: str) -> list[str] | None:
    if not value:
        return None
    return [token.strip() for token in value.split(",") if token.strip()]


def parse_date_or_none(value) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


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
        for path in monitor_dir.glob("*_noaa_isd_monitor_locations_*.csv")
        if "_raw_" not in path.name.lower()
    ]
    if not candidates:
        raise SystemExit(
            f"No NOAA ISD monitor inventory CSV found in {monitor_dir}. "
            "Pass --monitor-csv with a collect_NOAA_monitors.py output file."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_monitor_inventory(path: Path, station_ids: list[str] | None, active_only: bool) -> pd.DataFrame:
    dtype = {
        "noaa_station_id": "string",
        "global_hourly_station_id": "string",
        "usaf": "string",
        "wban": "string",
        "icao": "string",
        "county_code": "string",
        "available_isd_parameter_codes": "string",
    }
    df = pd.read_csv(path, dtype=dtype)

    required = ["noaa_station_id", "global_hourly_station_id"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise SystemExit(f"NOAA monitor inventory is missing required column(s): {', '.join(missing)}")

    df["noaa_station_id"] = df["noaa_station_id"].map(clean_text)
    df["global_hourly_station_id"] = df["global_hourly_station_id"].map(clean_text)
    if "usaf" in df.columns:
        df["usaf"] = df["usaf"].map(lambda value: clean_text(value).zfill(6) if clean_text(value) else "")
    if "wban" in df.columns:
        df["wban"] = df["wban"].map(lambda value: clean_text(value).zfill(5) if clean_text(value) else "")

    for column in STATION_COLUMNS + MEASURE_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    if station_ids:
        wanted = set(station_ids)
        df = df[
            df["noaa_station_id"].isin(wanted)
            | df["global_hourly_station_id"].isin(wanted)
        ].copy()
        found = set(df["noaa_station_id"]) | set(df["global_hourly_station_id"])
        missing_ids = sorted(wanted - found)
        if missing_ids:
            print(
                "WARNING: "
                f"{len(missing_ids)} requested station id(s) were not found: {', '.join(missing_ids)}"
            )

    if active_only:
        df = df[df["active"].map(parse_bool)].copy()

    df = df.drop_duplicates(subset=["noaa_station_id"]).reset_index(drop=True)
    if df.empty:
        raise SystemExit("No NOAA monitor stations remain after applying filters.")
    return df[STATION_COLUMNS + MEASURE_COLUMNS].copy()


def station_period_overlaps(station: pd.Series, start_date: date, end_date: date) -> bool:
    begin_date = parse_date_or_none(station.get("begin_date"))
    station_end_date = parse_date_or_none(station.get("end_date"))
    if station_end_date and station_end_date < start_date:
        return False
    if begin_date and begin_date > end_date:
        return False
    return True


def station_can_measure(station: pd.Series, variable: str) -> bool:
    column = WEATHER_VARIABLES[variable]["measure_column"]
    return parse_bool(station.get(column))


def station_variables_to_fetch(station: pd.Series, variables: list[str]) -> list[str]:
    return [variable for variable in variables if station_can_measure(station, variable)]


def station_year_url(base_url: str, station_id: str, year: int) -> str:
    return f"{base_url.rstrip('/')}/{year}/{station_id}.csv"


def request_station_year_csv(
    session: requests.Session,
    base_url: str,
    station_id: str,
    year: int,
    timeout_seconds: int,
    retries: int,
    request_delay: float,
) -> pd.DataFrame:
    url = station_year_url(base_url, station_id, year)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            if request_delay > 0:
                time.sleep(request_delay)
            response = session.get(url, timeout=timeout_seconds)
            if response.status_code == 404:
                return pd.DataFrame()
            response.raise_for_status()
            return pd.read_csv(io.StringIO(response.text), dtype=str, keep_default_na=False)
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(min(30, 2**attempt))

    print(f"[NOAA] WARNING: Failed to fetch {url}: {last_exc}")
    return pd.DataFrame()


def split_isd_group(value) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(",")]


def parse_scaled_number(text: str, missing_values: set[str], divisor: float) -> float | None:
    value = clean_text(text)
    if not value or value in missing_values:
        return None
    try:
        return int(value) / divisor
    except ValueError:
        return None


def parse_temperature_c(value) -> float | None:
    parts = split_isd_group(value)
    if not parts:
        return None
    return parse_scaled_number(parts[0], {"+9999", "-9999", "9999"}, 10.0)


def parse_dew_point_c(value) -> float | None:
    parts = split_isd_group(value)
    if not parts:
        return None
    return parse_scaled_number(parts[0], {"+9999", "-9999", "9999"}, 10.0)


def parse_relative_humidity(temp_c: float | None, dew_c: float | None) -> float | None:
    if temp_c is None or dew_c is None:
        return None
    if temp_c <= -243.04 or dew_c <= -243.04:
        return None
    exponent = (17.625 * dew_c) / (243.04 + dew_c) - (17.625 * temp_c) / (243.04 + temp_c)
    return max(0.0, min(100.0, 100.0 * math.exp(exponent)))


def parse_wind_direction_degrees(value) -> float | None:
    parts = split_isd_group(value)
    if len(parts) < 1:
        return None
    direction = parse_scaled_number(parts[0], {"999"}, 1.0)
    if direction is None:
        return None
    return direction


def parse_wind_speed_kmh(value) -> float | None:
    parts = split_isd_group(value)
    if len(parts) < 4:
        return None
    speed_ms = parse_scaled_number(parts[3], {"9999"}, 10.0)
    if speed_ms is None:
        return None
    return speed_ms * 3.6


def parse_wind_gust_kmh(value) -> float | None:
    parts = split_isd_group(value)
    if len(parts) < 1:
        return None
    gust_ms = parse_scaled_number(parts[0], {"9999"}, 10.0)
    if gust_ms is None:
        return None
    return gust_ms * 3.6


def parse_one_hour_precip_mm_from_group(value) -> float | None:
    parts = split_isd_group(value)
    if len(parts) < 2:
        return None
    period_hours = parts[0]
    depth_mm = parse_scaled_number(parts[1], {"9999"}, 10.0)
    if period_hours != "01" or depth_mm is None:
        return None
    return depth_mm


def parse_precipitation_mm(row: pd.Series) -> float | None:
    for field in ("AA1", "AA2", "AA3", "AA4"):
        if field not in row:
            continue
        value = parse_one_hour_precip_mm_from_group(row.get(field))
        if value is not None:
            return value
    return None


def parse_solar_radiation(value) -> float | None:
    parts = split_isd_group(value)
    if len(parts) < 1:
        return None
    return parse_scaled_number(parts[0], {"99999", "999999"}, 1.0)


def coverage_code_to_percent(code: str) -> float | None:
    text = clean_text(code)
    if not text or text in {"9", "99"}:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if 0 <= value <= 8:
        return (value / 8.0) * 100.0
    return None


def parse_cloud_cover_percent_from_group(value) -> float | None:
    parts = split_isd_group(value)
    for part in parts[:2]:
        percent = coverage_code_to_percent(part)
        if percent is not None:
            return percent
    return None


def parse_cloud_cover_percent(row: pd.Series) -> float | None:
    for field in ("GD1", "GD2", "GD3", "GD4", "GD5", "GD6"):
        if field not in row:
            continue
        value = parse_cloud_cover_percent_from_group(row.get(field))
        if value is not None:
            return value
    return None


def add_parsed_variable_columns(
    raw: pd.DataFrame,
    station: pd.Series,
    variables: list[str],
) -> pd.DataFrame:
    parsed = raw.copy()
    if "TMP" in parsed.columns:
        parsed["_tmp_c"] = parsed["TMP"].map(parse_temperature_c)
    else:
        parsed["_tmp_c"] = pd.NA
    if "DEW" in parsed.columns:
        parsed["_dew_c"] = parsed["DEW"].map(parse_dew_point_c)
    else:
        parsed["_dew_c"] = pd.NA

    for variable in variables:
        if not station_can_measure(station, variable):
            parsed[variable] = pd.NA
            continue

        if variable == "temperature_2m":
            parsed[variable] = parsed["_tmp_c"]
        elif variable == "relative_humidity_2m":
            parsed[variable] = parsed.apply(
                lambda row: parse_relative_humidity(row["_tmp_c"], row["_dew_c"]),
                axis=1,
            )
        elif variable == "precipitation":
            parsed[variable] = parsed.apply(parse_precipitation_mm, axis=1)
        elif variable == "wind_speed_10m":
            parsed[variable] = (
                parsed["WND"].map(parse_wind_speed_kmh)
                if "WND" in parsed.columns
                else pd.NA
            )
        elif variable == "wind_direction_10m":
            parsed[variable] = (
                parsed["WND"].map(parse_wind_direction_degrees)
                if "WND" in parsed.columns
                else pd.NA
            )
        elif variable == "wind_gusts_10m":
            parsed[variable] = (
                parsed["OC1"].map(parse_wind_gust_kmh)
                if "OC1" in parsed.columns
                else pd.NA
            )
        elif variable == "shortwave_radiation":
            parsed[variable] = (
                parsed["GH1"].map(parse_solar_radiation)
                if "GH1" in parsed.columns
                else pd.NA
            )
        elif variable == "cloud_cover":
            parsed[variable] = parsed.apply(parse_cloud_cover_percent, axis=1)
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
    if raw.empty or "DATE" not in raw.columns:
        return pd.DataFrame(columns=["noaa_station_id", "time", *variables])

    obs = raw.copy()
    obs["_observed_time"] = pd.to_datetime(obs["DATE"], utc=True, errors="coerce")
    obs = obs.dropna(subset=["_observed_time"])
    if obs.empty:
        return pd.DataFrame(columns=["noaa_station_id", "time", *variables])

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
        return pd.DataFrame(columns=["noaa_station_id", "time", *variables])

    obs["_distance_to_hour_seconds"] = (
        obs["_observed_time"] - obs["_binned_time_utc"]
    ).dt.total_seconds().abs()
    obs = add_parsed_variable_columns(obs, station, variables)
    obs["noaa_station_id"] = station["noaa_station_id"]
    obs = obs.sort_values(["time", "_distance_to_hour_seconds"], kind="stable")

    def first_valid(series):
        for value in series:
            if pd.notna(value):
                return value
        return pd.NA

    aggregation = {variable: first_valid for variable in variables}
    hourly = obs.groupby(["noaa_station_id", "time"], as_index=False).agg(aggregation)
    return hourly[["noaa_station_id", "time", *variables]]


def build_local_hourly_grid(
    stations: pd.DataFrame,
    start_date: date,
    end_date: date,
    timezone: str,
) -> pd.DataFrame:
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
    base_url: str,
    timeout_seconds: int,
    retries: int,
    request_delay: float,
) -> pd.DataFrame:
    local_start = pd.Timestamp(start_date).tz_localize(timezone)
    local_end = pd.Timestamp(end_date).tz_localize(timezone) + pd.Timedelta(hours=23)
    years = years_needed_for_grid(local_start, local_end)

    frames = []
    for index, station in stations.iterrows():
        station_id = clean_text(station["global_hourly_station_id"])
        fetch_variables = station_variables_to_fetch(station, variables)
        if not fetch_variables:
            print(f"[NOAA] Skipping {station['noaa_station_id']}: no selected measurable variables")
            continue
        if not station_period_overlaps(station, start_date, end_date):
            print(f"[NOAA] Skipping {station['noaa_station_id']}: station period does not overlap date range")
            continue

        print(
            "[NOAA] Fetching station "
            f"{index + 1}/{len(stations)} station={station['noaa_station_id']} "
            f"years={','.join(str(year) for year in years)}"
        )
        station_frames = []
        for year in years:
            raw = request_station_year_csv(
                session=session,
                base_url=base_url,
                station_id=station_id,
                year=year,
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
        return pd.DataFrame(columns=["noaa_station_id", "time", *variables])

    measurements = pd.concat(frames, ignore_index=True)
    measurements = measurements.sort_values(["noaa_station_id", "time"], kind="stable")
    measurements = measurements.drop_duplicates(subset=["noaa_station_id", "time"], keep="first")
    return measurements[["noaa_station_id", "time", *variables]]


def build_final_output(
    stations: pd.DataFrame,
    measurements: pd.DataFrame,
    variables: list[str],
    start_date: date,
    end_date: date,
    timezone: str,
) -> pd.DataFrame:
    grid = build_local_hourly_grid(stations, start_date, end_date, timezone)
    final = grid.merge(measurements, on=["noaa_station_id", "time"], how="left")

    for variable in variables:
        if variable not in final.columns:
            final[variable] = pd.NA

    final = final.sort_values(["noaa_station_id", "time"], kind="stable").reset_index(drop=True)
    final["date_local"] = final["time"].dt.strftime("%Y-%m-%d")
    final["time_local"] = final["time"].dt.strftime("%H:%M")
    final["time"] = final["time"].astype(str)

    output_columns = [*OUTPUT_BASE_COLUMNS, *variables]
    return final[output_columns]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Collect hourly NOAA ISD Global Hourly weather variables for station sites listed "
            "in a collect_NOAA_monitors.py inventory CSV."
        )
    )
    parser.add_argument("--start-date", type=parse_iso_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=parse_iso_date, required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--timezone",
        default="America/Chicago",
        help="IANA timezone for output hourly timestamps. Defaults to America/Chicago.",
    )
    parser.add_argument(
        "--monitor-csv",
        default=None,
        help=(
            "Path to NOAA ISD monitor inventory CSV. Defaults to the newest "
            "NOAA_data/monitor_locations/*_noaa_isd_monitor_locations_*.csv file."
        ),
    )
    parser.add_argument(
        "--monitor-dir",
        default=str(Path("NOAA_data") / "monitor_locations"),
        help="Directory searched when --monitor-csv is omitted.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path("NOAA_data") / "weather_data"),
        help="Directory for the generated hourly weather CSV.",
    )
    parser.add_argument("--out-prefix", default="greater_houston")
    parser.add_argument("--output-file", default=None, help="Optional explicit output CSV path.")
    parser.add_argument(
        "--variables",
        default=",".join(WEATHER_VARIABLE_ORDER),
        help="Comma-separated weather variable columns to collect.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only query monitor inventory rows whose active column is true.",
    )
    parser.add_argument(
        "--station-ids",
        default="",
        help="Optional comma-separated noaa_station_id or global_hourly_station_id subset.",
    )
    parser.add_argument(
        "--max-stations",
        type=int,
        default=None,
        help="Optional first-N station limit after filters, useful for smoke tests.",
    )
    parser.add_argument(
        "--hour-binning",
        choices=["round", "floor", "ceil"],
        default="round",
        help="How to align sub-hourly NOAA observation timestamps to output hourly intervals.",
    )
    parser.add_argument(
        "--base-url",
        default=NOAA_GLOBAL_HOURLY_BASE_URL,
        help="Base URL for NOAA Global Hourly station-year CSV files.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.05,
        help="Seconds to wait between NOAA station-year requests.",
    )
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
    station_ids = parse_site_ids(args.station_ids)

    monitor_csv = Path(args.monitor_csv) if args.monitor_csv else latest_monitor_csv(Path(args.monitor_dir))
    stations = load_monitor_inventory(
        path=monitor_csv,
        station_ids=station_ids,
        active_only=args.active_only,
    )
    if args.max_stations:
        stations = stations.head(args.max_stations).copy()
    if stations.empty:
        raise SystemExit("No NOAA monitor stations remain after applying filters.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = (
            out_dir
            / f"{args.out_prefix}_noaa_weather_hourly_"
            f"{args.start_date:%Y%m%d}_{args.end_date:%Y%m%d}_{timestamp}.csv"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[NOAA] Monitor inventory: {monitor_csv}")
    print(f"[NOAA] Stations in output grid: {len(stations)}")
    print(f"[NOAA] Variables: {', '.join(variables)}")

    session = requests.Session()
    session.headers.update({"User-Agent": "2026-Air-Quality-Summer-Research NOAA weather collector"})

    measurements = fetch_station_measurements(
        session=session,
        stations=stations,
        variables=variables,
        start_date=args.start_date,
        end_date=args.end_date,
        timezone=args.timezone,
        hour_binning=args.hour_binning,
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        request_delay=args.request_delay,
    )
    print(f"[NOAA] Station-hour observations with at least one parsed value: {len(measurements)}")

    final = build_final_output(
        stations=stations,
        measurements=measurements,
        variables=variables,
        start_date=args.start_date,
        end_date=args.end_date,
        timezone=args.timezone,
    )
    final.to_csv(output_file, index=False)
    print(f"[NOAA] Saved hourly NOAA weather variables -> {output_file}")
    print(f"[NOAA] Output rows: {len(final)}")


if __name__ == "__main__":
    main()
