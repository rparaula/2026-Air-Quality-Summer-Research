import argparse
import csv
import io
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests


GHCNH_STATION_LIST_SOURCES = [
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/ghcnh-station-list.txt",
    "https://www.ncei.noaa.gov/data/global-historical-climate-network-hourly/doc/ghcnh-station-list.txt",
]
GHCNH_YEAR_FILE_TEMPLATES = [
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/{year}/psv/GHCNh_{station_id}_{year}.psv",
    "https://www.ncei.noaa.gov/data/global-historical-climate-network-hourly/access/by-year/{year}/psv/GHCNh_{station_id}_{year}.psv",
    "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/{year}/GHCNh_{station_id}_{year}.psv",
    "https://www.ncei.noaa.gov/data/global-historical-climate-network-hourly/access/by-year/{year}/GHCNh_{station_id}_{year}.psv",
]
CENSUS_TIGERWEB_COUNTIES_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/State_County/MapServer/63/query"
)

TEXAS_STATE_CODE = "48"
TEXAS_STATE_ABBR = "TX"

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

HOUSTON_PREFILTER_BBOX = {
    "min_lat": 28.65,
    "max_lat": 30.75,
    "min_lon": -96.75,
    "max_lon": -94.20,
}

OPEN_METEO_WEATHER_ORDER = [
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

GHCNH_VARIABLE_CANDIDATES = {
    "temperature": ("temperature", "dry_bulb_temperature"),
    "dew_point_temperature": ("dew_point_temperature", "dew_point", "dewpoint"),
    "relative_humidity": ("relative_humidity", "relative_humidity_pct"),
    "precipitation": ("precipitation", "precipitation_amount"),
    "wind_speed": ("wind_speed",),
    "wind_direction": ("wind_direction",),
    "wind_gust": ("wind_gust",),
    "sky_cover": ("sky_cover_1", "sky_cover_2", "sky_cover_3"),
}

OPEN_METEO_TO_GHCNH = {
    "temperature_2m": {
        "measure_column": "measures_temperature_2m",
        "ghcnh_variables": ("temperature", "dry_bulb_temperature"),
        "parameter_name": "temperature / dry_bulb_temperature",
        "mapping_type": "direct",
    },
    "relative_humidity_2m": {
        "measure_column": "measures_relative_humidity_2m",
        "ghcnh_variables": ("relative_humidity", "temperature", "dew_point_temperature"),
        "parameter_name": "relative_humidity, or derived from temperature and dew_point_temperature",
        "mapping_type": "direct_or_derived",
    },
    "precipitation": {
        "measure_column": "measures_precipitation",
        "ghcnh_variables": ("precipitation",),
        "parameter_name": "precipitation",
        "mapping_type": "direct",
    },
    "wind_speed_10m": {
        "measure_column": "measures_wind_speed_10m",
        "ghcnh_variables": ("wind_speed",),
        "parameter_name": "wind_speed",
        "mapping_type": "direct_surface",
    },
    "wind_speed_100m": {
        "measure_column": "measures_wind_speed_100m",
        "ghcnh_variables": (),
        "parameter_name": "No direct NOAA GHCNh surface-station equivalent",
        "mapping_type": "unsupported",
    },
    "wind_direction_10m": {
        "measure_column": "measures_wind_direction_10m",
        "ghcnh_variables": ("wind_direction",),
        "parameter_name": "wind_direction",
        "mapping_type": "direct_surface",
    },
    "wind_direction_100m": {
        "measure_column": "measures_wind_direction_100m",
        "ghcnh_variables": (),
        "parameter_name": "No direct NOAA GHCNh surface-station equivalent",
        "mapping_type": "unsupported",
    },
    "wind_gusts_10m": {
        "measure_column": "measures_wind_gusts_10m",
        "ghcnh_variables": ("wind_gust",),
        "parameter_name": "wind_gust",
        "mapping_type": "direct_surface",
    },
    "shortwave_radiation": {
        "measure_column": "measures_shortwave_radiation",
        "ghcnh_variables": (),
        "parameter_name": "No direct NOAA GHCNh base product equivalent",
        "mapping_type": "unsupported",
    },
    "diffuse_radiation": {
        "measure_column": "measures_diffuse_radiation",
        "ghcnh_variables": (),
        "parameter_name": "No direct NOAA GHCNh base product equivalent",
        "mapping_type": "unsupported",
    },
    "cloud_cover": {
        "measure_column": "measures_cloud_cover",
        "ghcnh_variables": ("sky_cover_1", "sky_cover_2", "sky_cover_3"),
        "parameter_name": "sky_cover_1 / sky_cover_2 / sky_cover_3",
        "mapping_type": "direct",
    },
}

OUTPUT_COLUMNS = [
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
    "measures_temperature_2m",
    "measures_relative_humidity_2m",
    "measures_precipitation",
    "measures_wind_speed_10m",
    "measures_wind_speed_100m",
    "measures_wind_direction_10m",
    "measures_wind_direction_100m",
    "measures_wind_gusts_10m",
    "measures_shortwave_radiation",
    "measures_diffuse_radiation",
    "measures_cloud_cover",
    "available_ghcnh_variables",
    "open_meteo_to_ghcnh_mapping",
    "unsupported_open_meteo_variables",
    "capability_years_requested",
    "capability_years_with_files",
    "capability_rows_scanned",
    "capability_source_urls",
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


def normalize_code(value, width: int) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        text = str(int(float(text)))
    except ValueError:
        pass
    return text.zfill(width)


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


def parse_year_list(value: str) -> list[int]:
    years = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            step = 1 if end >= start else -1
            years.extend(range(start, end + step, step))
        else:
            years.append(int(token))
    unique_years = sorted(set(years), reverse=True)
    if not unique_years:
        raise argparse.ArgumentTypeError("No valid years were provided.")
    return unique_years


def request_text(
    session: requests.Session,
    url: str,
    timeout_seconds: int,
    retries: int,
    delay_seconds: float = 0.0,
) -> str:
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            response = session.get(url, timeout=timeout_seconds)
            response.raise_for_status()
            return response.text
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(min(30, 2**attempt))
    raise RuntimeError(f"Request failed after {retries} attempts for {url}: {last_exc}")


def station_sources_from_arg(value: str) -> list[str]:
    if value:
        return [part.strip() for part in value.split(";") if part.strip()]
    return GHCNH_STATION_LIST_SOURCES.copy()


def parse_station_list_text(text: str, source_name: str) -> pd.DataFrame:
    if "," in text.splitlines()[0]:
        return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)

    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        station_id = line[0:11].strip()
        if not station_id or station_id.upper() == "ID":
            continue
        rows.append(
            {
                "ID": station_id,
                "LATITUDE": line[12:20].strip(),
                "LONGITUDE": line[21:30].strip(),
                "ELEVATION": line[31:37].strip(),
                "STATE": line[38:40].strip(),
                "NAME": line[41:71].strip(),
                "WMO_ID": line[80:85].strip() if len(line) >= 85 else "",
            }
        )
    if not rows:
        raise RuntimeError(f"No station rows parsed from {source_name}")
    return pd.DataFrame(rows)


def load_ghcnh_station_list(
    session: requests.Session,
    sources: list[str],
    timeout_seconds: int,
    retries: int,
) -> pd.DataFrame:
    last_error = None
    for source in sources:
        try:
            if source.startswith(("http://", "https://")):
                text = request_text(session, source, timeout_seconds, retries)
                raw = parse_station_list_text(text, source)
            else:
                text = Path(source).read_text(encoding="utf-8")
                raw = parse_station_list_text(text, source)
            break
        except Exception as exc:
            last_error = exc
            print(f"[NOAA_GHCNh] WARNING: Failed station-list source {source}: {exc}")
    else:
        raise RuntimeError(f"Could not load a GHCNh station list: {last_error}")

    raw.columns = [column.strip() for column in raw.columns]
    rename_map = {
        "ID": "noaa_ghcnh_station_id",
        "STATION_ID": "noaa_ghcnh_station_id",
        "STATION": "noaa_ghcnh_station_id",
        "LATITUDE": "latitude",
        "LAT": "latitude",
        "LONGITUDE": "longitude",
        "LON": "longitude",
        "ELEVATION": "elevation_m",
        "ELEV": "elevation_m",
        "STATE": "state",
        "NAME": "station_name",
        "STATION_NAME": "station_name",
        "WMO_ID": "wmo_id",
        "WMO": "wmo_id",
    }
    raw = raw.rename(columns={old: new for old, new in rename_map.items() if old in raw.columns})
    required = ["noaa_ghcnh_station_id", "latitude", "longitude", "state", "station_name"]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise SystemExit(f"GHCNh station list is missing required column(s): {', '.join(missing)}")

    df = raw.copy()
    for column in ["noaa_ghcnh_station_id", "state", "station_name", "wmo_id"]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].map(clean_text)
    df["ctry"] = df["noaa_ghcnh_station_id"].str[:2]
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["elevation_m"] = pd.to_numeric(df.get("elevation_m", pd.NA), errors="coerce")
    return df


def filter_station_candidates(stations: pd.DataFrame) -> pd.DataFrame:
    bbox = HOUSTON_PREFILTER_BBOX
    return stations[
        stations["ctry"].str.upper().eq("US")
        & stations["state"].str.upper().eq(TEXAS_STATE_ABBR)
        & stations["latitude"].between(bbox["min_lat"], bbox["max_lat"])
        & stations["longitude"].between(bbox["min_lon"], bbox["max_lon"])
    ].copy().reset_index(drop=True)


def load_county_boundaries(session: requests.Session, counties: dict, timeout_seconds: int, retries: int) -> list[dict]:
    params = {
        "where": f"STATE='{TEXAS_STATE_CODE}'",
        "outFields": "STATE,COUNTY,GEOID,NAME,BASENAME",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(CENSUS_TIGERWEB_COUNTIES_URL, params=params, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            break
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt >= retries:
                raise RuntimeError(f"Census TIGERweb county boundary request failed: {last_exc}") from exc
            time.sleep(min(30, 2**attempt))

    boundaries = []
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        county_code = normalize_code(props.get("COUNTY"), 3)
        if county_code not in counties:
            continue
        boundaries.append(
            {
                "county_code": county_code,
                "county_name": counties[county_code],
                "geometry": feature.get("geometry") or {},
            }
        )
    if len(boundaries) != len(counties):
        missing = sorted(set(counties) - {boundary["county_code"] for boundary in boundaries})
        raise RuntimeError("Missing TIGERweb boundaries for county code(s): " + ", ".join(missing))
    return boundaries


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        if (y1 > lat) != (y2 > lat):
            x_intersection = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < x_intersection:
                inside = not inside
        previous = current
    return inside


def point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    if not polygon or not point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in polygon[1:])


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    if geometry.get("type") == "Polygon":
        return point_in_polygon(lon, lat, geometry.get("coordinates", []))
    if geometry.get("type") == "MultiPolygon":
        return any(point_in_polygon(lon, lat, polygon) for polygon in geometry.get("coordinates", []))
    return False


def attach_counties(
    session: requests.Session,
    candidates: pd.DataFrame,
    counties: dict,
    timeout_seconds: int,
    retries: int,
) -> pd.DataFrame:
    boundaries = load_county_boundaries(session, counties, timeout_seconds, retries)
    rows = []
    for _, row in candidates.iterrows():
        for boundary in boundaries:
            if point_in_geometry(float(row["longitude"]), float(row["latitude"]), boundary["geometry"]):
                enriched = row.to_dict()
                enriched["county_code"] = boundary["county_code"]
                enriched["county_name"] = boundary["county_name"]
                rows.append(enriched)
                break
    if not rows:
        return pd.DataFrame(columns=list(candidates.columns) + ["county_code", "county_name"])
    return pd.DataFrame(rows).reset_index(drop=True)


def year_file_urls(station_id: str, year: int, templates: list[str]) -> list[str]:
    return [template.format(station_id=station_id, year=year) for template in templates]


def read_station_year_header_and_rows(
    session: requests.Session,
    station_id: str,
    year: int,
    templates: list[str],
    timeout_seconds: int,
    retries: int,
    request_delay: float,
    max_rows: int,
) -> dict:
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
                text = response.text
                separator = "|" if "|" in text.splitlines()[0] else ","
                reader = csv.DictReader(io.StringIO(text), delimiter=separator)
                rows_scanned = 0
                present_columns = set(reader.fieldnames or [])
                nonblank_columns = set()
                for row in reader:
                    rows_scanned += 1
                    for column, value in row.items():
                        if clean_text(value):
                            nonblank_columns.add(column)
                    if max_rows and rows_scanned >= max_rows:
                        break
                return {
                    "url": url,
                    "has_file": True,
                    "rows_scanned": rows_scanned,
                    "present_columns": present_columns,
                    "nonblank_columns": nonblank_columns,
                }
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_exc = exc
                if attempt >= retries:
                    break
                time.sleep(min(30, 2**attempt))
    if last_exc:
        print(f"[NOAA_GHCNh] WARNING: Failed to scan {station_id} {year}: {last_exc}")
    return {"url": "", "has_file": False, "rows_scanned": 0, "present_columns": set(), "nonblank_columns": set()}


def canonical_variables_from_columns(columns: set[str]) -> set[str]:
    normalized = {column.lower(): column for column in columns}
    found = set()
    for canonical, candidates in GHCNH_VARIABLE_CANDIDATES.items():
        if any(candidate.lower() in normalized for candidate in candidates):
            found.add(canonical)
    return found


def variable_is_measured(open_meteo_variable: str, available: set[str]) -> bool:
    if open_meteo_variable == "relative_humidity_2m":
        return "relative_humidity" in available or {"temperature", "dew_point_temperature"}.issubset(available)
    if open_meteo_variable == "cloud_cover":
        return "sky_cover" in available
    config = OPEN_METEO_TO_GHCNH[open_meteo_variable]
    if config["mapping_type"] == "unsupported":
        return False
    return any(variable in available for variable in config["ghcnh_variables"])


def build_mapping_summary() -> str:
    parts = []
    for variable in OPEN_METEO_WEATHER_ORDER:
        config = OPEN_METEO_TO_GHCNH[variable]
        fields = ",".join(config["ghcnh_variables"]) if config["ghcnh_variables"] else "UNSUPPORTED"
        parts.append(f"{variable}->{fields} ({config['mapping_type']})")
    return "; ".join(parts)


def inspect_station_capabilities(
    session: requests.Session,
    stations: pd.DataFrame,
    years: list[int],
    templates: list[str],
    timeout_seconds: int,
    retries: int,
    request_delay: float,
    max_rows: int,
) -> pd.DataFrame:
    rows = []
    unsupported = [
        variable
        for variable in OPEN_METEO_WEATHER_ORDER
        if OPEN_METEO_TO_GHCNH[variable]["mapping_type"] == "unsupported"
    ]
    for idx, station in stations.iterrows():
        station_id = station["noaa_ghcnh_station_id"]
        print(
            "[NOAA_GHCNh] Scanning capabilities "
            f"{idx + 1}/{len(stations)} station={station_id} years={','.join(str(year) for year in years)}"
        )
        available = set()
        years_with_files = []
        urls_with_files = []
        rows_scanned = 0
        for year in years:
            result = read_station_year_header_and_rows(
                session=session,
                station_id=station_id,
                year=year,
                templates=templates,
                timeout_seconds=timeout_seconds,
                retries=retries,
                request_delay=request_delay,
                max_rows=max_rows,
            )
            rows_scanned += result["rows_scanned"]
            if result["has_file"]:
                years_with_files.append(year)
                urls_with_files.append(result["url"])
                available.update(canonical_variables_from_columns(result["present_columns"] | result["nonblank_columns"]))

        out = station.to_dict()
        out["active"] = bool(years_with_files)
        for variable in OPEN_METEO_WEATHER_ORDER:
            out[OPEN_METEO_TO_GHCNH[variable]["measure_column"]] = variable_is_measured(variable, available)
        out["available_ghcnh_variables"] = ";".join(sorted(available))
        out["open_meteo_to_ghcnh_mapping"] = build_mapping_summary()
        out["unsupported_open_meteo_variables"] = ";".join(unsupported)
        out["capability_years_requested"] = ";".join(str(year) for year in years)
        out["capability_years_with_files"] = ";".join(str(year) for year in years_with_files)
        out["capability_rows_scanned"] = rows_scanned
        out["capability_source_urls"] = ";".join(urls_with_files)
        rows.append(out)

    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Create a one-row-per-site NOAA GHCNh station inventory for Greater Houston "
            "and flag whether each station reports the weather variables used in Open-Meteo."
        )
    )
    parser.add_argument("--station-list-source", default="", help="Semicolon-separated GHCNh station list URL/path override.")
    parser.add_argument(
        "--capability-years",
        default=str(date.today().year),
        help="Comma-separated years or ranges to scan for GHCNh capabilities. Defaults to the current year.",
    )
    parser.add_argument("--counties", default="", help="Optional comma-separated subset of Greater Houston counties.")
    parser.add_argument("--out-dir", default=str(Path("NOAA_GHCNh_data") / "monitor_locations"))
    parser.add_argument("--out-prefix", default="greater_houston")
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--max-stations", type=int, default=0, help="Optional first-N station limit after county filtering.")
    parser.add_argument("--max-rows-per-station-year", type=int, default=200, help="Rows to scan in each station-year file for capability detection.")
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.max_rows_per_station_year < 0:
        raise SystemExit("--max-rows-per-station-year cannot be negative")

    session = requests.Session()
    session.headers.update({"User-Agent": "2026-Air-Quality-Summer-Research NOAA_GHCNh monitor collector"})

    counties = selected_counties(args.counties)
    years = parse_year_list(args.capability_years)
    station_sources = station_sources_from_arg(args.station_list_source)

    station_list = load_ghcnh_station_list(
        session=session,
        sources=station_sources,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )
    print(f"[NOAA_GHCNh] GHCNh station-list rows: {len(station_list)}")

    candidates = filter_station_candidates(station_list)
    print(f"[NOAA_GHCNh] Texas bbox candidates: {len(candidates)}")
    stations = attach_counties(
        session=session,
        candidates=candidates,
        counties=counties,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )
    stations = stations.sort_values(["county_code", "station_name", "noaa_ghcnh_station_id"], kind="stable").reset_index(drop=True)
    if args.max_stations:
        stations = stations.head(args.max_stations).copy()
    print(f"[NOAA_GHCNh] Greater Houston GHCNh stations: {len(stations)}")

    inventory = inspect_station_capabilities(
        session=session,
        stations=stations,
        years=years,
        templates=GHCNH_YEAR_FILE_TEMPLATES,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        request_delay=args.request_delay,
        max_rows=args.max_rows_per_station_year,
    )
    if inventory.empty and not args.allow_empty:
        raise SystemExit("No NOAA GHCNh stations were found. Use --allow-empty to write a header-only CSV.")

    for column in OUTPUT_COLUMNS:
        if column not in inventory.columns:
            inventory[column] = pd.NA
    inventory = inventory[OUTPUT_COLUMNS].sort_values(["county_code", "station_name", "noaa_ghcnh_station_id"], kind="stable")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = out_dir / f"{args.out_prefix}_noaa_ghcnh_monitor_locations_{timestamp}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory.to_csv(output_file, index=False)
    print(f"[NOAA_GHCNh] Saved Greater Houston GHCNh station inventory -> {output_file}")
    print(f"[NOAA_GHCNh] Stations: {len(inventory)}")


if __name__ == "__main__":
    main()
