"""
Fallback: fetch filing metadata and XBRL company facts from data.sec.gov (JSON).
Does not rely on SGML parsing; use when edgartools fails (e.g. Unknown SGML format).
SEC requires a descriptive User-Agent (contact identity); no API key.
"""

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

SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_COMPANY_TICKERS = "https://data.sec.gov/files/company_tickers.json"

# Periodic report forms we care about; we'll request only those that appear in a company's submissions.
PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F")
DEFAULT_FORMS = ["10-K", "10-Q", "20-F"]


def _cik_pad(cik: int | str) -> str:
    """Zero-pad CIK to 10 digits for SEC URLs."""
    s = str(cik).strip()
    return s.zfill(10)


def _headers(user_agent: str) -> dict[str, str]:
    """SEC requires a descriptive User-Agent (e.g. 'YourName your@email.com')."""
    return {"User-Agent": user_agent.strip() or "CommonSense commonsense@localhost"}


def fetch_submissions(cik: int | str, user_agent: str) -> dict[str, Any] | None:
    """Fetch submissions JSON for a CIK. Returns None on failure."""
    import urllib.request
    url = SEC_SUBMISSIONS.format(cik=_cik_pad(cik))
    req = urllib.request.Request(url, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json
            return json.load(resp)
    except Exception:
        return None


def fetch_companyfacts(cik: int | str, user_agent: str) -> dict[str, Any] | None:
    """Fetch company facts (XBRL) JSON for a CIK. Returns None on failure."""
    import urllib.request
    url = SEC_COMPANYFACTS.format(cik=_cik_pad(cik))
    req = urllib.request.Request(url, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            import json
            return json.load(resp)
    except Exception:
        return None


def ticker_to_cik(ticker: str, user_agent: str) -> str | None:
    """Resolve ticker to CIK via SEC company_tickers.json. Returns zero-padded CIK string or None."""
    import urllib.request
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None
    req = urllib.request.Request(SEC_COMPANY_TICKERS, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json
            data = json.load(resp)
        if not isinstance(data, dict):
            return None
        for _key, entry in data.items():
            if isinstance(entry, dict) and str(entry.get("ticker", "")).strip().upper() == ticker:
                cik = entry.get("cik_str")
                if cik is not None:
                    return _cik_pad(cik)
        return None
    except Exception:
        return None


def get_periodic_forms_from_submissions(sub: dict[str, Any] | None) -> list[str]:
    """
    Return the list of periodic report forms (from PERIODIC_FORMS) that this company
    actually files, based on filings.recent["form"]. Use this to request only forms
    the company files (e.g. 20-F for foreign issuers, 10-K/10-Q for domestic).
    """
    if not sub:
        return []
    filings = sub.get("filings") or {}
    recent = filings.get("recent") or {}
    form_list = recent.get("form")
    if not isinstance(form_list, list):
        return []
    seen: set[str] = set()
    for f in form_list:
        if isinstance(f, str):
            f = f.strip()
            if f in PERIODIC_FORMS:
                seen.add(f)
    # Return in a stable order (same as PERIODIC_FORMS)
    return [f for f in PERIODIC_FORMS if f in seen]


def _ticker_from_submissions(sub: dict[str, Any] | None) -> str | None:
    """Get primary ticker from SEC submissions JSON (e.g. for use as folder name). Returns None if not found."""
    if not sub:
        return None
    tickers = sub.get("tickers")
    if isinstance(tickers, list) and len(tickers) > 0 and tickers[0]:
        return str(tickers[0]).strip().upper()
    return None


def _submissions_to_dataframe(data: dict[str, Any], ticker_label: str, cik: str) -> pd.DataFrame:
    """Build a filings metadata table from submissions JSON."""
    rows = []
    # Recent filings are often in data.get("filings", {}).get("recent", {}) with column names in a key
    filings = data.get("filings") or {}
    recent = filings.get("recent") or {}
    if not recent:
        return pd.DataFrame()
    # recent is dict of lists: {"accessionNumber": [...], "form": [...], "filingDate": [...], ...}
    cols = list(recent.keys())
    n = len(recent.get(cols[0], [])) if cols else 0
    for i in range(n):
        row = {"ticker": ticker_label, "cik": cik, "company": data.get("name") or ticker_label}
        for c in cols:
            val = recent[c][i] if i < len(recent[c]) else None
            row[c] = val
        rows.append(row)
    return pd.DataFrame(rows)


def _companyfacts_to_dataframes(data: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Flatten companyfacts JSON into long-form tables.
    Returns (income_df, balance_df, cash_df) - we put concepts into buckets by common tags;
    otherwise returns one combined 'facts' style table in income_df and empty others for simplicity.
    """
    facts = data.get("facts") or {}
    us_gaap = facts.get("us-gaap") or {}
    dei = facts.get("dei") or {}
    rows_income: list[dict] = []
    rows_balance: list[dict] = []
    rows_cash: list[dict] = []

    # Common US-GAAP tags by statement type (subset)
    INCOME_TAGS = {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "NetIncomeLoss", "GrossProfit", "OperatingIncomeLoss", "CostOfRevenue", "OperatingExpenses", "ResearchAndDevelopmentExpense", "SellingGeneralAndAdministrativeExpense"}
    BALANCE_TAGS = {"Assets", "Liabilities", "StockholdersEquity", "LiabilitiesAndStockholdersEquity", "CashAndCashEquivalentsAtCarryingValue", "AccountsReceivableNetCurrent", "InventoryNet", "PropertyPlantAndEquipmentNet", "AccountsPayableCurrent", "LongTermDebt", "ShortTermDebt"}
    CASH_TAGS = {"NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInFinancingActivities", "CashAndCashEquivalentsPeriodIncreaseDecrease"}

    def emit(concept: str, unit: str, facts_list: list, target: list[dict], tag: str):
        for f in facts_list:
            end = f.get("end") or f.get("instant")
            val = f.get("val")
            if val is None:
                continue
            target.append({
                "concept": concept,
                "unit": unit,
                "end": end,
                "value": val,
                "fy": f.get("fy"),
                "fp": f.get("fp"),
                "form": f.get("form"),
                "accession": f.get("accn"),
            })

    for concept, meta in list(us_gaap.items()) + list(dei.items()):
        tag = concept.split("/")[-1] if "/" in concept else concept
        units = meta.get("units") or {}
        for unit_name, facts_list in units.items():
            if not isinstance(facts_list, list):
                continue
            if tag in INCOME_TAGS:
                emit(concept, unit_name, facts_list, rows_income, tag)
            elif tag in BALANCE_TAGS:
                emit(concept, unit_name, facts_list, rows_balance, tag)
            elif tag in CASH_TAGS:
                emit(concept, unit_name, facts_list, rows_cash, tag)

    def to_df(rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    return to_df(rows_income), to_df(rows_balance), to_df(rows_cash)


def run_sec_api_fallback(
    cik: int | str,
    ticker_label: str,
    output_dir: str | Path,
    user_agent: str,
    delay_seconds: float = 0.2,
) -> dict[str, Any]:
    """
    Fetch submissions and company facts from data.sec.gov for the given CIK;
    write Parquet under output_dir/<ticker>/ so storage is by ticker (e.g. AAPL, GOOGL).
    Uses ticker from submissions JSON when available, else ticker_label (e.g. CIK).
    """
    base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    cik_str = _cik_pad(cik)
    files_written: list[str] = []
    errors: list[str] = []

    # Submissions (fetch first so we can resolve ticker for folder name)
    sub = fetch_submissions(cik, user_agent)
    time.sleep(delay_seconds)
    display_ticker = _ticker_from_submissions(sub) or ticker_label
    company_dir = base_dir / display_ticker
    company_dir.mkdir(parents=True, exist_ok=True)

    if sub is None:
        errors.append(f"SEC submissions fetch failed for CIK {cik_str}")
    else:
        df_sub = _submissions_to_dataframe(sub, display_ticker, cik_str)
        if not df_sub.empty:
            if "filingDate" in df_sub.columns:
                df_sub = df_sub.sort_values("filingDate", ascending=False).head(500)
            out = company_dir / f"{display_ticker}_sec_submissions.parquet"
            df_sub.to_parquet(out, index=False)
            files_written.append(str(out))

    # Company facts (XBRL)
    cf = fetch_companyfacts(cik, user_agent)
    time.sleep(delay_seconds)
    if cf is None:
        errors.append(f"SEC companyfacts fetch failed for CIK {cik_str}")
    else:
        inc_df, bal_df, cash_df = _companyfacts_to_dataframes(cf)
        base = f"{display_ticker}_sec_facts"
        if not inc_df.empty:
            out = company_dir / f"{base}_{INCOME_STATEMENT_TABLE}.parquet"
            inc_df.to_parquet(out, index=False)
            files_written.append(str(out))
        if not bal_df.empty:
            out = company_dir / f"{base}_{BALANCE_SHEET_TABLE}.parquet"
            bal_df.to_parquet(out, index=False)
            files_written.append(str(out))
        if not cash_df.empty:
            out = company_dir / f"{base}_{CASH_FLOW_TABLE}.parquet"
            cash_df.to_parquet(out, index=False)
            files_written.append(str(out))
        if inc_df.empty and bal_df.empty and cash_df.empty:
            all_rows: list[dict] = []
            facts = cf.get("facts") or {}
            for taxonomy, concepts in facts.items():
                if not isinstance(concepts, dict):
                    continue
                for concept, meta in concepts.items():
                    for unit_name, facts_list in (meta.get("units") or {}).items():
                        if not isinstance(facts_list, list):
                            continue
                        for f in facts_list:
                            all_rows.append({
                                "taxonomy": taxonomy,
                                "concept": concept,
                                "unit": unit_name,
                                "end": f.get("end") or f.get("instant"),
                                "value": f.get("val"),
                                "fy": f.get("fy"),
                                "fp": f.get("fp"),
                                "form": f.get("form"),
                                "accession": f.get("accn"),
                            })
            if all_rows:
                out = company_dir / f"{base}_all_concepts.parquet"
                pd.DataFrame(all_rows).to_parquet(out, index=False)
                files_written.append(str(out))

    return {
        "files_written": files_written,
        "errors": errors,
        "cik": cik_str,
        "ticker": display_ticker,
    }
