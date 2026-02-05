#!/usr/bin/env python3
"""
Test runner: pass a ticker as the only argument to run ingestion + analysis and verify storage.

Usage (from project root, with PYTHONPATH=src or via .venv):
  python run_ticker.py AAPL
  python run_ticker.py GOOGL
  .venv/bin/python run_ticker.py MSFT

Runs:
  1. SEC EDGAR ingestion for the ticker (10-K, 10-Q) → facts Parquet under data/parquet/<ticker>/
  2. Common-size and flux analysis → CSV only under data/parquet/<ticker>/

Prints a short summary and the paths to the analysis CSVs so you can confirm data is stored correctly.
"""

import sys
from pathlib import Path

# Ensure project root and src on path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from commonsense.config import DATA_DIR, EDGAR_EMAIL
from commonsense.edgar.ingestion import run_ingestion
from commonsense.analysis import run_analysis_all


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_ticker.py <TICKER>", file=sys.stderr)
        print("Example: python run_ticker.py AAPL", file=sys.stderr)
        sys.exit(1)

    ticker = sys.argv[1].strip().upper()
    if not ticker:
        print("Error: provide a non-empty ticker (e.g. AAPL, GOOGL).", file=sys.stderr)
        sys.exit(1)

    if not EDGAR_EMAIL:
        print("Error: EDGAR_EMAIL is required (set in .env or environment).", file=sys.stderr)
        sys.exit(1)

    # 20-F included so foreign private issuers (e.g. NVO, BHP) get annual data
    forms = ["10-K", "10-Q", "20-F"]
    print(f"Running ingestion for {ticker} ({', '.join(forms)})...")
    ingest = run_ingestion(
        tickers=[ticker],
        forms=forms,
        output_dir=DATA_DIR,
        email=EDGAR_EMAIL,
    )
    print(f"  Tickers processed: {ingest['tickers_processed']}")
    print(f"  Filings: {ingest.get('filings_count', 0)}")
    print(f"  Files written: {len(ingest['files_written'])}")
    if ingest["errors"]:
        for e in ingest["errors"]:
            print(f"  Error: {e}")

    print("\nRunning common-size and flux analysis (CSV only)...")
    analysis = run_analysis_all(DATA_DIR, write_csv=True)
    print(f"  Companies processed: {analysis['companies_processed']}")
    print(f"  Analysis files written: {len(analysis['files_written'])}")
    if analysis["errors"]:
        for e in analysis["errors"]:
            print(f"  Error: {e}")

    # Show where data landed: prefer ticker subdir, else any subdir with analysis CSVs
    company_dir = DATA_DIR / ticker
    if not company_dir.is_dir():
        dirs_with_csv = [d for d in DATA_DIR.iterdir() if d.is_dir() and list(d.glob("*.csv"))]
        if dirs_with_csv:
            company_dir = dirs_with_csv[0]
            print(f"\nNote: ingestion wrote under {company_dir.name}/ (e.g. CIK resolved to ticker).")

    if company_dir.is_dir():
        csvs = sorted(company_dir.glob("*.csv"))
        print(f"\nAnalysis CSVs under {company_dir}:")
        for f in csvs:
            print(f"  {f}")
        if not csvs:
            print("  (no CSV files yet; check that fact Parquets exist in this directory)")
    else:
        print(f"\nNo company directory at {DATA_DIR / ticker}; check ingestion errors above.")

    print("\nDone.")


if __name__ == "__main__":
    main()
