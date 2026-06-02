# benchmarks/run_csv_pipeline_benchmark.py

from pathlib import Path
import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time


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


def size_mib(path: Path) -> float | None:
    if not path.exists():
        return None
    return round(path.stat().st_size / (1024 * 1024), 4)


def summarize(values: list[float]) -> dict:
    return {
        "runs": len(values),
        "min_seconds": round(min(values), 4),
        "median_seconds": round(statistics.median(values), 4),
        "max_seconds": round(max(values), 4),
        "mean_seconds": round(statistics.mean(values), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", default="benchmark_results/csv_pipeline_benchmark.json")
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    output_path = repo / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_runs = []

    for run_num in range(1, args.runs + 1):
        run_dir = repo / "benchmark_runs" / f"csv_run_{run_num}"

        if run_dir.exists():
            shutil.rmtree(run_dir)

        run_dir.mkdir(parents=True)

        air_master = run_dir / "air_quality_master.csv"
        weather_master = run_dir / "weather_master.csv"
        air_stripped = run_dir / "air_quality_master_tz_stripped.csv"
        weather_stripped = run_dir / "weather_master_tz_stripped.csv"
        features_dir = run_dir / "features"

        step_times = {}

        step_times["merge_seconds"] = run_step(
            [
                sys.executable,
                "preprocessing/merge_data_into_master_file.py",
                "--input-dir",
                "data",
                "--state-file",
                str(run_dir / "merge_state.json"),
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
            ],
            cwd=repo,
        )

        total_seconds = sum(step_times.values())

        pipeline_summary_path = features_dir / "metadata" / "pipeline_summary.json"
        internal_preprocessing_summary = None
        if pipeline_summary_path.exists():
            internal_preprocessing_summary = json.loads(
                pipeline_summary_path.read_text(encoding="utf-8")
            )

        final_features = features_dir / "all_features.csv"

        run_report = {
            "run": run_num,
            "step_times": step_times,
            "total_seconds": round(total_seconds, 4),
            "output_sizes_mib": {
                "air_quality_master": size_mib(air_master),
                "weather_master": size_mib(weather_master),
                "air_quality_master_tz_stripped": size_mib(air_stripped),
                "weather_master_tz_stripped": size_mib(weather_stripped),
                "all_features": size_mib(final_features),
            },
            "internal_preprocessing_summary": internal_preprocessing_summary,
        }

        all_runs.append(run_report)

        print(f"Run {run_num} complete: {total_seconds:.4f} seconds")

    aggregate = {
        "merge": summarize([r["step_times"]["merge_seconds"] for r in all_runs]),
        "strip_air": summarize([r["step_times"]["strip_air_seconds"] for r in all_runs]),
        "strip_weather": summarize([r["step_times"]["strip_weather_seconds"] for r in all_runs]),
        "preprocessing": summarize([r["step_times"]["preprocessing_seconds"] for r in all_runs]),
        "total": summarize([r["total_seconds"] for r in all_runs]),
    }

    report = {
        "format": "csv",
        "runs": all_runs,
        "aggregate": aggregate,
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()