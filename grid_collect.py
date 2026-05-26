import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from collect import (
    AQ_FREE_URL,
    AQ_URL,
    HOURLY_VARS,
    WEATHER_HOURLY_VARS,
    WEATHER_URL,
    WeightRateLimiter,
    _is_auth_error,
    compute_safe_batch_size,
    fetch_and_save_csv,
)
from metadata_tracker import PipelineRunTracker


def load_grid_centroids(grid_csv_path: str) -> pd.DataFrame:
    """Load Houston grid centroids and normalize columns for collect.py helpers."""
    df = pd.read_csv(
        grid_csv_path,
        dtype={
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
    if rename_map:
        df = df.rename(columns=rename_map)

    required_columns = ["city", "state", "zip", "latitude", "longitude"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"Grid centroids file is missing required columns: {missing}")

    loc_df = df[required_columns].copy()
    loc_df["city"] = loc_df["city"].astype(str).str.strip()
    loc_df["state"] = loc_df["state"].astype(str).str.strip().str.upper()
    loc_df["zip"] = loc_df["zip"].astype(str).str.strip().str.zfill(5)
    loc_df["latitude"] = pd.to_numeric(loc_df["latitude"], errors="coerce")
    loc_df["longitude"] = pd.to_numeric(loc_df["longitude"], errors="coerce")

    before_drop = len(loc_df)
    loc_df = loc_df.dropna(subset=["latitude", "longitude"])
    loc_df = loc_df.drop_duplicates(subset=required_columns).reset_index(drop=True)
    dropped = before_drop - len(loc_df)

    if loc_df.empty:
        raise ValueError("No usable grid centroid coordinates found.")
    if dropped:
        print(f"WARNING: Dropped {dropped} grid centroid rows with missing/duplicate coordinates.")

    return loc_df


def parse_args():
    p = argparse.ArgumentParser(
        description="Bulk historical air quality and weather pull from Open-Meteo for Houston grid centroids."
    )
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--timezone", default="America/Chicago", help="IANA timezone")
    p.add_argument("--batch-size", type=int, default=50, help="Grid centroids per API request")
    p.add_argument(
        "--grid-centroids",
        default=str(Path("static data") / "houston_grid_centroids_2x2.csv"),
        help="Path to 2x2 Houston grid centroids CSV",
    )
    p.add_argument("--out-dir", default="new data", help="Output directory")
    p.add_argument("--out-prefix", default="grid", help="Output filename prefix")
    return p.parse_args()


def main():
    args = parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"{args.out_prefix}_air_quality_hourly_{timestamp}.csv"
    output_file_weather = out_dir / f"{args.out_prefix}_weather_hourly_{timestamp}.csv"

    tracker = PipelineRunTracker(out_dir=out_dir)
    tracker.start(args, script="grid_collect")

    try:
        loc_df = load_grid_centroids(args.grid_centroids)
        tracker.record_locations(loc_df, skipped_cities=[])

        safe_bs = compute_safe_batch_size(HOURLY_VARS, args.start_date, args.end_date)
        safe_bs_weather = compute_safe_batch_size(WEATHER_HOURLY_VARS, args.start_date, args.end_date)
        batch_size = min(args.batch_size, safe_bs)
        batch_size_weather = min(args.batch_size, safe_bs_weather)

        shared_limiter = WeightRateLimiter(max_weight=600.0, window_seconds=60)

        aq_url_used = AQ_URL
        try:
            fetch_and_save_csv(
                loc_df=loc_df,
                start_date=args.start_date,
                end_date=args.end_date,
                output_file=output_file,
                timezone=args.timezone,
                batch_size=batch_size,
                url=AQ_URL,
                hourly_vars=HOURLY_VARS,
                limiter=shared_limiter,
            )
        except Exception as aq_exc:
            if AQ_URL != AQ_FREE_URL and _is_auth_error(aq_exc):
                print(f"WARNING: Paid AQ endpoint failed ({aq_exc}). Retrying with Open-Meteo free tier...")
                if output_file.exists():
                    output_file.unlink()
                aq_url_used = AQ_FREE_URL
                fetch_and_save_csv(
                    loc_df=loc_df,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    output_file=output_file,
                    timezone=args.timezone,
                    batch_size=batch_size,
                    url=AQ_FREE_URL,
                    hourly_vars=HOURLY_VARS,
                    limiter=shared_limiter,
                )
            else:
                raise

        tracker.record_output("air_quality", output_file, HOURLY_VARS, aq_url_used, batch_size)

        fetch_and_save_csv(
            loc_df=loc_df,
            start_date=args.start_date,
            end_date=args.end_date,
            output_file=output_file_weather,
            timezone=args.timezone,
            batch_size=batch_size_weather,
            url=WEATHER_URL,
            hourly_vars=WEATHER_HOURLY_VARS,
            limiter=shared_limiter,
        )
        tracker.record_output("weather", output_file_weather, WEATHER_HOURLY_VARS, WEATHER_URL, batch_size_weather)

        tracker.finish(status="success")
    except Exception as e:
        tracker.finish(status="error", error=str(e))
        raise


if __name__ == "__main__":
    main()
