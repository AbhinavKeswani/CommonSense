#!/usr/bin/env python3
from __future__ import annotations

"""
Fetch and save a sample SEC filing exactly the way the MD&A pipeline does.

Run_ticker often works because it can skip the filing *index* page: it gets the
primary document filename from data.sec.gov (submissions API), then fetches
that one URL from sec.gov/Archives. The sample script now does the same first:
try submissions API -> direct document URL. If that fails, it falls back to
fetching the index (which sometimes returns 503).

Usage (from project root, with PYTHONPATH=src or .venv):
  python scripts/fetch_sample_filing.py AMZN
  python scripts/fetch_sample_filing.py AAPL
  python scripts/fetch_sample_filing.py 320193 0000320193-24-000106 10-K

  With one ticker (e.g. AMZN): uses the same path as run_ticker — submissions API
  then direct document URL. No index request, so no 503 on the index.

Output:
  - Prints the exact URLs used (submissions API and/or index + document(s)).
  - Saves files under data/sample_filing/<accession>/ so you can open them locally.
"""

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from commonsense.config import EDGAR_EMAIL
from commonsense.edgar.sec_api import _headers, fetch_submissions, ticker_to_cik
from commonsense.edgar.mdna import (
    _filing_base_url,
    _index_url,
    fetch_index_html,
    _all_filing_doc_urls_from_index,
    fetch_document,
)

# Default when no args: use Apple 10-K
DEFAULT_CIK = "320193"
DEFAULT_ACCESSION = "0000320193-24-000106"
DEFAULT_FORM = "10-K"

# SEC submissions API uses these keys (camelCase)
SUB_ACCESSION_KEY = "accessionNumber"
SUB_PRIMARY_KEY = "primaryDocument"


def _print_fetch_error(url: str, user_agent: str) -> None:
    """Re-run the request and print the actual exception so we can fix it."""
    import urllib.request
    print("Failed to fetch index. Re-trying to capture error:", file=sys.stderr)
    req = urllib.request.Request(url, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        if "403" in str(e) or "Forbidden" in str(e):
            print(
                "  SEC often returns 403 without a proper User-Agent. Set EDGAR_EMAIL in .env (e.g. your@email.com).",
                file=sys.stderr,
            )
        if "503" in str(e) or "Service Unavailable" in str(e):
            print(
                "  SEC returned 503 (temporary). Wait a minute and run the script again.",
                file=sys.stderr,
            )
    return None


def _get_latest_filing_from_submissions(
    cik: str | int, form: str, user_agent: str
) -> tuple[str, str, str] | None:
    """
    Same as run_ticker's sec_api path: get submissions from data.sec.gov,
    find the most recent filing for this form, return (accession, primary_doc, filing_date).
    Returns None if submissions fail or no matching filing.
    """
    sub = fetch_submissions(cik, user_agent)
    if not sub:
        return None
    filings = sub.get("filings") or {}
    recent = filings.get("recent") or {}
    # SEC API uses camelCase; try both in case of API change
    acc_list = recent.get(SUB_ACCESSION_KEY) or recent.get("accessionNumber")
    form_list = recent.get("form")
    primary_list = recent.get(SUB_PRIMARY_KEY) or recent.get("primaryDocument")
    if not acc_list or not form_list:
        return None
    form_upper = (form or "10-K").strip().upper()
    date_list = recent.get("filingDate") or []
    for i in range(len(acc_list) - 1, -1, -1):
        if (form_list[i] or "").strip().upper() != form_upper:
            continue
        acc = (acc_list[i] or "").strip()
        if not acc:
            continue
        primary_doc = (primary_list[i] or "").strip() if primary_list and i < len(primary_list) else None
        if not primary_doc or not (primary_doc.endswith(".htm") or primary_doc.endswith(".html")):
            primary_doc = None
        fdate = date_list[i] if i < len(date_list) else "unknown"
        return (acc, primary_doc or "", fdate)
    return None


def _primary_doc_for_accession(cik: str | int, accession: str, form: str, user_agent: str) -> str | None:
    """Get primary document filename for this exact accession from submissions (when user passes CIK + accession)."""
    sub = fetch_submissions(cik, user_agent)
    if not sub:
        return None
    recent = (sub.get("filings") or {}).get("recent") or {}
    acc_list = recent.get(SUB_ACCESSION_KEY) or recent.get("accessionNumber")
    form_list = recent.get("form")
    primary_list = recent.get(SUB_PRIMARY_KEY) or recent.get("primaryDocument")
    if not acc_list or not primary_list:
        return None
    acc_normalized = (accession or "").strip()
    form_upper = (form or "10-K").strip().upper()
    for i in range(len(acc_list) - 1, -1, -1):
        if (acc_list[i] or "").strip() != acc_normalized:
            continue
        if (form_list[i] or "").strip().upper() != form_upper:
            continue
        doc = (primary_list[i] or "").strip()
        if doc and (doc.endswith(".htm") or doc.endswith(".html")):
            return doc
        return None
    return None


def main() -> None:
    import time
    user_agent = EDGAR_EMAIL or "CommonSense sample@localhost"
    if len(sys.argv) >= 5:
        user_agent = sys.argv[4]

    # Ticker-only mode: "python fetch_sample_filing.py AMZN" — same path as run_ticker (submissions -> latest filing).
    if len(sys.argv) == 2 and sys.argv[1].strip() and not sys.argv[1].strip().isdigit():
        ticker = sys.argv[1].strip().upper()
        form = "10-K"
        cik_str = ticker_to_cik(ticker, user_agent)
        if not cik_str:
            print(f"Error: could not resolve ticker {ticker} to CIK.", file=sys.stderr)
            sys.exit(1)
        cik = cik_str
        result = _get_latest_filing_from_submissions(cik, form, user_agent)
        if not result:
            print(f"Error: no {form} in submissions for {ticker} (CIK {cik}).", file=sys.stderr)
            sys.exit(1)
        accession, primary_doc, _fdate = result
        if not primary_doc:
            print(f"Error: submissions have no primaryDocument for {ticker} {form} {accession}.", file=sys.stderr)
            sys.exit(1)
        print(f"Ticker mode: {ticker} -> CIK {cik}, latest {form} = {accession}, primary doc = {primary_doc}\n")
    else:
        cik = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CIK
        accession = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ACCESSION
        form = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_FORM
        primary_doc = None  # resolve below

    base_url = _filing_base_url(cik, accession)
    index_url = _index_url(cik, accession)
    if not base_url or not index_url:
        print("Error: invalid CIK or accession.", file=sys.stderr)
        sys.exit(1)

    out_dir = _PROJECT_ROOT / "data" / "sample_filing" / accession.strip().replace("-", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching sample filing (same path as run_ticker when it works):\n")
    print(f"  Base:   {base_url}")
    print(f"  User-Agent: {user_agent or '(set EDGAR_EMAIL in .env)'}\n")

    doc_urls: list[str] = []

    # 1. Get primary doc from data.sec.gov (submissions) so we skip the index — avoids 503.
    if primary_doc is None:
        primary_doc = _primary_doc_for_accession(cik, accession, form, user_agent)
    if primary_doc:
        direct_url = base_url + primary_doc
        doc_urls.append(direct_url)
        print(f"  Using primary doc from submissions (no index request): {direct_url}\n")
    else:
        # 2. Fallback: fetch index from sec.gov/Archives — this often returns 503.
        print("  Primary doc not found in submissions; fetching index (this can 503)...")
        print(f"  Index: {index_url}\n")
        index_html = fetch_index_html(cik, accession, user_agent)
        if not index_html:
            time.sleep(2)
            index_html = fetch_index_html(cik, accession, user_agent)
        if not index_html:
            _print_fetch_error(index_url, user_agent)
            sys.exit(1)
        index_path = out_dir / f"{accession.replace('-', '_')}-index.htm"
        index_path.write_text(index_html, encoding="utf-8")
        print(f"  Saved index: {index_path}\n")
        doc_urls = _all_filing_doc_urls_from_index(index_html, base_url, form)
        if not doc_urls:
            print("No main document URLs found in index.", file=sys.stderr)
            sys.exit(1)
        print("Main document URL(s) (from index):\n")
        for i, url in enumerate(doc_urls):
            print(f"  [{i+1}] {url}")

    # 3. Fetch and save each document (same sec.gov/Archives URL run_ticker uses)
    for i, url in enumerate(doc_urls):
        time.sleep(0.2)
        html = fetch_document(url, user_agent)
        if not html:
            print(f"  Failed to fetch doc {i+1}.", file=sys.stderr)
            continue
        name = Path(url.rstrip("/")).name or f"doc_{i+1}"
        safe_name = name.replace("-", "_")
        if len(doc_urls) == 1:
            out_path = out_dir / safe_name
        else:
            out_path = out_dir / f"part{i+1}_{safe_name}"
        out_path.write_text(html, encoding="utf-8")
        print(f"\n  Saved: {out_path} ({len(html):,} chars)")

    print(f"\nDone. Inspect files in: {out_dir}")
    print("Use these to see where Item 7 / Item 2 / Item 5 (MD&A) starts and ends and how it’s structured.")


if __name__ == "__main__":
    main()
