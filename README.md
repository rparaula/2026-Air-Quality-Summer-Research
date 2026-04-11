Basically, how this all works is that we aim for data to be pulled from:
  - [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)  (PM2.5, PM10, AQI, CO, NO2, ozone, etc.)
  - [Open-Meteo Weather API](https://open-meteo.com/en/docs)      (temperature, humidity, wind, precipitation, etc.)
  - [EPA FRS API](https://www.epa.gov/frs/frs-api)                 (nearby industrial pollution sources by ZIP)
  - [US Census ACS API](https://www.census.gov/programs-surveys/acs/data/data-via-api.html)           (population by ZIP code)
  - [OSMnx](https://osmnx.readthedocs.io/en/stable/) / [OpenStreetMap](https://www.openstreetmap.org)       (road density by ZIP code)
  - TRI text files              (Toxics Release Inventory, processed separately)

All output CSVs are saved to the data/ folder.
Each run is logged to data/pipeline_metadata.json for tracking and deduplication.


FILES AT A GLANCE
-----------------
`run_pipeline.py`          
- Main entry point. Orchestrates everything. Run this.

`collect.py`            
- Fetches hourly air quality + weather data via Open-Meteo.

`collect_population.py`    
- Pulls population data by ZIP from the US Census API.

`dump_pollution_sources.py` 
- Queries EPA FRS for industrial facilities by ZIP code.

`dump_zip_road_density.py` 
- Computes road density per ZIP using OpenStreetMap.

`process_tri_data.py`
- Processes TRI toxic release inventory text files.

`metadata_tracker.py`      
- Logs metadata for every pipeline run (timing, files, etc.)

`state.py`                 
- Tracks the last collected date so runs stay incremental.

`uszips.csv`               
- ZIP code reference data (from [simplemaps.com](https://simplemaps.com/data/us-zips)).

`houston_zips.csv`         
- List of Houston ZIP codes used for population collection.


HOW TO RUN
----------
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

3. To manually collect a specific date range:
       `python collect.py --cities "Houston,TX" --start-date 2025-01-01 --end-date 2025-01-07`

4. To regenerate static dimension tables only:
       `python run_pipeline.py --refresh-static`


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


NOTES
-----
- The uszips.csv file is required. Download it from: https://simplemaps.com/data/us-zips
- TRI data (process_tri_data.py) requires manually downloaded TXT files from the
  EPA TRI website. Expected files: US_1a_2023.txt, US_1a_2024.txt
- The Open-Meteo forecast endpoint is used (covers recent history + today).
  There is roughly a 5-day lag on the archive endpoint if you switch to that.

================================================================================
