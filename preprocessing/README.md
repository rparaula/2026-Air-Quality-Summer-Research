# Houston AQ/Weather Preprocessing Pipeline

This README documents the preprocessing workflow used to the various data sources gathered into a single machine-learning-ready feature table. The pipeline merges hourly air-quality and weather data, normalizes time formatting, optionally removes redundant columns, filters ZIP Code Tabulation Area geometry to only the ZIP codes present in the dataset, precomputes static spatial relationships, and writes a final feature CSV with temporal, wind-direction, spatial-impact, lag, cardinality, and optional variance-filter metadata.

The workflow is built around these scripts:

- `merge_data_into_master_file.py`
- `strip_tz_info.py`
- `remove_column.py`
- `filter_houston_zcta.py`
- `preprocessing.py`

---

## 1. What the pipeline produces

The main output is:

```text
<output-dir>/all_features.csv
```

This file is a merged and feature-engineered table where rows are keyed by:

```text
zip, time
```

Depending on the command-line options used, the final table can include:

- Raw air-quality features, such as AQI and pollutant values.
- Raw weather features, such as wind speed, wind direction, temperature, humidity, precipitation, and radiation.
- Wind direction converted into sine/cosine columns.
- Calendar/time features such as month, hour, day of week, weekend flag, and cyclic sine/cosine encodings.
- Spatial impact features:
  - `road_impact_score`
  - `facility_impact_score`
- Lag features such as `us_aqi_past_1`, `us_aqi_past_2`, etc.
- Optionally low-cardinality-filtered and variance-filtered columns are removed.

The main script also writes metadata and logs:

```text
<output-dir>/metadata/pipeline_summary.json
<output-dir>/metadata/spatial_lookup.json
<output-dir>/metadata/cardinality_report.json       # if cardinality filtering is enabled
<output-dir>/metadata/variance_report.json          # if variance filtering is enabled
<output-dir>/logs/pipeline_steps.log
<output-dir>/intermediate/pre_filter_all_features.csv
<output-dir>/intermediate/pre_variance_all_features.csv
```

Temporary sorted run files are created during external sorting. They are removed by default unless `--keep-temp-files` is passed.

---

## 2. Data sources used

### 2.1 Streamed air-quality CSV files

**Used by:**

- `merge_data_into_master_file.py`
- `strip_tz_info.py`
- `remove_column.py`, optional
- `filter_houston_zcta.py`
- `preprocessing.py`

**Purpose:**

The air-quality files provide pollutant and AQI features, and the weather files provide weather data such as percepitation, temperature, and wind speed/direction, both using ZIP code and time as primary keys. 
They are first appended into a master CSVs using `merge_data_into_master_file.py`, then time-zone suffixes are stripped using `strip_tz_info.py`. Sorting, feature engineering, and cardinality and variance filtering is then done by `preprocessing.py` to produce the final result.

The pipeline begins by gathering data from data-collection and merging it into one master file using `merge_data_into_master_file.py`

**How to gather data:**

Data-collection data is stored in this github in the `data/` and `static_data/` folder, only the `data/` folder is preprocessed using `merge_data_into_master_file.py`. This data is stored in Git Large File Storage (Git LFS).

These files can either be downloaded directly from the remote github repo, or by cloning the repo into your local machine and using git lfs commands like so:

1) install git-lfs into your local system by running `sudo apt install git-lfs`
2) clone this repo to your local machine
3) cd into the local repo, and run `git lfs pull`

Note: running these commands will install all files in this repo which are stored in Git LFS, which is all the data you will need to run the preprocessing portion of the code. These files include data in folders `data/` and `static_data/`, as well as `preprocessing/houston-zip-shapefiles` and `preprocessing/road-data`.

`preprocessing/houston-zip-shapefiles` contains the Zip polygon information gathered from [US Census Zip Shapefiles for 2020 download](https://www.census.gov/cgi-bin/geo/shapefiles/index.php?year=2020&layergroup=ZIP%20Code%20Tabulation%20Areas) filtered through `filter_houston_zcta.py`.
If you are using this data, there is no need to run `filter_houston_zcta.py`. 
These files are used in `preprocessing.py`

`preprocessing/road-data` contains the road information gathered from [US Census Primary and Secondary Roads for 2025 download](https://www.census.gov/cgi-bin/geo/shapefiles/index.php?year=2025&layergroup=Roads). 
No further preprocessing is done to this file. 
These files are using in `preprocessing.py`

`data/` holds the time variant data collected by data-collection (the air-quality and weather data). 
This is used in `merge_data_into_master_file`, then the output files of this script are used in `preprocessing.py`, `strip_tz_info.py`, and `remove_column.py`.

`static_data/` holds the time invariant data gathered by data-collection (`tri_checmials_houston.csv` and `tri_facilities_houston.csv` are using in the `preprocessing.py` script. 
This contains information on important facilities and their emmissions which is used to caluclate the facility_impact_score feature.

**Running `merge_data_into_master_file.py`**
This script is used to combine all data from `data/` into two master files, one containing all air-quality rows and one containing all weather rows. 

##Note: For this script to work in its current implementation, the air-quality data must have `_air_quality_` and `_weather_` included in the file name for the respective data type. 
The current implementation appends files based on earliest data first, so the date is also required in the filename. This restriction can be removed since these files are sorted later in `preprocessing.py`

This script uses a state file to track which csv's from the `data/` folder have already been appended into the 'master' files. 
This allows you to rerun this command on the same 'master' files multiple times without appending the same file twice. 
This state file is stored as a json and is given as a command line argument. If the argument given does not point to a state file json, it creates one.
The merge script sorts files using the month token and numeric part of the second token in the filename to append by earliest time first.

This script has 4 command line arguments:

1) `--input-dir` takes the path to your `data/` folder.
2) `--state-file` takes the path to your statefile.
3) `--air-master` takes the path to the output file of air-quality (the 'master' file with all appended rows from `_air_quality_` files).
4) `--weather-master` takes the path to the outout file of weather (the 'master' file with all appended rows from `_weather_` files).


The important columns used directly by `preprocessing.py` are:

```text
trifd
latitude
longitude
total_air_emissions_lbs
```

The script converts facility latitude/longitude into point geometry and computes ZIP-to-facility distances and directions. These are used to build `facility_impact_score`.

**How to gather:**

The provided `preprocessing.py` header says this project used `tri_facilities_houston.csv` from the repository/static data and did not require additional preprocessing before using it in `preprocessing.py`.

The referenced upstream GitHub repository also includes `process_tri_data.py` and notes that TRI text files are processed separately. Use that repository as a reference if regenerating TRI CSVs from raw EPA TRI files.

## 4. Recommended directory layout

A clean project layout might look like this:

```text
preprocessing-root/
├── scripts/
│   ├── merge_data_into_master_file.py
│   ├── strip_tz_info.py
│   ├── remove_column.py
│   ├── filter_houston_zcta.py
│   └── preprocessing.py
├── data/
│   ├── raw-streamed/
│   │   ├── feb_4thweek_air_quality_hourly_20260312_210107.csv
│   │   ├── feb_4thweek_weather_hourly_20260312_210107.csv
│   │   └── ...
│   ├── important-locations/
│   │   ├── tri_facilities_houston.csv
│   │   └── tri_chemicals_houston.csv
│   ├── census-zcta-2020/
│   │   └── tl_2020_us_zcta520.shp
│   ├── texas-shape-data/
│   │   └── tl_2025_48_prisecroads.shp
│   ├── air-quality-master.csv
│   ├── weather-master.csv
│   ├── air-quality-master-tz-stripped.csv
│   ├── weather-master-tz-stripped.csv
│   └── houston_zcta_filtered.shp
└── data/pipeline-output/
```

A shapefile is actually multiple files with the same base name, usually including `.shp`, `.shx`, `.dbf`, `.prj`, and sometimes `.cpg`. Keep all of those files together in the same directory.

---

## 5. Python environment

Recommended Python packages:

```bash
pip install pandas numpy geopandas shapely pyogrio fiona pyproj
```

Depending on your system, installing GeoPandas may require system GIS libraries. On many Linux/WSL systems, using conda is often easier:

```bash
conda create -n aq-preprocess python=3.11 -y
conda activate aq-preprocess
conda install -c conda-forge pandas numpy geopandas shapely pyogrio fiona pyproj -y
```

---

## 6. Full pipeline order

Run the scripts in this order:

1. Merge raw streamed air-quality/weather files into master CSVs.
2. Strip timezone suffixes from the master CSVs.
3. Optionally remove redundant `city` and `state` columns.
4. Download and filter the national ZCTA shapefile to Houston/data ZIPs.
5. Run the main preprocessing pipeline.

---

## 7. Step-by-step commands

The commands below assume you are running them from the directory containing the scripts. Adjust paths as needed.

### Step 1: Merge raw streamed files into master CSVs

Use `merge_data_into_master_file.py` to append all collected air-quality and weather CSVs into two master files.

```bash
python merge_data_into_master_file.py \
  --input-dir ../data/raw-streamed \
  --state-file ../data/merge_state.json \
  --air-master ../data/air-quality-master.csv \
  --weather-master ../data/weather-master.csv
```

What this does:

- Searches `--input-dir` for `.csv` files.
- Classifies files containing `_air_quality_` as air-quality files.
- Classifies files containing `_weather_` as weather files.
- Sorts the files chronologically using their filename tokens.
- Appends new files only once using `--state-file`.
- Rebuilds the master file if newly discovered files belong earlier in chronological order.

You can rerun this command after adding new streamed files. The state JSON tracks which files were already merged.

---

### Step 2: Strip timezone information from both master files

Use `strip_tz_info.py` on both master files. The input and output files must be different paths.

```bash
python strip_tz_info.py \
  ../data/air-quality-master.csv \
  ../data/air-quality-master-tz-stripped.csv \
  --time-col time
```

```bash
python strip_tz_info.py \
  ../data/weather-master.csv \
  ../data/weather-master-tz-stripped.csv \
  --time-col time
```

This keeps only:

```text
YYYY-MM-DD HH:MM:SS
```

For example:

```text
2026-03-05 12:34:56-06:00 -> 2026-03-05 12:34:56
2026-03-05 12:34:56Z      -> 2026-03-05 12:34:56
```

---

### Step 3: Optionally remove redundant columns

Because the project focuses on Houston, `city` and `state` may be redundant. You can either remove them before running `preprocessing.py` or let `preprocessing.py` drop them with `--left-drop-columns city state --right-drop-columns city state`.

To remove them ahead of time:

```bash
python remove_column.py \
  ../data/air-quality-master-tz-stripped.csv \
  ../data/air-quality-master-clean.csv \
  city state
```

```bash
python remove_column.py \
  ../data/weather-master-tz-stripped.csv \
  ../data/weather-master-clean.csv \
  city state
```

If you remove the columns here, use the `*-clean.csv` files in `preprocessing.py`. If you do not remove them here, use the `--left-drop-columns` and `--right-drop-columns` options in the main preprocessing command.

---

### Step 4: Filter the ZCTA shapefile to ZIP codes in the data. 

**This step is redundant if using data from the `preprocessing/houston-zip-shapefiles` directory.**

After downloading the 2020 national ZCTA shapefile, run:

```bash
python filter_houston_zcta.py \
  --csv ../data/air-quality-master-tz-stripped.csv \
  --shp ../data/census-zcta-2020/tl_2020_us_zcta520.shp \
  --output ../data/houston_zcta_filtered.shp
```

You can use either the air-quality or weather CSV as `--csv`, as long as it contains the ZIP codes used by the pipeline.

This creates a smaller shapefile containing only the ZIP/ZCTA polygons needed for the collected Houston data.

---

### Step 5: Run the full preprocessing pipeline

Basic run:

```bash
python preprocessing.py \
  --air-quality ../data/air-quality-master-tz-stripped.csv \
  --weather ../data/weather-master-tz-stripped.csv \
  --tri-facilities ../data/important-locations/tri_facilities_houston.csv \
  --tri-chemicals ../data/important-locations/tri_chemicals_houston.csv \
  --zip-shapefile ../data/houston_zcta_filtered.shp \
  --roads-shapefile ../data/texas-shape-data/tl_2025_48_prisecroads.shp \
  --output-dir ../data/pipeline-output \
  --chunk-rows 25000 \
  --temp-dir ../data/pipeline-output/temp-files \
  --left-drop-columns city state \
  --right-drop-columns city state \
  --feats-for-past us_aqi pm2_5 ozone wind_speed_100m wind_direction_100m_cos wind_direction_100m_sin \
  --num-past-feats 24
```

Smaller validation/test run:

```bash
python preprocessing.py \
  --air-quality ../data/air-quality-master-VALIDATION-tz-stripped.csv \
  --weather ../data/weather-master-VALIDATION-tz-stripped.csv \
  --tri-facilities ../data/important-locations/tri_facilities_houston.csv \
  --tri-chemicals ../data/important-locations/tri_chemicals_houston.csv \
  --zip-shapefile ../data/houston_zcta_filtered.shp \
  --roads-shapefile ../data/texas-shape-data/tl_2025_48_prisecroads.shp \
  --output-dir ../data/pipeline-output \
  --chunk-rows 10000 \
  --temp-dir ../data/pipeline-output/temp-files \
  --left-drop-columns city state \
  --right-drop-columns city state \
  --feats-for-past us_aqi \
  --num-past-feats 8
```

Run with cardinality filtering:

```bash
python preprocessing.py \
  --air-quality ../data/air-quality-master-tz-stripped.csv \
  --weather ../data/weather-master-tz-stripped.csv \
  --tri-facilities ../data/important-locations/tri_facilities_houston.csv \
  --tri-chemicals ../data/important-locations/tri_chemicals_houston.csv \
  --zip-shapefile ../data/houston_zcta_filtered.shp \
  --roads-shapefile ../data/texas-shape-data/tl_2025_48_prisecroads.shp \
  --output-dir ../data/pipeline-output \
  --chunk-rows 25000 \
  --left-drop-columns city state \
  --right-drop-columns city state \
  --feats-for-past us_aqi pm2_5 ozone \
  --num-past-feats 24 \
  --cardinality-threshold 2
```

Run with both cardinality and normalized variance filtering:

```bash
python preprocessing.py \
  --air-quality ../data/air-quality-master-tz-stripped.csv \
  --weather ../data/weather-master-tz-stripped.csv \
  --tri-facilities ../data/important-locations/tri_facilities_houston.csv \
  --tri-chemicals ../data/important-locations/tri_chemicals_houston.csv \
  --zip-shapefile ../data/houston_zcta_filtered.shp \
  --roads-shapefile ../data/texas-shape-data/tl_2025_48_prisecroads.shp \
  --output-dir ../data/pipeline-output \
  --chunk-rows 25000 \
  --left-drop-columns city state \
  --right-drop-columns city state \
  --feats-for-past us_aqi pm2_5 ozone \
  --num-past-feats 24 \
  --cardinality-threshold 2 \
  --variance-threshold 0.0001
```

---

## 8. Main `preprocessing.py` options

### Required inputs

```text
--air-quality        Path to the air-quality master CSV.
--weather            Path to the weather master CSV.
--tri-facilities     Path to tri_facilities_houston.csv.
--tri-chemicals      Path to tri_chemicals_houston.csv.
--zip-shapefile      Path to filtered Houston/ZCTA .shp file.
--roads-shapefile    Path to Texas roads .shp file.
--output-dir         Directory where final CSV, metadata, logs, and intermediates are written.
```

### Sorting and temporary files

```text
--chunk-rows         Number of rows per in-memory sort chunk. Default: 25000.
--temp-dir           Directory for temporary sorted run CSVs.
--keep-temp-files    Keep temporary sort-run files instead of deleting them.
```

The script uses an external-sort pattern so that large input CSVs do not need to be loaded fully into memory. It sorts chunks by `zip, time`, then performs a heap-based k-way merge.

### Column dropping

```text
--left-drop-columns city state
--right-drop-columns city state
```

The left input is the air-quality CSV. The right input is the weather CSV.

Do not drop required key columns:

```text
zip
time
```

### Spatial radius options

```text
--road-radius-km 2.0
--facility-radius-km 10.0
```

Roads within `road-radius-km` and facilities within `facility-radius-km` of a ZIP centroid are considered in spatial-impact scoring.

### Wind options

```text
--facility-wind-mode 10m|100m|blend
--facility-wind-blend-100m 0.7
--road-wind-mode 10m|100m|blend
--road-wind-blend-100m 0.0
```

By default:

- Facility impact uses a blend of 10m and 100m wind, weighted 70% toward 100m wind.
- Road impact uses 10m wind.

### Direction-column options

```text
--direction-columns "wind_direction_10m,wind_direction_100m"
--no-auto-detect-direction-columns
--keep-original-direction-columns
```

By default, direction-like columns are auto-detected and expanded into sine/cosine features. The original direction columns are dropped unless `--keep-original-direction-columns` is passed.

### Lag-feature options

```text
--feats-for-past us_aqi pm2_5 ozone
--num-past-feats 24
```

This creates lag columns for each listed feature. For example, with `--feats-for-past us_aqi --num-past-feats 3`, the output includes:

```text
us_aqi_past_1
us_aqi_past_2
us_aqi_past_3
```

Lag state is tracked separately by ZIP code.

### Cardinality filter options

```text
--cardinality-threshold 2
--exclude-cardinality time,zip,road_impact_score,facility_impact_score,...
```

Columns with cardinality below the threshold are removed unless they are excluded from the cardinality filter.

### Variance filter options

```text
--variance-threshold 0.0001
--exclude-variance time,zip,road_impact_score,facility_impact_score
```

The variance filter uses normalized variance:

```text
variance / (max - min)^2
```

Columns below the threshold are removed unless they are excluded from the variance filter.

---

## 9. How spatial-impact scoring works

The script precomputes ZIP-to-road and ZIP-to-facility relationships once, before streaming the merged rows. This avoids recalculating geometry distances for every row.

### Facility impact

For each ZIP centroid, the script finds nearby TRI facilities within `--facility-radius-km`. For each facility, it computes:

- Direction vector from facility to ZIP centroid.
- Distance decay based on facility distance.
- Facility severity based on total air emissions and number of unique chemicals.

For each row, it computes a wind vector and projects the wind onto the facility-to-ZIP direction. Only positive downwind projections contribute to the score.

Conceptually:

```text
facility_impact_score = sum(severity * distance_decay * max(dot(wind_vector, source_to_zip_unit_vector), 0))
```

### Road impact

For each ZIP centroid, the script finds nearby roads within `--road-radius-km`. For each road, it computes:

- Nearest road point to the ZIP centroid.
- Direction vector from the road to the ZIP centroid.
- Distance decay based on road distance.

For each row, it projects the road wind vector onto the road-to-ZIP direction. Only positive downwind projections contribute to the score.

Conceptually:

```text
road_impact_score = sum(distance_decay * max(dot(wind_vector, road_to_zip_unit_vector), 0))
```

---

## 10. Troubleshooting notes

### `zip` or `time` column errors

The main script expects both air-quality and weather CSVs to contain:

```text
zip
time
```

These are the join keys. Do not remove them.

### Shapefile not found or missing columns

Make sure all shapefile sidecar files are present in the same directory:

```text
.shp
.shx
.dbf
.prj
.cpg, if present
```

If the ZIP shapefile cannot be matched, check that it has one of these columns:

```text
ZCTA5CE20, ZCTA5CE10, GEOID20, GEOID10, zip, zcta
```

### CRS/distance confusion

The spatial precompute converts ZIP polygons, roads, and facilities to EPSG:3857 before distance calculations. In that projected CRS, distance units are meters. Radius arguments are given in kilometers and converted to meters internally.

### Input and output path cannot be the same for `strip_tz_info.py`

Use separate paths:

```bash
python strip_tz_info.py input.csv output.csv
```

Do not overwrite the input file directly.

### Filename pattern matters for master merging

`merge_data_into_master_file.py` expects filenames where:

- The first underscore-separated token is a month, such as `feb` or `march`.
- The second token begins with a number, such as `4thweek`, `1stweek`, or `10`.
- The filename contains `_air_quality_` or `_weather_`.

Files that do not match this pattern may not be merged correctly.

---

## 11. Minimal end-to-end command block

```bash
# 1. Merge streamed raw files.
python merge_data_into_master_file.py \
  --input-dir ../data/raw-streamed \
  --state-file ../data/merge_state.json \
  --air-master ../data/air-quality-master.csv \
  --weather-master ../data/weather-master.csv

# 2. Strip timezone suffixes.
python strip_tz_info.py ../data/air-quality-master.csv ../data/air-quality-master-tz-stripped.csv --time-col time
python strip_tz_info.py ../data/weather-master.csv ../data/weather-master-tz-stripped.csv --time-col time

# 3. Filter ZCTA polygons to the ZIP codes in the data.
python filter_houston_zcta.py \
  --csv ../data/air-quality-master-tz-stripped.csv \
  --shp ../data/census-zcta-2020/tl_2020_us_zcta520.shp \
  --output ../data/houston_zcta_filtered.shp

# 4. Run preprocessing.
python preprocessing.py \
  --air-quality ../data/air-quality-master-tz-stripped.csv \
  --weather ../data/weather-master-tz-stripped.csv \
  --tri-facilities ../data/important-locations/tri_facilities_houston.csv \
  --tri-chemicals ../data/important-locations/tri_chemicals_houston.csv \
  --zip-shapefile ../data/houston_zcta_filtered.shp \
  --roads-shapefile ../data/texas-shape-data/tl_2025_48_prisecroads.shp \
  --output-dir ../data/pipeline-output \
  --chunk-rows 25000 \
  --left-drop-columns city state \
  --right-drop-columns city state \
  --feats-for-past us_aqi pm2_5 ozone wind_speed_100m wind_direction_100m_cos wind_direction_100m_sin \
  --num-past-feats 24
