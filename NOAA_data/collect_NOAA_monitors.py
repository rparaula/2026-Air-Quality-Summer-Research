import argparse
import csv
import io
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


NOAA_ISD_HISTORY_URL = "https://noaa-isd-pds.s3.amazonaws.com/isd-history.csv"
NOAA_GLOBAL_HOURLY_ACCESS_URL = "https://noaa-global-hourly-pds.s3.amazonaws.com"
CENSUS_COORDINATE_GEOCODER_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
)
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

# Loose prefilter around the nine-county region. County membership is still
# decided by the Census geocoder after this bounding-box reduction.
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

NOAA_ISD_PARAMETER_NAMES = {
    "TMP": "AIR-TEMPERATURE-OBSERVATION air temperature",
    "DEW": "AIR-TEMPERATURE-OBSERVATION dew point temperature",
    "WND": "WIND-OBSERVATION direction angle / speed rate",
    "AA1": "LIQUID-PRECIPITATION occurrence #1",
    "AA2": "LIQUID-PRECIPITATION occurrence #2",
    "AA3": "LIQUID-PRECIPITATION occurrence #3",
    "AA4": "LIQUID-PRECIPITATION occurrence #4",
    "GD1": "SKY-COVER-SUMMATION-STATE #1",
    "GD2": "SKY-COVER-SUMMATION-STATE #2",
    "GD3": "SKY-COVER-SUMMATION-STATE #3",
    "GD4": "SKY-COVER-SUMMATION-STATE #4",
    "GD5": "SKY-COVER-SUMMATION-STATE #5",
    "GD6": "SKY-COVER-SUMMATION-STATE #6",
    "OC1": "WIND-GUST-OBSERVATION speed rate",
    "GH1": "Hourly Solar Radiation Section / SOLARAD hourly average solar radiation",
}

OPEN_METEO_TO_NOAA_ISD = {
    "temperature_2m": {
        "measure_column": "measures_temperature_2m",
        "required_fields": ("TMP",),
        "mapped_fields": ("TMP",),
        "parameter_name": NOAA_ISD_PARAMETER_NAMES["TMP"],
        "mapping_type": "direct",
    },
    "relative_humidity_2m": {
        "measure_column": "measures_relative_humidity_2m",
        "required_fields": ("TMP", "DEW"),
        "mapped_fields": ("TMP", "DEW"),
        "parameter_name": (
            "Derived from TMP air temperature and DEW dew point temperature; "
            "NOAA ISD Global Hourly does not store relative humidity directly."
        ),
        "mapping_type": "derived",
    },
    "precipitation": {
        "measure_column": "measures_precipitation",
        "required_fields": ("AA1", "AA2", "AA3", "AA4"),
        "mapped_fields": ("AA1", "AA2", "AA3", "AA4"),
        "parameter_name": "LIQUID-PRECIPITATION occurrence with period quantity = 01 hour",
        "mapping_type": "direct_1h_only",
    },
    "wind_speed_10m": {
        "measure_column": "measures_wind_speed_10m",
        "required_fields": ("WND_SPEED",),
        "mapped_fields": ("WND",),
        "parameter_name": "WIND-OBSERVATION speed rate",
        "mapping_type": "direct_surface",
    },
    "wind_speed_100m": {
        "measure_column": "measures_wind_speed_100m",
        "required_fields": (),
        "mapped_fields": (),
        "parameter_name": "No direct NOAA ISD surface-station equivalent",
        "mapping_type": "unsupported",
    },
    "wind_direction_10m": {
        "measure_column": "measures_wind_direction_10m",
        "required_fields": ("WND_DIRECTION",),
        "mapped_fields": ("WND",),
        "parameter_name": "WIND-OBSERVATION direction angle",
        "mapping_type": "direct_surface",
    },
    "wind_direction_100m": {
        "measure_column": "measures_wind_direction_100m",
        "required_fields": (),
        "mapped_fields": (),
        "parameter_name": "No direct NOAA ISD surface-station equivalent",
        "mapping_type": "unsupported",
    },
    "wind_gusts_10m": {
        "measure_column": "measures_wind_gusts_10m",
        "required_fields": ("OC1",),
        "mapped_fields": ("OC1",),
        "parameter_name": NOAA_ISD_PARAMETER_NAMES["OC1"],
        "mapping_type": "direct_surface",
    },
    "shortwave_radiation": {
        "measure_column": "measures_shortwave_radiation",
        "required_fields": ("GH1",),
        "mapped_fields": ("GH1",),
        "parameter_name": NOAA_ISD_PARAMETER_NAMES["GH1"],
        "mapping_type": "direct",
    },
    "diffuse_radiation": {
        "measure_column": "measures_diffuse_radiation",
        "required_fields": (),
        "mapped_fields": (),
        "parameter_name": "No direct NOAA ISD Global Hourly diffuse-radiation equivalent",
        "mapping_type": "unsupported",
    },
    "cloud_cover": {
        "measure_column": "measures_cloud_cover",
        "required_fields": ("GD1", "GD2", "GD3", "GD4", "GD5", "GD6"),
        "mapped_fields": ("GD1", "GD2", "GD3", "GD4", "GD5", "GD6"),
        "parameter_name": "SKY-COVER-SUMMATION-STATE coverage code / coverage code #2",
        "mapping_type": "direct",
    },
}

SUPPORTED_CAPABILITY_KEYS = {
    "TMP",
    "DEW",
    "AA1",
    "AA2",
    "AA3",
    "AA4",
    "WND_SPEED",
    "WND_DIRECTION",
    "OC1",
    "GH1",
    "GD1",
    "GD2",
    "GD3",
    "GD4",
    "GD5",
    "GD6",
}

OUTPUT_COLUMNS = [
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
    "available_isd_parameter_codes",
    "available_isd_parameter_names",
    "open_meteo_to_isd_mapping",
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


def parse_yyyymmdd(value) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.zfill(8)
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


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


def normalize_county_name(value: str) -> str:
    text = clean_text(value)
    if text.lower().endswith(" county"):
        return text[:-7].strip()
    return text


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
    for year in unique_years:
        if year < 1901 or year > 2100:
            raise argparse.ArgumentTypeError(f"Suspicious ISD year {year}; expected 1901-2100.")
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


def load_isd_history(
    session: requests.Session,
    source: str,
    timeout_seconds: int,
    retries: int,
) -> pd.DataFrame:
    if source.startswith(("http://", "https://")):
        text = request_text(
            session=session,
            url=source,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    else:
        raw = pd.read_csv(source, dtype=str, keep_default_na=False)

    raw.columns = [column.strip() for column in raw.columns]
    rename_map = {
        "USAF": "usaf",
        "WBAN": "wban",
        "STATION NAME": "station_name",
        "CTRY": "ctry",
        "STATE": "state",
        "ST": "state",
        "ICAO": "icao",
        "LAT": "latitude",
        "LON": "longitude",
        "ELEV(M)": "elevation_m",
        "ELEV": "elevation_m",
        "BEGIN": "begin_raw",
        "END": "end_raw",
    }
    raw = raw.rename(columns={old: new for old, new in rename_map.items() if old in raw.columns})

    required = ["usaf", "wban", "station_name", "ctry", "state", "latitude", "longitude", "begin_raw", "end_raw"]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise SystemExit(f"ISD station history is missing required column(s): {', '.join(missing)}")

    df = raw.copy()
    for column in ["usaf", "wban", "station_name", "ctry", "state", "icao"]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].map(clean_text)

    df["usaf"] = df["usaf"].str.zfill(6)
    df["wban"] = df["wban"].str.zfill(5)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    if "elevation_m" in df.columns:
        df["elevation_m"] = pd.to_numeric(df["elevation_m"], errors="coerce")
    else:
        df["elevation_m"] = pd.NA

    df["begin_date_obj"] = df["begin_raw"].map(parse_yyyymmdd)
    df["end_date_obj"] = df["end_raw"].map(parse_yyyymmdd)
    df["begin_date"] = df["begin_date_obj"].map(lambda value: value.isoformat() if value else "")
    df["end_date"] = df["end_date_obj"].map(lambda value: value.isoformat() if value else "")
    df["global_hourly_station_id"] = df["usaf"] + df["wban"]
    df["noaa_station_id"] = df["usaf"] + "-" + df["wban"]

    return df


def latest_station_history_date(stations: pd.DataFrame) -> date:
    dates = [value for value in stations["end_date_obj"].dropna().tolist() if isinstance(value, date)]
    if not dates:
        return date.today()
    return max(dates)


def filter_texas_station_candidates(
    stations: pd.DataFrame,
    start_date: date | None,
    end_date: date | None,
) -> pd.DataFrame:
    bbox = HOUSTON_PREFILTER_BBOX
    candidates = stations[
        stations["ctry"].str.upper().eq("US")
        & stations["state"].str.upper().eq(TEXAS_STATE_ABBR)
        & stations["latitude"].between(bbox["min_lat"], bbox["max_lat"])
        & stations["longitude"].between(bbox["min_lon"], bbox["max_lon"])
    ].copy()

    if start_date is not None:
        candidates = candidates[
            candidates["end_date_obj"].map(lambda value: value is None or value >= start_date)
        ].copy()
    if end_date is not None:
        candidates = candidates[
            candidates["begin_date_obj"].map(lambda value: value is None or value <= end_date)
        ].copy()

    return candidates.reset_index(drop=True)


def lookup_county_for_coordinate(
    session: requests.Session,
    latitude: float,
    longitude: float,
    timeout_seconds: int,
    retries: int,
    delay_seconds: float,
) -> tuple[str, str]:
    params = {
        "x": f"{longitude:.8f}",
        "y": f"{latitude:.8f}",
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "County",
        "format": "json",
    }
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            response = session.get(
                CENSUS_COORDINATE_GEOCODER_URL,
                params=params,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            geographies = payload.get("result", {}).get("geographies", {})
            counties = geographies.get("Counties") or geographies.get("County") or []
            if not counties:
                return "", ""
            county = counties[0]
            state_code = normalize_code(county.get("STATE"), 2)
            county_code = normalize_code(county.get("COUNTY"), 3)
            if not county_code:
                geoid = clean_text(county.get("GEOID") or county.get("GEOID20"))
                if len(geoid) >= 5:
                    state_code = geoid[:2]
                    county_code = geoid[2:5]
            if state_code and state_code != TEXAS_STATE_CODE:
                return "", ""
            county_name = normalize_county_name(
                clean_text(
                    county.get("NAME")
                    or county.get("BASENAME")
                    or HOUSTON_COUNTIES.get(county_code, "")
                )
            )
            return county_code, county_name
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(min(30, 2**attempt))

    raise RuntimeError(
        f"Census county lookup failed for lat={latitude}, lon={longitude}: {last_exc}"
    )


def attach_counties(
    session: requests.Session,
    candidates: pd.DataFrame,
    counties: dict,
    boundary_source: str,
    timeout_seconds: int,
    retries: int,
    delay_seconds: float,
    use_geocoder: bool,
) -> pd.DataFrame:
    if not use_geocoder:
        return attach_counties_from_boundaries(
            session=session,
            candidates=candidates,
            counties=counties,
            boundary_source=boundary_source,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )

    rows = []
    coordinate_cache = {}
    for _, row in candidates.iterrows():
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        cache_key = (round(lat, 6), round(lon, 6))
        if cache_key not in coordinate_cache:
            coordinate_cache[cache_key] = lookup_county_for_coordinate(
                session=session,
                latitude=lat,
                longitude=lon,
                timeout_seconds=timeout_seconds,
                retries=retries,
                delay_seconds=delay_seconds,
            )
        county_code, county_name = coordinate_cache[cache_key]
        if county_code not in counties:
            continue
        enriched = row.to_dict()
        enriched["county_code"] = county_code
        enriched["county_name"] = counties.get(county_code, county_name)
        rows.append(enriched)

    if not rows:
        return pd.DataFrame(columns=list(candidates.columns) + ["county_code", "county_name"])
    return pd.DataFrame(rows).reset_index(drop=True)


def load_county_boundaries(
    session: requests.Session,
    counties: dict,
    boundary_source: str,
    timeout_seconds: int,
    retries: int,
) -> list[dict]:
    if boundary_source.startswith(("http://", "https://")):
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
                response = session.get(
                    boundary_source,
                    params=params,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt >= retries:
                    raise RuntimeError(
                        f"Census TIGERweb county boundary request failed: {last_exc}"
                    ) from exc
                time.sleep(min(30, 2**attempt))
    else:
        with open(boundary_source, "r", encoding="utf-8") as handle:
            import json

            payload = json.load(handle)

    features = payload.get("features", [])
    boundaries = []
    for feature in features:
        props = feature.get("properties", {})
        state_code = normalize_code(props.get("STATE"), 2)
        county_code = normalize_code(props.get("COUNTY"), 3)
        if state_code != TEXAS_STATE_CODE or county_code not in counties:
            continue
        geometry = feature.get("geometry") or {}
        if not geometry:
            continue
        boundaries.append(
            {
                "county_code": county_code,
                "county_name": counties.get(
                    county_code,
                    normalize_county_name(
                        clean_text(props.get("NAME") or props.get("BASENAME"))
                    ),
                ),
                "geometry": geometry,
            }
        )

    missing = sorted(set(counties) - {boundary["county_code"] for boundary in boundaries})
    if missing:
        raise RuntimeError(
            "Census TIGERweb boundary response did not include county code(s): "
            + ", ".join(missing)
        )
    return boundaries


def point_on_segment(lon: float, lat: float, start: list[float], end: list[float]) -> bool:
    x1, y1 = start[:2]
    x2, y2 = end[:2]
    cross = (lat - y1) * (x2 - x1) - (lon - x1) * (y2 - y1)
    if abs(cross) > 1e-10:
        return False
    if min(x1, x2) - 1e-10 <= lon <= max(x1, x2) + 1e-10 and min(y1, y2) - 1e-10 <= lat <= max(y1, y2) + 1e-10:
        return True
    return False


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    if len(ring) < 4:
        return False

    inside = False
    previous = ring[-1]
    for current in ring:
        if point_on_segment(lon, lat, previous, current):
            return True
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        intersects = (y1 > lat) != (y2 > lat)
        if intersects:
            x_intersection = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < x_intersection:
                inside = not inside
        previous = current
    return inside


def point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    if not polygon or not point_in_ring(lon, lat, polygon[0]):
        return False
    for hole in polygon[1:]:
        if point_in_ring(lon, lat, hole):
            return False
    return True


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        return point_in_polygon(lon, lat, coordinates)
    if geometry_type == "MultiPolygon":
        return any(point_in_polygon(lon, lat, polygon) for polygon in coordinates)
    return False


def attach_counties_from_boundaries(
    session: requests.Session,
    candidates: pd.DataFrame,
    counties: dict,
    boundary_source: str,
    timeout_seconds: int,
    retries: int,
) -> pd.DataFrame:
    boundaries = load_county_boundaries(
        session=session,
        counties=counties,
        boundary_source=boundary_source,
        timeout_seconds=timeout_seconds,
        retries=retries,
    )
    print(f"[NOAA] Loaded Census TIGERweb county boundaries: {len(boundaries)}")

    rows = []
    for _, row in candidates.iterrows():
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        matched_boundary = None
        for boundary in boundaries:
            if point_in_geometry(lon, lat, boundary["geometry"]):
                matched_boundary = boundary
                break
        if not matched_boundary:
            continue
        enriched = row.to_dict()
        enriched["county_code"] = matched_boundary["county_code"]
        enriched["county_name"] = matched_boundary["county_name"]
        rows.append(enriched)

    if not rows:
        return pd.DataFrame(columns=list(candidates.columns) + ["county_code", "county_name"])
    return pd.DataFrame(rows).reset_index(drop=True)


def split_isd_group(value) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(",")]


def has_valid_tmp(value) -> bool:
    parts = split_isd_group(value)
    return bool(parts and parts[0] not in {"", "+9999", "9999", "-9999"})


def has_valid_dew(value) -> bool:
    parts = split_isd_group(value)
    return bool(parts and parts[0] not in {"", "+9999", "9999", "-9999"})


def has_valid_wnd_speed(value) -> bool:
    parts = split_isd_group(value)
    return len(parts) >= 4 and parts[3] not in {"", "9999"}


def has_valid_wnd_direction(value) -> bool:
    parts = split_isd_group(value)
    return len(parts) >= 1 and parts[0] not in {"", "999"}


def has_valid_one_hour_precip(value) -> bool:
    parts = split_isd_group(value)
    return len(parts) >= 2 and parts[0] == "01" and parts[1] not in {"", "9999"}


def has_valid_cloud_cover(value) -> bool:
    parts = split_isd_group(value)
    if len(parts) >= 1 and parts[0] not in {"", "9"}:
        return True
    if len(parts) >= 2 and parts[1] not in {"", "99"}:
        return True
    return False


def has_valid_wind_gust(value) -> bool:
    parts = split_isd_group(value)
    return len(parts) >= 1 and parts[0] not in {"", "9999"}


def has_valid_solar_radiation(value) -> bool:
    parts = split_isd_group(value)
    return len(parts) >= 1 and parts[0] not in {"", "99999"}


def update_capabilities_from_row(row: dict, capabilities: set[str]) -> None:
    if "TMP" in row and has_valid_tmp(row.get("TMP")):
        capabilities.add("TMP")
    if "DEW" in row and has_valid_dew(row.get("DEW")):
        capabilities.add("DEW")
    if "WND" in row:
        if has_valid_wnd_speed(row.get("WND")):
            capabilities.add("WND_SPEED")
        if has_valid_wnd_direction(row.get("WND")):
            capabilities.add("WND_DIRECTION")
    for field in ("AA1", "AA2", "AA3", "AA4"):
        if field in row and has_valid_one_hour_precip(row.get(field)):
            capabilities.add(field)
    for field in ("GD1", "GD2", "GD3", "GD4", "GD5", "GD6"):
        if field in row and has_valid_cloud_cover(row.get(field)):
            capabilities.add(field)
    if "OC1" in row and has_valid_wind_gust(row.get("OC1")):
        capabilities.add("OC1")
    if "GH1" in row and has_valid_solar_radiation(row.get("GH1")):
        capabilities.add("GH1")


def capability_scan_complete(capabilities: set[str]) -> bool:
    return SUPPORTED_CAPABILITY_KEYS.issubset(capabilities)


def scan_station_year_csv(
    session: requests.Session,
    station_id: str,
    year: int,
    timeout_seconds: int,
    retries: int,
    delay_seconds: float,
    max_rows: int,
) -> dict:
    url = f"{NOAA_GLOBAL_HOURLY_ACCESS_URL}/{year}/{station_id}.csv"
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            response = session.get(url, stream=True, timeout=timeout_seconds)
            if response.status_code == 404:
                return {
                    "url": url,
                    "year": year,
                    "has_file": False,
                    "rows_scanned": 0,
                    "capabilities": set(),
                }
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"

            capabilities = set()
            rows_scanned = 0
            lines = response.iter_lines(decode_unicode=True)
            reader = csv.DictReader(line for line in lines if line)
            for csv_row in reader:
                rows_scanned += 1
                update_capabilities_from_row(csv_row, capabilities)
                if capability_scan_complete(capabilities):
                    break
                if max_rows > 0 and rows_scanned >= max_rows:
                    break

            response.close()
            return {
                "url": url,
                "year": year,
                "has_file": True,
                "rows_scanned": rows_scanned,
                "capabilities": capabilities,
            }
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(min(30, 2**attempt))

    print(f"[NOAA] WARNING: Failed to scan {url}: {last_exc}")
    return {
        "url": url,
        "year": year,
        "has_file": False,
        "rows_scanned": 0,
        "capabilities": set(),
    }


def resolve_capability_years_for_station(
    station: pd.Series,
    explicit_years: list[int] | None,
    latest_available_year: int,
    lookback_years: int,
) -> list[int]:
    if explicit_years:
        return explicit_years

    end_date = station.get("end_date_obj")
    begin_date = station.get("begin_date_obj")
    end_year = latest_available_year
    if isinstance(end_date, date):
        end_year = min(end_date.year, latest_available_year)

    begin_year = 1901
    if isinstance(begin_date, date):
        begin_year = begin_date.year

    years = []
    for year in range(end_year, begin_year - 1, -1):
        years.append(year)
        if len(years) >= lookback_years:
            break
    return years


def build_mapping_summary() -> str:
    parts = []
    for variable in OPEN_METEO_WEATHER_ORDER:
        config = OPEN_METEO_TO_NOAA_ISD[variable]
        fields = ",".join(config["mapped_fields"]) if config["mapped_fields"] else "UNSUPPORTED"
        parts.append(f"{variable}->{fields} ({config['mapping_type']})")
    return "; ".join(parts)


def sorted_available_parameter_codes(capabilities: set[str]) -> list[str]:
    order = [
        "TMP",
        "DEW",
        "WND",
        "AA1",
        "AA2",
        "AA3",
        "AA4",
        "GD1",
        "GD2",
        "GD3",
        "GD4",
        "GD5",
        "GD6",
        "OC1",
        "GH1",
    ]
    detected_codes = set()
    for capability in capabilities:
        if capability in {"WND_SPEED", "WND_DIRECTION"}:
            detected_codes.add("WND")
        else:
            detected_codes.add(capability)
    return [code for code in order if code in detected_codes]


def parameter_names_for_codes(codes: Iterable[str]) -> str:
    return "; ".join(
        f"{code}: {NOAA_ISD_PARAMETER_NAMES.get(code, '')}".strip()
        for code in codes
    )


def variable_is_measured(variable: str, capabilities: set[str]) -> bool:
    config = OPEN_METEO_TO_NOAA_ISD[variable]
    if config["mapping_type"] == "unsupported":
        return False
    if variable == "precipitation":
        return bool(capabilities & {"AA1", "AA2", "AA3", "AA4"})
    if variable == "cloud_cover":
        return bool(capabilities & {"GD1", "GD2", "GD3", "GD4", "GD5", "GD6"})
    return set(config["required_fields"]).issubset(capabilities)


def inspect_station_capabilities(
    session: requests.Session,
    stations: pd.DataFrame,
    explicit_years: list[int] | None,
    latest_available_year: int,
    lookback_years: int,
    timeout_seconds: int,
    retries: int,
    request_delay: float,
    max_rows_per_station_year: int,
) -> pd.DataFrame:
    rows = []
    mapping_summary = build_mapping_summary()
    unsupported_variables = [
        variable
        for variable in OPEN_METEO_WEATHER_ORDER
        if OPEN_METEO_TO_NOAA_ISD[variable]["mapping_type"] == "unsupported"
    ]

    for index, station in stations.iterrows():
        station_id = station["global_hourly_station_id"]
        requested_years = resolve_capability_years_for_station(
            station=station,
            explicit_years=explicit_years,
            latest_available_year=latest_available_year,
            lookback_years=lookback_years,
        )
        print(
            "[NOAA] Scanning capabilities "
            f"{index + 1}/{len(stations)} station={station['noaa_station_id']} "
            f"years={','.join(str(year) for year in requested_years)}"
        )

        capabilities = set()
        years_with_files = []
        urls_with_files = []
        rows_scanned = 0
        for year in requested_years:
            result = scan_station_year_csv(
                session=session,
                station_id=station_id,
                year=year,
                timeout_seconds=timeout_seconds,
                retries=retries,
                delay_seconds=request_delay,
                max_rows=max_rows_per_station_year,
            )
            rows_scanned += result["rows_scanned"]
            if result["has_file"]:
                years_with_files.append(year)
                urls_with_files.append(result["url"])
                capabilities.update(result["capabilities"])
            if capability_scan_complete(capabilities):
                break

        available_codes = sorted_available_parameter_codes(capabilities)
        out_row = station.to_dict()
        for variable in OPEN_METEO_WEATHER_ORDER:
            column = OPEN_METEO_TO_NOAA_ISD[variable]["measure_column"]
            out_row[column] = variable_is_measured(variable, capabilities)
        out_row["available_isd_parameter_codes"] = ";".join(available_codes)
        out_row["available_isd_parameter_names"] = parameter_names_for_codes(available_codes)
        out_row["open_meteo_to_isd_mapping"] = mapping_summary
        out_row["unsupported_open_meteo_variables"] = ";".join(unsupported_variables)
        out_row["capability_years_requested"] = ";".join(str(year) for year in requested_years)
        out_row["capability_years_with_files"] = ";".join(str(year) for year in years_with_files)
        out_row["capability_rows_scanned"] = rows_scanned
        out_row["capability_source_urls"] = ";".join(urls_with_files)
        rows.append(out_row)

    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(rows)


def build_inventory(args) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update({"User-Agent": "2026-Air-Quality-Summer-Research NOAA ISD collector"})

    counties = selected_counties(args.counties)
    station_history = load_isd_history(
        session=session,
        source=args.station_history_source,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
    )
    latest_history_date = latest_station_history_date(station_history)
    end_date = args.end_date or latest_history_date
    active_as_of = args.active_as_of or end_date
    latest_available_year = end_date.year

    print(f"[NOAA] ISD station history rows: {len(station_history)}")
    print(f"[NOAA] Latest station-history end date observed: {latest_history_date.isoformat()}")

    candidates = filter_texas_station_candidates(
        stations=station_history,
        start_date=args.start_date,
        end_date=end_date,
    )
    print(f"[NOAA] Texas bbox candidates after date filter: {len(candidates)}")

    stations_in_counties = attach_counties(
        session=session,
        candidates=candidates,
        counties=counties,
        boundary_source=args.county_boundary_source,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        delay_seconds=args.county_request_delay,
        use_geocoder=args.use_census_geocoder,
    )
    stations_in_counties = stations_in_counties.sort_values(
        ["county_code", "station_name", "noaa_station_id"],
        kind="stable",
    ).reset_index(drop=True)

    if args.max_stations:
        stations_in_counties = stations_in_counties.head(args.max_stations).copy()

    print(f"[NOAA] Greater Houston ISD stations: {len(stations_in_counties)}")
    if stations_in_counties.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    active_cutoff = active_as_of - timedelta(days=args.active_tolerance_days)
    stations_in_counties["active"] = stations_in_counties["end_date_obj"].map(
        lambda value: bool(value and value >= active_cutoff)
    )

    explicit_years = parse_year_list(args.capability_years) if args.capability_years else None
    if args.skip_capability_scan:
        inventory = stations_in_counties.copy()
        for variable in OPEN_METEO_WEATHER_ORDER:
            column = OPEN_METEO_TO_NOAA_ISD[variable]["measure_column"]
            inventory[column] = False
        inventory["available_isd_parameter_codes"] = ""
        inventory["available_isd_parameter_names"] = ""
        inventory["open_meteo_to_isd_mapping"] = build_mapping_summary()
        inventory["unsupported_open_meteo_variables"] = ";".join(
            variable
            for variable in OPEN_METEO_WEATHER_ORDER
            if OPEN_METEO_TO_NOAA_ISD[variable]["mapping_type"] == "unsupported"
        )
        inventory["capability_years_requested"] = ""
        inventory["capability_years_with_files"] = ""
        inventory["capability_rows_scanned"] = 0
        inventory["capability_source_urls"] = ""
    else:
        inventory = inspect_station_capabilities(
            session=session,
            stations=stations_in_counties,
            explicit_years=explicit_years,
            latest_available_year=latest_available_year,
            lookback_years=args.capability_lookback_years,
            timeout_seconds=args.timeout_seconds,
            retries=args.retries,
            request_delay=args.request_delay,
            max_rows_per_station_year=args.max_rows_per_station_year,
        )

    for column in OUTPUT_COLUMNS:
        if column not in inventory.columns:
            inventory[column] = pd.NA
    return inventory[OUTPUT_COLUMNS].sort_values(
        ["county_code", "station_name", "noaa_station_id"],
        kind="stable",
    ).reset_index(drop=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Create a one-row-per-site NOAA ISD station inventory for the Greater Houston "
            "counties and flag whether each station reports the weather variables used in Open-Meteo."
        )
    )
    parser.add_argument(
        "--station-history-source",
        default=NOAA_ISD_HISTORY_URL,
        help="NOAA ISD station history CSV URL or local path.",
    )
    parser.add_argument(
        "--start-date",
        type=parse_iso_date,
        default=date(1980, 1, 1),
        help="Earliest station period-of-record overlap date to include. Defaults to 1980-01-01.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_iso_date,
        default=None,
        help="Latest station period-of-record overlap date to include. Defaults to latest date in ISD station history.",
    )
    parser.add_argument(
        "--active-as-of",
        type=parse_iso_date,
        default=None,
        help="Date used to compute active. Defaults to --end-date/latest station-history date.",
    )
    parser.add_argument(
        "--active-tolerance-days",
        type=int,
        default=31,
        help=(
            "A station is marked active if its ISD end date is within this many days "
            "of --active-as-of. Defaults to 31 because the legacy ISD station-history "
            "file often ends active stations on nearby final update dates."
        ),
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
        "--capability-years",
        default="",
        help=(
            "Optional comma-separated years or ranges to scan for station capabilities, "
            "for example 2025,2024 or 2023-2025. Defaults to each station's latest "
            "available ISD years."
        ),
    )
    parser.add_argument(
        "--capability-lookback-years",
        type=int,
        default=3,
        help="Number of latest station years to scan when --capability-years is not provided.",
    )
    parser.add_argument(
        "--max-rows-per-station-year",
        type=int,
        default=0,
        help="Maximum rows to scan per station-year CSV. Use 0 to scan until all capabilities are found or EOF.",
    )
    parser.add_argument(
        "--skip-capability-scan",
        action="store_true",
        help="Only build the location inventory; leave all measures_* capability columns False.",
    )
    parser.add_argument(
        "--max-stations",
        type=int,
        default=0,
        help="Optional smoke-test limit after county filtering.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path("AQS_data") / "monitor_locations"),
        help="Directory for the generated NOAA ISD station inventory CSV.",
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
        default=0.05,
        help="Seconds to wait between NOAA Global Hourly station-year requests.",
    )
    parser.add_argument(
        "--county-request-delay",
        type=float,
        default=0.05,
        help="Seconds to wait between Census county lookup requests.",
    )
    parser.add_argument(
        "--county-boundary-source",
        default=CENSUS_TIGERWEB_COUNTIES_URL,
        help=(
            "Census TIGERweb county GeoJSON query URL, or a local GeoJSON file with county "
            "boundaries. Defaults to the official TIGERweb State_County Counties layer."
        ),
    )
    parser.add_argument(
        "--use-census-geocoder",
        action="store_true",
        help=(
            "Use the Census coordinate geocoder one station at a time instead of the "
            "default TIGERweb boundary download and local point-in-polygon check."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Write a header-only CSV if no stations are found. By default, no-row runs fail.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.end_date and args.end_date < args.start_date:
        raise SystemExit("--end-date must be on or after --start-date")
    if args.active_as_of and args.active_as_of < args.start_date:
        print("[NOAA] WARNING: --active-as-of is before --start-date.")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.capability_lookback_years < 1:
        raise SystemExit("--capability-lookback-years must be at least 1")
    if args.active_tolerance_days < 0:
        raise SystemExit("--active-tolerance-days cannot be negative")
    if args.max_rows_per_station_year < 0:
        raise SystemExit("--max-rows-per-station-year cannot be negative")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output_file:
        output_file = Path(args.output_file)
        out_dir = output_file.parent
    else:
        out_dir = Path(args.out_dir)
        output_file = out_dir / f"{args.out_prefix}_noaa_isd_monitor_locations_{timestamp}.csv"
    out_dir.mkdir(parents=True, exist_ok=True)

    inventory = build_inventory(args)
    if inventory.empty and not args.allow_empty:
        raise SystemExit(
            "No NOAA ISD stations were found for the requested Greater Houston counties and "
            "date window. No CSV was written. If an empty inventory is expected, rerun with --allow-empty."
        )

    inventory.to_csv(output_file, index=False)
    print(f"[NOAA] Saved Greater Houston NOAA ISD station inventory -> {output_file}")
    print(f"[NOAA] Stations: {len(inventory)}")


if __name__ == "__main__":
    main()
