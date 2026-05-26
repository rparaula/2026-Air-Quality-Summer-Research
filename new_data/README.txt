new_data Folder Data Dictionary
=============================

This folder contains four CSV datasets for the Houston 2x2 grid workflow.
Each dataset is hourly and tied to grid-centroid locations in Greater Houston.

Files in this folder
--------------------

1) om_grid_air_pollutants.csv

What this CSV represents:
- Open-Meteo air-quality variables for Houston 2x2 grid centroids.
- This is model/API-derived air-quality data sampled hourly at each centroid.

What each row represents:
- One centroid-hour observation.
- Practical key: city, state, zip, latitude, longitude, time.
- Pollutant/air columns include:
  us_aqi, pm10, pm2_5, carbon_monoxide, nitrogen_dioxide,
  sulphur_dioxide, ozone, uv_index_clear_sky, uv_index, dust,
  aerosol_optical_depth.

How this CSV was generated:
- Script: grid_collect.py
- Core fetch helper: fetch_and_save_csv(...) from collect.py
- Inputs:
  - static data/houston_grid_centroids_2x2.csv (grid centroids)
  - start/end date window
- Endpoint used:
  - Open-Meteo air-quality endpoint (paid customer endpoint if API key exists,
    otherwise free endpoint)
- Notes:
  - Time values are timezone-aware (America/Chicago offsets in the timestamp).


2) om_grid_weather.csv

What this CSV represents:
- Open-Meteo hourly weather variables for the same Houston 2x2 grid centroids.

What each row represents:
- One centroid-hour observation (same location-hour grain as the air CSV).
- Practical key: city, state, zip, latitude, longitude, time.
- Weather columns include:
  temperature_2m, relative_humidity_2m, precipitation,
  wind_speed_10m, wind_speed_100m,
  wind_direction_10m, wind_direction_100m,
  wind_gusts_10m, shortwave_radiation, diffuse_radiation, cloud_cover.

How this CSV was generated:
- Primary script: grid_collect.py (second pass after air pull)
- Core fetch helper: fetch_and_save_csv(...) from collect.py
- Endpoint used:
  - Open-Meteo archive weather endpoint
- Repair workflow (if weather collection was interrupted):
  - Script: grid_collect_resume_weather.py
  - Uses om_grid_air_pollutants.csv as expected location-hour coverage
  - Fetches only missing weather location-hour records and merges/deduplicates.


3) AQS_feb22_mar28.csv

What this CSV represents:
- EPA AQS monitor-based air pollutant readings mapped onto 2x2 Houston
  grid centroids using nearest-monitor assignment.
- This is a monitor-driven alternative to Open-Meteo air pollutants.

What each row represents:
- One centroid-hour row for each grid centroid and each hourly timestamp in the
  selected AQS time window.
- Includes centroid metadata plus assigned monitor metadata:
  assigned_monitor_site_id, assigned_monitor_site_name,
  assigned_monitor_distance_miles, assigned_monitor_latitude,
  assigned_monitor_longitude, assigned_monitor_county_code,
  assigned_monitor_county_name, assigned_monitor_site_number.
- Pollutant values (pm10, pm2_5, carbon_monoxide, nitrogen_dioxide,
  sulphur_dioxide, ozone) come from the assigned monitor at that hour.
- Open-Meteo-only columns (us_aqi, uv_index_clear_sky, uv_index, dust,
  aerosol_optical_depth) are placeholders and may be blank.

How this CSV was generated:
- Upstream station inventory: AQS_data/collect_AQS_monitors.py
- Upstream hourly concentrations: AQS_data/AQS_concentrations.py
- Mapping script: AQS_data/mapping_AQS_to_2x2centroids.py
- Mapping method:
  - Assign each centroid to nearest AQS monitor via haversine distance.
  - For each timestamp, join assigned monitor pollutant values into centroid-hour rows.
- File naming:
  - This filename indicates the filtered window 2026-02-22 through 2026-03-28,
    commonly produced via --output-file or by renaming a timestamped mapping output.


4) NOAA_GHCNh_feb22_mar28.csv

What this CSV represents:
- NOAA GHCNh station weather observations mapped onto 2x2 Houston grid
  centroids using nearest-station assignment.
- This is a station-driven weather alternative to Open-Meteo weather.

What each row represents:
- One centroid-hour row for each grid centroid and each hourly timestamp in the
  selected NOAA GHCNh window.
- Includes centroid metadata plus assigned station metadata:
  assigned_monitor_noaa_ghcnh_station_id, assigned_monitor_station_name,
  assigned_monitor_distance_miles, assigned_monitor_latitude,
  assigned_monitor_longitude, assigned_monitor_county_code,
  assigned_monitor_county_name.
- Weather values come from the assigned NOAA GHCNh station at that hour
  (or are blank if not available for that station-hour).

How this CSV was generated:
- Upstream station inventory: NOAA_GHCNh_data/collect_NOAA_GHCNh_monitors.py
- Upstream hourly weather variables: NOAA_GHCNh_data/NOAA_GHCNh_variables.py
- Mapping script: NOAA_GHCNh_data/mapping_NOAA_GHCNh_to_2x2centroids.py
- Mapping method:
  - Assign each centroid to nearest NOAA GHCNh station via haversine distance.
  - For each timestamp, join assigned station weather values into centroid-hour rows.
- File naming:
  - This filename indicates the filtered window 2026-02-22 through 2026-03-28,
    commonly produced via --output-file or by renaming a timestamped mapping output.


Time-format note
----------------
- Open-Meteo files in this folder use timezone-aware timestamps with UTC offsets.
- Mapped AQS output uses local naive hour strings (no offset suffix).
- Mapped NOAA_GHCNh output keeps offset-formatted time strings.
