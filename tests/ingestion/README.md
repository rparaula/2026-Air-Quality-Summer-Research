# tests/ingestion

This folder validates ingestion-side behavior for ZIP centroid lookup, API response parsing, ingestion state handling, and CSV output validation.

## Script-to-method mapping

| Script | Methods/classes validated | Origin script |
|---|---|---|
| `test_API_parsing.py` | `response_to_dataframe` | `collect.py` |
| `test_zip_centroids.py` | `get_zip_centroids` | `collect.py` |
| `test_state.py` | `load_state`, `save_state`, `mark_in_progress`, `clear_in_progress`, `compute_window` | `state.py` |
| `validate_CSVs.py` | `latest_file`, `validate_csv`, `main` (validation utility; not a pytest suite) | `tests/ingestion/validate_CSVs.py` |

## What each script checks

- `test_API_parsing.py`: ensures hourly API-like responses are converted into the expected DataFrame schema, values, and time dtype.
- `test_zip_centroids.py`: confirms ZIP centroid extraction filters by city/state, removes duplicates, and drops invalid coordinate rows.
- `test_state.py`: verifies ingestion state lifecycle behavior (defaults, persistence, in-progress lock behavior, and ingest window computation).
- `validate_CSVs.py`: validates latest ingestion CSV outputs against required schema, numeric coercion, duplicate key checks, and timestamp integrity.
