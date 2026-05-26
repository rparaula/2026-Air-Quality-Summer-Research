# pip install requests pandas

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from metadata_tracker import PipelineRunTracker

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_HEADERS = {
    "User-Agent": "AQ_DataCollection_ML_Pipeline/1.0 (https://github.com/GlowSand/AQ_DataCollection_ML_Pipeline)"
}



# City schemas: "Austin,TX;Houston,TX"
def parse_city_state_list(s: str):
    pairs = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        if "," not in part:
            raise ValueError(f"Bad --cities entry '{part}'. Expected 'City,ST' (comma-separated).")
        city, st = part.split(",", 1)
        city = city.strip()
        st = st.strip().upper()
        if not city or not st:
            raise ValueError(f"Bad --cities entry '{part}'. Expected 'City,ST'.")
        pairs.append((city, st))
    if not pairs:
        raise ValueError("No valid city/state pairs provided in --cities.")
    return pairs


# Get the center lat/lon for a zip using data from https://simplemaps.com/data/us-zips
def get_zip_centroids(city: str, state_id: str, uszips_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(uszips_csv_path)

    city_norm = city.strip().lower()
    state_norm = state_id.strip().upper()

    sub = df[
        (df["city"].astype(str).str.strip().str.lower() == city_norm)
        & (df["state_id"].astype(str).str.strip().str.upper() == state_norm)
    ][["zip", "lat", "lng", "density"]].copy()

    sub = sub.rename(columns={"lat": "latitude", "lng": "longitude"})
    sub = sub.dropna(subset=["latitude", "longitude"]).drop_duplicates(subset=["zip"]).reset_index(drop=True)

    return sub


def density_to_radius(density: float, max_radius_m: int) -> int:
    """
    Derive a search radius from population density (people/km²).

    Denser ZIPs are geographically smaller, so a tighter radius is sufficient.
    Sparse ZIPs can span many km, so a wider radius is needed to capture buildings.

    Tiers (capped by max_radius_m):
      > 4000 /km²  → 2 000 m  (dense urban core)
      > 1000 /km²  → 3 500 m  (inner suburban)
      >  300 /km²  → 5 500 m  (outer suburban)
      ≤  300 /km²  → 9 000 m  (rural / exurban)
    """
    if density > 4000:
        r = 2000
    elif density > 1000:
        r = 3500
    elif density > 300:
        r = 5500
    else:
        r = 9000
    return min(r, max_radius_m)


def chunked(df: pd.DataFrame, size: int):
    for i in range(0, len(df), size):
        yield df.iloc[i: i + size]


def _query_overpass(query: str, timeout: int = 60, max_retries: int = 5) -> dict | None:
    """POST a raw Overpass QL query and return the parsed JSON, or None on failure.

    Retries with exponential backoff on 406/429 (rate-limit) responses.
    """
    wait = 10  # initial wait in seconds before first retry
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers=OVERPASS_HEADERS,
                timeout=timeout + 5,  # HTTP timeout slightly longer than Overpass timeout
            )
        except requests.RequestException as e:
            print(f"WARNING: Overpass request failed: {e}. Skipping.")
            return None

        if response.status_code == 200:
            return response.json()

        if response.status_code in (406, 429):
            if attempt < max_retries:
                print(f"  Overpass rate-limited (HTTP {response.status_code}). "
                      f"Waiting {wait}s before retry {attempt}/{max_retries - 1} ...")
                time.sleep(wait)
                wait *= 2  # exponential backoff
                continue
            print(f"WARNING: Overpass rate-limited after {max_retries} attempts. Skipping.")
            return None

        print(f"WARNING: Overpass returned HTTP {response.status_code}. Skipping.")
        return None

    return None


def _get_buildings_for_zip(zip_code: int, lat: float, lon: float, radius_m: int) -> list[dict]:
    """
    Query Overpass for all building=* elements within radius_m metres of the ZIP centroid.

    US ZIP code boundary polygons are largely absent from OSM, so we query directly
    using a circular radius around the centroid instead.

    Returns a list of per-building record dicts.
    """
    zip_str = str(zip_code).zfill(5)

    # Fetch all building=* elements (including generic "yes", which dominates US OSM data)
    # plus amenity=* elements for type resolution.
    query = f"""
[out:json][timeout:60];
(
  node["building"](around:{radius_m},{lat},{lon});
  way["building"](around:{radius_m},{lat},{lon});
  node["amenity"](around:{radius_m},{lat},{lon});
  way["amenity"](around:{radius_m},{lat},{lon});
);
out center tags;
"""
    data = _query_overpass(query)
    elements = (data or {}).get("elements", [])

    if not elements:
        print(f"  ZIP {zip_str}: no buildings found within {radius_m}m of centroid.")
        return []

    records = []
    for element in elements:
        tags = element.get("tags", {})
        building_value = tags.get("building", "").strip().lower()

        # lat/lon: nodes store it directly; ways expose it under "center"
        if element["type"] == "node":
            elem_lat = element.get("lat")
            elem_lon = element.get("lon")
        else:
            center = element.get("center", {})
            elem_lat = center.get("lat")
            elem_lon = center.get("lon")

        records.append({
            "osm_id":       element.get("id"),
            "osm_type":     element.get("type"),
            "zip":          zip_code,
            "name":         tags.get("name", ""),
            "housenumber":  tags.get("addr:housenumber", ""),
            "street":       tags.get("addr:street", ""),
            "city":         tags.get("addr:city", ""),
            "postcode":     tags.get("addr:postcode", ""),
            "building_tag": building_value,
            "amenity_tag":  tags.get("amenity", ""),
            "lat":          elem_lat,
            "lon":          elem_lon,
        })

    return records


def dump_zip_building_types(loc_df: pd.DataFrame, output_file: Path, radius_m: int, batch_size: int, sleep_s: float):
    first_write = True

    for batch in chunked(loc_df, batch_size):
        rows = []
        for _, row in batch.iterrows():
            zip_code = int(row["zip"])
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            density = float(row.get("density", 0) or 0)

            zip_radius = density_to_radius(density, max_radius_m=radius_m)
            print(f"  Querying ZIP {str(zip_code).zfill(5)} (density={density:.0f}/km², radius={zip_radius}m) ...")
            buildings = _get_buildings_for_zip(zip_code, lat, lon, zip_radius)
            for b in buildings:
                b["query_radius_m"] = zip_radius
            rows.extend(buildings)

            # Rate-limit: Overpass public instance recommends no more than 1 request/second
            time.sleep(sleep_s)

        if rows:
            out_df = pd.DataFrame(rows)
            out_df.to_csv(output_file, mode="a", header=first_write, index=False)
            first_write = False
        elif first_write:
            # Write a header-only CSV so a run with no results still produces a visible file
            pd.DataFrame(columns=[
                "osm_id", "osm_type", "zip", "name", "housenumber", "street", "city",
                "postcode", "building_tag", "amenity_tag", "lat", "lon", "query_radius_m",
            ]).to_csv(output_file, index=False)

        print(f"Saved batch of {len(batch)} ZIPs ({len(rows)} buildings) -> {output_file.name}")

    print(f"\nDONE: saved to {output_file}")


def parse_args():
    p = argparse.ArgumentParser(description="Dump ZIP->building type counts CSV using the Overpass API (OSM).")
    p.add_argument("--cities", required=True, help='Semicolon-separated list like "Houston,TX;Austin,TX"')
    p.add_argument("--uszips", default="uszips.csv",
                   help="Path to simplemaps uszips.csv (https://simplemaps.com/data/us-zips)")
    p.add_argument("--radius-m", type=int, default=9000,
                   help="Maximum search radius in meters (default: 9000). "
                        "Actual radius per ZIP is derived from population density and capped at this value.")
    p.add_argument("--batch-size", type=int, default=10, help="ZIPs per write batch")
    p.add_argument("--sleep-s", type=float, default=1.0,
                   help="Seconds to sleep between Overpass requests (rate-limit)")
    p.add_argument("--out-dir", default="static data", help="Output directory")
    p.add_argument("--out-prefix", default="zip_building_types", help="Output filename prefix")
    return p.parse_args()


def main():
    args = parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    output_file = out_dir / f"{args.out_prefix}_{timestamp}.csv"

    if str(out_dir) != "static data":
        raise SystemExit("ERROR: This script must only write to the 'static data' folder. Do not change --out-dir.")

    tracker = PipelineRunTracker(out_dir=out_dir)
    tracker.start(args, script="dump_zip_building_types")

    pairs = parse_city_state_list(args.cities)

    all_frames = []
    skipped_cities = []
    for city, st in pairs:
        sub = get_zip_centroids(city, st, uszips_csv_path=args.uszips)
        if sub.empty:
            print(f"WARNING: No ZIP centroids found for the city: {city} in the state: {st}. Skipping gracefully...")
            skipped_cities.append(f"{city},{st}")
            continue
        all_frames.append(sub)

    if not all_frames:
        tracker.finish(status="error", error="No ZIP centroids found for all provided cities and states.")
        raise SystemExit("No ZIP centroids found for all provided cities and states.")

    loc_df = pd.concat(all_frames, ignore_index=True)
    cities_found = [f"{city},{st}" for city, st in pairs if f"{city},{st}" not in skipped_cities]
    tracker.record_locations(loc_df, skipped_cities, cities_found=cities_found)

    try:
        dump_zip_building_types(
            loc_df=loc_df,
            output_file=output_file,
            radius_m=args.radius_m,
            batch_size=args.batch_size,
            sleep_s=args.sleep_s,
        )
        tracker.record_output(
            "building_types",
            output_file,
            ["osm_id", "osm_type", "zip", "name", "housenumber", "street", "city",
             "postcode", "building_tag", "amenity_tag", "lat", "lon"],
            OVERPASS_URL,
            args.batch_size,
        )
        tracker.finish(status="success")
    except Exception as e:
        tracker.finish(status="error", error=str(e))
        raise


if __name__ == "__main__":
    main()
