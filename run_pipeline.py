import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from state import compute_window, save_state
from metadata_tracker import PipelineRunTracker


def build_prefix(start: str, end: str) -> str:
    """Build a human-readable CSV prefix from the collection window.
    e.g. 'march_14' for a single day, 'march_13_14' for two days."""
    s = date.fromisoformat(start)    
    e = date.fromisoformat(end)   
    month = s.strftime("%B").lower()   
    if s == e:
        return f"{month}_{s.day}"
    elif s.month == e.month:
        return f"{month}_{s.day}_{e.day}"
    else:
        return f"{s.strftime('%B').lower()}_{s.day}_{e.strftime('%B').lower()}_{e.day}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Incremental AQ pipeline orchestrator")
    p.add_argument("--cities",        default="Houston,TX",  help="Semicolon-separated City,ST pairs")
    p.add_argument("--out-dir",       default="data",        help="Output directory for CSVs")
    p.add_argument("--out-prefix",    default="aq",          help="Prefix for output CSV filenames")
    p.add_argument("--timezone",      default="America/Chicago")
    p.add_argument("--batch-size",    type=int, default=50)
    p.add_argument("--uszips",        default="uszips.csv")
    p.add_argument("--zip-traffic",   default=None,          help="Optional road-density CSV to join")
    p.add_argument("--refresh-static", dest="refresh_static", action="store_true",
                   help="Also regenerate static dimension tables (slow)")
    p.add_argument("--start-date",     default=None,
                   help="Backfill start date (YYYY-MM-DD). Required with --per-day.")
    p.add_argument("--per-day",        action="store_true",
                   help="Collect one CSV per calendar day from --start-date to the archive limit (yesterday).")
    return p.parse_args()


def run(cmd: list[str], label: str) -> None:
    # Run a subprocess command and print its output, raising an error if it fails
    print(f"\n[pipeline] Starting : {label}")
    print(f"Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running {label}: {result.stderr}")
        sys.exit(result.returncode)
    print(f"{label} output:\n{result.stdout}")
    
    
def _collect_day(python: str, args, out_dir: "Path", day_str: str) -> None:
    """Run collect.py for a single day and report result. Does not update state."""
    prefix = build_prefix(day_str, day_str)
    if PipelineRunTracker.is_window_already_collected(out_dir, day_str, day_str):
        print(f"[pipeline] {day_str} already collected — skipping.")
        return
    collect_cmd = [
        python, "collect.py",
        "--start-date", day_str,
        "--end-date",   day_str,
        "--cities",     args.cities,
        "--out-dir",    args.out_dir,
        "--out-prefix", prefix,
        "--timezone",   args.timezone,
        "--batch-size", str(args.batch_size),
        "--uszips",     args.uszips,
    ]
    if args.zip_traffic:
        collect_cmd += ["--zip-traffic", args.zip_traffic]
    run(collect_cmd, f"collect.py ({day_str})")


def main():
    args = parse_args()
    python = sys.executable # reusing the same venv that used for this script
    out_dir = Path(args.out_dir)

    #Per-day backfill mode
    if args.per_day:
        if not args.start_date:
            print("[pipeline] --per-day requires --start-date (YYYY-MM-DD).")
            sys.exit(1)
        backfill_start = date.fromisoformat(args.start_date)
        archive_limit  = date.today() - timedelta(days=5)  # Open Meteo archive API is ~5 days behind today
        if backfill_start > archive_limit:
            print(f"[pipeline] --start-date {args.start_date} is beyond the archive limit ({archive_limit}). Nothing to do.")
            return
        current = backfill_start
        while current <= archive_limit:
            _collect_day(python, args, out_dir, current.isoformat())
            current += timedelta(days=1)
        print(f"[pipeline] Per-day backfill complete: {args.start_date} → {archive_limit}.")
        return

    #Normal incremental mode
    # First, we have to compute the date window to fetch data for, based on our state
    start, end = compute_window()

    if  start is None:
        print("[pipeline] Already up to date — nothing to ingest.")
    else:
        print(f"[pipeline] Date window: {start}  →  {end}")
        current = date.fromisoformat(start)
        archive_end = date.fromisoformat(end)
        while current <= archive_end:
            day_str = current.isoformat()
            _collect_day(python, args, out_dir, day_str)
            save_state(day_str)
            print(f"[pipeline] Checkpoint saved. Next run starts from {day_str} + 1 day.")
            current += timedelta(days=1)
        print(f"[pipeline] Incremental collect complete: {start} → {end}.")
        
        
    # ── Optional: static dimension tables (slow, run on demand) ─────────────
    # Video mentioned use of static dimension tables, not sure if this is correct though
    if args.refresh_static:
        run([python, "collect_population.py"],
            "collect_population.py")

        run([
            python, "dump_pollution_sources.py",
            "--state",   "TX",
            "--uszips",  args.uszips,
            "--out-dir", args.out_dir,
        ], "dump_pollution_sources.py")

        run([
            python, "dump_zip_road_density.py",
            "--cities",  args.cities,
            "--uszips",  args.uszips,
            "--out-dir", args.out_dir,
        ], "dump_zip_road_density.py")


if __name__ == "__main__":
    main()