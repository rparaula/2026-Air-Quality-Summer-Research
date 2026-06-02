# benchmarks/tiny_csv_smoke_benchmark.py

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
import argparse
import csv
import json
import shutil
import statistics
import subprocess
import sys
import time


AIR_QUALITY_COLUMNS = [
    "city",
    "state",
    "zip",
    "latitude",
    "longitude",
    "time",
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


WEATHER_COLUMNS = [
    "city",
    "state",
    "zip",
    "latitude",
    "longitude",
    "time",
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


HOUSTON_ZIPS = [
    {"zip": "77002", "latitude": 29.7550, "longitude": -95.3650},
    {"zip": "77003", "latitude": 29.7490, "longitude": -95.3470},
]


def size_mib(path: Path) -> float | None:
    if not path.exists():
        return None
    return round(path.stat().st_size / (1024 * 1024), 4)


def count_csv_rows(path: Path) -> int | None:
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def run_step(command: list[str], cwd: Path) -> float:
    start = time.perf_counter()

    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
    )

    elapsed = time.perf_counter() - start

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed:\n{' '.join(command)}\n\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}"
        )

    return elapsed


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def generate_smoke_input_data(input_dir: Path, hours: int, zips: list[dict]) -> dict:
    """
    Generate tiny synthetic CSVs that mimic collect.py outputs.

    These are not pulled from Open-Meteo.
    They only imitate the shape/columns of the ingestion output.
    """

    input_dir.mkdir(parents=True, exist_ok=True)

    start = datetime(2026, 4, 1, 0, 0, 0)

    air_rows = []
    weather_rows = []

    for hour_index in range(hours):
        timestamp = start + timedelta(hours=hour_index)

        # Include timezone text so strip_tz_info.py has something realistic to remove.
        time_text = timestamp.strftime("%Y-%m-%dT%H:%M:%S-05:00")

        for zip_index, loc in enumerate(zips):
            base = {
                "city": "Houston",
                "state": "TX",
                "zip": loc["zip"],
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "time": time_text,
            }

            air_rows.append(
                {
                    **base,
                    "us_aqi": 40 + hour_index + zip_index,
                    "pm10": 18.0 + 0.2 * hour_index,
                    "pm2_5": 7.5 + 0.1 * hour_index,
                    "carbon_monoxide": 180.0 + hour_index,
                    "nitrogen_dioxide": 12.0 + 0.1 * zip_index,
                    "sulphur_dioxide": 2.0,
                    "ozone": 55.0 + 0.2 * hour_index,
                    "uv_index_clear_sky": 5.0,
                    "uv_index": 4.5,
                    "dust": 0.1,
                    "aerosol_optical_depth": 0.2,
                }
            )

            weather_rows.append(
                {
                    **base,
                    "temperature_2m": 72.0 + 0.25 * hour_index,
                    "relative_humidity_2m": 65.0 - 0.1 * hour_index,
                    "precipitation": 0.0,
                    "wind_speed_10m": 6.0 + 0.1 * zip_index,
                    "wind_speed_100m": 11.0 + 0.1 * zip_index,
                    "wind_direction_10m": 135.0 + hour_index,
                    "wind_direction_100m": 145.0 + hour_index,
                    "wind_gusts_10m": 14.0,
                    "shortwave_radiation": 300.0 + hour_index,
                    "diffuse_radiation": 80.0,
                    "cloud_cover": 30.0,
                }
            )

    # Filenames intentionally match merge_data_into_master_file.py's expected pattern:
    # month_day_air_quality_...
    # month_day_weather_...
    air_csv = input_dir / "april_1_air_quality_hourly_smoke.csv"
    weather_csv = input_dir / "april_1_weather_hourly_smoke.csv"

    write_csv(air_csv, AIR_QUALITY_COLUMNS, air_rows)
    write_csv(weather_csv, WEATHER_COLUMNS, weather_rows)

    return {
        "input_dir": str(input_dir),
        "air_quality_csv": str(air_csv),
        "weather_csv": str(weather_csv),
        "air_quality_rows": len(air_rows),
        "weather_rows": len(weather_rows),
        "hours": hours,
        "zips": [z["zip"] for z in zips],
    }


def summarize(values: list[float]) -> dict:
    return {
        "runs": len(values),
        "min_seconds": round(min(values), 4),
        "median_seconds": round(statistics.median(values), 4),
        "max_seconds": round(max(values), 4),
        "mean_seconds": round(statistics.mean(values), 4),
    }


def run_one_smoke_benchmark(repo: Path, run_num: int, hours: int, keep_run: bool) -> dict:
    benchmarks_dir = repo / "benchmarks"
    run_dir = benchmarks_dir / "runs" / "tiny_csv_smoke" / f"run_{run_num}"

    if run_dir.exists():
        shutil.rmtree(run_dir)

    input_dir = run_dir / "input_data"
    features_dir = run_dir / "features"

    air_master = run_dir / "air_quality_master.csv"
    weather_master = run_dir / "weather_master.csv"
    air_stripped = run_dir / "air_quality_master_tz_stripped.csv"
    weather_stripped = run_dir / "weather_master_tz_stripped.csv"
    merge_state = run_dir / "merge_state.json"

    run_dir.mkdir(parents=True, exist_ok=True)

    generated_inputs = generate_smoke_input_data(
        input_dir=input_dir,
        hours=hours,
        zips=HOUSTON_ZIPS,
    )

    step_times = {}

    step_times["generate_inputs_seconds"] = 0.0

    step_times["merge_to_master_seconds"] = run_step(
        [
            sys.executable,
            "preprocessing/merge_data_into_master_file.py",
            "--input-dir",
            str(input_dir),
            "--state-file",
            str(merge_state),
            "--air-master",
            str(air_master),
            "--weather-master",
            str(weather_master),
        ],
        cwd=repo,
    )

    step_times["strip_air_seconds"] = run_step(
        [
            sys.executable,
            "preprocessing/strip_tz_info.py",
            str(air_master),
            str(air_stripped),
            "--time-col",
            "time",
        ],
        cwd=repo,
    )

    step_times["strip_weather_seconds"] = run_step(
        [
            sys.executable,
            "preprocessing/strip_tz_info.py",
            str(weather_master),
            str(weather_stripped),
            "--time-col",
            "time",
        ],
        cwd=repo,
    )

    step_times["preprocessing_seconds"] = run_step(
        [
            sys.executable,
            "preprocessing/preprocessing.py",
            "--air-quality",
            str(air_stripped),
            "--weather",
            str(weather_stripped),
            "--tri-facilities",
            "static data/tri_facilities_houston.csv",
            "--tri-chemicals",
            "static data/tri_chemicals_houston.csv",
            "--zip-shapefile",
            "preprocessing/houston-zip-shapefiles/houston_zcta_filtered.shp",
            "--roads-shapefile",
            "preprocessing/road-data/tl_2025_48_prisecroads.shp",
            "--output-dir",
            str(features_dir),
            "--left-drop-columns",
            "city",
            "state",
            "--right-drop-columns",
            "city",
            "state",
        ],
        cwd=repo,
    )

    final_features = features_dir / "all_features.csv"
    pipeline_summary = features_dir / "metadata" / "pipeline_summary.json"

    internal_preprocessing_summary = None
    if pipeline_summary.exists():
        internal_preprocessing_summary = json.loads(
            pipeline_summary.read_text(encoding="utf-8")
        )

    total_seconds = sum(step_times.values())

    report = {
        "run": run_num,
        "run_dir": str(run_dir),
        "generated_inputs": generated_inputs,
        "step_times": {k: round(v, 4) for k, v in step_times.items()},
        "total_seconds": round(total_seconds, 4),
        "output_rows": {
            "air_quality_master": count_csv_rows(air_master),
            "weather_master": count_csv_rows(weather_master),
            "air_quality_master_tz_stripped": count_csv_rows(air_stripped),
            "weather_master_tz_stripped": count_csv_rows(weather_stripped),
            "all_features": count_csv_rows(final_features),
        },
        "output_sizes_mib": {
            "air_quality_master": size_mib(air_master),
            "weather_master": size_mib(weather_master),
            "air_quality_master_tz_stripped": size_mib(air_stripped),
            "weather_master_tz_stripped": size_mib(weather_stripped),
            "all_features": size_mib(final_features),
        },
        "internal_preprocessing_summary": internal_preprocessing_summary,
    }

    if not keep_run:
        # Keep the JSON report elsewhere, but clean the temporary generated files.
        shutil.rmtree(run_dir, ignore_errors=True)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate tiny synthetic ingestion-like CSVs and benchmark the CSV preprocessing pipeline."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument(
        "--keep-runs",
        action="store_true",
        help="Keep generated temporary input/output files under benchmarks/runs/.",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/results/tiny_csv_smoke_benchmark.json",
    )

    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    output_path = repo / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_runs = []

    for run_num in range(1, args.runs + 1):
        start = time.perf_counter()

        run_report = run_one_smoke_benchmark(
            repo=repo,
            run_num=run_num,
            hours=args.hours,
            keep_run=args.keep_runs,
        )

        wall_seconds = time.perf_counter() - start
        run_report["wall_clock_seconds"] = round(wall_seconds, 4)

        all_runs.append(run_report)

        print(
            f"Run {run_num} complete: "
            f"{run_report['total_seconds']:.4f} timed seconds "
            f"({wall_seconds:.4f} wall-clock seconds)"
        )

    aggregate = {
        "generate_inputs": summarize([r["step_times"]["generate_inputs_seconds"] for r in all_runs]),
        "merge_to_master": summarize([r["step_times"]["merge_to_master_seconds"] for r in all_runs]),
        "strip_air": summarize([r["step_times"]["strip_air_seconds"] for r in all_runs]),
        "strip_weather": summarize([r["step_times"]["strip_weather_seconds"] for r in all_runs]),
        "preprocessing": summarize([r["step_times"]["preprocessing_seconds"] for r in all_runs]),
        "total": summarize([r["total_seconds"] for r in all_runs]),
        "wall_clock": summarize([r["wall_clock_seconds"] for r in all_runs]),
    }

    report = {
        "benchmark_name": "tiny_csv_smoke_benchmark",
        "format": "csv",
        "description": (
            "Generates tiny synthetic ingestion-like air-quality and weather CSV files, "
            "then runs merge -> strip_tz_info -> preprocessing."
        ),
        "parameters": {
            "runs": args.runs,
            "hours": args.hours,
            "zips": [z["zip"] for z in HOUSTON_ZIPS],
            "rows_per_generated_file": args.hours * len(HOUSTON_ZIPS),
        },
        "runs": all_runs,
        "aggregate": aggregate,
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nBenchmark complete.")
    print(f"Report: {output_path}")
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()