#!/usr/bin/env python3
"""
This file uses data which is available on the github through Github Large File Storage.
To access this data, either download it though the remote repository (data is stored in both 'data' and 'static data' folders), or you can also use the command line:
First install git-lfs using the command "sudo apt install git-lfs", then run the command "git lfs install". Now clone the repo, and cd into the repo, then run
"git lfs pull" to install on files from the repo which are stored in using Github Large File Storage to your local machine.
The data we used for this project was all of the data inside the 'data' folder, along with tri_chemicals_houston.csv and tri_facilities_houston.csv.
No data preprocessing was done to tri_chemicals_houston.csv and tri_facilities_houston.csv for use in this script.
To begin preprocessing the streaming data, we first merge all air-quality and weather data into one "master" data file, which is simply an appending of all the air-quality
or weather files together, making it easier to use. This is done through merge_data_into_master_file.py
Once this is done, run both of these "master" files through strip_tz_info.py, note that the input and output csv needs to be different for this script to work correctly.
This removes all time zone information from the "time" column (since we are only looking at Houston, time zone information is redundant).
We recommend using remove_column.py to remove City and State columns from this data as well, since it is also redundant. Alternatively, you can set:
"--left-drop-columns city state --right-drop-columns city state" to drop these columns during this scripts runtime. 
In addition this file uses data from the US Census, the downloading and preprocessing information is below:
The US Census TIGER/Line shapefiles for zip code polygons, can be found here:
https://www.census.gov/cgi-bin/geo/shapefiles/index.php?year=2020&layergroup=ZIP%20Code%20Tabulation%20Areas
To retreive this data, simply click the link above and download the ZIP Code Tabulation Areas (2020) national file.
Once this data is collected, run:
filter_houston_zcta.py --csv **air-quality OR weather csv** --shp **PATH TO THE SHAPEFILE YOU JUST DOWNLOADED** --output **NEW SHAPEFILE NAME**
This will create a new .shp file which contains only information required to make zip polygons for the zip codes which are included in the data gathered from 
data-collection. 
In addition, we use US Census TIGER/Line shapefiles for roads for Texas, which can be found here:
https://www.census.gov/cgi-bin/geo/shapefiles/index.php?year=2025&layergroup=Roads
To retrieve this data, click the above link, then select the state of Texas in the "Select a State:" down under "Primary and Secondary Roads", then press download.
For simplicity this file is not reduced in size, since depending on the road-radius-km command line argument, roads outside of houston may affect some zip codes. 
The files resulting from these links and preprocessing is also available on the github: https://github.com/GlowSand/AQ_ML_Pipeline
No further data preprocessing is needed. A sample run for this script looks like:
py preprocessing.py --air-quality ../data/air-quality-master-VALIDATION-tz-stripped.csv --weather ../data/weather-master-VALIDATION-tz-stripped.csv \
--tri-facilities ../data/important-locations/tri_facilities_houston.csv --tri-chemicals ../data/important-locations/tri_chemicals_houston.csv \
--zip-shapefile houston_zcta_filtered.shp --roads-shapefile ../data/texas-shape-data/tl_2025_48_prisecroads.shp --output-dir ../data/pipeline-output \
--chunk-rows 10000 --temp-dir ../data/pipeline-output/temp-files/ --left-drop-columns city state --right-drop-columns city state --feats-for-past us_aqi \
--num-past-feats 8"""
import argparse
import csv
import heapq
import json
import math
import os
import time
from collections import defaultdict, deque
from itertools import count
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import nearest_points



#Class for logging information about the pipeline
class IOTracker:
    def __init__(self) -> None:
        # Total logical read operations performed by the pipeline.
        self.read_ops = 0
        # Total logical write operations performed by the pipeline.
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



#Class for tracking cardinality
class CardinalityTracker:

    def __init__(self) -> None:
        self.unique_values: Dict[str, Set[str]] = defaultdict(set)

    def update_row(self, row: Dict[str, Any]) -> None:
        for col, value in row.items():
            self.unique_values[col].add(self._normalize(value))

    def _normalize(self, value: Any) -> str:
        if value is None:
            return "nan"
        text = str(value).strip()
        return "nan" if text == "" else text

    def summary(self) -> Dict[str, Any]:
        counts = {col: len(vals) for col, vals in self.unique_values.items()}
        return {
            "column_cardinality": dict(sorted(counts.items())),
        }

class PipelineLogger: #For logging the pipeline information into a .json file
    def __init__(self, path: str, io_tracker: Optional[IOTracker] = None):
        self.path = path
        self.io_tracker = io_tracker
        self.lines: List[str] = []
        self.step_timings: Dict[str, float] = {}
        self.total_runtime_seconds: Optional[float] = None

    def section(self, title: str) -> None: #Inserts section header into log
        self.lines.append("\n" + "=" * 100)
        self.lines.append(title)
        self.lines.append("=" * 100)

    def kv(self, key: str, value: Any) -> None: #Makes printing easier
        if isinstance(value, (dict, list, tuple)):
            self.lines.append(f"{key}: {json.dumps(value, indent=2, default=str)}")
        else:
            self.lines.append(f"{key}: {value}")

    def record_step_time(self, step_name: str, seconds: float) -> None:
        self.step_timings[step_name] = float(seconds)
        self.kv(f"{step_name}_runtime_seconds", round(float(seconds), 6))

    def record_total_runtime(self, seconds: float) -> None:
        self.total_runtime_seconds = float(seconds)

    def write(self) -> None: #Flush to disk
        if self.step_timings or self.total_runtime_seconds is not None:
            self.lines.append("\n" + "=" * 100)
            self.lines.append("TIMING SUMMARY")
            self.lines.append("=" * 100)
            for step_name, seconds in self.step_timings.items():
                self.lines.append(f"{step_name}: {seconds:.6f} seconds")
            if self.total_runtime_seconds is not None:
                self.lines.append(f"total_pipeline_runtime: {self.total_runtime_seconds:.6f} seconds")
        Path(self.path).write_text("\n".join(self.lines), encoding="utf-8")
        if self.io_tracker is not None:
            self.io_tracker.add_write("log_write")


class RunReader: #Used for reading through the temp files made during sorting
    def __init__(self, path: str, io_tracker: Optional[IOTracker] = None):
        self.io_tracker = io_tracker
        # Open one run file for sequential reading during the merge phase.
        self.file = open(path, "r", newline="", encoding="utf-8")
        if self.io_tracker is not None:
            self.io_tracker.add_read("sorted_run_read")
        self.reader = csv.DictReader(self.file)
        self.fieldnames = self.reader.fieldnames or []
    def pop(self) -> Optional[Dict[str, str]]: #Get next line from file
        try:
            return next(self.reader)
        except StopIteration:
            return None
    def close(self) -> None:
        self.file.close()


class SortedRunStream: #Useful for the 2nd pass of external merge sort
    #Builds a heap containing the current row from each sorted chunk file
    def __init__(self, run_paths: Sequence[str], key_columns: Sequence[str], io_tracker: Optional[IOTracker] = None):
        self.io_tracker = io_tracker
        self.key_columns = list(key_columns)
        self.readers: List[RunReader] = []
        self.heap: List[Tuple[Tuple[str, ...], int, int, Dict[str, str]]] = [] #Stores heap contents for push and pop
        self.unique = count()
        self.fieldnames: List[str] = []

        for reader_idx, path in enumerate(run_paths): #Each run contributes its current row in sorted order
            reader = RunReader(path, io_tracker=self.io_tracker)
            self.readers.append(reader)
            if not self.fieldnames:
                self.fieldnames = list(reader.fieldnames)
            row = reader.pop() #Gets 'smallest' row in heap, because we are using a minheap the top row is guaranteed to have smallest key values (sorted by zip then time)
            if row is not None:
                heapq.heappush(self.heap, (row_key(row, self.key_columns), next(self.unique), reader_idx, row))

    def pop(self) -> Optional[Dict[str, str]]: #Get next sorted file
        if not self.heap:
            return None
        _, _, reader_idx, row = heapq.heappop(self.heap)
        next_row = self.readers[reader_idx].pop() #Get next row from same file
        if next_row is not None:
            heapq.heappush(self.heap, (row_key(next_row, self.key_columns), next(self.unique), reader_idx, next_row)) #Put next row in heap
        return row

    def close(self) -> None:
        for reader in self.readers:
            reader.close()


class LagState: #Buffer so we can keep features which we will be adding as a lag feature
#Note a lag feature is a feature containing information from a previous time step (ex. us_aqi_past_1 is the aqi from the previous timestep)
#Note currently the buffer is never dumped, which can be an issue for big data
    def __init__(self, feature_cols: Sequence[str], num_past_feats: int):
        self.feature_cols = list(feature_cols)
        self.num_past_feats = int(num_past_feats)
        # For each ZIP, keep one deque per lagged feature.
        self.buffers: Dict[str, Dict[str, Deque[Any]]] = defaultdict(
            lambda: {feat: deque(maxlen=self.num_past_feats) for feat in self.feature_cols}
        )

    def apply(self, row: Dict[str, Any], zip_col: str) -> None: #Populate the columns, then push current values into the buffer
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
            value = row.get(feat, "nan")
            state[feat].append(value)

#Tracks variance of the features so they can be removed
class OnlineVariance:
    def __init__(self, excluded_columns):
        self.excluded_columns = set(excluded_columns)
        self.counts = defaultdict(int)
        self.sums = defaultdict(float)
        self.sumsq = defaultdict(float)
        self.mins = {}
        self.maxs = {}

    def update_row(self, row):
        for col, value in row.items():
            if col in self.excluded_columns:
                continue
            if value is None:
                continue

            try:
                f = float(value)
            except (TypeError, ValueError):
                continue

            self.counts[col] += 1
            self.sums[col] += f
            self.sumsq[col] += f * f

            if col not in self.mins or f < self.mins[col]:
                self.mins[col] = f
            if col not in self.maxs or f > self.maxs[col]:
                self.maxs[col] = f

    def normalized_variances(self):
        out = {}
        for col, n in self.counts.items():
            if n <= 0:
                out[col] = 0.0
                continue

            mean = self.sums[col] / n
            var = max(self.sumsq[col] / n - mean * mean, 0.0)

            col_min = self.mins[col]
            col_max = self.maxs[col]
            rng = col_max - col_min

            if rng <= 0:
                out[col] = 0.0
            else:
                out[col] = var / (rng * rng)

        return out



#Helper Functions
def ensure_dir(path: str) -> None: #Makes directories
    Path(path).mkdir(parents=True, exist_ok=True)


def json_dump(path: str, obj: Any, io_tracker: Optional[IOTracker] = None) -> None: #Write json file (pipeline summary)
    Path(path).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    if io_tracker is not None:
        io_tracker.add_write("json_write")


def parse_csv_list(value: Optional[str]) -> List[str]: #Parses feature names
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def detect_column(columns: Sequence[str], candidates: Sequence[str]) -> Optional[str]: #Finds columns based a candidates
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


def standardize_time_value(value: Any) -> str: #Removes time zone information since all data is in the same timezone
    raw = str(value).replace("T", " ").strip()
    raw = pd.Series([raw]).str.replace(r"([+-]\d{2}:\d{2}|Z)$", "", regex=True).iloc[0].strip()
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def row_key(row: Dict[str, Any], key_columns: Sequence[str]) -> Tuple[str, ...]: #Builds Primary key (zip, time)
    return tuple(str(row[col]) for col in key_columns)



def make_sorted_runs_collect_keys(
    input_csv: str,
    key_columns: Sequence[str],
    chunk_rows: int,
    temp_dir: str,
    run_prefix: str,
    unique_times: Set[str],
    unique_zips: Set[str],
    time_column: str,
    zip_column: str,
    drop_columns: Sequence[str] = (),
    io_tracker: Optional[IOTracker] = None,
) -> Tuple[List[str], List[str]]:
    run_paths: List[str] = []
    output_columns: Optional[List[str]] = None

    forbidden_to_drop = set(key_columns) | {time_column, zip_column}
    bad_drops = [col for col in drop_columns if col in forbidden_to_drop]
    if bad_drops:
        raise ValueError(f"Cannot drop required columns used for sorting/timestep mapping: {bad_drops}")

    # Read the large CSV a chunk at a time so we never need the full file in RAM.
    for run_idx, chunk in enumerate(pd.read_csv(input_csv, chunksize=chunk_rows)):
        if io_tracker is not None:
            io_tracker.add_read("csv_chunk_read")
        missing = [col for col in key_columns if col not in chunk.columns] #Flag Error if key columns are not present in CSV
        if missing:
            raise ValueError(f"Missing sort key columns in input {input_csv}: {missing}")

        unique_times.update(chunk[time_column].dropna().astype(str).tolist()) #Update set of primary keys, unique_times only used for metadata
        unique_zips.update(chunk[zip_column].dropna().astype(str).tolist()) #Used for spatial precompute step since we only care about ZIPs we have in our data, shapefile contains all Texas ZIPs

        cols_to_drop = [col for col in drop_columns if col in chunk.columns]
        if cols_to_drop:
            chunk = chunk.drop(columns=cols_to_drop)

        if output_columns is None:
            output_columns = list(chunk.columns)

        # Stable sort so rows with equal keys preserve their original relative order.
        # This can likely be changed to quicksort since it is an in memory sort
        chunk = chunk.sort_values(by=list(key_columns), kind="mergesort")

        # Write one sorted run file that will later participate in the k-way merge.
        run_path = os.path.join(temp_dir, f"{run_prefix}_run_{run_idx:06d}.csv")
        chunk.to_csv(run_path, index=False)
        if io_tracker is not None:
            io_tracker.add_write("sorted_run_write")
        run_paths.append(run_path)

    if output_columns is None:
        df0 = pd.read_csv(input_csv, nrows=0)
        if io_tracker is not None:
            io_tracker.add_read("csv_header_read")
        cols_to_drop = [col for col in drop_columns if col in df0.columns]
        if cols_to_drop:
            df0 = df0.drop(columns=cols_to_drop)
        output_columns = list(df0.columns)

    return run_paths, output_columns

"""Using the shapefiles and the facilities data we have, we precompute the distances to each zip code.
This makes future processing easier since we do not have to find distance every row when processing the spatial impact score.
"""
def build_spatial_zip_lookup(
    tri_facilities_csv: str,
    tri_chemicals_csv: str,
    zip_shapefile: str,
    roads_shapefile: str,
    zips_needed: Set[str],
    road_radius_km: float,
    facility_radius_km: float,
    logger: PipelineLogger,
    io_tracker: Optional[IOTracker] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    logger.section("Static spatial precompute")

    # Read ZIP polygons and keep only the ZIPs that actually appear in the streamed data.
    zcta = gpd.read_file(zip_shapefile)
    if io_tracker is not None:
        io_tracker.add_read("shapefile_read")
    zcta_zip_col = detect_column(zcta.columns, ["ZCTA5CE20", "ZCTA5CE10", "GEOID20", "GEOID10", "zip", "zcta"])
    if zcta_zip_col is None:
        raise ValueError("Could not detect ZIP/ZCTA column in ZIP shapefile.")

    zcta = zcta.copy()
    zcta["zip_norm"] = zcta[zcta_zip_col].astype(str).str.extract(r"(\d{5})", expand=False) #Gets 5 digit zip code (i.e. if zip code is like 11111-4231, removes the '-4231')
    zcta = zcta[zcta["zip_norm"].isin(zips_needed)].copy()
    if zcta.empty:
        raise ValueError("None of the requested ZIP codes were found in the ZCTA shapefile.")

    # Read the road network once; all geometry work happens here rather than per row.
    roads = gpd.read_file(roads_shapefile)
    if io_tracker is not None:
        io_tracker.add_read("shapefile_read")
    fac = pd.read_csv(tri_facilities_csv)
    chem = pd.read_csv(tri_chemicals_csv)
    if io_tracker is not None:
        io_tracker.add_read("csv_full_read", 2)

    fac_cols = {
        "facility_id": "trifd",
        "lat": "latitude",
        "lon": "longitude",
        "facility_name": "facility"
    }
    chem_cols = {
        "facility_id": "trifd",
        "chemical_name": "chemical",
        "amount": "total_air_emissions_lbs"
    }
    if fac_cols["facility_id"] is None or fac_cols["lat"] is None or fac_cols["lon"] is None:
        raise ValueError(f"Could not detect required TRI facility columns. Detected: {fac_cols}")
    if chem_cols["facility_id"] is None or chem_cols["chemical_name"] is None:
        raise ValueError(f"Could not detect required TRI chemical columns. Detected: {chem_cols}")

    facility_amount_col = "total_air_emissions_lbs"

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
    if chem_cols["amount"] is not None:
        chem_keep.append(chem_cols["amount"])
    chem_use = chem[chem_keep].copy()
    chem_use.columns = ["facility_id", "chemical_name"] + (["amount"] if chem_cols["amount"] is not None else [])
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
        fac_enriched["amount"] = fac_enriched["amount"].fillna(fac_enriched.get("amount_y", 0.0)).fillna(0.0)
    else:
        fac_enriched["amount"] = pd.to_numeric(fac_enriched["amount"], errors="coerce").fillna(0.0)
    fac_enriched["chemical_count"] = pd.to_numeric(fac_enriched["chemical_count"], errors="coerce").fillna(0.0)

    fac_gdf = gpd.GeoDataFrame(
        fac_enriched,
        geometry=gpd.points_from_xy(fac_enriched["lon"], fac_enriched["lat"]),
        crs="EPSG:4326",
    )

    target_crs = "EPSG:3857" #Changes units to meters for distance calculation
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
    # Build all ZIP-to-facility candidate pairs once, then precompute direction/decay.
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

    # For each ZIP centroid, find nearby roads and precompute the source->ZIP direction.
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


# -----------------------------
# Streaming feature transforms
# -----------------------------

def resolve_blend_weight(mode: str, blend_100m: float) -> float:
    """Convert a named wind-mode option into the effective 100m weight."""
    mode = str(mode).lower()
    if mode == "10m":
        return 0.0
    if mode == "100m":
        return 1.0
    if mode == "blend":
        return float(blend_100m)
    raise ValueError(f"Unsupported wind mode: {mode}")


def compute_wind_vector(row: Dict[str, Any], mode: str, blend_100m: float) -> Tuple[float, float]:
    """Compute a 2D wind vector from the row's 10m/100m speed and direction features."""
    w100 = resolve_blend_weight(mode, blend_100m)
    w10 = 1.0 - w100
    speed10 = float(row.get("wind_speed_10m"))
    speed100 = float(row.get("wind_speed_100m"))
    cos10 = float(row.get("wind_direction_10m_cos"))
    sin10 = float(row.get("wind_direction_10m_sin"))
    cos100 = float(row.get("wind_direction_100m_cos"))
    sin100 = float(row.get("wind_direction_100m_sin"))
    wind_x = w10 * speed10 * cos10 + w100 * speed100 * cos100
    wind_y = w10 * speed10 * sin10 + w100 * speed100 * sin100
    return wind_x, wind_y

#Edited by rparaula because somehow a weather recorrd from 2026-04-02 2026-04-02 15:00:00-05:00 has missing winnd direction
def add_direction_features_to_row(row, direction_columns, drop_original):
    """Expand direction columns into sine/cosine features for one output row."""
    for c in direction_columns:
        if c not in row:
            continue

        raw_value = row.get(c)

        if raw_value is None or str(raw_value).strip() == "":
            row[f"{c}_sin"] = "nan"
            row[f"{c}_cos"] = "nan"
            if drop_original:
                row.pop(c, None)
            continue

        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            row[f"{c}_sin"] = "nan"
            row[f"{c}_cos"] = "nan"
            if drop_original:
                row.pop(c, None)
            continue

        radians = math.radians(value)
        row[f"{c}_sin"] = math.sin(radians)
        row[f"{c}_cos"] = math.cos(radians)

        if drop_original:
            row.pop(c, None)


def add_time_features_to_row(row: Dict[str, Any], time_col: str) -> None:
    """Add calendar/cyclic time features derived from the normalized time column."""
    dt = pd.to_datetime(row.get(time_col, ""), errors="coerce")
    if pd.isna(dt):
        additions = {
            # "year": "nan",
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
            # "year": int(dt.year),
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
    if entry is None: #likely to remove redundancy we can get rid of everything except overall score
        # row["road_count_nearby"] = 0
        row["road_impact_score"] = 0.0
        # row["facility_count_nearby"] = 0
        row["facility_impact_score"] = 0.0
        # row["overall_spatial_impact_score"] = 0.0
        return

    # Compute the wind vector seen by roads and facilities under the chosen height/blend mode.
    road_wx, road_wy = compute_wind_vector(row, mode=road_wind_mode, blend_100m=road_wind_blend_100m)
    facility_wx, facility_wy = compute_wind_vector(row, mode=facility_wind_mode, blend_100m=facility_wind_blend_100m)

    road_score = 0.0
    for pair in entry["road_pairs"]:
        projection = road_wx * pair["dir_x"] + road_wy * pair["dir_y"] #Scalar projection (dot product), pair[x,y] is normalized to a basis vector in build_spatial_zip_lookup
        downwind = max(projection, 0.0)
        road_score += pair["decay"] * downwind

    facility_score = 0.0
    for pair in entry["facility_pairs"]:
        projection = facility_wx * pair["dir_x"] + facility_wy * pair["dir_y"]
        downwind = max(projection, 0.0)
        facility_score += pair["severity"] * pair["decay"] * downwind

    # row["road_count_nearby"] = int(entry["road_count_nearby"])
    row["road_impact_score"] = float(road_score)
    # row["facility_count_nearby"] = int(entry["facility_count_nearby"])
    row["facility_impact_score"] = float(facility_score)
    # row["overall_spatial_impact_score"] = float(road_score + facility_score)


# -----------------------------
# Merge + transform
# -----------------------------

def merge_rows_full_outer(
    key: Tuple[str, ...],
    left_row: Optional[Dict[str, str]],
    right_row: Optional[Dict[str, str]],
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
    """Find which columns should be expanded into direction sine/cosine features."""
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
    output_csv: str,
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
    # Determine the exact column order of the streamed output file before writing rows.
    fieldnames = build_output_fieldnames(
        left_columns=left_columns,
        right_columns=right_columns,
        key_columns=key_columns,
        direction_columns=direction_columns,
        drop_original_direction_columns=drop_original_direction_columns,
        lag_feature_cols=lag_feature_cols,
        num_past_feats=num_past_feats,
    )

    lag_state = LagState(feature_cols=lag_feature_cols, num_past_feats=num_past_feats)
    rows_written = 0
    left_only = 0
    right_only = 0
    matched = 0

    def finalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Apply all feature-engineering steps to one merged row before writing it."""
        # Normalize the join keys again defensively in case one side provided odd formatting.
        row[time_column] = standardize_time_value(row.get(time_column, "")) #Remove time zone (we are in Texas so information is redundant)
        row[zip_column] = row.get(zip_column, "") #Redundant?
        add_direction_features_to_row(row, direction_columns, drop_original=drop_original_direction_columns) #Change direction columns to be sin & cos
        add_time_features_to_row(row, time_col=time_column) #add features for day, is_weekend, day_of_year etc.
        add_spatial_scores_to_row( #compute spatial impact score for row and add features
            row,
            spatial_lookup=spatial_lookup,
            zip_col=zip_column,
            facility_wind_mode=facility_wind_mode,
            facility_wind_blend_100m=facility_wind_blend_100m,
            road_wind_mode=road_wind_mode,
            road_wind_blend_100m=road_wind_blend_100m,
        )
        lag_state.apply(row, zip_col=zip_column) #add lag features which are stored in buffer
        # Emit exactly the configured output columns, filling anything missing with "nan".
        out = {col: row.get(col, "nan") for col in fieldnames}
        if variance_stats is not None:
            variance_stats.update_row(out)
        if cardinality_tracker is not None:
            cardinality_tracker.update_row(out)
        return out

    with open(output_csv, "w", newline="", encoding="utf-8") as out_f:
        if io_tracker is not None:
            io_tracker.add_write("final_csv_write")
        writer = csv.DictWriter(out_f, fieldnames=fieldnames)
        writer.writeheader()

        left_row = left_stream.pop() #Because there are no missing rows, these rows will corresponnd to the same primary key from both weather and air quality
        right_row = right_stream.pop()

        # Standard streaming merge-join over two already-sorted inputs.
        while left_row is not None or right_row is not None:
            if left_row is None:
                if merge_how == "outer":
                    rkey = row_key(right_row, key_columns)
                    writer.writerow(finalize_row(merge_rows_full_outer(rkey, None, right_row, key_columns, left_columns, right_columns)))
                    rows_written += 1
                    right_only += 1
                right_row = right_stream.pop()
                continue

            if right_row is None:
                if merge_how == "outer":
                    lkey = row_key(left_row, key_columns)
                    writer.writerow(finalize_row(merge_rows_full_outer(lkey, left_row, None, key_columns, left_columns, right_columns)))
                    rows_written += 1
                    left_only += 1
                left_row = left_stream.pop()
                continue

            lkey = row_key(left_row, key_columns)
            rkey = row_key(right_row, key_columns)

            if lkey == rkey:
                writer.writerow(finalize_row(merge_rows_full_outer(lkey, left_row, right_row, key_columns, left_columns, right_columns)))
                rows_written += 1
                matched += 1
                left_row = left_stream.pop()
                right_row = right_stream.pop()
            elif lkey < rkey:
                if merge_how == "outer":
                    writer.writerow(finalize_row(merge_rows_full_outer(lkey, left_row, None, key_columns, left_columns, right_columns)))
                    rows_written += 1
                    left_only += 1
                left_row = left_stream.pop()
            else:
                if merge_how == "outer":
                    writer.writerow(finalize_row(merge_rows_full_outer(rkey, None, right_row, key_columns, left_columns, right_columns)))
                    rows_written += 1
                    right_only += 1
                right_row = right_stream.pop()

    return {
        "output_csv": output_csv,
        "rows_written": rows_written,
        "matched_rows": matched,
        "left_only_rows": left_only,
        "right_only_rows": right_only,
        "output_columns": fieldnames,
    }



def apply_variance_filter_csv( #Pass through removing columns under the varaince threshold
    input_csv: str,
    output_csv: str,
    report_json: str,
    variance_stats: OnlineVariance,
    variance_threshold: float,
    exclude_cols: Sequence[str],
    io_tracker: Optional[IOTracker] = None,
) -> Dict[str, Any]:
    variances = variance_stats.normalized_variances()
    removed = sorted([c for c, v in variances.items() if v < variance_threshold])
    exclude_set = set(exclude_cols)

    # Re-read the CSV once and rewrite only the columns that survive the filter.
    # Re-read the CSV once and rewrite only the columns that survive the filter.
    with open(input_csv, "r", newline="", encoding="utf-8") as in_f:
        if io_tracker is not None:
            io_tracker.add_read("variance_input_read")
        reader = csv.DictReader(in_f)
        input_cols = reader.fieldnames or []
        kept_cols = [c for c in input_cols if c in exclude_set or c not in removed]
        with open(output_csv, "w", newline="", encoding="utf-8") as out_f:
            if io_tracker is not None:
                io_tracker.add_write("variance_output_write")
            writer = csv.DictWriter(out_f, fieldnames=kept_cols)
            writer.writeheader()
            for row in reader:
                writer.writerow({c: row.get(c, "nan") for c in kept_cols})

    report = {
        "excluded_from_variance": list(exclude_cols),
        "variance_threshold": float(variance_threshold),
        "variances": variances,
        "removed_columns": removed,
        "kept_columns": kept_cols,
        "input_csv": input_csv,
        "output_csv": output_csv,
    }
    json_dump(report_json, report, io_tracker=io_tracker)
    return report

def apply_low_cardinality_filter_csv( #Pass through removing columns under the cardinality threshold
    input_csv: str,
    output_csv: str,
    report_json: str,
    cardinality_summary: Dict[str, Any],
    cardinality_threshold: int,
    exclude_cols: Sequence[str],
    io_tracker: Optional[IOTracker] = None,
) -> Dict[str, Any]:
    exclude_set = set(exclude_cols)
    counts = cardinality_summary.get("column_cardinality", {})
    removed = sorted([c for c, v in counts.items() if c not in exclude_set and int(v) < int(cardinality_threshold)])

    with open(input_csv, "r", newline="", encoding="utf-8") as in_f:
        if io_tracker is not None:
            io_tracker.add_read("cardinality_input_read")
        reader = csv.DictReader(in_f)
        input_cols = reader.fieldnames or []
        kept_cols = [c for c in input_cols if c in exclude_set or c not in removed]
        with open(output_csv, "w", newline="", encoding="utf-8") as out_f:
            if io_tracker is not None:
                io_tracker.add_write("cardinality_output_write")
            writer = csv.DictWriter(out_f, fieldnames=kept_cols)
            writer.writeheader()
            for row in reader:
                writer.writerow({c: row.get(c, "nan") for c in kept_cols})

    report = {
        "excluded_from_cardinality": list(exclude_cols),
        "cardinality_threshold": int(cardinality_threshold),
        "column_cardinality": counts,
        "removed_columns": removed,
        "kept_columns": kept_cols,
        "input_csv": input_csv,
        "output_csv": output_csv,
    }
    json_dump(report_json, report, io_tracker=io_tracker)
    return report

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Block-oriented AQ/weather preprocessing pipeline with external sort, streaming merge, "
            "static spatial precompute, inline direction/time/lag features, and optional variance filtering."
        )
    )
    ap.add_argument("--air-quality", required=True) #path to csv
    ap.add_argument("--weather", required=True) #path to csv
    ap.add_argument("--tri-facilities", required=True) #path to csv
    ap.add_argument("--tri-chemicals", required=True) #path to csv
    ap.add_argument("--zip-shapefile", required=True) #path to .shp
    ap.add_argument("--roads-shapefile", required=True) #path to .shp
    ap.add_argument("--output-dir", required=True) #path to output directory
    ap.add_argument("--key-columns", nargs="+", default=None, help="Sort/join columns. Default: zip time") #primary key
    ap.add_argument("--chunk-rows", type=int, default=25000) #chunk size
    ap.add_argument("--temp-dir", default=None) #path to temporary directory (for external sort)
    ap.add_argument("--keep-temp-files", action="store_true") #bool
    ap.add_argument("--left-drop-columns", nargs="*", default=[]) #what columns to remove from the left 
    ap.add_argument("--right-drop-columns", nargs="*", default=[]) #what columns to remove from the right
    ap.add_argument("--road-radius-km", type=float, default=2.0) #roads closer then this distance factored into spatial impact score
    ap.add_argument("--facility-radius-km", type=float, default=10.0) #facilities closer then this distance factored into the spatial impact score
    ap.add_argument("--facility-wind-mode", choices=["10m", "100m", "blend"], default="blend") #which wind columns should we use to determine spatial impact score for facilities
    ap.add_argument("--facility-wind-blend-100m", type=float, default=0.7) #determines the weight of 100m wind columns relative to 10m wind columns
    ap.add_argument("--road-wind-mode", choices=["10m", "100m", "blend"], default="10m")#which wind columns should we use to determine spatial impact score for roads
    ap.add_argument("--road-wind-blend-100m", type=float, default=0.0) #determines the weight of 100m wind columns relative to 10m wind columns
    ap.add_argument("--direction-columns", default="") #specify which columns we should expand to sin and cos
    ap.add_argument("--no-auto-detect-direction-columns", action="store_true") 
    ap.add_argument("--keep-original-direction-columns", action="store_true") #should we keep the original direction column (no)
    ap.add_argument("--feats-for-past", nargs="*", default=[]) #What features should we use as past time features
    #How many past time features should we have (ex. if set to 2, then for each past time features, we will hold the values from the previous two rows)
    ap.add_argument("--num-past-feats", type=int, default=0)
    ap.add_argument( #columns to exclude from variance check
        "--exclude-variance",
        default="time,zip,road_impact_score,facility_impact_score",
    )
    ap.add_argument("--variance-threshold", type=float, default=None) #removes columns below this threshold
    ap.add_argument( #columns to exclude from the cardinalty check
        "--exclude-cardinality",
        default="time,zip,road_impact_score,facility_impact_score," \
        "day,day_of_week,day_of_week_cos,day_of_week_sin,day_of_year,hour,hour_sin,hour_cos" \
        "is_weekend,latitude,longitude,month,month_cos,month_sin,ozone,us_aqi",
    )
    ap.add_argument("--cardinality-threshold", type=int, default=None) #removes columns below this threshold

    args = ap.parse_args()

    key_columns = ['zip', 'time']

    ensure_dir(args.output_dir) #make directories
    ensure_dir(os.path.join(args.output_dir, "metadata"))
    ensure_dir(os.path.join(args.output_dir, "logs"))
    ensure_dir(os.path.join(args.output_dir, "intermediate"))

    io_tracker = IOTracker() #initialize classes
    logger = PipelineLogger(os.path.join(args.output_dir, "logs", "pipeline_steps.log"), io_tracker=io_tracker)
    pipeline_start = time.perf_counter() #start the timer

    temp_dir = args.temp_dir or os.path.join(args.output_dir, "intermediate", "sort_runs")
    ensure_dir(temp_dir)

    paths = { #paths to intermediate csvs
        "pre_filter_csv": os.path.join(args.output_dir, "intermediate", "pre_filter_all_features.csv"),
        "pre_variance_csv": os.path.join(args.output_dir, "intermediate", "pre_variance_all_features.csv"),
        "final_csv": os.path.join(args.output_dir, "all_features.csv"),
        "spatial_meta_json": os.path.join(args.output_dir, "metadata", "spatial_lookup.json"),
        "variance_report_json": os.path.join(args.output_dir, "metadata", "variance_report.json"),
        "cardinality_report_json": os.path.join(args.output_dir, "metadata", "cardinality_report.json"),
        "summary_json": os.path.join(args.output_dir, "metadata", "pipeline_summary.json"),
    }

    created_temp_dir = False
    if args.temp_dir is None:
        ensure_dir(temp_dir)
    else:
        ensure_dir(temp_dir)

    left_run_paths: List[str] = []
    right_run_paths: List[str] = []

    try:
        # Pass 1: build sorted run files for both source CSVs and collect global key metadata.
        logger.section("Pass 1 - External sort runs + global key collection")
        t0 = time.perf_counter()
        unique_times: Set[str] = set()
        unique_zips: Set[str] = set()
        left_run_paths, left_columns = make_sorted_runs_collect_keys(
            input_csv=args.air_quality,
            key_columns=key_columns,
            chunk_rows=args.chunk_rows,
            temp_dir=temp_dir,
            run_prefix="air_quality",
            unique_times=unique_times,
            unique_zips=unique_zips,
            time_column='time',
            zip_column='zip',
            drop_columns=args.left_drop_columns,
            io_tracker=io_tracker,
        )
        right_run_paths, right_columns = make_sorted_runs_collect_keys(
            input_csv=args.weather,
            key_columns=key_columns,
            chunk_rows=args.chunk_rows,
            temp_dir=temp_dir,
            run_prefix="weather",
            unique_times=unique_times,
            unique_zips=unique_zips,
            time_column='time',
            zip_column='zip',
            drop_columns=args.right_drop_columns,
            io_tracker=io_tracker,
        )
        logger.kv("left_run_count", len(left_run_paths))
        logger.kv("right_run_count", len(right_run_paths))
        logger.kv("unique_times", len(unique_times))
        logger.kv("unique_zips", len(unique_zips))
        logger.record_step_time("pass1_external_sort", time.perf_counter() - t0)

        # Static spatial precompute is separated from the row stream so geometry work happens once.
        t0 = time.perf_counter()
        spatial_lookup, spatial_meta = build_spatial_zip_lookup(
            tri_facilities_csv=args.tri_facilities,
            tri_chemicals_csv=args.tri_chemicals,
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

        # Pass 2: k-way merge the sorted runs and perform all row-wise feature engineering.
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
        merge_output_target = paths["pre_filter_csv"] if (args.variance_threshold is not None or args.cardinality_threshold is not None) else paths["final_csv"]
        try:
            merge_meta = stream_merge_join_and_transform(
                left_stream=left_stream,
                right_stream=right_stream,
                output_csv=merge_output_target,
                key_columns=key_columns,
                time_column='time',
                zip_column='zip',
                left_columns=left_columns,
                right_columns=right_columns,
                merge_how='outer',
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

        if args.cardinality_threshold is not None: #We can combine cardinality and variance filter into one pass, since variance filter is currently unused it does not really matter
            t0 = time.perf_counter()
            cardinality_output = paths["pre_variance_csv"] if args.variance_threshold is not None else paths["final_csv"]
            cardinality_meta = apply_low_cardinality_filter_csv(
                input_csv=current_filter_input,
                output_csv=cardinality_output,
                report_json=paths["cardinality_report_json"],
                cardinality_summary=cardinality_summary,
                cardinality_threshold=args.cardinality_threshold,
                exclude_cols=parse_csv_list(args.exclude_cardinality),
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
            variance_meta = apply_variance_filter_csv(
                input_csv=current_filter_input,
                output_csv=paths["final_csv"],
                report_json=paths["variance_report_json"],
                variance_stats=variance_stats,
                variance_threshold=args.variance_threshold,
                exclude_cols=parse_csv_list(args.exclude_variance),
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
            "time_col": 'time',
            "zip_col": 'zip',
            "key_columns": key_columns,
            "merge_how": 'outer',
            "chunk_rows": args.chunk_rows,
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
    print(f"Final CSV: {paths['final_csv']}")
    print(f"Summary JSON: {paths['summary_json']}")
    print(f"Step log: {logger.path}")
    print(f"Total runtime: {total_runtime:.6f} seconds")


if __name__ == "__main__":
    main()
