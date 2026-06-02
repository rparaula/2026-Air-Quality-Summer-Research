# benchmarks

This folder contains performance and storage benchmark scripts for the ingestion + preprocessing workflow.

## Benchmark scripts

| Script | What it measures | How metrics are captured |
|---|---|---|
| `ingestion_preprocessing_timer.py` | End-to-end timing for CSV pipeline stages using repo data (`merge_data_into_master_file.py` -> `strip_tz_info.py` -> `preprocessing.py`) | Uses `time.perf_counter()` around each subprocess call (`run_step`) to capture per-stage durations and total run time; records output file sizes (MiB) from filesystem stats; optionally pulls internal preprocessing summary JSON from pipeline metadata; aggregates min/median/max/mean across runs |
| `smoke_timer.py` | Small synthetic smoke benchmark for the same pipeline on generated tiny CSV inputs | Generates synthetic input rows, runs the same stage sequence, times each stage with `time.perf_counter()`, also captures per-run wall-clock time, output row counts, output sizes, and internal preprocessing summary; writes per-run details and aggregate min/median/max/mean statistics |
| `storage_benchmark.py` | Storage footprint comparison of CSV vs Parquet outputs | Reads each CSV with pandas, writes Parquet with configurable compression, then computes per-file and overall size metrics from file byte sizes (MiB), compression ratio (`csv/parquet`), and percent reduction |

## Output artifacts

- `benchmarks/results/`: persistent benchmark reports (JSON).
- `benchmarks/runs/`: generated run directories for smoke benchmarks when run retention is enabled.

## Common metric definitions

- Step duration: elapsed seconds for an individual stage command.
- Total timed seconds: sum of tracked stage durations in one run.
- Wall-clock seconds: full run elapsed time, including orchestration overhead.
- Output size (MiB): file size converted from bytes using `bytes / (1024 * 1024)`.
- Compression ratio: `csv_size / parquet_size`.
- Percent reduction: `100 * (1 - parquet_size / csv_size)`.
