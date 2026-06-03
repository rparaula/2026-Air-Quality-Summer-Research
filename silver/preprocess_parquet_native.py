#!/usr/bin/env python3
"""Native-Parquet version of the AQ/weather preprocessing pipeline.

I tried to make this script follow the same high-level operations as the original CSV-based
preprocessing.py, but it keeps all tabular air/weather/static/intermediate/final I/O 
in Parquet format instead of using CSV adapters.

Main stages:
  1. Read air-quality and weather Parquet datasets in batches.
  2. Build sorted Parquet run files for an external-sort style merge.
  3. Precompute static spatial lookup from ZIP polygons, roads, and TRI Parquet tables.
  4. Stream-merge the sorted Parquet runs on (zip, time).
  5. Add wind-direction sin/cos, calendar features, spatial scores, and lag features.
  6. Optionally apply low-cardinality and variance filters.
  7. Write final feature table as a Parquet dataset folder.

"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import shutil
import time
from collections import defaultdict, deque
from itertools import count
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from shapely.ops import nearest_points


ZIP_COLUMNS = {"zip", "zipcode", "zip_code", "postal_code", "zcta"}


# -----------------------------------------------------------------------------
# Logging / tracking helpers
# -----------------------------------------------------------------------------


class IOTracker:
    def __init__(self) -> None:
        self.read_ops = 0
        self.write_ops = 0
        self.read_detail: Dict[str, int] = defaultdict(int)
        self.write_detail: Dict[str, int] = defaultdict(int)

    def add_read(self, label: str, count: int = 1) -> None:
        self.read_ops += int(count)
        self.read_detail[label] += int(count)

    def add_write(self, label: str, count: int = 1) -> None:
        self.write_ops += int(count)
        self.write_detail[label] += int(count)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_read_ops": int(self.read_ops),
            "total_write_ops": int(self.write_ops),
            "read_breakdown": dict(sorted(self.read_detail.items())),
            "write_breakdown": dict(sorted(self.write_detail.items())),
        }


class CardinalityTracker:
    def __init__(self) -> None:
        self.unique_values: Dict[str, Set[str]] = defaultdict(set)

    def update_row(self, row: Dict[str, Any]) -> None:
        for col, value in row.items():
            self.unique_values[col].add(self._normalize(value))

    def _normalize(self, value: Any) -> str:
        if value is None:
            return "nan"
        try:
            if pd.isna(value):
                return "nan"
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return "nan" if text == "" else text

    def summary(self) -> Dict[str, Any]:
        counts = {col: len(vals) for col, vals in self.unique_values.items()}
        return {"column_cardinality": dict(sorted(counts.items()))}


class PipelineLogger:
    def __init__(self, path: str, io_tracker: Optional[IOTracker] = None):
        self.path = path
        self.io_tracker = io_tracker
        self.lines: List[str] = []
        self.step_timings: Dict[str, float] = {}
        self.total_runtime_seconds: Optional[float] = None

    def section(self, title: str) -> None:
        self.lines.append("\n" + "=" * 100)
        self.lines.append(title)
        self.lines.append("=" * 100)

    def kv(self, key: str, value: Any) -> None:
        if isinstance(value, (dict, list, tuple)):
            self.lines.append(f"{key}: {json.dumps(value, indent=2, default=str)}")
        else:
            self.lines.append(f"{key}: {value}")

    def record_step_time(self, step_name: str, seconds: float) -> None:
        self.step_timings[step_name] = float(seconds)
        self.kv(f"{step_name}_runtime_seconds", round(float(seconds), 6))

    def record_total_runtime(self, seconds: float) -> None:
        self.total_runtime_seconds = float(seconds)

    def write(self) -> None:
        if self.step_timings or self.total_runtime_seconds is not None:
            self.lines.append("\n" + "=" * 100)
            self.lines.append("TIMING SUMMARY")
            self.lines.append("=" * 100)
            for step_name, seconds in self.step_timings.items():
                self.lines.append(f"{step_name}: {seconds:.6f} seconds")
            if self.total_runtime_seconds is not None:
                self.lines.append(f"total_pipeline_runtime: {self.total_runtime_seconds:.6f} seconds")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text("\n".join(self.lines), encoding="utf-8")
        if self.io_tracker is not None:
            self.io_tracker.add_write("log_write")


class OnlineVariance:
    def __init__(self, excluded_columns: Sequence[str]):
        self.excluded_columns = set(excluded_columns)
        self.counts = defaultdict(int)
        self.sums = defaultdict(float)
        self.sumsq = defaultdict(float)
        self.mins: Dict[str, float] = {}
        self.maxs: Dict[str, float] = {}

    def update_row(self, row: Dict[str, Any]) -> None:
        for col, value in row.items():
            if col in self.excluded_columns:
                continue
            if value is None:
                continue
            try:
                if pd.isna(value):
                    continue
            except (TypeError, ValueError):
                pass
            try:
                f = float(value)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(f):
                continue
            self.counts[col] += 1
            self.sums[col] += f
            self.sumsq[col] += f * f
            if col not in self.mins or f < self.mins[col]:
                self.mins[col] = f
            if col not in self.maxs or f > self.maxs[col]:
                self.maxs[col] = f

    def normalized_variances(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for col, n in self.counts.items():
            if n <= 0:
                out[col] = 0.0
                continue
            mean = self.sums[col] / n
            var = max(self.sumsq[col] / n - mean * mean, 0.0)
            rng = self.maxs[col] - self.mins[col]
            out[col] = 0.0 if rng <= 0 else var / (rng * rng)
        return out


# -----------------------------------------------------------------------------
# General helpers
# -----------------------------------------------------------------------------


def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def json_dump(path: str | Path, obj: Any, io_tracker: Optional[IOTracker] = None) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    if io_tracker is not None:
        io_tracker.add_write("json_write")


def parse_csv_list(value: Optional[str]) -> List[str]:
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def decode_bytes_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def normalize_zip_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(decode_bytes_value(value)).strip().split(".")[0]
    if text == "" or text.lower() == "nan":
        return ""
    return text.zfill(5)


def normalize_zip_series(series: pd.Series) -> pd.Series:
    return series.map(normalize_zip_value).astype("string")


def standardize_time_value(value: Any) -> str:
    raw = str(decode_bytes_value(value)).replace("T", " ").strip()
    raw = pd.Series([raw]).str.replace(r"([+-]\d{2}:\d{2}|Z)$", "", regex=True).iloc[0].strip()
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def standardize_time_series(series: pd.Series) -> pd.Series:
    return series.map(standardize_time_value).astype("string")


def row_key(row: Dict[str, Any], key_columns: Sequence[str]) -> Tuple[str, ...]:
    return tuple(str(row[col]) for col in key_columns)


def detect_column(columns: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    lowered = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    for c in columns:
        cl = c.lower()
        for cand in candidates:
            if cand.lower() in cl:
                return c
    return None


def is_parquet_input(path: str | Path) -> bool:
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".parquet":
        return True
    if p.is_dir() and any(p.glob("*.parquet")):
        return True
    return False


def parquet_dataset_columns(path: str | Path) -> List[str]:
    dataset = ds.dataset(str(path), format="parquet")
    return list(dataset.schema.names)


def iter_parquet_batches(path: str | Path, batch_rows: int, columns: Optional[List[str]] = None) -> Iterator[pd.DataFrame]:
    dataset = ds.dataset(str(path), format="parquet")
    scanner = dataset.scanner(columns=columns, batch_size=batch_rows)
    for batch in scanner.to_batches():
        yield batch.to_pandas()


def normalize_input_chunk(
    chunk: pd.DataFrame,
    time_column: str,
    zip_column: str,
    key_columns: Sequence[str],
) -> pd.DataFrame:
    chunk = chunk.copy()
    for col in chunk.select_dtypes(include=["object"]).columns:
        chunk[col] = chunk[col].map(decode_bytes_value)
    if zip_column in chunk.columns:
        chunk[zip_column] = normalize_zip_series(chunk[zip_column])
    if time_column in chunk.columns:
        chunk[time_column] = standardize_time_series(chunk[time_column])
    for key in key_columns:
        if key == zip_column and key in chunk.columns:
            chunk[key] = normalize_zip_series(chunk[key])
        elif key == time_column and key in chunk.columns:
            chunk[key] = standardize_time_series(chunk[key])
    return chunk


def prepare_dataframe_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Make mixed object columns safe for PyArrow while preserving useful numeric dtypes."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].map(decode_bytes_value)

    for col in out.columns:
        col_lower = col.lower().strip()
        s = out[col]
        if col_lower in ZIP_COLUMNS:
            out[col] = normalize_zip_series(s)
            continue
        if col_lower == "time":
            out[col] = s.map(lambda x: standardize_time_value(x) if not is_missing_scalar(x) else pd.NA).astype("string")
            continue

        if s.dtype == "object" or str(s.dtype).startswith("string"):
            s2 = s.replace({"nan": pd.NA, "": pd.NA, "None": pd.NA})
            non_null = s2.dropna()
            if not non_null.empty:
                numeric = pd.to_numeric(s2, errors="coerce")
                if int(numeric.notna().sum()) == int(s2.notna().sum()):
                    out[col] = numeric
                else:
                    out[col] = s2.astype("string")
            else:
                out[col] = s2.astype("string")
    return out


def is_missing_scalar(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def value_for_row(value: Any) -> Any:
    value = decode_bytes_value(value)
    if is_missing_scalar(value):
        return "nan"
    if isinstance(value, pd.Timestamp):
        return standardize_time_value(value)
    return value


def row_dicts_from_df(df: pd.DataFrame) -> Iterator[Dict[str, Any]]:
    records = df.to_dict(orient="records")
    for rec in records:
        yield {k: value_for_row(v) for k, v in rec.items()}


# -----------------------------------------------------------------------------
# Parquet sorted run files and external merge stream
# -----------------------------------------------------------------------------


class ParquetRunReader:
    def __init__(self, path: str | Path, io_tracker: Optional[IOTracker] = None):
        self.path = Path(path)
        self.io_tracker = io_tracker
        if self.io_tracker is not None:
            self.io_tracker.add_read("sorted_parquet_run_read")
        df = pd.read_parquet(self.path, engine="pyarrow")
        self.fieldnames = list(df.columns)
        self._rows = iter(row_dicts_from_df(df))

    def pop(self) -> Optional[Dict[str, Any]]:
        try:
            return next(self._rows)
        except StopIteration:
            return None

    def close(self) -> None:
        return None


class SortedRunStream:
    def __init__(self, run_paths: Sequence[str], key_columns: Sequence[str], io_tracker: Optional[IOTracker] = None):
        self.io_tracker = io_tracker
        self.key_columns = list(key_columns)
        self.readers: List[ParquetRunReader] = []
        self.heap: List[Tuple[Tuple[str, ...], int, int, Dict[str, Any]]] = []
        self.unique = count()
        self.fieldnames: List[str] = []

        for reader_idx, path in enumerate(run_paths):
            reader = ParquetRunReader(path, io_tracker=self.io_tracker)
            self.readers.append(reader)
            if not self.fieldnames:
                self.fieldnames = list(reader.fieldnames)
            row = reader.pop()
            if row is not None:
                heapq.heappush(self.heap, (row_key(row, self.key_columns), next(self.unique), reader_idx, row))

    def pop(self) -> Optional[Dict[str, Any]]:
        if not self.heap:
            return None
        _, _, reader_idx, row = heapq.heappop(self.heap)
        next_row = self.readers[reader_idx].pop()
        if next_row is not None:
            heapq.heappush(self.heap, (row_key(next_row, self.key_columns), next(self.unique), reader_idx, next_row))
        return row

    def close(self) -> None:
        for reader in self.readers:
            reader.close()


class ParquetPartWriter:
    def __init__(
        self,
        output_dir: str | Path,
        fieldnames: Sequence[str],
        rows_per_part: int,
        compression: str | None,
        io_tracker: Optional[IOTracker] = None,
        replace: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.fieldnames = list(fieldnames)
        self.rows_per_part = int(rows_per_part)
        self.compression = compression
        self.io_tracker = io_tracker
        self.part_idx = 0
        self.buffer: List[Dict[str, Any]] = []
        self.rows_written = 0
        if replace and self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def append(self, row: Dict[str, Any]) -> None:
        self.buffer.append({col: row.get(col, "nan") for col in self.fieldnames})
        if len(self.buffer) >= self.rows_per_part:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        df = pd.DataFrame(self.buffer, columns=self.fieldnames)
        df = df.astype("string")
        part_path = self.output_dir / f"part-{self.part_idx:05d}.parquet"
        df.to_parquet(part_path, index=False, engine="pyarrow", compression=self.compression)
        if self.io_tracker is not None:
            self.io_tracker.add_write("parquet_part_write")
        self.rows_written += len(self.buffer)
        self.buffer = []
        self.part_idx += 1

    def close(self) -> None:
        self.flush()
        if self.rows_written == 0 and self.part_idx == 0:
            df = prepare_dataframe_for_parquet(pd.DataFrame(columns=self.fieldnames))
            part_path = self.output_dir / "part-00000.parquet"
            df.to_parquet(part_path, index=False, engine="pyarrow", compression=self.compression)
            if self.io_tracker is not None:
                self.io_tracker.add_write("parquet_part_write")
            self.part_idx += 1


def write_dataframe_parquet_dataset(
    df: pd.DataFrame,
    output_dir: str | Path,
    rows_per_part: int,
    compression: str | None,
    io_tracker: Optional[IOTracker] = None,
    replace: bool = True,
) -> int:
    writer = ParquetPartWriter(
        output_dir=output_dir,
        fieldnames=list(df.columns),
        rows_per_part=rows_per_part,
        compression=compression,
        io_tracker=io_tracker,
        replace=replace,
    )
    if df.empty:
        writer.close()
        return 0
    for start in range(0, len(df), rows_per_part):
        chunk = df.iloc[start : start + rows_per_part]
        chunk = prepare_dataframe_for_parquet(chunk)
        part_path = writer.output_dir / f"part-{writer.part_idx:05d}.parquet"
        chunk.to_parquet(part_path, index=False, engine="pyarrow", compression=compression)
        if io_tracker is not None:
            io_tracker.add_write("parquet_part_write")
        writer.part_idx += 1
        writer.rows_written += len(chunk)
    return writer.rows_written


def make_sorted_runs_collect_keys(
    input_parquet: str,
    key_columns: Sequence[str],
    chunk_rows: int,
    temp_dir: str,
    run_prefix: str,
    unique_times: Set[str],
    unique_zips: Set[str],
    time_column: str,
    zip_column: str,
    drop_columns: Sequence[str] = (),
    compression: str | None = "snappy",
    io_tracker: Optional[IOTracker] = None,
) -> Tuple[List[str], List[str]]:
    if not is_parquet_input(input_parquet):
        raise ValueError(f"Expected Parquet file or dataset folder for {run_prefix}: {input_parquet}")

    run_paths: List[str] = []
    output_columns: Optional[List[str]] = None
    all_columns = parquet_dataset_columns(input_parquet)

    forbidden_to_drop = set(key_columns) | {time_column, zip_column}
    bad_drops = [col for col in drop_columns if col in forbidden_to_drop]
    if bad_drops:
        raise ValueError(f"Cannot drop required columns used for sorting/timestep mapping: {bad_drops}")

    missing_global = [col for col in key_columns if col not in all_columns]
    if missing_global:
        raise ValueError(f"Missing sort key columns in input {input_parquet}: {missing_global}")

    for run_idx, chunk in enumerate(iter_parquet_batches(input_parquet, batch_rows=chunk_rows)):
        if io_tracker is not None:
            io_tracker.add_read("parquet_batch_read")

        missing = [col for col in key_columns if col not in chunk.columns]
        if missing:
            raise ValueError(f"Missing sort key columns in input {input_parquet}: {missing}")

        chunk = normalize_input_chunk(chunk, time_column=time_column, zip_column=zip_column, key_columns=key_columns)
        unique_times.update(chunk[time_column].dropna().astype(str).tolist())
        unique_zips.update(chunk[zip_column].dropna().astype(str).tolist())

        cols_to_drop = [col for col in drop_columns if col in chunk.columns]
        if cols_to_drop:
            chunk = chunk.drop(columns=cols_to_drop)

        if output_columns is None:
            output_columns = list(chunk.columns)

        chunk = chunk.sort_values(by=list(key_columns), kind="mergesort").reset_index(drop=True)
        chunk = prepare_dataframe_for_parquet(chunk)

        run_path = os.path.join(temp_dir, f"{run_prefix}_run_{run_idx:06d}.parquet")
        chunk.to_parquet(run_path, index=False, engine="pyarrow", compression=compression)
        if io_tracker is not None:
            io_tracker.add_write("sorted_parquet_run_write")
        run_paths.append(run_path)

    if output_columns is None:
        output_columns = [col for col in all_columns if col not in set(drop_columns)]

    return run_paths, output_columns


# -----------------------------------------------------------------------------
# Static spatial precompute
# -----------------------------------------------------------------------------


def read_parquet_table(path: str | Path, io_tracker: Optional[IOTracker] = None, label: str = "parquet_full_read") -> pd.DataFrame:
    if not is_parquet_input(path):
        raise ValueError(f"Expected Parquet file or dataset folder: {path}")
    df = pd.read_parquet(path, engine="pyarrow")
    if io_tracker is not None:
        io_tracker.add_read(label)
    return df


def build_spatial_zip_lookup(
    tri_facilities_parquet: str,
    tri_chemicals_parquet: str,
    zip_shapefile: str,
    roads_shapefile: str,
    zips_needed: Set[str],
    road_radius_km: float,
    facility_radius_km: float,
    logger: PipelineLogger,
    io_tracker: Optional[IOTracker] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    logger.section("Static spatial precompute")

    zcta = gpd.read_file(zip_shapefile)
    if io_tracker is not None:
        io_tracker.add_read("shapefile_read")
    zcta_zip_col = detect_column(zcta.columns, ["ZCTA5CE20", "ZCTA5CE10", "GEOID20", "GEOID10", "zip", "zcta"])
    if zcta_zip_col is None:
        raise ValueError("Could not detect ZIP/ZCTA column in ZIP shapefile.")

    zcta = zcta.copy()
    zcta["zip_norm"] = zcta[zcta_zip_col].astype(str).str.extract(r"(\d{5})", expand=False)
    zcta = zcta[zcta["zip_norm"].isin(zips_needed)].copy()
    if zcta.empty:
        raise ValueError("None of the requested ZIP codes were found in the ZCTA shapefile.")

    roads = gpd.read_file(roads_shapefile)
    if io_tracker is not None:
        io_tracker.add_read("shapefile_read")

    fac = read_parquet_table(tri_facilities_parquet, io_tracker=io_tracker, label="tri_facilities_parquet_read")
    chem = read_parquet_table(tri_chemicals_parquet, io_tracker=io_tracker, label="tri_chemicals_parquet_read")

    fac_cols = {
        "facility_id": "trifd",
        "lat": "latitude",
        "lon": "longitude",
        "facility_name": "facility",
    }
    chem_cols = {
        "facility_id": "trifd",
        "chemical_name": "chemical",
        "amount": "total_air_emissions_lbs",
    }

    required_fac = [fac_cols["facility_id"], fac_cols["lat"], fac_cols["lon"]]
    missing_fac = [c for c in required_fac if c not in fac.columns]
    if missing_fac:
        raise ValueError(f"TRI facilities dataset is missing required columns: {missing_fac}")

    required_chem = [chem_cols["facility_id"], chem_cols["chemical_name"]]
    missing_chem = [c for c in required_chem if c not in chem.columns]
    if missing_chem:
        raise ValueError(f"TRI chemicals dataset is missing required columns: {missing_chem}")

    facility_amount_col = "total_air_emissions_lbs" if "total_air_emissions_lbs" in fac.columns else None

    fac_keep = [fac_cols["facility_id"], fac_cols["lat"], fac_cols["lon"]]
    if facility_amount_col is not None:
        fac_keep.append(facility_amount_col)
    fac_use = fac[fac_keep].copy().rename(
        columns={
            fac_cols["facility_id"]: "facility_id",
            fac_cols["lat"]: "lat",
            fac_cols["lon"]: "lon",
            **({facility_amount_col: "facility_amount"} if facility_amount_col is not None else {}),
        }
    )
    fac_use["lat"] = pd.to_numeric(fac_use["lat"], errors="coerce")
    fac_use["lon"] = pd.to_numeric(fac_use["lon"], errors="coerce")
    fac_use = fac_use.dropna(subset=["lat", "lon"]).copy()

    chem_keep = [chem_cols["facility_id"], chem_cols["chemical_name"]]
    if chem_cols["amount"] in chem.columns:
        chem_keep.append(chem_cols["amount"])
    chem_use = chem[chem_keep].copy()
    chem_use.columns = ["facility_id", "chemical_name"] + (["amount"] if len(chem_keep) == 3 else [])
    if "amount" not in chem_use.columns:
        chem_use["amount"] = 1.0
    chem_use["amount"] = pd.to_numeric(chem_use["amount"], errors="coerce").fillna(0.0)

    chem_total_amount = chem_use.groupby("facility_id", as_index=False)["amount"].sum()
    chem_unique_count = (
        chem_use.groupby("facility_id", as_index=False)["chemical_name"]
        .nunique()
        .rename(columns={"chemical_name": "chemical_count"})
    )

    fac_enriched = fac_use.merge(chem_total_amount, on="facility_id", how="left")
    fac_enriched = fac_enriched.merge(chem_unique_count, on="facility_id", how="left")
    if "facility_amount" in fac_enriched.columns:
        fac_enriched["amount"] = pd.to_numeric(fac_enriched["facility_amount"], errors="coerce")
        fac_enriched["amount"] = fac_enriched["amount"].fillna(pd.to_numeric(fac_enriched.get("amount", 0.0), errors="coerce")).fillna(0.0)
    else:
        fac_enriched["amount"] = pd.to_numeric(fac_enriched["amount"], errors="coerce").fillna(0.0)
    fac_enriched["chemical_count"] = pd.to_numeric(fac_enriched["chemical_count"], errors="coerce").fillna(0.0)

    fac_gdf = gpd.GeoDataFrame(
        fac_enriched,
        geometry=gpd.points_from_xy(fac_enriched["lon"], fac_enriched["lat"]),
        crs="EPSG:4326",
    )

    target_crs = "EPSG:3857"
    zcta_m = zcta.to_crs(target_crs)
    roads_m = roads.to_crs(target_crs)
    fac_m = fac_gdf.to_crs(target_crs)

    zcta_cent = zcta_m.copy()
    zcta_cent["geometry"] = zcta_cent.geometry.centroid
    zcta_cent = zcta_cent[["zip_norm", "geometry"]].copy()
    zcta_cent["zip_x"] = zcta_cent.geometry.x
    zcta_cent["zip_y"] = zcta_cent.geometry.y

    lookup: Dict[str, Dict[str, Any]] = {
        z: {"facility_pairs": [], "road_pairs": [], "facility_count_nearby": 0, "road_count_nearby": 0}
        for z in zcta_cent["zip_norm"].tolist()
    }

    facility_radius_m = facility_radius_km * 1000.0
    fac_pairs = (
        zcta_cent[["zip_norm", "zip_x", "zip_y", "geometry"]]
        .rename(columns={"geometry": "zip_geom"})
        .merge(
            fac_m[["facility_id", "amount", "chemical_count", "geometry"]].rename(columns={"geometry": "src_geom"}),
            how="cross",
        )
    )
    fac_pairs["dx"] = fac_pairs["zip_x"] - fac_pairs["src_geom"].x
    fac_pairs["dy"] = fac_pairs["zip_y"] - fac_pairs["src_geom"].y
    fac_pairs["dist_m"] = np.sqrt(fac_pairs["dx"] ** 2 + fac_pairs["dy"] ** 2)
    fac_pairs = fac_pairs[(fac_pairs["dist_m"] > 0) & (fac_pairs["dist_m"] <= facility_radius_m)].copy()

    if not fac_pairs.empty:
        fac_pairs["dir_x"] = fac_pairs["dx"] / fac_pairs["dist_m"]
        fac_pairs["dir_y"] = fac_pairs["dy"] / fac_pairs["dist_m"]
        amount_scale = fac_pairs["amount"].quantile(0.95)
        if not np.isfinite(amount_scale) or amount_scale <= 0:
            amount_scale = max(float(fac_pairs["amount"].max()), 1.0)
        chem_scale = max(float(fac_pairs["chemical_count"].max()), 1.0)
        fac_pairs["amount_norm"] = np.clip(fac_pairs["amount"] / amount_scale, 0, 1)
        fac_pairs["chem_norm"] = np.clip(fac_pairs["chemical_count"] / chem_scale, 0, 1)
        fac_pairs["severity"] = 0.7 * fac_pairs["amount_norm"] + 0.3 * fac_pairs["chem_norm"]
        fac_pairs["decay"] = np.exp(-fac_pairs["dist_m"] / facility_radius_m)
        for row in fac_pairs.itertuples(index=False):
            lookup[row.zip_norm]["facility_pairs"].append(
                {
                    "facility_id": row.facility_id,
                    "dir_x": float(row.dir_x),
                    "dir_y": float(row.dir_y),
                    "decay": float(row.decay),
                    "severity": float(row.severity),
                }
            )
        facility_counts = fac_pairs.groupby("zip_norm")["facility_id"].nunique().to_dict()
        for z, cnt in facility_counts.items():
            lookup[z]["facility_count_nearby"] = int(cnt)

    road_radius_m = road_radius_km * 1000.0
    roads_m = roads_m.copy().explode(index_parts=False).reset_index(drop=True)
    roads_m = roads_m[roads_m.geometry.notna()].copy()
    roads_m = roads_m[~roads_m.geometry.is_empty].copy()
    roads_m["road_id"] = np.arange(len(roads_m), dtype=int)

    for zip_row in zcta_cent.itertuples(index=False):
        zip_norm = zip_row.zip_norm
        zip_geom = zip_row.geometry
        zip_x = zip_row.zip_x
        zip_y = zip_row.zip_y
        dists = roads_m.geometry.distance(zip_geom)
        nearby_mask = dists <= road_radius_m
        if not nearby_mask.any():
            continue
        nearby = roads_m.loc[nearby_mask, ["road_id", "geometry"]].copy()
        nearby["dist_m"] = dists.loc[nearby_mask].values
        nearest_pts = nearby.geometry.apply(lambda g: nearest_points(g, zip_geom)[0])
        nearby["src_x"] = nearest_pts.x.values
        nearby["src_y"] = nearest_pts.y.values
        road_ids_seen: Set[int] = set()
        for row in nearby.itertuples(index=False):
            dist_m = float(row.dist_m)
            if dist_m <= 0:
                continue
            dx = zip_x - float(row.src_x)
            dy = zip_y - float(row.src_y)
            dir_x = dx / dist_m
            dir_y = dy / dist_m
            decay = math.exp(-dist_m / road_radius_m)
            lookup[zip_norm]["road_pairs"].append(
                {
                    "road_id": int(row.road_id),
                    "dir_x": float(dir_x),
                    "dir_y": float(dir_y),
                    "decay": float(decay),
                }
            )
            road_ids_seen.add(int(row.road_id))
        lookup[zip_norm]["road_count_nearby"] = len(road_ids_seen)

    meta = {
        "zip_codes_scored": len(lookup),
        "roads_feature_count": int(len(roads_m)),
        "facility_lookup_zips": sum(1 for v in lookup.values() if v["facility_pairs"]),
        "road_lookup_zips": sum(1 for v in lookup.values() if v["road_pairs"]),
        "parameters": {
            "road_radius_km": road_radius_km,
            "facility_radius_km": facility_radius_km,
        },
        "detected_columns": {
            "tri_facilities": fac_cols,
            "tri_chemicals": chem_cols,
            "zcta_zip_column": zcta_zip_col,
            "facility_amount_column": facility_amount_col,
        },
    }
    logger.kv("spatial_lookup_meta", meta)
    return lookup, meta


# -----------------------------------------------------------------------------
# Row-wise feature engineering
# -----------------------------------------------------------------------------


class LagState:
    def __init__(self, feature_cols: Sequence[str], num_past_feats: int):
        self.feature_cols = list(feature_cols)
        self.num_past_feats = int(num_past_feats)
        self.buffers: Dict[str, Dict[str, Deque[Any]]] = defaultdict(
            lambda: {feat: deque(maxlen=self.num_past_feats) for feat in self.feature_cols}
        )

    def apply(self, row: Dict[str, Any], zip_col: str) -> None:
        if self.num_past_feats <= 0 or not self.feature_cols:
            return
        zip_code = str(row.get(zip_col, ""))
        state = self.buffers[zip_code]
        for feat in self.feature_cols:
            dq = state[feat]
            values = list(dq)
            for lag in range(1, self.num_past_feats + 1):
                idx = len(values) - lag
                row[f"{feat}_past_{lag}"] = values[idx] if idx >= 0 else "nan"
        for feat in self.feature_cols:
            state[feat].append(row.get(feat, "nan"))


def resolve_blend_weight(mode: str, blend_100m: float) -> float:
    mode = str(mode).lower()
    if mode == "10m":
        return 0.0
    if mode == "100m":
        return 1.0
    if mode == "blend":
        return float(blend_100m)
    raise ValueError(f"Unsupported wind mode: {mode}")


def safe_float(value: Any) -> float:
    if is_missing_scalar(value) or str(value).strip() == "":
        return float("nan")
    return float(value)


def compute_wind_vector(row: Dict[str, Any], mode: str, blend_100m: float) -> Tuple[float, float]:
    w100 = resolve_blend_weight(mode, blend_100m)
    w10 = 1.0 - w100
    speed10 = safe_float(row.get("wind_speed_10m"))
    speed100 = safe_float(row.get("wind_speed_100m"))
    cos10 = safe_float(row.get("wind_direction_10m_cos"))
    sin10 = safe_float(row.get("wind_direction_10m_sin"))
    cos100 = safe_float(row.get("wind_direction_100m_cos"))
    sin100 = safe_float(row.get("wind_direction_100m_sin"))
    wind_x = w10 * speed10 * cos10 + w100 * speed100 * cos100
    wind_y = w10 * speed10 * sin10 + w100 * speed100 * sin100
    return wind_x, wind_y


def add_direction_features_to_row(row: Dict[str, Any], direction_columns: Sequence[str], drop_original: bool) -> None:
    for c in direction_columns:
        if c not in row:
            continue
        raw_value = row.get(c)
        if is_missing_scalar(raw_value) or str(raw_value).strip() == "":
            row[f"{c}_sin"] = "nan"
            row[f"{c}_cos"] = "nan"
            if drop_original:
                row.pop(c, None)
            continue
        value = float(raw_value)
        radians = math.radians(value)
        row[f"{c}_sin"] = math.sin(radians)
        row[f"{c}_cos"] = math.cos(radians)
        if drop_original:
            row.pop(c, None)


def add_time_features_to_row(row: Dict[str, Any], time_col: str) -> None:
    dt = pd.to_datetime(row.get(time_col, ""), errors="coerce")
    if pd.isna(dt):
        additions = {
            "month": "nan",
            "month_sin": "nan",
            "month_cos": "nan",
            "day": "nan",
            "hour": "nan",
            "hour_sin": "nan",
            "hour_cos": "nan",
            "day_of_week": "nan",
            "day_of_week_sin": "nan",
            "day_of_week_cos": "nan",
            "day_of_year": "nan",
            "is_weekend": "nan",
        }
    else:
        additions = {
            "month": int(dt.month),
            "month_sin": math.sin(2 * math.pi * dt.month / 12),
            "month_cos": math.cos(2 * math.pi * dt.month / 12),
            "day": int(dt.day),
            "hour": int(dt.hour),
            "hour_sin": math.sin(2 * math.pi * dt.hour / 24),
            "hour_cos": math.cos(2 * math.pi * dt.hour / 24),
            "day_of_week": int(dt.dayofweek),
            "day_of_week_sin": math.sin(2 * math.pi * dt.dayofweek / 7),
            "day_of_week_cos": math.cos(2 * math.pi * dt.dayofweek / 7),
            "day_of_year": int(dt.dayofyear),
            "is_weekend": int(dt.dayofweek in [5, 6]),
        }
    row.update(additions)


def add_spatial_scores_to_row(
    row: Dict[str, Any],
    spatial_lookup: Dict[str, Dict[str, Any]],
    zip_col: str,
    facility_wind_mode: str,
    facility_wind_blend_100m: float,
    road_wind_mode: str,
    road_wind_blend_100m: float,
) -> None:
    zip_code = str(row.get(zip_col, ""))
    entry = spatial_lookup.get(zip_code)
    if entry is None:
        row["road_impact_score"] = 0.0
        row["facility_impact_score"] = 0.0
        return

    road_wx, road_wy = compute_wind_vector(row, mode=road_wind_mode, blend_100m=road_wind_blend_100m)
    facility_wx, facility_wy = compute_wind_vector(row, mode=facility_wind_mode, blend_100m=facility_wind_blend_100m)

    road_score = 0.0
    for pair in entry["road_pairs"]:
        projection = road_wx * pair["dir_x"] + road_wy * pair["dir_y"]
        downwind = max(projection, 0.0)
        road_score += pair["decay"] * downwind

    facility_score = 0.0
    for pair in entry["facility_pairs"]:
        projection = facility_wx * pair["dir_x"] + facility_wy * pair["dir_y"]
        downwind = max(projection, 0.0)
        facility_score += pair["severity"] * pair["decay"] * downwind

    row["road_impact_score"] = float(road_score)
    row["facility_impact_score"] = float(facility_score)


# -----------------------------------------------------------------------------
# Merge and transform
# -----------------------------------------------------------------------------


def merge_rows_full_outer(
    key: Tuple[str, ...],
    left_row: Optional[Dict[str, Any]],
    right_row: Optional[Dict[str, Any]],
    key_columns: Sequence[str],
    left_columns: Sequence[str],
    right_columns: Sequence[str],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {key_columns[i]: key[i] for i in range(len(key_columns))}
    key_set = set(key_columns)
    for col in left_columns:
        if col in key_set:
            continue
        out[col] = left_row[col] if left_row is not None else "nan"
    for col in right_columns:
        if col in key_set:
            continue
        out[col] = right_row[col] if right_row is not None else "nan"
    return out


def detect_direction_columns(columns: Sequence[str], explicit: Sequence[str], auto_detect: bool = True) -> List[str]:
    detected: List[str] = []
    seen: Set[str] = set()
    if auto_detect:
        for c in columns:
            cl = c.lower()
            if "direction" in cl or "wind_dir" in cl or "winddirection" in cl:
                if c not in seen:
                    detected.append(c)
                    seen.add(c)
    for c in explicit:
        if c in columns and c not in seen:
            detected.append(c)
            seen.add(c)
    return detected


def build_output_fieldnames(
    left_columns: Sequence[str],
    right_columns: Sequence[str],
    key_columns: Sequence[str],
    direction_columns: Sequence[str],
    drop_original_direction_columns: bool,
    lag_feature_cols: Sequence[str],
    num_past_feats: int,
) -> List[str]:
    key_set = set(key_columns)
    base: List[str] = list(key_columns)
    left_nonkeys = [c for c in left_columns if c not in key_set]
    right_nonkeys = [c for c in right_columns if c not in key_set and c not in left_nonkeys]
    base.extend(left_nonkeys)
    base.extend(right_nonkeys)
    if drop_original_direction_columns:
        base = [c for c in base if c not in set(direction_columns)]
    for c in direction_columns:
        if f"{c}_sin" not in base:
            base.append(f"{c}_sin")
        if f"{c}_cos" not in base:
            base.append(f"{c}_cos")
    for c in [
        "month", "month_sin", "month_cos", "day", "hour", "hour_sin", "hour_cos",
        "day_of_week", "day_of_week_sin", "day_of_week_cos", "day_of_year", "is_weekend",
        "road_impact_score", "facility_impact_score",
    ]:
        if c not in base:
            base.append(c)
    for feat in lag_feature_cols:
        for lag in range(1, num_past_feats + 1):
            col = f"{feat}_past_{lag}"
            if col not in base:
                base.append(col)
    return base


def stream_merge_join_and_transform(
    left_stream: SortedRunStream,
    right_stream: SortedRunStream,
    output_parquet_dir: str,
    output_rows_per_part: int,
    compression: str | None,
    key_columns: Sequence[str],
    time_column: str,
    zip_column: str,
    left_columns: Sequence[str],
    right_columns: Sequence[str],
    merge_how: str,
    spatial_lookup: Dict[str, Dict[str, Any]],
    direction_columns: Sequence[str],
    drop_original_direction_columns: bool,
    lag_feature_cols: Sequence[str],
    num_past_feats: int,
    facility_wind_mode: str,
    facility_wind_blend_100m: float,
    road_wind_mode: str,
    road_wind_blend_100m: float,
    variance_stats: Optional[OnlineVariance],
    cardinality_tracker: Optional[CardinalityTracker],
    io_tracker: Optional[IOTracker] = None,
) -> Dict[str, Any]:
    fieldnames = build_output_fieldnames(
        left_columns=left_columns,
        right_columns=right_columns,
        key_columns=key_columns,
        direction_columns=direction_columns,
        drop_original_direction_columns=drop_original_direction_columns,
        lag_feature_cols=lag_feature_cols,
        num_past_feats=num_past_feats,
    )

    writer = ParquetPartWriter(
        output_dir=output_parquet_dir,
        fieldnames=fieldnames,
        rows_per_part=output_rows_per_part,
        compression=compression,
        io_tracker=io_tracker,
        replace=True,
    )

    lag_state = LagState(feature_cols=lag_feature_cols, num_past_feats=num_past_feats)
    rows_written = 0
    left_only = 0
    right_only = 0
    matched = 0

    def finalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        row[time_column] = standardize_time_value(row.get(time_column, ""))
        row[zip_column] = normalize_zip_value(row.get(zip_column, ""))
        add_direction_features_to_row(row, direction_columns, drop_original=drop_original_direction_columns)
        add_time_features_to_row(row, time_col=time_column)
        add_spatial_scores_to_row(
            row,
            spatial_lookup=spatial_lookup,
            zip_col=zip_column,
            facility_wind_mode=facility_wind_mode,
            facility_wind_blend_100m=facility_wind_blend_100m,
            road_wind_mode=road_wind_mode,
            road_wind_blend_100m=road_wind_blend_100m,
        )
        lag_state.apply(row, zip_col=zip_column)
        out = {col: row.get(col, "nan") for col in fieldnames}
        if variance_stats is not None:
            variance_stats.update_row(out)
        if cardinality_tracker is not None:
            cardinality_tracker.update_row(out)
        return out

    left_row = left_stream.pop()
    right_row = right_stream.pop()

    while left_row is not None or right_row is not None:
        if left_row is None:
            if merge_how == "outer":
                rkey = row_key(right_row, key_columns)
                writer.append(finalize_row(merge_rows_full_outer(rkey, None, right_row, key_columns, left_columns, right_columns)))
                rows_written += 1
                right_only += 1
            right_row = right_stream.pop()
            continue

        if right_row is None:
            if merge_how == "outer":
                lkey = row_key(left_row, key_columns)
                writer.append(finalize_row(merge_rows_full_outer(lkey, left_row, None, key_columns, left_columns, right_columns)))
                rows_written += 1
                left_only += 1
            left_row = left_stream.pop()
            continue

        lkey = row_key(left_row, key_columns)
        rkey = row_key(right_row, key_columns)

        if lkey == rkey:
            writer.append(finalize_row(merge_rows_full_outer(lkey, left_row, right_row, key_columns, left_columns, right_columns)))
            rows_written += 1
            matched += 1
            left_row = left_stream.pop()
            right_row = right_stream.pop()
        elif lkey < rkey:
            if merge_how == "outer":
                writer.append(finalize_row(merge_rows_full_outer(lkey, left_row, None, key_columns, left_columns, right_columns)))
                rows_written += 1
                left_only += 1
            left_row = left_stream.pop()
        else:
            if merge_how == "outer":
                writer.append(finalize_row(merge_rows_full_outer(rkey, None, right_row, key_columns, left_columns, right_columns)))
                rows_written += 1
                right_only += 1
            right_row = right_stream.pop()

    writer.close()

    return {
        "output_parquet": output_parquet_dir,
        "rows_written": rows_written,
        "matched_rows": matched,
        "left_only_rows": left_only,
        "right_only_rows": right_only,
        "output_columns": fieldnames,
        "parquet_parts": writer.part_idx,
    }


# -----------------------------------------------------------------------------
# Filters
# -----------------------------------------------------------------------------


def read_parquet_dataset_all(path: str | Path, io_tracker: Optional[IOTracker] = None, label: str = "parquet_dataset_read") -> pd.DataFrame:
    df = pd.read_parquet(path, engine="pyarrow")
    if io_tracker is not None:
        io_tracker.add_read(label)
    return df


def apply_variance_filter_parquet(
    input_parquet: str,
    output_parquet: str,
    report_json: str,
    variance_stats: OnlineVariance,
    variance_threshold: float,
    exclude_cols: Sequence[str],
    rows_per_part: int,
    compression: str | None,
    io_tracker: Optional[IOTracker] = None,
) -> Dict[str, Any]:
    variances = variance_stats.normalized_variances()
    removed = sorted([c for c, v in variances.items() if v < variance_threshold])
    exclude_set = set(exclude_cols)

    df = read_parquet_dataset_all(input_parquet, io_tracker=io_tracker, label="variance_input_parquet_read")
    kept_cols = [c for c in df.columns if c in exclude_set or c not in removed]
    write_dataframe_parquet_dataset(
        df[kept_cols],
        output_dir=output_parquet,
        rows_per_part=rows_per_part,
        compression=compression,
        io_tracker=io_tracker,
        replace=True,
    )

    report = {
        "excluded_from_variance": list(exclude_cols),
        "variance_threshold": float(variance_threshold),
        "variances": variances,
        "removed_columns": removed,
        "kept_columns": kept_cols,
        "input_parquet": input_parquet,
        "output_parquet": output_parquet,
    }
    json_dump(report_json, report, io_tracker=io_tracker)
    return report


def apply_low_cardinality_filter_parquet(
    input_parquet: str,
    output_parquet: str,
    report_json: str,
    cardinality_summary: Dict[str, Any],
    cardinality_threshold: int,
    exclude_cols: Sequence[str],
    rows_per_part: int,
    compression: str | None,
    io_tracker: Optional[IOTracker] = None,
) -> Dict[str, Any]:
    exclude_set = set(exclude_cols)
    counts = cardinality_summary.get("column_cardinality", {})
    removed = sorted([c for c, v in counts.items() if c not in exclude_set and int(v) < int(cardinality_threshold)])

    df = read_parquet_dataset_all(input_parquet, io_tracker=io_tracker, label="cardinality_input_parquet_read")
    kept_cols = [c for c in df.columns if c in exclude_set or c not in removed]
    write_dataframe_parquet_dataset(
        df[kept_cols],
        output_dir=output_parquet,
        rows_per_part=rows_per_part,
        compression=compression,
        io_tracker=io_tracker,
        replace=True,
    )

    report = {
        "excluded_from_cardinality": list(exclude_cols),
        "cardinality_threshold": int(cardinality_threshold),
        "column_cardinality": counts,
        "removed_columns": removed,
        "kept_columns": kept_cols,
        "input_parquet": input_parquet,
        "output_parquet": output_parquet,
    }
    json_dump(report_json, report, io_tracker=io_tracker)
    return report


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Native-Parquet AQ/weather preprocessing pipeline with external sort, streaming merge, "
            "static spatial precompute, inline direction/time/lag features, and optional filters."
        )
    )
    ap.add_argument("--air-quality", required=True, help="Air-quality Parquet file or dataset folder")
    ap.add_argument("--weather", required=True, help="Weather Parquet file or dataset folder")
    ap.add_argument("--tri-facilities", required=True, help="TRI facilities Parquet file or dataset folder")
    ap.add_argument("--tri-chemicals", required=True, help="TRI chemicals Parquet file or dataset folder")
    ap.add_argument("--zip-shapefile", required=True)
    ap.add_argument("--roads-shapefile", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--key-columns", nargs="+", default=None, help="Sort/join columns. Default: zip time")
    ap.add_argument("--chunk-rows", type=int, default=25000, help="Rows per input scan batch / sorted run")
    ap.add_argument("--output-rows-per-part", type=int, default=100000, help="Rows per output Parquet part file")
    ap.add_argument("--temp-dir", default=None)
    ap.add_argument("--keep-temp-files", action="store_true")
    ap.add_argument("--left-drop-columns", nargs="*", default=[])
    ap.add_argument("--right-drop-columns", nargs="*", default=[])
    ap.add_argument("--road-radius-km", type=float, default=2.0)
    ap.add_argument("--facility-radius-km", type=float, default=10.0)
    ap.add_argument("--facility-wind-mode", choices=["10m", "100m", "blend"], default="blend")
    ap.add_argument("--facility-wind-blend-100m", type=float, default=0.7)
    ap.add_argument("--road-wind-mode", choices=["10m", "100m", "blend"], default="10m")
    ap.add_argument("--road-wind-blend-100m", type=float, default=0.0)
    ap.add_argument("--direction-columns", default="")
    ap.add_argument("--no-auto-detect-direction-columns", action="store_true")
    ap.add_argument("--keep-original-direction-columns", action="store_true")
    ap.add_argument("--feats-for-past", nargs="*", default=[])
    ap.add_argument("--num-past-feats", type=int, default=0)
    ap.add_argument("--exclude-variance", default="time,zip,road_impact_score,facility_impact_score")
    ap.add_argument("--variance-threshold", type=float, default=None)
    ap.add_argument(
        "--exclude-cardinality",
        default=(
            "time,zip,road_impact_score,facility_impact_score,"
            "day,day_of_week,day_of_week_cos,day_of_week_sin,day_of_year,"
            "hour,hour_sin,hour_cos,is_weekend,latitude,longitude,month,"
            "month_cos,month_sin,ozone,us_aqi"
        ),
    )
    ap.add_argument("--cardinality-threshold", type=int, default=None)
    ap.add_argument(
        "--compression",
        default="snappy",
        choices=["snappy", "gzip", "brotli", "zstd", "none"],
        help="Parquet compression codec",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    compression = None if args.compression == "none" else args.compression
    key_columns = args.key_columns or ["zip", "time"]

    for label, path in [
        ("air-quality", args.air_quality),
        ("weather", args.weather),
        ("tri-facilities", args.tri_facilities),
        ("tri-chemicals", args.tri_chemicals),
    ]:
        if not is_parquet_input(path):
            raise ValueError(f"Expected Parquet file or dataset folder for --{label}: {path}")

    ensure_dir(args.output_dir)
    ensure_dir(Path(args.output_dir) / "metadata")
    ensure_dir(Path(args.output_dir) / "logs")
    ensure_dir(Path(args.output_dir) / "intermediate")

    io_tracker = IOTracker()
    logger = PipelineLogger(str(Path(args.output_dir) / "logs" / "pipeline_steps.log"), io_tracker=io_tracker)
    pipeline_start = time.perf_counter()

    temp_dir = args.temp_dir or str(Path(args.output_dir) / "intermediate" / "sort_runs")
    ensure_dir(temp_dir)

    paths = {
        "pre_filter_parquet": str(Path(args.output_dir) / "intermediate" / "pre_filter_all_features"),
        "pre_variance_parquet": str(Path(args.output_dir) / "intermediate" / "pre_variance_all_features"),
        "final_parquet": str(Path(args.output_dir) / "all_features"),
        "spatial_meta_json": str(Path(args.output_dir) / "metadata" / "spatial_lookup.json"),
        "variance_report_json": str(Path(args.output_dir) / "metadata" / "variance_report.json"),
        "cardinality_report_json": str(Path(args.output_dir) / "metadata" / "cardinality_report.json"),
        "summary_json": str(Path(args.output_dir) / "metadata" / "pipeline_summary.json"),
    }

    left_run_paths: List[str] = []
    right_run_paths: List[str] = []

    try:
        logger.section("Pass 1 - External sort Parquet runs + global key collection")
        t0 = time.perf_counter()
        unique_times: Set[str] = set()
        unique_zips: Set[str] = set()

        left_run_paths, left_columns = make_sorted_runs_collect_keys(
            input_parquet=args.air_quality,
            key_columns=key_columns,
            chunk_rows=args.chunk_rows,
            temp_dir=temp_dir,
            run_prefix="air_quality",
            unique_times=unique_times,
            unique_zips=unique_zips,
            time_column="time",
            zip_column="zip",
            drop_columns=args.left_drop_columns,
            compression=compression,
            io_tracker=io_tracker,
        )
        right_run_paths, right_columns = make_sorted_runs_collect_keys(
            input_parquet=args.weather,
            key_columns=key_columns,
            chunk_rows=args.chunk_rows,
            temp_dir=temp_dir,
            run_prefix="weather",
            unique_times=unique_times,
            unique_zips=unique_zips,
            time_column="time",
            zip_column="zip",
            drop_columns=args.right_drop_columns,
            compression=compression,
            io_tracker=io_tracker,
        )
        logger.kv("left_run_count", len(left_run_paths))
        logger.kv("right_run_count", len(right_run_paths))
        logger.kv("unique_times", len(unique_times))
        logger.kv("unique_zips", len(unique_zips))
        logger.record_step_time("pass1_external_sort", time.perf_counter() - t0)

        t0 = time.perf_counter()
        spatial_lookup, spatial_meta = build_spatial_zip_lookup(
            tri_facilities_parquet=args.tri_facilities,
            tri_chemicals_parquet=args.tri_chemicals,
            zip_shapefile=args.zip_shapefile,
            roads_shapefile=args.roads_shapefile,
            zips_needed=unique_zips,
            road_radius_km=args.road_radius_km,
            facility_radius_km=args.facility_radius_km,
            logger=logger,
            io_tracker=io_tracker,
        )
        json_dump(paths["spatial_meta_json"], spatial_meta, io_tracker=io_tracker)
        logger.record_step_time("static_spatial_precompute", time.perf_counter() - t0)

        t0 = time.perf_counter()
        left_stream = SortedRunStream(left_run_paths, key_columns, io_tracker=io_tracker)
        right_stream = SortedRunStream(right_run_paths, key_columns, io_tracker=io_tracker)
        variance_stats = OnlineVariance(excluded_columns=parse_csv_list(args.exclude_variance)) if args.variance_threshold is not None else None
        cardinality_tracker = CardinalityTracker()
        merged_columns = list(dict.fromkeys(list(left_columns) + list(right_columns)))
        direction_columns = detect_direction_columns(
            columns=merged_columns,
            explicit=parse_csv_list(args.direction_columns),
            auto_detect=not args.no_auto_detect_direction_columns,
        )
        merge_output_target = paths["pre_filter_parquet"] if (args.variance_threshold is not None or args.cardinality_threshold is not None) else paths["final_parquet"]
        try:
            merge_meta = stream_merge_join_and_transform(
                left_stream=left_stream,
                right_stream=right_stream,
                output_parquet_dir=merge_output_target,
                output_rows_per_part=args.output_rows_per_part,
                compression=compression,
                key_columns=key_columns,
                time_column="time",
                zip_column="zip",
                left_columns=left_columns,
                right_columns=right_columns,
                merge_how="outer",
                spatial_lookup=spatial_lookup,
                direction_columns=direction_columns,
                drop_original_direction_columns=not args.keep_original_direction_columns,
                lag_feature_cols=args.feats_for_past,
                num_past_feats=args.num_past_feats,
                facility_wind_mode=args.facility_wind_mode,
                facility_wind_blend_100m=args.facility_wind_blend_100m,
                road_wind_mode=args.road_wind_mode,
                road_wind_blend_100m=args.road_wind_blend_100m,
                variance_stats=variance_stats,
                cardinality_tracker=cardinality_tracker,
                io_tracker=io_tracker,
            )
        finally:
            left_stream.close()
            right_stream.close()
        logger.kv("stream_merge_meta", merge_meta)
        logger.record_step_time("pass2_stream_merge_transform", time.perf_counter() - t0)

        cardinality_summary = cardinality_tracker.summary()
        current_filter_input = merge_output_target

        if args.cardinality_threshold is not None:
            t0 = time.perf_counter()
            cardinality_output = paths["pre_variance_parquet"] if args.variance_threshold is not None else paths["final_parquet"]
            cardinality_meta = apply_low_cardinality_filter_parquet(
                input_parquet=current_filter_input,
                output_parquet=cardinality_output,
                report_json=paths["cardinality_report_json"],
                cardinality_summary=cardinality_summary,
                cardinality_threshold=args.cardinality_threshold,
                exclude_cols=parse_csv_list(args.exclude_cardinality),
                rows_per_part=args.output_rows_per_part,
                compression=compression,
                io_tracker=io_tracker,
            )
            current_filter_input = cardinality_output
            logger.kv("cardinality_filter_meta", cardinality_meta)
            logger.record_step_time("cardinality_filter", time.perf_counter() - t0)
        else:
            cardinality_meta = {"skipped": True, "reason": "cardinality_threshold not provided"}
            logger.record_step_time("cardinality_filter", 0.0)

        if args.variance_threshold is not None:
            t0 = time.perf_counter()
            if variance_stats is None:
                raise RuntimeError("variance_stats was not initialized despite variance_threshold being set")
            variance_meta = apply_variance_filter_parquet(
                input_parquet=current_filter_input,
                output_parquet=paths["final_parquet"],
                report_json=paths["variance_report_json"],
                variance_stats=variance_stats,
                variance_threshold=args.variance_threshold,
                exclude_cols=parse_csv_list(args.exclude_variance),
                rows_per_part=args.output_rows_per_part,
                compression=compression,
                io_tracker=io_tracker,
            )
            logger.kv("variance_meta", variance_meta)
            logger.record_step_time("variance_filter", time.perf_counter() - t0)
        else:
            variance_meta = {"skipped": True, "reason": "variance_threshold not provided"}
            logger.record_step_time("variance_filter", 0.0)

    finally:
        if not args.keep_temp_files:
            for path in left_run_paths + right_run_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if args.temp_dir is None:
                try:
                    os.rmdir(temp_dir)
                except OSError:
                    pass

    logger.kv("io_summary", io_tracker.summary())
    logger.kv("cardinality_summary", cardinality_tracker.summary())
    total_runtime = time.perf_counter() - pipeline_start
    logger.record_total_runtime(total_runtime)
    logger.write()

    summary = {
        "inputs": {
            "air_quality": args.air_quality,
            "weather": args.weather,
            "tri_facilities": args.tri_facilities,
            "tri_chemicals": args.tri_chemicals,
            "zip_shapefile": args.zip_shapefile,
            "roads_shapefile": args.roads_shapefile,
        },
        "parameters": {
            "time_col": "time",
            "zip_col": "zip",
            "key_columns": key_columns,
            "merge_how": "outer",
            "chunk_rows": args.chunk_rows,
            "output_rows_per_part": args.output_rows_per_part,
            "road_radius_km": args.road_radius_km,
            "facility_radius_km": args.facility_radius_km,
            "facility_wind_mode": args.facility_wind_mode,
            "facility_wind_blend_100m": args.facility_wind_blend_100m,
            "road_wind_mode": args.road_wind_mode,
            "road_wind_blend_100m": args.road_wind_blend_100m,
            "feats_for_past": args.feats_for_past,
            "num_past_feats": args.num_past_feats,
            "variance_threshold": args.variance_threshold,
            "exclude_cardinality": parse_csv_list(args.exclude_cardinality),
            "cardinality_threshold": args.cardinality_threshold,
            "compression": args.compression,
        },
        "timing": {
            "step_runtime_seconds": logger.step_timings,
            "total_pipeline_runtime_seconds": logger.total_runtime_seconds,
        },
        "io_summary": io_tracker.summary(),
        "cardinality_summary": cardinality_tracker.summary(),
        "outputs": paths,
    }
    json_dump(paths["summary_json"], summary, io_tracker=io_tracker)

    print("\nPipeline complete.")
    print(f"Final Parquet dataset: {paths['final_parquet']}")
    print(f"Summary JSON: {paths['summary_json']}")
    print(f"Step log: {logger.path}")
    print(f"Total runtime: {total_runtime:.6f} seconds")


if __name__ == "__main__":
    main()
