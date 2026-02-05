"""
Common-size and flux analysis from SEC facts Parquet.

Uses each company's stored line item keys (concept names from the parquet)
for all references—no canonical mapping. Pivot long-form facts to wide by period,
then compute common-size (% of denominator) and flux (period-over-period change).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from commonsense.edgar.models import (
    BALANCE_SHEET_TABLE,
    CASH_FLOW_TABLE,
    INCOME_STATEMENT_TABLE,
)

# Likely denominator concept names per statement (company may use any of these).
# We pick the first column that matches one of these; all keys are company-supplied.
_INCOME_DENOMINATOR_NAMES = frozenset({
    "revenues", "revenue", "revenuefromcontractwithcustomerexcludingassessedtax",
    "salesrevenuenet", "salesrevenuegoodsnet", "salesrevenueservicesnet",
})
_BALANCE_DENOMINATOR_NAMES = frozenset({
    "assets", "liabilitiesandstockholdersequity", "stockholdersequity",
})
_CASH_DENOMINATOR_NAMES = frozenset({
    "netcashprovidedbyusedinoperatingactivities",
    "netcashprovidedbyusedininvestingactivities",
    "netcashprovidedbyusedinfinancingactivities",
})


def _normalize_concept_for_match(name: str) -> str:
    """Normalize for denominator lookup only (lowercase, no spaces)."""
    return (name or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def _get_denominator_column(columns: list[str], candidate_names: frozenset[str]) -> str | None:
    """Return the first column whose normalized name is in candidate_names."""
    for col in columns:
        if _normalize_concept_for_match(col) in candidate_names:
            return col
    return None


def _long_to_wide(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot long-form facts (concept, end, value) to wide: index=end, columns=concept.
    Uses company's concept names as column names. Drops duplicates (end, concept) by taking first.
    """
    if df.empty or "concept" not in df.columns or "end" not in df.columns or "value" not in df.columns:
        return pd.DataFrame()
    # One value per (end, concept); if multiple (e.g. different forms), aggregate by taking first
    agg = df.groupby(["end", "concept"], as_index=False)["value"].first()
    wide = agg.pivot(index="end", columns="concept", values="value")
    wide.index = pd.to_datetime(wide.index, errors="coerce")
    wide = wide.sort_index()
    return wide


def _common_size_wide(wide: pd.DataFrame, denominator_col: str | None) -> pd.DataFrame:
    """Compute common-size (each column / denominator * 100). Uses company column names."""
    if wide.empty or denominator_col is None or denominator_col not in wide.columns:
        return pd.DataFrame()
    denom = wide[denominator_col]
    denom = denom.replace(0, float("nan"))
    pct = wide.div(denom, axis=0) * 100.0
    return pct


def _flux_wide(wide: pd.DataFrame) -> pd.DataFrame:
    """Period-over-period percent change. Uses company column names."""
    if wide.empty or len(wide) < 2:
        return pd.DataFrame()
    return wide.pct_change(fill_method=None).iloc[1:] * 100.0


def _line_item_keys_from_wide(wide: pd.DataFrame) -> list[str]:
    """Extract line item keys (column names) from the wide table."""
    return list(wide.columns)


def _ensure_company_dir(output_dir: Path, company_ticker: str) -> Path:
    """Output under output_dir/company_ticker/ for localized storage."""
    out = Path(output_dir) / str(company_ticker).strip()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _discover_companies_from_parquet(data_dir: Path) -> list[str]:
    """
    Discover company identifiers from parquet layout.
    Supports: (1) flat files {ticker}_sec_facts_income_statement.parquet;
    (2) subdirs data_dir/ticker/ with *_sec_facts_*.parquet inside.
    """
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []
    companies: set[str] = set()
    # Subdirs as company tickers
    for child in data_dir.iterdir():
        if child.is_dir():
            for p in child.glob("*_sec_facts_*.parquet"):
                companies.add(child.name)
                break
    # Flat: prefix before _sec_facts_
    for p in data_dir.glob("*_sec_facts_*.parquet"):
        name = p.stem
        if "_sec_facts_" in name:
            ticker = name.split("_sec_facts_")[0].strip()
            if ticker:
                companies.add(ticker)
    return sorted(companies)


def _load_facts_for_company(
    data_dir: Path,
    company_ticker: str,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    """Load income, balance, cash long-form parquets for one company. Tries subdir then flat."""
    data_dir = Path(data_dir)
    base = f"{company_ticker}_sec_facts"
    subdir = data_dir / company_ticker

    def path_in_subdir(name: str) -> Path:
        return subdir / f"{base}_{name}.parquet"

    def path_flat(name: str) -> Path:
        return data_dir / f"{base}_{name}.parquet"

    def load(name: str) -> pd.DataFrame | None:
        # Prefer subdir if file exists; else flat (so we support both layouts)
        for path in (path_in_subdir(name), path_flat(name)):
            if path.exists():
                try:
                    return pd.read_parquet(path)
                except Exception:
                    return None
        return None

    inc = load(INCOME_STATEMENT_TABLE)
    bal = load(BALANCE_SHEET_TABLE)
    cash = load(CASH_FLOW_TABLE)
    return inc, bal, cash


def run_analysis_for_company(
    company_ticker: str,
    data_dir: str | Path,
    *,
    write_csv: bool = True,
) -> dict[str, Any]:
    """
    Run common-size and flux for one company using its parquet fact files.
    Line item keys are taken from each file's concept column (company's reporting format).
    Writes common-size and flux as CSV only under data_dir/company_ticker/.
    """
    data_dir = Path(data_dir)
    out_dir = _ensure_company_dir(data_dir, company_ticker)
    files_written: list[str] = []
    errors: list[str] = []

    inc_long, bal_long, cash_long = _load_facts_for_company(data_dir, company_ticker)

    def process(
        long_df: pd.DataFrame | None,
        statement_name: str,
        denominator_candidates: frozenset[str],
    ) -> None:
        if long_df is None or long_df.empty:
            return
        wide = _long_to_wide(long_df)
        if wide.empty:
            return
        line_item_keys = _line_item_keys_from_wide(wide)
        denom_col = _get_denominator_column(line_item_keys, denominator_candidates)

        # Common-size (CSV only for analysis outputs)
        cs = _common_size_wide(wide, denom_col)
        if not cs.empty:
            csv_path = out_dir / f"common_size_{statement_name}.csv"
            cs.to_csv(csv_path)
            files_written.append(str(csv_path))

        # Flux (CSV only for analysis outputs)
        flux = _flux_wide(wide)
        if not flux.empty:
            flux_csv = out_dir / f"flux_{statement_name}.csv"
            flux.to_csv(flux_csv)
            files_written.append(str(flux_csv))

    process(inc_long, INCOME_STATEMENT_TABLE, _INCOME_DENOMINATOR_NAMES)
    process(bal_long, BALANCE_SHEET_TABLE, _BALANCE_DENOMINATOR_NAMES)
    process(cash_long, CASH_FLOW_TABLE, _CASH_DENOMINATOR_NAMES)

    return {
        "company_ticker": company_ticker,
        "files_written": files_written,
        "errors": errors,
    }


def run_analysis_all(
    data_dir: str | Path,
    *,
    write_csv: bool = True,
) -> dict[str, Any]:
    """
    Discover companies from data_dir and run common-size + flux for each.
    Writes CSV only (common_size_*.csv, flux_*.csv). Uses each company's line item keys.
    """
    data_dir = Path(data_dir)
    companies = _discover_companies_from_parquet(data_dir)
    all_files: list[str] = []
    all_errors: list[str] = []
    for ticker in companies:
        result = run_analysis_for_company(ticker, data_dir, write_csv=write_csv)
        all_files.extend(result["files_written"])
        all_errors.extend(result["errors"])
    return {
        "companies_processed": len(companies),
        "companies": companies,
        "files_written": all_files,
        "errors": all_errors,
    }
