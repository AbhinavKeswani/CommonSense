"""SEC EDGAR ingestion: fetch via SEC JSON + Archives HTML, write Parquet."""

from __future__ import annotations

import time
from pathlib import Path
from commonsense.edgar.sec_api import (
    DEFAULT_FORMS,
    fetch_submissions,
    get_periodic_forms_from_submissions,
    run_sec_api_fallback,
    ticker_to_cik,
    _ticker_from_submissions,
)


def run_ingestion(
    tickers: list[str],
    forms: list[str],
    output_dir: str | Path,
    email: str,
    delay_between_companies: float = 0.3,
    max_filings_per_form: int = 5,
) -> dict[str, Any]:
    """Ingest SEC data using only SEC JSON APIs + SEC Archives HTML for MD&A."""
    if not email.strip():
        return {
            "tickers_processed": 0,
            "files_written": [],
            "errors": ["EDGAR_EMAIL is required by the SEC. Set it in .env or in the dashboard."],
            "filings_count": 0,
        }

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    files_written: list[str] = []
    errors: list[str] = []
    filings_count = 0
    tickers_processed = 0

    for ticker in tickers:
        raw = (ticker or "").strip()
        if not raw:
            continue
        ticker = raw.upper()
        # Resolve to CIK from numeric input or SEC ticker map.
        if raw.isdigit():
            cik_str = str(raw).zfill(10)
            cik_int: int | None = int(raw)
        else:
            cik_str = ticker_to_cik(raw, email)
            cik_int = int(cik_str) if cik_str else None
        if not cik_str and not cik_int:
            errors.append(f"{ticker}: could not resolve to CIK (ticker not in SEC list?)")
            continue

        # Fetch submissions to discover which periodic forms this company actually files.
        sub = fetch_submissions(cik_str or cik_int, email) if (cik_str or cik_int) else None
        time.sleep(delay_between_companies * 0.5)
        forms_to_use = get_periodic_forms_from_submissions(sub) or forms or list(DEFAULT_FORMS)
        display_ticker = _ticker_from_submissions(sub) or ticker

        try:
            primary = run_sec_api_fallback(
                cik=cik_int if cik_int is not None else cik_str,
                ticker_label=display_ticker,
                output_dir=output_path,
                user_agent=email,
                delay_seconds=max(0.1, delay_between_companies * 0.5),
                forms=forms_to_use,
                max_filings_per_form=max_filings_per_form,
            )
            files_written.extend(primary.get("files_written", []))
            filings_count += int(primary.get("filings_count", 0) or 0)
            if primary.get("errors"):
                errors.extend(primary["errors"])
            tickers_processed += 1
        except Exception as e:
            errors.append(f"{ticker}: {e!s}")
        time.sleep(delay_between_companies)

    return {
        "tickers_processed": tickers_processed,
        "files_written": files_written,
        "errors": errors,
        "filings_count": filings_count,
    }
