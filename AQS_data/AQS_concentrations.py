import argparse
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests


AQS_BASE_URL = "https://aqs.epa.gov/data/api"
MAX_AQS_PARAMS_PER_REQUEST = 5

POLLUTANT_ORDER = [
    "pm2_5",
    "pm10",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
]

POLLUTANTS = {
    "pm2_5": {
        "measure_column": "measures_pm2_5",
        "parameter_codes": ("88502", "88101"),
        "label": "PM2.5",
    },
    "pm10": {
        "measure_column": "measures_pm10",
        "parameter_codes": ("81102",),
        "label": "PM10",
    },
    "carbon_monoxide": {
        "measure_column": "measures_carbon_monoxide",
        "parameter_codes": ("42101",),
        "label": "Carbon monoxide",
    },
    "nitrogen_dioxide": {
        "measure_column": "measures_nitrogen_dioxide",
        "parameter_codes": ("42602",),
        "label": "Nitrogen dioxide",
    },
    "sulphur_dioxide": {
        "measure_column": "measures_sulphur_dioxide",
        "parameter_codes": ("42401",),
        "label": "Sulphur dioxide",
    },
    "ozone": {
        "measure_column": "measures_ozone",
        "parameter_codes": ("44201",),
        "label": "Ozone",
    },
}

STATION_COLUMNS = [
    "monitor_site_id",
    "state_code",
    "county_code",
    "county_name",
    "site_number",
    "site_name",
    "latitude",
    "longitude",
    "datum",
    "address",
    "city",
    "cbsa_code",
    "open_date",
    "close_date",
    "active",
    "available_parameter_codes",
]

MEASURE_COLUMNS = [POLLUTANTS[pollutant]["measure_column"] for pollutant in POLLUTANT_ORDER]

OUTPUT_COLUMNS = [
    *STATION_COLUMNS,
    "time",
    "date_local",
    "time_local",
    *POLLUTANT_ORDER,
]


class AQSRequestPacer:
    """Keep EPA AQS requests inside its 10 requests/minute guidance."""

    def __init__(self, delay_seconds: float = 6.0):
        self.delay_seconds = float(delay_seconds)
        self._last_request = 0.0

    def wait(self) -> None:
        if self.delay_seconds <= 0:
            return
        now = time.monotonic()
        sleep_for = self.delay_seconds - (now - self._last_request)
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_request = time.monotonic()


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD date, got {value!r}") from exc


def aqs_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def iter_year_chunks(start: date, end: date):
    current = start
    while current <= end:
        chunk_end = min(end, date(current.year, 12, 31))
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def chunk_list(values: list[str], size: int):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def normalize_code(value, width: int) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        text = str(int(float(text)))
    except ValueError:
        pass
    return text.zfill(width)


def clean_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def parse_bool(value) -> bool:
    text = clean_text(value).lower()
    return text in {"1", "true", "t", "yes", "y"}


def resolve_credentials(args) -> tuple[str, str]:
    email = args.email or os.environ.get("AQS_EMAIL")
    key = args.key or os.environ.get("AQS_KEY")
    if not email or not key:
        raise SystemExit(
            "EPA AQS API credentials are required. Set AQS_EMAIL and AQS_KEY, "
            "or pass --email and --key. Register/reset a key at "
            "https://aqs.epa.gov/data/api/signup?email=your.email@example.com"
        )
    return email, key


def selected_pollutants(pollutant_text: str) -> dict:
    names = [name.strip() for name in pollutant_text.split(",") if name.strip()]
    unknown = sorted(set(names) - set(POLLUTANTS))
    if unknown:
        raise SystemExit(
            "Unknown pollutant(s): "
            + ", ".join(unknown)
            + ". Valid options: "
            + ", ".join(POLLUTANT_ORDER)
        )
    return {name: POLLUTANTS[name] for name in POLLUTANT_ORDER if name in names}


def latest_monitor_csv(monitor_dir: Path) -> Path:
    candidates = [
        path
        for path in monitor_dir.glob("*_aqs_monitor_locations_*.csv")
        if "_raw_" not in path.name.lower()
    ]
    if not candidates:
        raise SystemExit(
            f"No monitor inventory CSV found in {monitor_dir}. "
            "Pass --monitor-csv with a collect_AQS_monitors.py output file."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_monitor_inventory(path: Path, site_ids: list[str] | None, active_only: bool) -> pd.DataFrame:
    dtype = {
        "monitor_site_id": "string",
        "state_code": "string",
        "county_code": "string",
        "site_number": "string",
        "cbsa_code": "string",
        "available_parameter_codes": "string",
    }
    df = pd.read_csv(path, dtype=dtype)

    required = ["monitor_site_id", "state_code", "county_code", "site_number"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise SystemExit(f"Monitor inventory is missing required column(s): {', '.join(missing)}")

    for column, width in [("state_code", 2), ("county_code", 3), ("site_number", 4)]:
        df[column] = df[column].map(lambda value: normalize_code(value, width))

    df["monitor_site_id"] = (
        df["state_code"] + "-" + df["county_code"] + "-" + df["site_number"]
    )

    for column in STATION_COLUMNS + MEASURE_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    if site_ids:
        wanted = set(site_ids)
        df = df[df["monitor_site_id"].isin(wanted)].copy()
        missing_sites = sorted(wanted - set(df["monitor_site_id"]))
        if missing_sites:
            print(f"WARNING: {len(missing_sites)} requested site id(s) were not found: {', '.join(missing_sites)}")

    if active_only and "active" in df.columns:
        df = df[df["active"].map(parse_bool)].copy()

    df = df.drop_duplicates(subset=["monitor_site_id"]).reset_index(drop=True)
    if df.empty:
        raise SystemExit("No monitor sites remain after applying filters.")
    return df[STATION_COLUMNS + MEASURE_COLUMNS].copy()


def response_error_text(header: dict) -> str:
    errors = header.get("error") or header.get("errors") or header.get("message") or header
    return str(errors)


def aqs_get(
    session: requests.Session,
    pacer: AQSRequestPacer,
    endpoint: str,
    params: dict,
    timeout_seconds: int,
    retries: int,
    allow_no_data: bool = True,
) -> list[dict]:
    clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
    last_exc = None

    for attempt in range(1, retries + 1):
        try:
            pacer.wait()
            response = session.get(
                f"{AQS_BASE_URL}/{endpoint}",
                params=clean_params,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()

            header = {}
            if isinstance(payload.get("Header"), list) and payload["Header"]:
                header = payload["Header"][0]

            status = str(header.get("status", "")).lower()
            if "failed" in status or status in {"error", "failure"}:
                error_text = response_error_text(header)
                if allow_no_data and "no data" in error_text.lower():
                    return []
                raise RuntimeError(f"AQS API request failed for {endpoint}: {error_text}")

            data = payload.get("Data")
            if data is None:
                data = payload.get("Body", [])
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected AQS response data for {endpoint}: {type(data)}")
            return data
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(min(30, 2**attempt))

    raise RuntimeError(f"AQS API request failed after {retries} attempts: {last_exc}")


def station_parameter_codes(station: pd.Series, pollutants: dict) -> list[str]:
    available_codes = {
        normalize_code(code, 5)
        for code in clean_text(station.get("available_parameter_codes")).replace(",", ";").split(";")
        if clean_text(code)
    }

    selected_codes = []
    for pollutant_name, config in pollutants.items():
        measure_column = config["measure_column"]
        if measure_column in station and not parse_bool(station[measure_column]):
            continue

        matching_available = [
            code for code in config["parameter_codes"] if code in available_codes
        ]
        if matching_available:
            selected_codes.extend(matching_available)
        elif parse_bool(station.get(measure_column)):
            selected_codes.extend(config["parameter_codes"])

    seen = set()
    unique_codes = []
    for code in selected_codes:
        if code not in seen:
            seen.add(code)
            unique_codes.append(code)
    return unique_codes


def fetch_sample_rows(
    session: requests.Session,
    pacer: AQSRequestPacer,
    email: str,
    key: str,
    stations: pd.DataFrame,
    pollutants: dict,
    start_date: date,
    end_date: date,
    sample_duration: str,
    timeout_seconds: int,
    retries: int,
) -> pd.DataFrame:
    all_rows = []
    for _, station in stations.iterrows():
        parameter_codes = station_parameter_codes(station, pollutants)
        if not parameter_codes:
            print(f"[AQS] Skipping {station['monitor_site_id']}: no selected pollutant parameter codes")
            continue

        for chunk_start, chunk_end in iter_year_chunks(start_date, end_date):
            for param_chunk in chunk_list(parameter_codes, MAX_AQS_PARAMS_PER_REQUEST):
                print(
                    "[AQS] Fetching sampleData "
                    f"site={station['monitor_site_id']} "
                    f"params={','.join(param_chunk)} "
                    f"dates={aqs_date(chunk_start)}-{aqs_date(chunk_end)}"
                )
                rows = aqs_get(
                    session=session,
                    pacer=pacer,
                    endpoint="sampleData/bySite",
                    params={
                        "email": email,
                        "key": key,
                        "param": ",".join(param_chunk),
                        "bdate": aqs_date(chunk_start),
                        "edate": aqs_date(chunk_end),
                        "state": station["state_code"],
                        "county": station["county_code"],
                        "site": station["site_number"],
                        "duration": sample_duration,
                    },
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                )
                print(f"[AQS] -> {len(rows)} sample rows")
                for row in rows:
                    normalized = dict(row)
                    normalized["monitor_site_id"] = station["monitor_site_id"]
                    normalized["state_code"] = normalize_code(
                        normalized.get("state_code", station["state_code"]),
                        2,
                    )
                    normalized["county_code"] = normalize_code(
                        normalized.get("county_code", station["county_code"]),
                        3,
                    )
                    normalized["site_number"] = normalize_code(
                        normalized.get("site_number", station["site_number"]),
                        4,
                    )
                    normalized["parameter_code"] = normalize_code(normalized.get("parameter_code"), 5)
                    all_rows.append(normalized)

    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows)


def build_hourly_grid(stations: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) + pd.Timedelta(hours=23)
    times = pd.date_range(start=start_ts, end=end_ts, freq="h")

    time_df = pd.DataFrame({"time": times})
    time_df["date_local"] = time_df["time"].dt.strftime("%Y-%m-%d")
    time_df["time_local"] = time_df["time"].dt.strftime("%H:%M")

    stations = stations.copy()
    stations["_join_key"] = 1
    time_df["_join_key"] = 1
    grid = stations.merge(time_df, on="_join_key", how="outer").drop(columns="_join_key")
    return grid


def build_measurement_wide(samples: pd.DataFrame, pollutants: dict) -> pd.DataFrame:
    key_columns = ["monitor_site_id", "time", "pollutant"]
    if samples.empty:
        return pd.DataFrame(columns=key_columns + ["sample_measurement"])

    code_to_pollutant = {
        code: pollutant_name
        for pollutant_name, config in pollutants.items()
        for code in config["parameter_codes"]
    }
    samples = samples.copy()
    samples["parameter_code"] = samples["parameter_code"].map(lambda value: normalize_code(value, 5))
    samples["pollutant"] = samples["parameter_code"].map(code_to_pollutant)
    samples = samples.dropna(subset=["pollutant"])
    if samples.empty:
        return pd.DataFrame(columns=["monitor_site_id", "time", *POLLUTANT_ORDER])

    samples["sample_measurement"] = pd.to_numeric(samples.get("sample_measurement"), errors="coerce")
    samples["time"] = pd.to_datetime(
        samples["date_local"].astype(str) + " " + samples["time_local"].astype(str),
        errors="coerce",
    )
    samples = samples.dropna(subset=["time", "sample_measurement"])
    if samples.empty:
        return pd.DataFrame(columns=["monitor_site_id", "time", *POLLUTANT_ORDER])

    grouped = samples.groupby(
        ["monitor_site_id", "time", "pollutant"],
        as_index=False,
    )["sample_measurement"].mean()

    wide = grouped.pivot_table(
        index=["monitor_site_id", "time"],
        columns="pollutant",
        values="sample_measurement",
        aggfunc="mean",
    ).reset_index()
    wide.columns.name = None

    for pollutant in POLLUTANT_ORDER:
        if pollutant not in wide.columns:
            wide[pollutant] = pd.NA
    return wide[["monitor_site_id", "time", *POLLUTANT_ORDER]]


def build_final_output(
    stations: pd.DataFrame,
    samples: pd.DataFrame,
    pollutants: dict,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    grid = build_hourly_grid(stations, start_date, end_date)
    measurements = build_measurement_wide(samples, pollutants)
    final = grid.merge(measurements, on=["monitor_site_id", "time"], how="left")

    for pollutant in POLLUTANT_ORDER:
        if pollutant not in final.columns:
            final[pollutant] = pd.NA

    final["time"] = pd.to_datetime(final["time"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    final["date_local"] = pd.to_datetime(final["time"], errors="coerce").dt.strftime("%Y-%m-%d")
    final["time_local"] = pd.to_datetime(final["time"], errors="coerce").dt.strftime("%H:%M")

    return final[OUTPUT_COLUMNS].sort_values(
        ["monitor_site_id", "time"],
        kind="stable",
    ).reset_index(drop=True)


def parse_site_ids(value: str) -> list[str] | None:
    if not value:
        return None
    return [token.strip() for token in value.split(",") if token.strip()]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Collect hourly EPA AQS pollutant concentrations for monitor sites listed in a "
            "collect_AQS_monitors.py inventory CSV."
        )
    )
    parser.add_argument("--start-date", type=parse_iso_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", type=parse_iso_date, required=True, help="YYYY-MM-DD")
    parser.add_argument("--email", default=None, help="EPA AQS API email. Defaults to AQS_EMAIL.")
    parser.add_argument("--key", default=None, help="EPA AQS API key. Defaults to AQS_KEY.")
    parser.add_argument(
        "--monitor-csv",
        default=None,
        help=(
            "Path to monitor inventory CSV. Defaults to the newest "
            "AQS_data/monitor_locations/*_aqs_monitor_locations_*.csv file."
        ),
    )
    parser.add_argument(
        "--monitor-dir",
        default=str(Path("AQS_data") / "monitor_locations"),
        help="Directory searched when --monitor-csv is omitted.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path("AQS_data") / "air_data"),
        help="Directory for the generated hourly concentration CSV.",
    )
    parser.add_argument("--out-prefix", default="greater_houston")
    parser.add_argument("--output-file", default=None, help="Optional explicit output CSV path.")
    parser.add_argument(
        "--pollutants",
        default=",".join(POLLUTANT_ORDER),
        help="Comma-separated pollutant columns to collect.",
    )
    parser.add_argument(
        "--sample-duration",
        default="1",
        help="AQS sample duration code. Use 1 for 1-hour samples.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only query monitor inventory rows whose active column is true.",
    )
    parser.add_argument(
        "--site-ids",
        default="",
        help="Optional comma-separated monitor_site_id subset, useful for smoke tests.",
    )
    parser.add_argument(
        "--max-sites",
        type=int,
        default=None,
        help="Optional first-N site limit after filters, useful for smoke tests.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=6.0,
        help="Seconds to wait between AQS API requests. EPA requests no more than 10/minute.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--save-raw",
        action="store_true",
        help="Also save raw AQS sampleData rows used to build the hourly wide CSV.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.max_sites is not None and args.max_sites < 1:
        raise SystemExit("--max-sites must be at least 1")

    email, key = resolve_credentials(args)
    pollutants = selected_pollutants(args.pollutants)

    monitor_csv = Path(args.monitor_csv) if args.monitor_csv else latest_monitor_csv(Path(args.monitor_dir))
    site_ids = parse_site_ids(args.site_ids)
    stations = load_monitor_inventory(monitor_csv, site_ids=site_ids, active_only=args.active_only)
    if args.max_sites is not None:
        stations = stations.head(args.max_sites).copy()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_token = args.start_date.isoformat().replace("-", "")
    end_token = args.end_date.isoformat().replace("-", "")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = out_dir / (
            f"{args.out_prefix}_aqs_concentrations_hourly_{start_token}_{end_token}_{timestamp}.csv"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[AQS] Monitor inventory: {monitor_csv}")
    print(f"[AQS] Sites to query: {len(stations)}")

    session = requests.Session()
    pacer = AQSRequestPacer(delay_seconds=args.request_delay)
    samples = fetch_sample_rows(
        session=session,
        pacer=pacer,
        email=email,
        key=key,
        stations=stations,
        pollutants=pollutants,
        start_date=args.start_date,
        end_date=args.end_date,
        sample_duration=args.sample_duration,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )
    print(f"[AQS] Total raw sample rows: {len(samples)}")

    final = build_final_output(
        stations=stations,
        samples=samples,
        pollutants=pollutants,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    final.to_csv(output_file, index=False)
    print(f"[AQS] Saved hourly AQS concentrations -> {output_file}")
    print(f"[AQS] Output rows: {len(final)}")

    if args.save_raw:
        raw_file = out_dir / (
            f"{args.out_prefix}_aqs_concentrations_raw_{start_token}_{end_token}_{timestamp}.csv"
        )
        samples.to_csv(raw_file, index=False)
        print(f"[AQS] Saved raw AQS sampleData rows -> {raw_file}")


if __name__ == "__main__":
    main()
