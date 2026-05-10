Data Collection for the Air Quality Pipeline


OVERVIEW
--------
# Explain the goal of the project in 2-3 sentences.
# What question are we trying to answer? What is the ML task?


Basically, how this all works is that we aim for data to be pulled from:
  - [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)  (PM2.5, PM10, AQI, CO, NO2, ozone, etc.)
  - [Open-Meteo Weather API](https://open-meteo.com/en/docs)      (temperature, humidity, wind, precipitation, etc.)
  - [EPA FRS API](https://www.epa.gov/frs/frs-api)                 (nearby industrial pollution sources by ZIP)
  - [US Census ACS API](https://www.census.gov/programs-surveys/acs/data/data-via-api.html)           (population by ZIP code)
  - [OSMnx](https://osmnx.readthedocs.io/en/stable/) / [OpenStreetMap](https://www.openstreetmap.org)       (road density by ZIP code)
  - TRI text files              (Toxics Release Inventory, processed separately)

All output CSVs are saved to the data/ folder.
Each run is logged to data/pipeline_metadata.json for tracking and deduplication.


PREREQUISITES
-------------

- Python 3.10+ is required
- uszips.csv must be manually downloaded from (https://simplemaps.com/data/us-zips) and placed within the root folder before running anything.
- TRI csv and json files from (Ask Tabriz for source) into project folder
- OpenMeteo API Key may be needed if using a large data range when using the backfill mode; however, it is not required. API key access is managed through github secrets..



INSTALLATION
------------

1. Clone the repo: 
       - git clone https://github.com/GlowSand/AQ_ML_Pipeline.git
       - cd AQ_ML_Pipelinne
2. (optional but recommended) Create a virtual environment:
       - python -m venv .venv
       .venv\Scripts\activate

3. Install dependencies:
       pip install -r requirements.txt



PROJECT STRUCTURE
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





HOW TO RUN
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




HOW IT WORKS
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


HOW INCREMENTAL COLLECTION WORKS
---------------------------------
`state.py` stores the last successfully collected end date in `data/pipeline_state.json.`
On each run, `run_pipeline.py` automatically figures out what date window is missing
and only fetches new data. Already-collected windows are skipped (checked via metadata).
If a run crashes mid-way, the next run will detect the incomplete state and retry.


OUTPUT FILES
------------

`data/<prefix>_air_quality_hourly_<timestamp>.csv`   Hourly AQ data per ZIP centroid

`data/<prefix>_weather_hourly_<timestamp>.csv`        Hourly weather data per ZIP centroid

`data/population_density.csv`                         Population per ZIP (static)

`data/zip_pollution_sources_<state>_<timestamp>.csv`  EPA facilities per ZIP (static)

`data/zip_road_density_<timestamp>.csv`               Road density per ZIP (static)

`data/pipeline_metadata.json`                         Log of every pipeline run


KNOWN LIMITATIONS / NOTES
--------------------------


- The uszips.csv file is required. Download it from: https://simplemaps.com/data/us-zips
- TRI data (process_tri_data.py) requires manually downloaded TXT files from the
  EPA TRI website. Expected files: US_1a_2023.txt, US_1a_2024.txt
- The Open-Meteo forecast endpoint is used (covers recent history + today).
  There is roughly a 5-day lag on the archive endpoint if you switch to that.


 
