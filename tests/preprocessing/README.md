# tests/preprocessing

This folder tests preprocessing pipeline logic, including sorting/merge behavior, feature engineering, filtering, lag/state tracking, timezone cleanup, and geospatial ZIP filtering.

## Script-to-method mapping

| Script | Methods/classes validated | Origin script |
|---|---|---|
| `smoke_test.py` | `main` (end-to-end orchestration), mocked contract for `build_spatial_zip_lookup` | `preprocessing/preprocessing.py` |
| `test_helpers.py` | `parse_csv_list`, `detect_column`, `standardize_time_value`, `row_key`, `detect_direction_columns` | `preprocessing/preprocessing.py` |
| `test_feature_engineering.py` | `resolve_blend_weight`, `compute_wind_vector`, `add_direction_features_to_row`, `add_time_features_to_row`, `add_spatial_scores_to_row` | `preprocessing/preprocessing.py` |
| `test_filters.py` | `OnlineVariance`, `CardinalityTracker`, `IOTracker`, `apply_variance_filter_csv`, `apply_low_cardinality_filter_csv` | `preprocessing/preprocessing.py` |
| `test_state_tracker.py` | `LagState`, `OnlineVariance`, `CardinalityTracker`, `IOTracker` | `preprocessing/preprocessing.py` |
| `test_merge.py` | `merge_rows_full_outer`, `build_output_fieldnames` | `preprocessing/preprocessing.py` |
| `test_integration.py` | `make_sorted_runs_collect_keys`, `SortedRunStream`, `RunReader`, `stream_merge_join_and_transform`, plus tracker classes used in integration context | `preprocessing/preprocessing.py` |
| `test_filter_houston_zcta.py` | `normalize_zip`, `filter_shapefile_by_dataset_zips` | `preprocessing/filter_houston_zcta.py` |
| `test_merge_data_into_master_file.py` | `load_state`, `save_state`, `classify_file`, `extract_sort_key`, `find_files`, `append_csvs`, `process_category`, `read_header`, `rebuild_master` | `preprocessing/merge_data_into_master_file.py` |
| `test_strip_tz_info.py` | `strip_after_second`, `find_time_col_index`, `process_csv` | `preprocessing/strip_tz_info.py` |

## Coverage summary by theme

- Merge and ordering: chunked sorted-run generation, stream merge correctness, full outer behavior, and output field ordering.
- Feature engineering: directional trigonometric features, time/cyclic features, wind-vector blending, and spatial impact scoring.
- Data quality filters: normalized variance filtering, low-cardinality filtering, and IO accounting.
- Stateful transforms: lag feature generation per ZIP and online statistics/cardinality tracking.
- File-level utilities: timezone stripping in CSV timestamps and master-file rebuild/append state logic.
- Geospatial support: ZIP normalization and shapefile filtering against dataset ZIP coverage.
