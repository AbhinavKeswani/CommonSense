"""SEC EDGAR ingestion: fetch via edgartools, normalize to DataFrames, write Parquet."""

import time
from pathlib import Path
from typing import Any

import pandas as pd

from commonsense.edgar.models import (
    BALANCE_SHEET_TABLE,
    CASH_FLOW_TABLE,
    INCOME_STATEMENT_TABLE,
)


def _to_dataframe(obj: Any) -> pd.DataFrame | None:
    """Convert edgartools financial statement to pandas DataFrame if possible."""
    if obj is None:
        return None
    if hasattr(obj, "to_dataframe"):
        return obj.to_dataframe()
    if isinstance(obj, pd.DataFrame):
        return obj
    return None


def _safe_financials(filing: Any) -> tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    """Extract income, balance_sheet, cash_flow from filing.financials. Return (income_df, balance_df, cash_df)."""
    fin = getattr(filing, "financials", None)
    if fin is None:
        return None, None, None
    income = _to_dataframe(getattr(fin, "income", None) or getattr(fin, "income_statement", None))
    balance = _to_dataframe(getattr(fin, "balance_sheet", None))
    cash = _to_dataframe(getattr(fin, "cash_flow", None) or getattr(fin, "cash_flow_statement", None))
    return income, balance, cash


def run_ingestion(
    tickers: list[str],
    forms: list[str],
    output_dir: str | Path,
    email: str,
    delay_between_companies: float = 0.3,
    max_filings_per_form: int = 5,
) -> dict[str, Any]:
    """
    Ingest SEC EDGAR data for given tickers and form types; write Parquet to output_dir.

    Returns summary: { "tickers_processed", "files_written", "errors", "filings_count" }.
    """
    from edgar import set_identity
    from edgar import Company

    set_identity(email.strip())
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
        ticker = (ticker or "").strip().upper()
        if not ticker:
            continue
        try:
            company = Company(ticker)
            for form in forms:
                form = (form or "").strip()
                if not form:
                    continue
                try:
                    filings = company.get_filings(form=form)
                    if filings is None:
                        continue
                    # Iterate over filings (edgartools CompanyFilings is iterable)
                    try:
                        filing_list = list(filings)[:max_filings_per_form]
                    except Exception:
                        latest = getattr(filings, "latest", None)
                        filing_list = [latest()] if latest and callable(latest) and latest() else []
                    for filing in filing_list:
                        if filing is None:
                            continue
                        filings_count += 1
                        cik = getattr(filing, "cik", None) or ""
                        company_name = getattr(filing, "company", "") or ticker
                        filing_date = getattr(filing, "filing_date", "") or ""
                        accession_no = getattr(filing, "accession_no", "") or getattr(filing, "accession_number", "") or ""
                        period = getattr(filing, "period_of_report", "") or ""

                        meta_row = {
                            "ticker": ticker,
                            "cik": cik,
                            "company": company_name,
                            "form": form,
                            "filing_date": filing_date,
                            "accession_no": accession_no,
                            "period_of_report": period,
                        }
                        meta_df = pd.DataFrame([meta_row])

                        # Safe filename: ticker_form_date (sanitize date and accession)
                        safe_date = (filing_date or "unknown").replace("-", "")[:8]
                        base_name = f"{ticker}_{form}_{safe_date}"

                        # Write metadata
                        meta_file = output_path / f"{base_name}_meta.parquet"
                        meta_df.to_parquet(meta_file, index=False)
                        files_written.append(str(meta_file))

                        income_df, balance_df, cash_df = _safe_financials(filing)
                        if income_df is not None and not income_df.empty:
                            out_file = output_path / f"{base_name}_{INCOME_STATEMENT_TABLE}.parquet"
                            income_df.to_parquet(out_file, index=True)
                            files_written.append(str(out_file))
                        if balance_df is not None and not balance_df.empty:
                            out_file = output_path / f"{base_name}_{BALANCE_SHEET_TABLE}.parquet"
                            balance_df.to_parquet(out_file, index=True)
                            files_written.append(str(out_file))
                        if cash_df is not None and not cash_df.empty:
                            out_file = output_path / f"{base_name}_{CASH_FLOW_TABLE}.parquet"
                            cash_df.to_parquet(out_file, index=True)
                            files_written.append(str(out_file))

                except Exception as e:
                    errors.append(f"{ticker} {form}: {e!s}")
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
