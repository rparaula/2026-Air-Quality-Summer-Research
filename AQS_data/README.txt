Greater Houston EPA AQS Data Ingestion Pipeline
===============================================

This folder contains a three-part EPA AQS ingestion pipeline for replacing or
cross-checking the Open-Meteo air-quality data with observed monitor-station
measurements.

Greater Houston Definition
--------------------------

The AQS monitor inventory step defines Greater Houston as monitor stations in
these Texas counties:

- Austin
- Brazoria
- Chambers
- Fort Bend
- Galveston
- Harris
- Liberty
- Montgomery
- Waller

The pollutant columns are aligned to the pollutant variables used in collect.py:

- pm2_5
- pm10
- carbon_monoxide
- nitrogen_dioxide
- sulphur_dioxide
- ozone

Credentials
-----------

The EPA AQS API requires credentials. Set them in PowerShell before running the
first two scripts:

    $env:AQS_EMAIL="your.email@example.com"
    $env:AQS_KEY="your_aqs_key"

You can also pass credentials directly with --email and --key.


Part 1: Monitor Inventory
-------------------------

Script:

    AQS_data\collect_AQS_monitors.py

Purpose:

    Finds every AQS monitor station in the Greater Houston counties that can
    measure at least one tracked pollutant. The output is one row per physical
    monitor site, with location metadata and boolean measures_* capability
    columns.

Main output folder:

    AQS_data\monitor_locations

Example:

    .\.venv\Scripts\python.exe .\AQS_data\collect_AQS_monitors.py `
      --start-date 2026-01-01 `
      --end-date 2026-05-22 `
      --active-as-of 2026-05-22 `
      --out-dir "AQS_data\monitor_locations"

Important output columns:

    monitor_site_id, state_code, county_code, county_name, site_number,
    site_name, latitude, longitude, address, city, open_date, close_date,
    active, measures_pm2_5, measures_pm10, measures_ozone,
    measures_carbon_monoxide, measures_nitrogen_dioxide,
    measures_sulphur_dioxide, available_parameter_codes


Part 2: Hourly Monitor Concentrations
-------------------------------------

Script:

    AQS_data\AQS_concentrations.py

Purpose:

    Reads a monitor inventory CSV from Part 1, then queries hourly AQS
    sampleData/bySite readings for each station and pollutant it can measure.
    The output is one row per monitor station per hour. Pollutants that a
    station cannot measure, or hours with no reported AQS reading, are left as
    blank/NaN.

Main output folder:

    AQS_data\air_data

Example:

    .\.venv\Scripts\python.exe .\AQS_data\AQS_concentrations.py `
      --start-date 2026-02-22 `
      --end-date 2026-03-28 `
      --active-only `
      --out-dir "AQS_data\air_data"

By default, the script uses the newest CSV matching:

    AQS_data\monitor_locations\*_aqs_monitor_locations_*.csv

To use a specific inventory:

    --monitor-csv "AQS_data\monitor_locations\greater_houston_aqs_monitor_locations_YYYYMMDD_HHMMSS.csv"

Important output columns:

    monitor_site_id, state_code, county_code, county_name, site_number,
    site_name, latitude, longitude, time, date_local, time_local,
    pm2_5, pm10, carbon_monoxide, nitrogen_dioxide, sulphur_dioxide, ozone


Part 3: Map AQS Monitors to 2x2 Grid Centroids
----------------------------------------------

Script:

    AQS_data\mapping_AQS_to_2x2centroids.py

Purpose:

    Reads the 2x2 Houston grid centroid file and the hourly AQS concentration
    CSV from Part 2. Each centroid is assigned to its nearest AQS monitor
    station by haversine distance. The script then emits one row per centroid
    per timestamp, using the pollutant concentrations from that centroid's
    assigned monitor station.

Main output folder:

    new_data

Default grid input:

    static data\houston_grid_centroids_2x2.csv

Example:

    .\.venv\Scripts\python.exe .\AQS_data\mapping_AQS_to_2x2centroids.py `
      --start-date 2026-02-22 `
      --end-date 2026-03-28 `
      --out-dir "new_data"

By default, the script uses the newest CSV matching:

    AQS_data\air_data\*_aqs_concentrations_hourly_*.csv

To use a specific concentration file:

    --aqs-concentrations "AQS_data\air_data\greater_houston_aqs_concentrations_hourly_YYYYMMDD_YYYYMMDD_TIMESTAMP.csv"

Important output columns:

    city, state, zip, latitude, longitude, time, grid_id, grid_row, grid_col,
    cell_size_miles, assigned_monitor_site_id, assigned_monitor_site_name,
    assigned_monitor_distance_miles, assigned_monitor_latitude,
    assigned_monitor_longitude, pm10, pm2_5, carbon_monoxide,
    nitrogen_dioxide, sulphur_dioxide, ozone


Recommended Run Order
---------------------

1. Build or refresh the monitor inventory:

    .\.venv\Scripts\python.exe .\AQS_data\collect_AQS_monitors.py `
      --start-date 2026-01-01 `
      --end-date 2026-05-22 `
      --active-as-of 2026-05-22 `
      --out-dir "AQS_data\monitor_locations"

2. Download hourly station concentrations:

    .\.venv\Scripts\python.exe .\AQS_data\AQS_concentrations.py `
      --start-date 2026-02-22 `
      --end-date 2026-03-28 `
      --active-only `
      --out-dir "AQS_data\air_data"

3. Map station readings to 2x2 centroids:

    .\.venv\Scripts\python.exe .\AQS_data\mapping_AQS_to_2x2centroids.py `
      --start-date 2026-02-22 `
      --end-date 2026-03-28 `
      --out-dir "new_data"


Preprocessing Notes
-------------------

The mapped output intentionally includes the Open-Meteo-style air pollutant
columns used by the existing preprocessing stage. The columns us_aqi,
uv_index_clear_sky, uv_index, dust, and aerosol_optical_depth are included as
blank placeholders because EPA AQS monitor data does not provide those
Open-Meteo-derived fields.

The mapped file also preserves grid_id, grid_row, and grid_col. Because multiple
2x2 centroids can share the same ZIP code, grid-aware preprocessing should use
grid_id,time as the preferred key. The older ZIP-level preprocessing path uses
zip,time, which can collapse multiple centroids in the same ZIP.


Common Debug Options
--------------------

Limit Part 2 to one monitor site:

    --site-ids 48-201-0024

Limit Part 2 to the first N monitor sites:

    --max-sites 3

Limit Part 3 to the first N centroids:

    --max-centroids 10

Save raw AQS API rows from Part 2:

    --save-raw

Use faster smoke-test requests during small tests:

    --request-delay 0

For full EPA AQS runs, keep the default request delay. EPA asks users to stay
within 10 requests per minute.
