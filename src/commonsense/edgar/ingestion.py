"""SEC EDGAR ingestion: fetch via edgartools, normalize to DataFrames, write Parquet."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd

from commonsense.edgar.models import (
    BALANCE_SHEET_TABLE,
    CASH_FLOW_TABLE,
    INCOME_STATEMENT_TABLE,
)
from commonsense.edgar.mdna import write_mdna_for_filing
from commonsense.edgar.sec_api import (
    DEFAULT_FORMS,
    fetch_submissions,
    get_periodic_forms_from_submissions,
    run_sec_api_fallback,
    ticker_to_cik,
    _ticker_from_submissions,
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
        raw = (ticker or "").strip()
        if not raw:
            continue
        ticker = raw.upper()
        # Resolve to CIK first (SEC API or numeric input) so we can fetch submissions and derive forms
        if raw.isdigit():
            cik_str = str(raw).zfill(10)
            cik_int: int | None = int(raw)
        else:
            cik_str = ticker_to_cik(raw, email)
            cik_int = int(cik_str) if cik_str else None
            if not cik_int and not cik_str:
                # Fallback: try edgartools so we still support tickers not in SEC list
                try:
                    company = Company(ticker)
                    cik_for_edgar = getattr(company, "cik", None)
                    if cik_for_edgar is not None:
                        cik_int = int(cik_for_edgar)
                        cik_str = str(cik_for_edgar).zfill(10)
                except Exception:
                    pass
        if not cik_str and not cik_int:
            errors.append(f"{ticker}: could not resolve to CIK (ticker not in SEC list?)")
            continue
        # Fetch submissions to discover which periodic forms this company actually files
        sub = fetch_submissions(cik_str or cik_int, email) if (cik_str or cik_int) else None
        time.sleep(delay_between_companies * 0.5)  # brief delay after submissions
        forms_to_use = get_periodic_forms_from_submissions(sub) or forms or list(DEFAULT_FORMS)
        display_ticker = _ticker_from_submissions(sub) or ticker
        company_dir = output_path / display_ticker
        company_dir.mkdir(parents=True, exist_ok=True)
        fallback_done_for_ticker = False
        cik_for_fallback: int | None = cik_int
        try:
            company = Company(cik_int if cik_int is not None else ticker)
            for form in forms_to_use:
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
                        company_name = getattr(filing, "company", "") or display_ticker
                        filing_date = getattr(filing, "filing_date", "") or ""
                        accession_no = getattr(filing, "accession_no", "") or getattr(filing, "accession_number", "") or ""
                        period = getattr(filing, "period_of_report", "") or ""

                        meta_row = {
                            "ticker": display_ticker,
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
                        base_name = f"{display_ticker}_{form}_{safe_date}"

                        # Write metadata under company subdir (parquet/ticker/)
                        meta_file = company_dir / f"{base_name}_meta.parquet"
                        meta_df.to_parquet(meta_file, index=False)
                        files_written.append(str(meta_file))

                        # MD&A first (only needs accession + CIK from SEC; does not use filing.financials).
                        # So we always try MD&A even if edgartools financials fail later.
                        cik_for_mdna = cik_for_fallback if cik_for_fallback is not None else (int(cik_str) if (cik_str and str(cik_str).strip().isdigit()) else None)
                        if accession_no and cik_for_mdna is not None:
                            try:
                                mdna_path = write_mdna_for_filing(
                                    cik=cik_for_mdna,
                                    accession_no=accession_no,
                                    form=form,
                                    user_agent=email,
                                    company_dir=company_dir,
                                    base_name=base_name,
                                    delay_seconds=0.2,
                                    use_md=False,
                                )
                                if mdna_path is not None:
                                    files_written.append(str(mdna_path))
                            except Exception:
                                pass  # do not fail ingestion on MD&A errors

                        income_df, balance_df, cash_df = _safe_financials(filing)
                        if income_df is not None and not income_df.empty:
                            out_file = company_dir / f"{base_name}_{INCOME_STATEMENT_TABLE}.parquet"
                            income_df.to_parquet(out_file, index=True)
                            files_written.append(str(out_file))
                        if balance_df is not None and not balance_df.empty:
                            out_file = company_dir / f"{base_name}_{BALANCE_SHEET_TABLE}.parquet"
                            balance_df.to_parquet(out_file, index=True)
                            files_written.append(str(out_file))
                        if cash_df is not None and not cash_df.empty:
                            out_file = company_dir / f"{base_name}_{CASH_FLOW_TABLE}.parquet"
                            cash_df.to_parquet(out_file, index=True)
                            files_written.append(str(out_file))

                except Exception as e:
                    errors.append(f"{ticker} {form}: {e!s}")
                    # Fallback: use data.sec.gov JSON APIs so we can still operate on the data (no SGML)
                    if not fallback_done_for_ticker and cik_for_fallback is not None:
                        try:
                            fb = run_sec_api_fallback(
                                cik=cik_for_fallback,
                                ticker_label=display_ticker,
                                output_dir=output_path,
                                user_agent=email,
                            )
                            # Fallback writes to output_path/<ticker>/ (ticker resolved from SEC when CIK)
                            files_written.extend(fb["files_written"])
                            if fb["errors"]:
                                errors.extend(fb["errors"])
                            fallback_done_for_ticker = True
                        except Exception as fb_e:
                            errors.append(f"{ticker} (sec_api fallback): {fb_e!s}")
            tickers_processed += 1
        except Exception as e:
            errors.append(f"{ticker}: {e!s}")
            # If we never entered the form loop (e.g. Company() failed), try fallback by CIK when possible
            if not fallback_done_for_ticker and cik_for_fallback is not None:
                try:
                    fb = run_sec_api_fallback(
                        cik=cik_for_fallback,
                        ticker_label=display_ticker,
                        output_dir=output_path,
                        user_agent=email,
                    )
                    files_written.extend(fb["files_written"])
                    if fb["errors"]:
                        errors.extend(fb["errors"])
                    fallback_done_for_ticker = True
                except Exception as fb_e:
                    errors.append(f"{ticker} (sec_api fallback): {fb_e!s}")
        time.sleep(delay_between_companies)

    return {
        "tickers_processed": tickers_processed,
        "files_written": files_written,
        "errors": errors,
        "filings_count": filings_count,
    }
