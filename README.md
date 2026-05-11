# Air Quality Machine Learning Pipeline

This README text file contains the combined documentation that was written by each team/phase.


<h1 align="center">📥Data Collection Phase📥</h1>


### OVERVIEW
--------



Basically, how this all works is that we aim for data to be pulled from:
  - [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)  (PM2.5, PM10, AQI, CO, NO2, ozone, etc.)
  - [Open-Meteo Weather API](https://open-meteo.com/en/docs)      (temperature, humidity, wind, precipitation, etc.)
  - [EPA FRS API](https://www.epa.gov/frs/frs-api)                 (nearby industrial pollution sources by ZIP)
  - [US Census ACS API](https://www.census.gov/programs-surveys/acs/data/data-via-api.html)           (population by ZIP code)
  - [OSMnx](https://osmnx.readthedocs.io/en/stable/) / [OpenStreetMap](https://www.openstreetmap.org)       (road density by ZIP code)
  - TRI text files              (Toxics Release Inventory, processed separately)

All output CSVs are saved to the data/ folder.
Each run is logged to data/pipeline_metadata.json for tracking and deduplication.


### PREREQUISITES
-------------

- Python 3.10+ is required
- uszips.csv must be manually downloaded from (https://simplemaps.com/data/us-zips) and placed within the root folder before running anything.
- TRI csv and json files from (Ask Tabriz for source) into project folder
- OpenMeteo API Key may be needed if using a large data range when using the backfill mode; however, it is not required. API key access is managed through github secrets..



### INSTALLATION
------------

1. Clone the repo: 
       - git clone https://github.com/GlowSand/AQ_ML_Pipeline.git
       - cd AQ_ML_Pipelinne
2. (optional but recommended) Create a virtual environment:
       - python -m venv .venv
       .venv\Scripts\activate

3. Install dependencies:
       pip install -r requirements.txt



### PROJECT STRUCTURE
-----------------


Orchestration & State:

       - 'run_pipeline.py': The main entry point. Determines what date range is missing, then calls the other scripts in order using the missing date range. This is done in both incremental backfill modes.

       - 'state.py': Reads/writes state.json to track the last successfully ingested date. Also writes a in_progress lock so crashed runs can detected an retired.

       - 'metadata_tracker.py': Logs a structured JSON record to pipeline_metadata.json after every run of the pipeline. This includes details like timing, output files, row counts, covered ZIPs, etc.

Data Ingestion (Dynamic Data):

       - 'collect.py': Fetches hourly air quality (PM2.5, PM10, NO2, ozone, CO, etc.) and hourly weather (temperature, humidity, wind, precipitation, etc.) from the Open-Meteo APIs, batched by ZIP code centroid. Produces the air_quality_hourly and weather_hourly csvs within the data folder.

       - All dynamic CSVs land in the '/data/' folder.

Static Dimension Tables (regenerated only with --refresh-static)

       - 'collect_population.py': Queries the US Census ACS API for population by ZIP, using 'houston_zips.csv' as reference. Outputs population_density.csv

       - 'dump_pollution_sources.py': Queries the EPA FRS API for nearby industrial facilities per ZIP. Outputs 'zip_pollution_sources_<state>_<timestamp>.csv.

       - 'dump_zip_road_density.py': Uses OSMnx / OpenStreetMap to compute road density (road length per area) for each ZIP polygon. Outputs 'zip_road_density_<timestamp>.csv'.

       - 'process_tri_data.py' - Processes manually downloaded TRI (Toxics Release Inventory) text files from the EPA into a structured CSV. Standalone script, not called by 'run_pipeline.py'.

       - 'dump_zip_building_types.py' (WIP): Original goal was to use OOpenStreetMap to fetch info tags for every building within a radius of each ZIP code cenntroid.

       - All static CSVs land in the '/static data/' folder.

Reference Data

       - 'uszips.csv': Full US ZIP code lookup table. Used by 'collect.py' to resolve ZIP -> coordinates for API calls.

       - 'houston_zips.csv': Filtered ZIP list for Houston, used by 'collect_population.py'.





### HOW TO RUN
----------

--- Incremental Mode (default) ---
Fetches only the data that is missing since the last successful run.

1. Install dependencies:
       `pip install -r requirements.txt`

2. Run the pipeline (fetches data from the last saved date up to today):
       `python run_pipeline.py`

   Optional flags:
       --cities "Houston,TX;Austin,TX"   Collect for multiple cities
       --out-dir data                    Where to save CSVs (default: data/)
       --batch-size 50                   ZIPs per API request (default: 50)
       --timezone America/Chicago        IANA timezone (default: America/Chicago)
       --zip-traffic road_density.csv    Attach precomputed road density
       --refresh-static                  Also regenerate slow static tables
                                         (population, pollution sources, road density)
       --out-prefix (default: aq)        Controls filename prefix of CSVs
       --start-date + --per-day          Enables backfill mode

3. To manually collect a specific date range:
       `python collect.py --cities "Houston,TX" --start-date 2025-01-01 --end-date 2025-01-07`

4. To regenerate static dimension tables only:
       `python run_pipeline.py --refresh-static`

For how incremental and backfill modes differ, see HOW IT WORKS below.

--- Backfill Mode ---
Collects one CSV per caledar day from a specific start date up to 5 days before today (the Open-Meteo archive limit).

Already-collected days are skipped automatically.

--- Regenerate Static Tables ---
Static tables (population, road density, pollution sources) are slow and only need to be regenerated occasionally via:
       'python run_pipeline.py --refresh-static'

--




### HOW IT WORKS
------------


--- Incremental Mode (default) ---
'state.py' reads 'state.json' to find the last successfully collected date. 'run_pipeline.py' computes the missing window and collects one day at a time, saving a checkpoint after each day. If a run crashes mid-way, the next run detects the in-progress lock and retries from the last saved checkpoint.

--- Backfill Mode ---
Instead of reading the 'state.json' file, the pipeline iterates from '--start-date' up to 5 days before today (the Open-Meteo archive lag). Days that are already collected are skipped automatically via metadata checks.


--- Regenerate Static Tables ---
Population, road density, and polllution source data rarely change and are computationally way more expensive, and thus are only refresh occasionally via:
       'python run_pipeline.py --refresh-static'


--- Automation ---
The incremental mode runs daily via GitHub Actions. The workflow triggers run_pipeline.py with no extra flags, so it always collects whatever is missing. Manual runs are onlly needed for backfills or regenerating static tables.


### HOW INCREMENTAL COLLECTION WORKS
---------------------------------
`state.py` stores the last successfully collected end date in `data/pipeline_state.json.`
On each run, `run_pipeline.py` automatically figures out what date window is missing
and only fetches new data. Already-collected windows are skipped (checked via metadata).
If a run crashes mid-way, the next run will detect the incomplete state and retry.


### OUTPUT FILES
------------

`data/<prefix>_air_quality_hourly_<timestamp>.csv`   Hourly AQ data per ZIP centroid

`data/<prefix>_weather_hourly_<timestamp>.csv`        Hourly weather data per ZIP centroid

`data/population_density.csv`                         Population per ZIP (static)

`data/zip_pollution_sources_<state>_<timestamp>.csv`  EPA facilities per ZIP (static)

`data/zip_road_density_<timestamp>.csv`               Road density per ZIP (static)

`data/pipeline_metadata.json`                         Log of every pipeline run


### KNOWN LIMITATIONS / NOTES
--------------------------


- The uszips.csv file is required. Download it from: https://simplemaps.com/data/us-zips
- TRI data (process_tri_data.py) requires manually downloaded TXT files from the
  EPA TRI website. Expected files: US_1a_2023.txt, US_1a_2024.txt
- The Open-Meteo forecast endpoint is used (covers recent history + today).
  There is roughly a 5-day lag on the archive endpoint if you switch to that.

<br><br>

 

<h2 align="center">🧹Preprocessing🧹</h2>

### Houston AQ/Weather Preprocessing Pipeline

This README documents the preprocessing workflow used to the various data sources gathered into a single machine-learning-ready feature table. The pipeline merges hourly air-quality and weather data, normalizes time formatting, optionally removes redundant columns, filters ZIP Code Tabulation Area geometry to only the ZIP codes present in the dataset, precomputes static spatial relationships, and writes a final feature CSV with temporal, wind-direction, spatial-impact, lag, cardinality, and optional variance-filter metadata.

The workflow is built around these scripts:

- `merge_data_into_master_file.py`
- `strip_tz_info.py`
- `remove_column.py`
- `filter_houston_zcta.py`
- `preprocessing.py`

---

#### 1. What the pipeline produces

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

#### 2. Data sources used

##### 2.1 Streamed air-quality CSV files

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

#### 3. Recommended directory layout

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

#### 4. Python environment

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

#### 5. Full pipeline order

Run the scripts in this order:

1. Merge raw streamed air-quality/weather files into master CSVs.
2. Strip timezone suffixes from the master CSVs.
3. Optionally remove redundant `city` and `state` columns.
4. Download and filter the national ZCTA shapefile to Houston/data ZIPs.
5. Run the main preprocessing pipeline.

---

#### 6. Step-by-step commands

The commands below assume you are running them from the directory containing the scripts. Adjust paths as needed.

##### Step 1: Merge raw streamed files into master CSVs

Use `merge_data_into_master_file.py` to append all collected air-quality and weather CSVs into two master files.


**Running `merge_data_into_master_file.py`**
This script is used to combine all data from `data/` into two master files, one containing all air-quality rows and one containing all weather rows. 

**Note: For this script to work in its current implementation, the air-quality data must have `_air_quality_` and `_weather_` included in the file name for the respective data type. 
The current implementation appends files based on earliest data first, so the date is also required in the filename. This restriction can be removed since these files are sorted later in `preprocessing.py`**

This script uses a state file to track which csv's from the `data/` folder have already been appended into the 'master' files. 
This allows you to rerun this command on the same 'master' files multiple times without appending the same file twice. 
This state file is stored as a json and is given as a command line argument. If the argument given does not point to a state file json, it creates one.
The merge script sorts files using the month token and numeric part of the second token in the filename to append by earliest time first.

This script has 4 command line arguments:

1) `--input-dir` takes the path to your `data/` folder.
2) `--state-file` takes the path to your statefile.
3) `--air-master` takes the path to the output file of air-quality (the 'master' file with all appended rows from `_air_quality_` files).
4) `--weather-master` takes the path to the outout file of weather (the 'master' file with all appended rows from `_weather_` files).

Sample Command:

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

---

##### Step 2: Strip timezone information from both master files

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

##### Step 3: Optionally remove redundant columns

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

##### Step 4: Filter the ZCTA shapefile to ZIP codes in the data. 

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

##### Step 5: Run the full preprocessing pipeline

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

#### 6. Main `preprocessing.py` options

##### Required inputs

```text
--air-quality        Path to the air-quality master CSV.
--weather            Path to the weather master CSV.
--tri-facilities     Path to tri_facilities_houston.csv.
--tri-chemicals      Path to tri_chemicals_houston.csv.
--zip-shapefile      Path to filtered Houston/ZCTA .shp file.
--roads-shapefile    Path to Texas roads .shp file.
--output-dir         Directory where final CSV, metadata, logs, and intermediates are written.
```

##### Sorting and temporary files

```text
--chunk-rows         Number of rows per in-memory sort chunk. Default: 25000.
--temp-dir           Directory for temporary sorted run CSVs.
--keep-temp-files    Keep temporary sort-run files instead of deleting them.
```

The script uses an external-sort pattern so that large input CSVs do not need to be loaded fully into memory. It sorts chunks by `zip, time`, then performs a heap-based k-way merge.

##### Column dropping

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

##### Spatial radius options

```text
--road-radius-km 2.0
--facility-radius-km 10.0
```

Roads within `road-radius-km` and facilities within `facility-radius-km` of a ZIP centroid are considered in spatial-impact scoring.

##### Wind options

```text
--facility-wind-mode 10m|100m|blend
--facility-wind-blend-100m 0.7
--road-wind-mode 10m|100m|blend
--road-wind-blend-100m 0.0
```

By default:

- Facility impact uses a blend of 10m and 100m wind, weighted 70% toward 100m wind.
- Road impact uses 10m wind.

##### Direction-column options

```text
--direction-columns "wind_direction_10m,wind_direction_100m"
--no-auto-detect-direction-columns
--keep-original-direction-columns
```

By default, direction-like columns are auto-detected and expanded into sine/cosine features. The original direction columns are dropped unless `--keep-original-direction-columns` is passed.

##### Lag-feature options

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

##### Cardinality filter options

```text
--cardinality-threshold 2
--exclude-cardinality time,zip,road_impact_score,facility_impact_score,...
```

Columns with cardinality below the threshold are removed unless they are excluded from the cardinality filter.

##### Variance filter options

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

#### 7. How spatial-impact scoring works

The script precomputes ZIP-to-road and ZIP-to-facility relationships once, before streaming the merged rows. This avoids recalculating geometry distances for every row.

##### Facility impact

For each ZIP centroid, the script finds nearby TRI facilities within `--facility-radius-km`. For each facility, it computes:

- Direction vector from facility to ZIP centroid.
- Distance decay based on facility distance.
- Facility severity based on total air emissions and number of unique chemicals.

For each row, it computes a wind vector and projects the wind onto the facility-to-ZIP direction. Only positive downwind projections contribute to the score.

Conceptually:

```text
facility_impact_score = sum(severity * distance_decay * max(dot(wind_vector, source_to_zip_unit_vector), 0))
```

##### Road impact

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

#### 8. Troubleshooting notes

##### `zip` or `time` column errors

The main script expects both air-quality and weather CSVs to contain:

```text
zip
time
```

These are the join keys. Do not remove them.

##### Shapefile not found or missing columns

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

##### CRS/distance confusion

The spatial precompute converts ZIP polygons, roads, and facilities to EPSG:3857 before distance calculations. In that projected CRS, distance units are meters. Radius arguments are given in kilometers and converted to meters internally.

##### Input and output path cannot be the same for `strip_tz_info.py`

Use separate paths:

```bash
python strip_tz_info.py input.csv output.csv
```

Do not overwrite the input file directly.

##### Filename pattern matters for master merging

`merge_data_into_master_file.py` expects filenames where:

- The first underscore-separated token is a month, such as `feb` or `march`.
- The second token begins with a number, such as `4thweek`, `1stweek`, or `10`.
- The filename contains `_air_quality_` or `_weather_`.

Files that do not match this pattern may not be merged correctly.

---

#### 9. Minimal end-to-end command block

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
```

#### 10. Limitations

The main limitation of this pipeline is that only preprocessing.py is done in a block-based manner, meaning if files get to large to fit in main memory other scripts may fail. 

Another limitation is that currently intermediate data is stored very infrequently and mostly forcefully due to the data being in different scripts. Intermediate data is stored after running each script, then by `preprocessing.py` before the column filtering is applied.

A secondary limitation is that cardinality and variance filtering is not very useful, the best way to go about feature removal is to have the data exploration phase inform training about which columns to remove. 

#### 11. Recommendations

It is probably more beneficial to implement the external merge sort into the `merge_data_into_master_file.py` so the output can be sorted master files. Additionally, all of these different scripts should be merged into one script so intermediate data can be stored intentionally and not forcefully. 

The current log implementation is useful for seeing how data is processed, but does not allow you to restart the pipeline from a checkpoint if the pipeline fails in the middle (with the caveat that intermediate data is saved between different script runs, so you only have to rerun the script that fails). 

A better implementation of this stage would be to merge all of these scripts into one, then create a log which saves checkpoints periodically so the pipeline can restart from a checkpoint is the script fails during the run.

<br><br>


<h2 align="center">📊Exploratory Data Analysis Phase📊</h2>


#### Overview

This repository contains data exploration pipeline for a 5-team end-to-end air quality prediction project. The goal of this phase is to validate data quality, uncover statistical patterns, and establish regression baselines

The analysis covers **79,152 hourly observations** across **97 Houston ZIP codes** spanning **February 22 – March 28, 2026**, with 191 features including six primary pollutants, meteorological variables, spatial impact scores, cyclic time encodings, and 24-hour lag features.

---

#### Project Structure

```
air_quality_exploration/
│
├── data/
│   ├── all_features_all_data.csv          # Main preprocessed dataset (79,152 × 191)
│   ├── air_quality.csv                    # Raw air quality data from Open-Meteo API
│   ├── weather_hourly.csv                 # Raw weather data from Open-Meteo API
│   └── multi_air_quality_hourly_*.csv     # Intermediate collection output
│
├── notebooks/
│   ├── air_quality_expl_phase2.py         # Main EDA pipeline script (Phase 2)
│   ├── Data_Exploration.ipynb             # Phase 1 exploratory notebook (raw data)
│   ├── tabular.ipynb                      # Tabular analysis notebook
│   │
│   └── outputs/
│       ├── plots/                         # 32 PNG visualizations
│       │   ├── 01_schema_overview.png
│       │   ├── 02_descriptive_stats.png
│       │   ├── 03_pollutant_summary_table.png
│       │   ├── 04_aqi_by_hour_table.png
│       │   ├── 05_aqi_by_dow_table.png
│       │   ├── 06_data_quality.png
│       │   ├── 07_class_imbalance.png
│       │   ├── 08_core_distributions.png
│       │   ├── 09_extra_distributions.png
│       │   ├── 10_temporal_patterns.png
│       │   ├── 11_pollutant_heatmaps.png
│       │   ├── 12_correlations.png
│       │   ├── 13_top_correlations_table.png
│       │   ├── 14_spatial_aqi.png
│       │   ├── 14b_houston_aqi_map.png
│       │   ├── 15_lag_autocorrelation.png
│       │   ├── 16_2d_histograms.png
│       │   ├── 17_2d_pm25_ozone.png
│       │   ├── 18_3d_hour_pm25.png
│       │   ├── 19_3d_temp_humidity_aqi.png
│       │   ├── 20_violin_pollutants.png
│       │   ├── 21_box_weather_by_hour_group.png
│       │   ├── 22_pollutant_scatter_matrix.png
│       │   ├── 23_weekday_vs_weekend.png
│       │   ├── 24_spatial_vs_aqi.png
│       │   ├── 25_extreme_vs_normal.png
│       │   ├── 26_wind_rose_aqi.png
│       │   ├── 27_radiation_vs_aqi.png
│       │   ├── 28_aqi_boxplot_by_hour.png
│       │   ├── 29_ml_baselines.png
│       │   ├── 30_ml_metrics_table.png
│       │   ├── 31_feature_importance.png
│       │   └── 32_residual_analysis.png
│       │
│       └── reports/                       # 9 CSV statistical reports
│           ├── descriptive_stats.csv
│           ├── pollutant_summary_table.csv
│           ├── aqi_by_hour_table.csv
│           ├── aqi_by_dow_table.csv
│           ├── missing_values.csv
│           ├── top_correlations_table.csv
│           ├── zip_aqi_stats.csv
│           ├── ml_metrics.csv
│           ├── feature_importance.csv
│           └── residuals_by_category.csv
│
└── screenshots/
```

---

#### How to Run

##### Requirements

```bash
pip install pandas numpy matplotlib seaborn scipy scikit-learn
```

##### Run the full pipeline

```bash
python air_quality_expl_phase2.py --data /path/to/all_features_all_data.csv
```

All 32 plots and 9 CSV reports will be automatically saved to `notebooks/outputs/`.

---

#### Dataset

| Property | Value |
|---|---|
| Rows | 79,152 |
| Columns | 191 |
| ZIP codes | 97 |
| Date range | Feb 22 – Mar 28, 2026 |
| Time resolution | Hourly |
| Target variable | `us_aqi` (continuous, 0–500 scale) |

##### Feature Groups

| Group | # Cols | Key Variables |
|---|---|---|
| Identifiers | 6 | city, state, zip, latitude, longitude, time |
| Current Pollutants | 6 | pm2_5, pm10, ozone, NO2, CO, SO2 |
| Weather | 8 | temperature, humidity, precipitation, wind, radiation |
| Spatial Impact | 5 | road distance, facility count, impact scores |
| Cyclic / Time | 11 | hour/month/DOW sin-cos, is_weekend |
| Lag Features | ~155 | 24-hr lags for AQI, PM2.5, ozone, wind |

---

#### Key Findings

##### Data Quality
- Overall missingness: **1.155%** — zero missing in core features
- Zero duplicate rows, zero duplicate (ZIP, time) pairs
- All features pass domain bound validation
- Outliers retained — genuine atmospheric events near Ship Channel

##### Descriptive Statistics
| Feature | Mean | Std | Skew |
|---|---|---|---|
| us_aqi | 43.73 | 10.48 | 1.46 |
| pm2_5 | 9.28 | 4.96 | 2.21 |
| ozone | 76.32 | 26.48 | −0.22 |
| nitrogen_dioxide | 12.67 | 13.66 | 3.13 |
| carbon_monoxide | 186.62 | 71.53 | 3.70 |

##### Temporal Patterns
- AQI minimum: **40.24** at 09:00
- AQI peak: **51.58** at 20:00
- Worst days: **Friday (45.67)** and **Saturday (46.83)**
- Ozone peaks weekday afternoons — photochemical formation

##### Spatial Patterns
- Mean AQI range: **43.2 – 45.0** across all 97 ZIPs
- Every ZIP has exactly **816 observations** (CV = 0.0%)
- Highest AQI: ZIP codes 77002–77009 — **Ship Channel industrial corridor**
- Lowest AQI: ZIP codes 77041, 77084, 77095 — **northwest suburbs**

##### Correlation Analysis
| Feature A | Feature B | r |
|---|---|---|
| pm10 | pm2_5 | +0.976 ⚠ collinear — drop one |
| carbon_monoxide | nitrogen_dioxide | +0.842 |
| relative_humidity | ozone | −0.670 |
| wind_speed_10m | nitrogen_dioxide | −0.486 |

##### Lag Autocorrelation (Most Important Finding)
| Signal | Lag-1 | Lag-24 |
|---|---|---|
| **us_aqi** | **r = 0.977** | r = 0.454 |
| pm2_5 | r = 0.355 | r = 0.355 |
| ozone | r = 0.336 | r = 0.030 |
| wind_speed | r = −0.032 | r = −0.267 |

> AQI lag-1 is the **strongest predictor in the entire dataset** — stronger than any pollutant.

##### ML Baselines (No Lag Features, 80/20 Split)
| Model | RMSE | MAE | R² |
|---|---|---|---|
| Linear Regression | 7.203 | 5.327 | 0.526 |
| **Random Forest** | **2.177** | **1.520** | **0.957** |
| Gradient Boosting | 2.472 | 1.829 | 0.944 |

##### Top Random Forest Features
| Rank | Feature | Importance |
|---|---|---|
| 1 | ozone | 0.236 |
| 2 | pm2_5 | 0.186 |
| 3 | nitrogen_dioxide | 0.113 |
| 4 | day_of_week_sin | 0.089 |
| 5 | hour_cos | 0.080 |

---

#### Visualizations

##### Key Plots

| Plot | Description |
|---|---|
| `07_class_imbalance.png` | 81.8% Good, 18.2% Moderate — confirms regression task |
| `10_temporal_patterns.png` | Hourly AQI cycle, day-of-week bar chart, rolling time series, heatmap |
| `14b_houston_aqi_map.png` | Geospatial Voronoi map of Houston AQI by ZIP area |
| `15_lag_autocorrelation.png` | Lag-1 AQI r=0.977 — strongest predictor in dataset |
| `29_ml_baselines.png` | RMSE, MAE, R² comparison across 3 models |
| `31_feature_importance.png` | Random Forest top-15 feature importances |

---

#### Geospatial Map

The script generates a Voronoi tessellation map (`14b_houston_aqi_map.png`) showing mean AQI across all 97 Houston ZIP code areas:

- **Red regions** = highest pollution (eastern Houston, Ship Channel)
- **Green regions** = lowest pollution (northwest suburbs)
- Every ZIP code labelled with number and mean AQI
- Top 5 worst and best ZIP codes highlighted

---

#### Technologies Used

| Tool | Purpose |
|---|---|
| Python 3 | Main language |
| pandas | Data loading and manipulation |
| NumPy | Numerical computation |
| Matplotlib | All visualizations |
| Seaborn | Statistical plots |
| scikit-learn | ML baselines (RF, GB, LR) |
| SciPy | Statistical tests, Voronoi tessellation |

#### Acknowledgments

**Aidana Almazbek kyzy** — designed and implemented the complete Phase 2 EDA pipeline including all data quality diagnostics, statistical analyses, visualizations, regression baselines, and the geospatial Houston map.

**Yu Zhu Ou** — implemented table analysis code in Phase 1 and contributed to report writing in both Phase 1 and Phase 2.

Supervised by **Prof. Carlos Ordonez**, University of Houston.
Claude (Anthropic) used as AI coding assistant.

<br><br>



<h2 align="center">🧠Training Phase🧠</h2>

### AQI Autoencoder + LSTM Training

This script trains an air quality prediction model using a Time-Variant Autoencoder and an LSTM.

The autoencoder compresses selected sensor/weather features into a lower-dimensional latent representation. These latent features are then combined with lag features, cyclic time features, and binary features before being passed into the LSTM to predict future AQI values.

#### How to Run

Run the script normally from the terminal:

```bash
python3 fileName.py
```

Replace `fileName.py` with the actual name of the Python file.

Example:

```bash
python3 train_aqi_lstm.py
```

#### Required Imports / Libraries

This script uses the following Python libraries:

```python
logging
os
time
pickle
warnings
json
dataclasses
typing

numpy
pandas
matplotlib
torch
sklearn
```

Install the main external libraries with:

```bash
pip install numpy pandas matplotlib torch scikit-learn
```

#### Dataset Path

The dataset path is controlled in the `Config` class:

```python
all_data_path: str = "../datasets/all_features_training.csv"
```

Change this path if the dataset is stored somewhere else.

#### Main Parameters to Adjust

Most training settings are located inside the `Config` class.

##### Data Splitting

```python
val_size: float = 0.20
random_state: int = 10
```

- `val_size`: percentage of data used for validation.
- `random_state`: used for reproducibility.

##### Sequence Settings

```python
lookback: int = 24
horizon: int = 1
```

- `lookback`: number of previous time steps used as input.
- `horizon`: how many hours ahead the model predicts.

For example, `lookback = 24` means the LSTM uses the previous 24 hours of data.

##### Autoencoder Settings

```python
latent_dim: int = 8
ae_epochs: int = 50
ae_lr: float = 1e-3
ae_batch_size: int = 1048
```

- `latent_dim`: size of the compressed feature representation.
- `ae_epochs`: number of training epochs for the autoencoder.
- `ae_lr`: autoencoder learning rate.
- `ae_batch_size`: batch size used while training the autoencoder.

Increasing `latent_dim` keeps more information but may reduce compression.

##### LSTM Settings

```python
lstm_hidden: int = 96
lstm_layers: int = 1
lstm_dropout: float = 1e-4
lstm_epochs: int = 50
lstm_lr: float = .00099
patience: int = 5
lstm_batch_size = 4096
```

- `lstm_hidden`: number of hidden units in the LSTM.
- `lstm_layers`: number of LSTM layers.
- `lstm_dropout`: dropout used between LSTM layers.
- `lstm_epochs`: maximum number of LSTM training epochs.
- `lstm_lr`: LSTM learning rate.
- `patience`: early stopping patience.
- `lstm_batch_size`: batch size used for LSTM training.

##### Dropped Columns

Columns that are not needed are removed here:

```python
cols_to_drop: List[str] = field(default_factory=lambda: [
    'wind_speed_100m', 'month', 'day', 'hour',
    'day_of_week', 'day_of_year', 'month_sin', 'month_cos',
])
```

Add or remove columns from this list depending on which features should be excluded.

#### Output Files

Trained models and results are saved in:

```python
save_dir: str = "saved_models"
```

The script saves:

```text
saved_models/
├── lstm_ae16.pt
├── ae_16.pt
├── scalers.pkl
└── results_summary.csv
```

- `lstm_ae16.pt`: saved LSTM model, configuration, metrics, and loss curves.
- `ae_16.pt`: saved autoencoder weights.
- `scalers.pkl`: saved scaler object.
- `results_summary.csv`: RMSE, MAE, and R² results.

#### Code Structure

The code is organized into specific classes and methods for readability:

- `Config`: stores file paths and training parameters.
- `AQI_LSTM`: defines the LSTM model.
- `TimeVariantAutoencoder`: defines the autoencoder model.
- `AIQTrainingPipeline`: loads, cleans, splits, scales, and prepares the data.
- `AEReducer`: trains the autoencoder and creates latent features.
- `LSTMTrainer`: trains, predicts, and evaluates the LSTM.
- `EarlyStopping`: stops training when validation loss stops improving.
- `save_artifacts`: saves the trained models, scaler, and results.
- `run`: controls the full training pipeline.

Comments are included throughout the script to explain how the code works, especially for feature preparation, scaling, sequence creation, and model training.

#### Training Flow

The script follows this general process:

1. Load the dataset.
2. Remove missing values.
3. Drop unneeded columns.
4. Identify feature types:
   - cyclic features
   - binary features
   - lag features
   - regular sensor/weather features
5. Split data by ZIP code while preserving time order.
6. Scale selected features.
7. Train the autoencoder.
8. Extract latent features from the autoencoder.
9. Combine latent, lag, cyclic, and binary features.
10. Create LSTM time sequences.
11. Train the LSTM.
12. Evaluate the model.
13. Save the trained models and results.

#### Notes

The model automatically uses CUDA if a GPU is available:

```python
torch.device("cuda" if torch.cuda.is_available() else "cpu")
```

Otherwise, it runs on CPU.

<br><br>



<h2 align="center">🔧Validation Phase🔧</h2>

**Authors:** Alfredo Hernandez, Jose Perla  
**Project:** Houston Area Air Quality Index (AQI) Forecasting — Phase 2 Validation

---

### Overview

This repository covers the **validation phase** of a machine learning pipeline that forecasts Air Quality Index (AQI) values across Houston-area ZIP codes. The core model is an **Autoencoder + LSTM (AE+LSTM)** neural network that learns temporal AQI patterns from hourly environmental sensor data and predicts AQI one hour ahead.

The validation work documented here answers five questions:

1. Does the AE+LSTM beat a naive mean baseline on unseen data?
2. How does it compare against traditional machine learning models?
3. Does error vary by ZIP code or by timestamp?
4. Which features most influence AQI predictions?
5. Does the Autoencoder component actually help the LSTM?


---

### Hardware & Environment

The notebook was run locally on the following machine:

| Component | Specification |
|---|---|
| Operating System | Windows |
| CPU | Intel Core i7-14900F |
| RAM | 32 GB DDR5 |
| GPU | NVIDIA RTX 5070 (12 GB VRAM) |
| Python Version | Python 3 |
| Execution Environment | Jupyter Notebook |
| Main Libraries | PyTorch, NumPy, pandas, scikit-learn, matplotlib, scipy, statsmodels |
| Notebook File | `final_aiq_validation.ipynb` |

> PyTorch will automatically use the GPU (CUDA) if available. No manual device configuration is needed beyond having the correct CUDA drivers installed for your RTX 5070.

---

### Project Structure

```
AQ_ML_Pipeline/
├── data/
│   ├── all_features_training.csv       # Training dataset (95,448 rows, 185 columns)
│   └── all_features_validation.csv     # Unseen external validation dataset (25,608 rows, 185 columns)
├── models/                             # Saved model artifacts (weights, scalers, configs)
├── runs/
│   └── plots/                          # All generated figures
├── tex/                                # CSV outputs for report tables
├── py/                                 # Shared Python utility scripts
└── final_aiq_validation.ipynb          # Main validation notebook (this phase)
```

---

### Data Description

#### Training Dataset (`all_features_training.csv`)

- **Raw shape:** 95,448 rows × 185 columns
- **After missing-value removal:** 93,120 rows
- **Coverage:** Hourly AQI and environmental readings across 97 Houston-area ZIP codes
- **Target column:** `us_aqi` — the US Air Quality Index value

#### External Validation Dataset (`all_features_validation.csv`)

- **Raw shape:** 25,608 rows × 185 columns
- **After alignment and missing-value removal:** 23,280 rows
- **Validation window:** `2026-04-07 00:00:00` to `2026-04-15 23:00:00`
- **Final sequence tensor shape:** `(20,952, 24, 57)` — meaning 20,952 forecastable sequences, each with a 24-hour lookback window and 57 input features per timestep

#### Key Feature Groups

The pipeline organizes the 185 raw columns into four groups:

| Group | Description |
|---|---|
| **Sensor/Environmental features** | 21 columns used as Autoencoder input (PM2.5, ozone, temperature, humidity, cloud cover, etc.) |
| **Lag features** | Recent historical AQI, PM2.5, and ozone values: 24 AQI lags (`us_aqi_past_1` through `us_aqi_past_24`), 8 PM2.5 lags, 8 ozone lags |
| **Cyclic features** | Sine/cosine encodings of time (hour, day-of-week, etc.) |
| **Binary features** | Indicator variables for categorical conditions |

#### Dropped (Uninformative) Columns

The following columns are removed before training and validation:

```
wind_speed_100m, month, day, hour, day_of_week,
day_of_year, month_sin, month_cos
```

#### Distribution Shift (Train vs. Validation)

The training and external validation periods are not identical. Key differences:

| Feature | Training Mean | Validation Mean |
|---|---|---|
| PM2.5 | 9.22 | 7.73 |
| Ozone | 76.65 | 85.33 |
| Cloud Cover | 49.13 | 67.95 |
| AQI (`us_aqi`) | 43.36 | 41.30 |

This shift is expected because the validation set covers different calendar dates. The models must generalize despite these environmental differences.

---

### Model Architecture

#### Autoencoder (AE)

The Autoencoder is a `TimeVariantAutoencoder` that compresses the 21 selected sensor/environmental features into a lower-dimensional latent representation before passing them to the LSTM.

- **Input:** 21 environmental feature columns (scaled with `StandardScaler`)
- **Latent dimension:** 8
- **Architecture layers:** `[16, 4]` encoder → `[8]` bottleneck
- **Training:** 50 epochs, Adam optimizer, learning rate `1e-3`, batch size 1048, MSE loss
- **Purpose:** Reduce noise and dimensionality from the sensor inputs before temporal modeling

#### LSTM

The `AQI_LSTM` receives a concatenation of:
- The AE latent representation (8 dimensions)
- Lag features (24 AQI + 8 PM2.5 + 8 ozone = 40 lag features)
- Binary features

Each input sequence covers the **previous 24 hourly timesteps** (`lookback = 24`) and predicts AQI **1 hour ahead** (`horizon = 1`).

- **Total input features per timestep:** 57
- **Hidden size:** configurable (see `Config` in the notebook)
- **Early stopping:** patience of 10 epochs on validation loss

#### Configuration (from `Config` dataclass)

```python
val_size       = 0.20       # 20% of training data used for internal validation split
random_state   = 10
lookback       = 24         # Hours of history per sequence
horizon        = 1          # Hours ahead to predict
latent_dim     = 8          # AE bottleneck size
ae_epochs      = 50
ae_lr          = 1e-3
ae_batch_size  = 1048
```

---

### How to Replicate

#### Step 1: Install Dependencies

```bash
pip install torch torchvision numpy pandas scikit-learn matplotlib scipy statsmodels
```

For GPU acceleration on your RTX 5070, install the CUDA-enabled version of PyTorch from [pytorch.org](https://pytorch.org/get-started/locally/) matching your CUDA version.

#### Step 2: Prepare the Data

Place the two CSV files in the `data/` folder:

```
data/all_features_training.csv
data/all_features_validation.csv
```

Both files must have identical column structure (185 columns). The notebook will automatically detect the project root by looking for the presence of the `data/`, `models/`, `py/`, `runs/`, and `tex/` directories.

#### Step 3: Run the Notebook

Open `final_aiq_validation.ipynb` in Jupyter Notebook and run all cells top-to-bottom. The notebook is organized into 13 sections:

| Section | What it does |
|---|---|
| 1. Imports & Paths | Loads all libraries and detects project root |
| 2. Validation Helpers | Defines metric functions and output folders |
| 3. Configure Paths | Sets training/validation CSV paths and model save directory |
| 4. Train Final Model | Trains `aqi_model_3` (AE + LSTM) on the training dataset |
| 5. Prepare Unseen Data | Aligns the external validation CSV to match training preprocessing |
| 6. External Validation | Evaluates AE+LSTM on the unseen dataset; compares to mean baseline |
| 7. Grouped Validation | Breaks down error by ZIP code and by timestamp |
| 8. Traditional ML Baselines | Trains and evaluates Ridge, ElasticNet, GBM, RF, Linear, KNN |
| 9. Dataset Diagnostics | Checks missing values, duplicates, and distribution shift |
| 10. Permutation Importance | Identifies which features drive the best traditional model |
| 11. AE Usefulness Test | Compares AE+LSTM vs. raw-feature LSTM to validate the AE component |
| 12. Consensus Feature Summary | Combines Spearman correlation, model importance, and permutation importance |
| 13. Final Written Summary | Prints a text summary of all results |

#### Step 4: Outputs

After running the notebook, results are saved to:

- `runs/plots/` — all figures (time-series plots, RMSE comparisons, permutation importance charts, residual plots)
- `tex/` — CSV files with metric tables for use in reports

---

### Validation Results Summary

#### AE+LSTM vs. Mean Baseline (External)

| Model | RMSE | MAE | R² |
|---|---|---|---|
| AE+LSTM | 3.1080 | 1.8214 | 0.9094 |
| Mean Baseline | 10.3837 | 6.8134 | -0.0109 |

The neural model clearly learns meaningful AQI patterns and outperforms a naive average prediction.

#### All Models Ranked by External RMSE

| Model | External RMSE | External MAE | External R² |
|---|---|---|---|
| Ridge Regression | 1.1392 | 0.6143 | 0.9873 |
| ElasticNet | 1.1516 | 0.6081 | 0.9870 |
| Gradient Boosting | 1.4164 | 0.7573 | 0.9803 |
| Random Forest | 1.6100 | 0.8176 | 0.9746 |
| Linear Regression | 2.8050 | 1.6317 | 0.9229 |
| **AE+LSTM** | **3.1080** | **1.8214** | **0.9094** |
| KNN | 5.0319 | 3.2281 | 0.7518 |
| Mean Baseline | 10.3837 | 6.8134 | -0.0109 |

Traditional models outperform the neural model on this dataset. This is expected: the strong lag features (recent AQI history) are highly linear and are used efficiently by regression-based methods without needing sequence modeling.

#### AE Usefulness (AE+LSTM vs. Raw LSTM)

| Model | External RMSE | External R² |
|---|---|---|
| AE+LSTM | 3.1080 | 0.9094 |
| Raw-Feature LSTM | 3.3172 | 0.8968 |

The Autoencoder provides a modest but consistent improvement, confirming that feature compression reduces noise before temporal modeling.

#### Geographic Performance (ZIP Code)

Best performing ZIP codes (lowest RMSE): `77073`, `77032`, `77060`, `77093`, `77037` (RMSE ≈ 1.81–1.87, R² ≈ 0.945–0.949)

Worst performing ZIP codes: `77067`, `77018`, `77086`, `77092`, `77040` (RMSE ≈ 4.01–4.03, R² ≈ 0.888–0.889)

Even the worst ZIP codes remained substantially better than the mean baseline.

#### Timestamp Performance

- **Best timestamp:** `2026-04-12 01:00` (RMSE = 0.1678, MAE = 0.1381)
- **Worst timestamp:** `2026-04-08 19:00` (RMSE = 14.2509, R² = -4.04)

Evening hours in the first days of the validation window showed the highest errors, likely due to short-term pollution events or distribution shifts not seen during training.

#### Feature Importance

Across all methods (Spearman correlation, permutation importance, model-based importance), the dominant predictors were recent AQI lag features:

1. `us_aqi_past_1` (most recent previous hour)
2. `us_aqi_past_2`
3. `us_aqi_past_3`
4. `us_aqi_past_4`
5. Later AQI lags, followed by ozone and PM2.5 lag features

**Takeaway:** AQI forecasting on this dataset is strongly time-autoregressive. Careful lag design matters more than adding more environmental features.

---

### Key Definitions

**Internal Validation** — Evaluation on a held-out split of the training dataset (20%). Shows whether the model learned the training distribution but does not prove generalization.

**External Validation** — Evaluation on a completely separate, unseen dataset not touched during training. This is the primary validity test.

**RMSE (Root Mean Squared Error)** — Square root of the average squared prediction error. Penalizes large errors more heavily. Lower is better.

**MAE (Mean Absolute Error)** — Average absolute difference between predicted and actual AQI in AQI units. Lower is better.

**R² (R-squared)** — Proportion of AQI variance explained by the model. 1.0 is perfect; values near 0 or negative mean the model is no better than predicting the mean.

**Permutation Importance** — How much a model's error increases when one feature is randomly shuffled. High importance = the model relies heavily on that feature.

**Distribution Shift** — Statistical differences between the training data period and the validation data period (e.g., different seasonal conditions, pollution events).

---

### Important Notes for Replication

- The unseen validation dataset **must be processed using the exact same column drops, feature selection, scaling assumptions, lag windows, and 24-hour sequence format as the training dataset.** Any deviation will cause a feature mismatch and invalidate the evaluation.
- Missing values in lag columns are expected and normal — lag features cannot be computed for the first rows of each ZIP code's time series.
- The notebook auto-detects the project root. If it fails, verify that the folders `data/`, `models/`, `py/`, `runs/`, and `tex/` all exist at the same level as the notebook.
- Training the AE+LSTM and running all 13 sections takes roughly 20–40 minutes on the hardware listed above. Runtime will vary significantly on CPU-only machines.

---

### Authors & Contributions

- **Alfredo Hernandez** — Validation pipeline implementation, external validation evaluation, results analysis
- **Jose Perla** — Feature importance analysis, report writing, lag feature significance interpretation


