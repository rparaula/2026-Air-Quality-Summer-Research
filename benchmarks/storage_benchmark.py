# benchmarks/benchmark_storage.py

from pathlib import Path
import argparse
import json
import pandas as pd


def bytes_to_mib(num_bytes: int) -> float:
    return num_bytes / (1024 * 1024)


def convert_csv_to_parquet(csv_path: Path, csv_root: Path, parquet_root: Path, compression: str) -> dict:
    rel_path = csv_path.relative_to(csv_root)
    parquet_path = parquet_root / rel_path.with_suffix(".parquet")
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    # Keep ZIPs as strings so leading zeros do not get damaged.
    df = pd.read_csv(csv_path, dtype={"zip": "string"}, low_memory=False)
    df.to_parquet(parquet_path, index=False, compression=compression)

    csv_bytes = csv_path.stat().st_size
    parquet_bytes = parquet_path.stat().st_size

    return {
        "csv_file": str(csv_path),
        "parquet_file": str(parquet_path),
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "csv_mib": round(bytes_to_mib(csv_bytes), 4),
        "parquet_mib": round(bytes_to_mib(parquet_bytes), 4),
        "compression_ratio_csv_to_parquet": round(csv_bytes / parquet_bytes, 4) if parquet_bytes else None,
        "percent_reduction": round(100 * (1 - parquet_bytes / csv_bytes), 2) if csv_bytes else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-root", required=True)
    parser.add_argument("--parquet-root", required=True)
    parser.add_argument("--glob", default="*.csv")
    parser.add_argument("--compression", default="snappy")
    parser.add_argument("--output", default="benchmark_results/storage_report.json")
    args = parser.parse_args()

    csv_root = Path(args.csv_root)
    parquet_root = Path(args.parquet_root)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []

    for csv_path in sorted(csv_root.rglob(args.glob)):
        results.append(convert_csv_to_parquet(csv_path, csv_root, parquet_root, args.compression))

    total_csv_mib = sum(row["csv_mib"] for row in results)
    total_parquet_mib = sum(row["parquet_mib"] for row in results)

    report = {
        "compression": args.compression,
        "total_csv_mib": round(total_csv_mib, 4),
        "total_parquet_mib": round(total_parquet_mib, 4),
        "overall_compression_ratio_csv_to_parquet": round(total_csv_mib / total_parquet_mib, 4)
        if total_parquet_mib
        else None,
        "overall_percent_reduction": round(100 * (1 - total_parquet_mib / total_csv_mib), 2)
        if total_csv_mib
        else None,
        "files": results,
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()