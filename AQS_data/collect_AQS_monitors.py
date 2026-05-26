import argparse
import os
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests


AQS_BASE_URL = "https://aqs.epa.gov/data/api"
TEXAS_STATE_CODE = "48"

HOUSTON_COUNTIES = {
    "015": "Austin",
    "039": "Brazoria",
    "071": "Chambers",
    "157": "Fort Bend",
    "167": "Galveston",
    "201": "Harris",
    "291": "Liberty",
    "339": "Montgomery",
    "473": "Waller",
}

POLLUTANT_ORDER = [
    "pm2_5",
    "pm10",
    "ozone",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
]

OPEN_METEO_POLLUTANTS = {
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
    "ozone": {
        "measure_column": "measures_ozone",
        "parameter_codes": ("44201",),
        "label": "Ozone",
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
}

OUTPUT_COLUMNS = [
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
    "measures_pm2_5",
    "measures_pm10",
    "measures_ozone",
    "measures_carbon_monoxide",
    "measures_nitrogen_dioxide",
    "measures_sulphur_dioxide",
    "available_parameter_codes",
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


def first_non_empty_from_row(row: dict, names: list[str]) -> str:
    for name in names:
        value = clean_text(row.get(name))
        if value:
            return value
    return ""


def first_non_empty_from_group(group: pd.DataFrame, names: list[str]) -> str:
    for name in names:
        if name not in group.columns:
            continue
        for value in group[name]:
            text = clean_text(value)
            if text:
                return text
    return ""


def first_number_from_group(group: pd.DataFrame, names: list[str]):
    for name in names:
        if name not in group.columns:
            continue
        values = pd.to_numeric(group[name], errors="coerce").dropna()
        if not values.empty:
            return values.iloc[0]
    return pd.NA


def parse_optional_date(value):
    text = clean_text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


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
    unknown = sorted(set(names) - set(OPEN_METEO_POLLUTANTS))
    if unknown:
        raise SystemExit(
            "Unknown pollutant(s): "
            + ", ".join(unknown)
            + ". Valid options: "
            + ", ".join(POLLUTANT_ORDER)
        )
    return {
        name: OPEN_METEO_POLLUTANTS[name]
        for name in POLLUTANT_ORDER
        if name in names
    }


def selected_counties(county_text: str) -> dict:
    if not county_text:
        return HOUSTON_COUNTIES.copy()

    selected = {}
    county_name_to_code = {name.lower(): code for code, name in HOUSTON_COUNTIES.items()}
    for raw_token in county_text.split(","):
        token = raw_token.strip()
        if not token:
            continue
        code = normalize_code(token, 3)
        if code in HOUSTON_COUNTIES:
            selected[code] = HOUSTON_COUNTIES[code]
            continue
        code = county_name_to_code.get(token.lower())
        if code:
            selected[code] = HOUSTON_COUNTIES[code]
            continue
        raise SystemExit(
            f"Unknown Houston county {token!r}. Use a county name or one of: "
            + ", ".join(f"{code} ({name})" for code, name in HOUSTON_COUNTIES.items())
        )

    if not selected:
        raise SystemExit("No valid counties were selected.")
    return selected


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


def fetch_monitor_rows(
    session: requests.Session,
    pacer: AQSRequestPacer,
    email: str,
    key: str,
    counties: dict,
    pollutants: dict,
    start_date: date,
    end_date: date,
    timeout_seconds: int,
    retries: int,
) -> list[dict]:
    rows = []
    for county_code, county_name in counties.items():
        for pollutant_name, config in pollutants.items():
            for parameter_code in config["parameter_codes"]:
                print(
                    "[AQS] Fetching monitors "
                    f"county={county_name} ({county_code}) "
                    f"pollutant={config['label']} param={parameter_code}"
                )
                response_rows = aqs_get(
                    session=session,
                    pacer=pacer,
                    endpoint="monitors/byCounty",
                    params={
                        "email": email,
                        "key": key,
                        "param": parameter_code,
                        "bdate": aqs_date(start_date),
                        "edate": aqs_date(end_date),
                        "state": TEXAS_STATE_CODE,
                        "county": county_code,
                    },
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                )
                print(f"[AQS] -> {len(response_rows)} monitor rows")
                for row in response_rows:
                    normalized = dict(row)
                    normalized["state_code"] = normalize_code(
                        first_non_empty_from_row(normalized, ["state_code", "state"]),
                        2,
                    )
                    normalized["county_code"] = normalize_code(
                        first_non_empty_from_row(normalized, ["county_code", "county"]),
                        3,
                    )
                    normalized["site_number"] = normalize_code(
                        first_non_empty_from_row(normalized, ["site_number", "site"]),
                        4,
                    )
                    normalized["parameter_code"] = normalize_code(
                        first_non_empty_from_row(normalized, ["parameter_code", "param"]),
                        5,
                    )
                    if not normalized["parameter_code"]:
                        normalized["parameter_code"] = parameter_code
                    if not normalized["state_code"]:
                        normalized["state_code"] = TEXAS_STATE_CODE
                    if not normalized["county_code"]:
                        normalized["county_code"] = county_code
                    normalized["_requested_pollutant"] = pollutant_name
                    normalized["_requested_county_name"] = county_name
                    rows.append(normalized)
    return rows


def sort_parameter_codes(codes: set[str], pollutants: dict) -> list[str]:
    preferred_order = []
    for pollutant_name in POLLUTANT_ORDER:
        if pollutant_name not in pollutants:
            continue
        preferred_order.extend(pollutants[pollutant_name]["parameter_codes"])
    order_lookup = {code: idx for idx, code in enumerate(preferred_order)}
    return sorted(codes, key=lambda code: (order_lookup.get(code, 999), code))


def monitor_is_active(row: pd.Series, active_as_of: date) -> bool:
    open_date = parse_optional_date(first_non_empty_from_row(row, ["open_date", "monitor_open_date"]))
    close_date = parse_optional_date(first_non_empty_from_row(row, ["close_date", "monitor_close_date"]))
    if open_date and open_date > active_as_of:
        return False
    if close_date and close_date < active_as_of:
        return False
    return True


def min_open_date(group: pd.DataFrame) -> str:
    parsed_dates = []
    for _, row in group.iterrows():
        parsed = parse_optional_date(first_non_empty_from_row(row, ["open_date", "monitor_open_date"]))
        if parsed:
            parsed_dates.append(parsed)
    if not parsed_dates:
        return ""
    return min(parsed_dates).isoformat()


def station_close_date(group: pd.DataFrame) -> str:
    parsed_dates = []
    has_open_ended_monitor = False
    for _, row in group.iterrows():
        raw_close = first_non_empty_from_row(row, ["close_date", "monitor_close_date"])
        parsed = parse_optional_date(raw_close)
        if parsed:
            parsed_dates.append(parsed)
        else:
            has_open_ended_monitor = True

    if has_open_ended_monitor or not parsed_dates:
        return ""
    return max(parsed_dates).isoformat()


def build_station_inventory(
    raw_rows: list[dict],
    counties: dict,
    pollutants: dict,
    active_as_of: date,
) -> pd.DataFrame:
    if not raw_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    monitors = pd.DataFrame(raw_rows)
    for column, width in [("state_code", 2), ("county_code", 3), ("site_number", 4)]:
        monitors[column] = monitors[column].map(lambda value: normalize_code(value, width))
    monitors["parameter_code"] = monitors["parameter_code"].map(lambda value: normalize_code(value, 5))
    monitors = monitors[
        monitors["state_code"].ne("")
        & monitors["county_code"].ne("")
        & monitors["site_number"].ne("")
    ].copy()

    code_to_pollutant = {
        code: pollutant_name
        for pollutant_name, config in pollutants.items()
        for code in config["parameter_codes"]
    }
    monitors["_pollutant_sort"] = monitors["parameter_code"].map(
        lambda code: POLLUTANT_ORDER.index(code_to_pollutant[code])
        if code in code_to_pollutant
        else len(POLLUTANT_ORDER)
    )
    if "primary_indicator" in monitors.columns:
        monitors["_primary_sort"] = monitors["primary_indicator"].astype(str).str.upper().eq("Y").astype(int)
    else:
        monitors["_primary_sort"] = 0
    monitors = monitors.sort_values(
        ["county_code", "site_number", "_pollutant_sort", "_primary_sort"],
        ascending=[True, True, True, False],
    )

    inventory_rows = []
    for (state_code, county_code, site_number), group in monitors.groupby(
        ["state_code", "county_code", "site_number"],
        dropna=False,
    ):
        available_codes = {
            clean_text(code)
            for code in group["parameter_code"].dropna().tolist()
            if clean_text(code)
        }
        monitor_site_id = f"{state_code}-{county_code}-{site_number}"
        row = {
            "monitor_site_id": monitor_site_id,
            "state_code": state_code,
            "county_code": county_code,
            "county_name": first_non_empty_from_group(group, ["county_name"])
            or counties.get(county_code, ""),
            "site_number": site_number,
            "site_name": first_non_empty_from_group(group, ["local_site_name", "site_name"]),
            "latitude": first_number_from_group(group, ["latitude", "lat"]),
            "longitude": first_number_from_group(group, ["longitude", "lon", "lng"]),
            "datum": first_non_empty_from_group(group, ["datum"]),
            "address": first_non_empty_from_group(group, ["address"]),
            "city": first_non_empty_from_group(group, ["city_name", "city"]),
            "cbsa_code": first_non_empty_from_group(group, ["cbsa_code"]),
            "open_date": min_open_date(group),
            "close_date": station_close_date(group),
            "active": bool(any(monitor_is_active(monitor_row, active_as_of) for _, monitor_row in group.iterrows())),
            "available_parameter_codes": ";".join(sort_parameter_codes(available_codes, pollutants)),
        }

        for pollutant_name in POLLUTANT_ORDER:
            column = OPEN_METEO_POLLUTANTS[pollutant_name]["measure_column"]
            codes = set(OPEN_METEO_POLLUTANTS[pollutant_name]["parameter_codes"])
            row[column] = bool(available_codes & codes) if pollutant_name in pollutants else False

        inventory_rows.append(row)

    inventory = pd.DataFrame(inventory_rows)
    for column in OUTPUT_COLUMNS:
        if column not in inventory.columns:
            inventory[column] = pd.NA
    return inventory[OUTPUT_COLUMNS].sort_values(
        ["county_code", "site_number"],
        kind="stable",
    ).reset_index(drop=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Create a one-row-per-site EPA AQS monitor inventory for the Greater Houston "
            "counties and flag whether each site measures the pollutants used in Open-Meteo."
        )
    )
    parser.add_argument("--email", default=None, help="EPA AQS API email. Defaults to AQS_EMAIL.")
    parser.add_argument("--key", default=None, help="EPA AQS API key. Defaults to AQS_KEY.")
    parser.add_argument(
        "--start-date",
        type=parse_iso_date,
        default=date(1980, 1, 1),
        help="First monitor operating date to include. Defaults to 1980-01-01.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_iso_date,
        default=date.today(),
        help="Last monitor operating date to include. Defaults to today.",
    )
    parser.add_argument(
        "--active-as-of",
        type=parse_iso_date,
        default=date.today(),
        help="Date used to compute the active column. Defaults to today.",
    )
    parser.add_argument(
        "--counties",
        default="",
        help=(
            "Optional comma-separated subset of Houston counties by name or county code. "
            "Defaults to all configured Greater Houston counties."
        ),
    )
    parser.add_argument(
        "--pollutants",
        default=",".join(POLLUTANT_ORDER),
        help="Comma-separated Open-Meteo pollutant names to check.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path("AQS_data") / "monitor_locations"),
        help="Directory for the generated monitor inventory CSV.",
    )
    parser.add_argument(
        "--out-prefix",
        default="greater_houston",
        help="Prefix for the generated CSV filename.",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Optional explicit CSV path. Overrides --out-dir and --out-prefix.",
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
        help="Also save the raw parameter-level monitor rows used to build the inventory.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Write a header-only CSV if AQS returns no monitor rows. By default, no-row runs fail.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")

    email, key = resolve_credentials(args)
    counties = selected_counties(args.counties)
    pollutants = selected_pollutants(args.pollutants)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = out_dir / f"{args.out_prefix}_aqs_monitor_locations_{timestamp}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    pacer = AQSRequestPacer(delay_seconds=args.request_delay)

    raw_rows = fetch_monitor_rows(
        session=session,
        pacer=pacer,
        email=email,
        key=key,
        counties=counties,
        pollutants=pollutants,
        start_date=args.start_date,
        end_date=args.end_date,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )
    print(f"[AQS] Total raw monitor rows: {len(raw_rows)}")
    if not raw_rows and not args.allow_empty:
        raise SystemExit(
            "AQS returned no monitor rows for the requested counties, pollutants, and date window. "
            "No CSV was written. If an empty inventory is expected, rerun with --allow-empty."
        )

    inventory = build_station_inventory(
        raw_rows=raw_rows,
        counties=counties,
        pollutants=pollutants,
        active_as_of=args.active_as_of,
    )
    inventory.to_csv(output_file, index=False)
    print(f"[AQS] Saved Greater Houston monitor inventory -> {output_file}")
    print(f"[AQS] Sites: {len(inventory)}")

    if args.save_raw:
        raw_file = out_dir / f"{args.out_prefix}_aqs_monitor_locations_raw_{timestamp}.csv"
        pd.DataFrame(raw_rows).to_csv(raw_file, index=False)
        print(f"[AQS] Saved raw monitor rows -> {raw_file}")


if __name__ == "__main__":
    main()
