#!/usr/bin/env python3
"""
Merge daily bronze Parquet datasets into master Parquet datasets.

Expected input layout:

    bronze/air/march_10_air_quality_hourly_20260401_120609.parquet
    bronze/weather/march_10_weather_hourly_20260401_120609.parquet

Example:

        python silver/merge_parquets_into_master_file.py \
            --input-dir bronze \
            --state-file silver/merge_state_parquet.json \
            --air-master silver/master/air_quality_master \
            --weather-master silver/master/weather_master \
      --force-rebuild

Read outputs with:

    pd.read_parquet("silver/master/air_quality_master")
    pd.read_parquet("silver/master/weather_master")
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def load_state(path: Path) -> Dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {}

    for category in ["air_quality", "weather"]:
        state.setdefault(category, {})
        state[category].setdefault("datasets", [])
        state[category].setdefault("sort_keys", {})

    return state


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
            tmp_path = Path(f.name)

        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def is_parquet_dataset(path: Path) -> bool:
    if path.is_file() and path.suffix.lower() == ".parquet":
        return True
    if path.is_dir() and any(path.glob("*.parquet")):
        return True
    return False


def find_category_dir(input_root: Path, category: str) -> Optional[Path]:
    """
    Accepts either:
      --input-dir data/bronze
      --input-dir data
      --input-dir .
    """
    folder_name = "air" if category == "air_quality" else "weather"

    candidates = [
        input_root / folder_name,
        input_root / "bronze" / folder_name,
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    return None


def find_datasets(input_root: Path, category: str) -> List[Path]:
    category_dir = find_category_dir(input_root, category)

    if category_dir is None:
        return []

    datasets = [p for p in category_dir.iterdir() if is_parquet_dataset(p)]
    datasets.sort(key=lambda p: get_sort_key(p))

    return datasets


def read_parquet_columns(
    dataset_path: Path,
    columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    return pd.read_parquet(dataset_path, columns=columns, engine="pyarrow")


def fallback_sort_key_from_name(name: str) -> Tuple[str, str]:
    """
    Supports names like:
      march_1_air_quality_hourly_20260406_193543
      april_10_weather_hourly_20260415_194839

    This is only a fallback. The preferred sort key comes from the minimum
    value in the dataset's `time` column.
    """
    lowered = name.lower()
    parts = lowered.split("_")

    if len(parts) >= 2 and parts[0] in MONTH_MAP:
        month_num = MONTH_MAP[parts[0]]
        day_match = re.match(r"^(\d+)", parts[1])

        if day_match:
            day_num = int(day_match.group(1))
            return (f"month-day:{month_num:02d}-{day_num:02d}", lowered)

    return (f"name:{lowered}", lowered)


def get_sort_key(dataset_path: Path) -> Tuple[str, str]:
    """
    Sort by the earliest timestamp inside the Parquet dataset, not by the
    ingestion timestamp in the folder name.
    """
    try:
        df = read_parquet_columns(dataset_path, columns=["time"])

        if "time" in df.columns and not df.empty:
            time_values = df["time"].map(decode_bytes_value)

            parsed_time = pd.to_datetime(
                time_values,
                errors="coerce",
                utc=True,
            ).dt.tz_convert(None)

            min_time = parsed_time.min()

            if pd.notna(min_time):
                return (f"time:{min_time.isoformat()}", dataset_path.name.lower())

    except Exception:
        pass

    return fallback_sort_key_from_name(dataset_path.name)


def get_columns(dataset_path: Path) -> List[str]:
    df = read_parquet_columns(dataset_path)
    return list(df.columns)


def decode_bytes_value(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def normalize_common_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep key columns stable and make Parquet-sensitive columns consistent.
    """
    df = df.copy()

    for col in ["zip", "zipcode", "zip_code", "postal_code", "zcta"]:
        if col in df.columns:
            df[col] = df[col].astype("string").str.zfill(5)

    if "time" in df.columns:
        df["time"] = df["time"].map(decode_bytes_value)
        df["time"] = pd.to_datetime(
            df["time"],
            errors="coerce",
            utc=True,
        ).dt.tz_convert(None)

        bad_time_rows = df["time"].isna().sum()
        if bad_time_rows > 0:
            raise ValueError(
                f"Found {bad_time_rows} rows with invalid/unparseable time values."
            )

    for col in df.select_dtypes(include=["object"]).columns:
        has_bytes = df[col].map(lambda x: isinstance(x, (bytes, bytearray))).any()

        if has_bytes:
            df[col] = df[col].map(decode_bytes_value).astype("string")

    return df


def load_datasets(
    datasets: Iterable[Path],
    expected_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    columns_in_use = expected_columns

    for dataset_path in datasets:
        df = read_parquet_columns(dataset_path)
        df = normalize_common_columns(df)

        if columns_in_use is None:
            columns_in_use = list(df.columns)
        elif list(df.columns) != columns_in_use:
            raise ValueError(
                f"Schema mismatch in {dataset_path}\n"
                f"Expected: {columns_in_use}\n"
                f"Found:    {list(df.columns)}"
            )

        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=columns_in_use or [])

    merged = pd.concat(frames, ignore_index=True)

    sort_cols = [c for c in ["zip", "time"] if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)

    return merged


def write_parquet_dataset(
    df: pd.DataFrame,
    output_dir: Path,
    rows_per_part: int,
    compression: str,
    start_part: int = 0,
    replace: bool = False,
) -> int:
    df = normalize_common_columns(df)

    if replace and output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    if df.empty:
        part_path = output_dir / f"part-{start_part:05d}.parquet"
        df.to_parquet(
            part_path,
            index=False,
            engine="pyarrow",
            compression=compression,
        )
        return start_part + 1

    part_idx = start_part

    for start in range(0, len(df), rows_per_part):
        chunk = df.iloc[start:start + rows_per_part]
        part_path = output_dir / f"part-{part_idx:05d}.parquet"

        chunk.to_parquet(
            part_path,
            index=False,
            engine="pyarrow",
            compression=compression,
        )

        part_idx += 1

    return part_idx


def next_part_index(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0

    max_idx = -1

    for part in output_dir.glob("part-*.parquet"):
        match = re.match(r"part-(\d+)\.parquet$", part.name)

        if match:
            max_idx = max(max_idx, int(match.group(1)))

    return max_idx + 1


def process_category(
    category: str,
    datasets: List[Path],
    output_dir: Path,
    state: Dict[str, Any],
    rows_per_part: int,
    compression: str,
    force_rebuild: bool,
) -> None:
    print(f"\nProcessing {category}...")

    if not datasets:
        print("  No matching Parquet datasets found.")
        return

    category_state = state[category]
    seen_datasets = set(category_state["datasets"])

    new_datasets = [
        p for p in datasets
        if str(p.resolve()) not in seen_datasets
    ]

    if not new_datasets and output_dir.exists() and not force_rebuild:
        print("  No new datasets to merge.")
        return

    existing_keys = [
        tuple(category_state.get("sort_keys", {}).get(path, []))
        for path in category_state.get("datasets", [])
    ]
    existing_keys = [key for key in existing_keys if key]

    earliest_new_key = min((get_sort_key(p) for p in new_datasets), default=None)
    existing_last_key = max(existing_keys) if existing_keys else None

    needs_rebuild = (
        force_rebuild
        or not output_dir.exists()
        or existing_last_key is None
        or (earliest_new_key is not None and earliest_new_key < existing_last_key)
    )

    if needs_rebuild:
        print("  Rebuilding master Parquet dataset...")

        all_datasets = sorted(datasets, key=get_sort_key)
        merged = load_datasets(all_datasets)

        write_parquet_dataset(
            merged,
            output_dir=output_dir,
            rows_per_part=rows_per_part,
            compression=compression,
            start_part=0,
            replace=True,
        )

        category_state["datasets"] = [str(p.resolve()) for p in all_datasets]
        category_state["sort_keys"] = {
            str(p.resolve()): list(get_sort_key(p))
            for p in all_datasets
        }

        print(
            f"  Rebuilt {output_dir} from {len(all_datasets)} dataset(s), "
            f"{len(merged)} row(s)."
        )

    else:
        print(f"  Appending {len(new_datasets)} new dataset(s)...")

        new_datasets.sort(key=get_sort_key)

        expected_columns = get_columns(output_dir)
        merged_new = load_datasets(
            new_datasets,
            expected_columns=expected_columns,
        )

        start_part = next_part_index(output_dir)

        write_parquet_dataset(
            merged_new,
            output_dir=output_dir,
            rows_per_part=rows_per_part,
            compression=compression,
            start_part=start_part,
            replace=False,
        )

        for p in new_datasets:
            resolved = str(p.resolve())
            category_state["datasets"].append(resolved)
            category_state["sort_keys"][resolved] = list(get_sort_key(p))

        print(f"  Appended {len(merged_new)} row(s) to {output_dir}.")

    print("  Added:")
    for p in new_datasets:
        print(f"    - {p.name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge bronze air/weather Parquet datasets into master Parquet datasets."
    )

    parser.add_argument(
        "--input-dir",
        required=True,
        help="Input root containing bronze/air and bronze/weather, or the bronze directory itself.",
    )

    parser.add_argument(
        "--state-file",
        default="merge_state_parquet.json",
        help="JSON file used to track processed Parquet datasets.",
    )

    parser.add_argument(
        "--air-master",
        default="bronze/master/air_quality_master",
        help="Output master air-quality Parquet dataset directory.",
    )

    parser.add_argument(
        "--weather-master",
        default="bronze/master/weather_master",
        help="Output master weather Parquet dataset directory.",
    )

    parser.add_argument(
        "--rows-per-part",
        type=int,
        default=100_000,
        help="Maximum rows per output Parquet part file.",
    )

    parser.add_argument(
        "--compression",
        default="snappy",
        choices=["snappy", "gzip", "brotli", "zstd"],
        help="Parquet compression codec.",
    )

    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Rebuild both master datasets from all discovered source datasets.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_root = Path(args.input_dir).resolve()
    state_file = Path(args.state_file).resolve()
    air_master = Path(args.air_master).resolve()
    weather_master = Path(args.weather_master).resolve()

    if not input_root.exists() or not input_root.is_dir():
        raise ValueError(
            f"Input directory does not exist or is not a directory: {input_root}"
        )

    state = load_state(state_file)

    air_datasets = find_datasets(input_root, "air_quality")
    weather_datasets = find_datasets(input_root, "weather")

    process_category(
        "air_quality",
        air_datasets,
        air_master,
        state,
        rows_per_part=args.rows_per_part,
        compression=args.compression,
        force_rebuild=args.force_rebuild,
    )
    save_state(state_file, state)

    process_category(
        "weather",
        weather_datasets,
        weather_master,
        state,
        rows_per_part=args.rows_per_part,
        compression=args.compression,
        force_rebuild=args.force_rebuild,
    )

    save_state(state_file, state)

    print("\nDone.")
    print(f"State file:      {state_file}")
    print(f"Air master:      {air_master}")
    print(f"Weather master:  {weather_master}")


if __name__ == "__main__":
    main()