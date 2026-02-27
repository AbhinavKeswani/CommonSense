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

_REVENUE_NAMES = _INCOME_DENOMINATOR_NAMES
_GROSS_PROFIT_NAMES = frozenset({"grossprofit"})
_OPERATING_INCOME_NAMES = frozenset({"operatingincomeloss"})
_NET_INCOME_NAMES = frozenset({"netincomeloss", "profitloss"})
_ASSETS_NAMES = frozenset({"assets"})
_LIABILITIES_NAMES = frozenset({"liabilities"})
_EQUITY_NAMES = frozenset({
    "stockholdersequity",
    "stockholdersequityincludingportionattributabletononcontrollinginterest",
})
_LONG_DEBT_NAMES = frozenset({"longtermdebt", "longtermdebtnoncurrent"})
_SHORT_DEBT_NAMES = frozenset({"shorttermdebt", "shorttermborrowings", "debtcurrent"})
_CURRENT_ASSETS_NAMES = frozenset({"assetscurrent"})
_CURRENT_LIABILITIES_NAMES = frozenset({"liabilitiescurrent"})
_CASH_EQ_NAMES = frozenset({
    "cashandcashequivalentsatcarryingvalue",
    "cashcashequivalentsandshortterminvestments",
})
_AR_NAMES = frozenset({"accountsreceivablenetcurrent", "accountsreceivablecurrentnet"})
_INVENTORY_NAMES = frozenset({"inventorynet"})
_CFO_NAMES = frozenset({"netcashprovidedbyusedinoperatingactivities"})
_CAPEX_NAMES = frozenset({
    "paymentstoacquirepropertyplantandequipment",
    "capitalexpenditures",
    "purchaseofpropertyplantandequipment",
})
_SHARES_NAMES = frozenset({
    "commonstocksharesoutstanding",
    "entitycommonstocksharesoutstanding",
    "weightedaveragenumberofdilutedsharesoutstanding",
    "weightedaveragenumberofsharesoutstandingbasicanddiluted",
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


def _get_series_by_candidates(wide: pd.DataFrame, candidate_names: frozenset[str]) -> pd.Series | None:
    """Return first matching series from wide by exact normalized name, then loose contains match."""
    if wide.empty or not candidate_names:
        return None
    exact = _get_denominator_column(list(wide.columns), candidate_names)
    if exact is not None:
        return wide[exact]
    # Loose fallback: support minor SEC concept naming variants.
    normalized_cols = {col: _normalize_concept_for_match(str(col)) for col in wide.columns}
    for col, norm_col in normalized_cols.items():
        for cand in candidate_names:
            if cand in norm_col or norm_col in cand:
                return wide[col]
    return None


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Division with zero-handling."""
    denom = denominator.replace(0, float("nan"))
    return numerator.div(denom)


def _compute_ratio_table(
    income_wide: pd.DataFrame,
    balance_wide: pd.DataFrame,
    cash_wide: pd.DataFrame,
) -> pd.DataFrame:
    """Compute a broad set of financial ratios from available concepts."""
    frames = [df for df in (income_wide, balance_wide, cash_wide) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame()
    index = frames[0].index
    for frame in frames[1:]:
        index = index.union(frame.index)
    index = index.sort_values()

    def aligned(series: pd.Series | None) -> pd.Series | None:
        return series.reindex(index) if series is not None else None

    revenues = aligned(_get_series_by_candidates(income_wide, _REVENUE_NAMES))
    gross_profit = aligned(_get_series_by_candidates(income_wide, _GROSS_PROFIT_NAMES))
    operating_income = aligned(_get_series_by_candidates(income_wide, _OPERATING_INCOME_NAMES))
    net_income = aligned(_get_series_by_candidates(income_wide, _NET_INCOME_NAMES))
    assets = aligned(_get_series_by_candidates(balance_wide, _ASSETS_NAMES))
    liabilities = aligned(_get_series_by_candidates(balance_wide, _LIABILITIES_NAMES))
    equity = aligned(_get_series_by_candidates(balance_wide, _EQUITY_NAMES))
    long_debt = aligned(_get_series_by_candidates(balance_wide, _LONG_DEBT_NAMES))
    short_debt = aligned(_get_series_by_candidates(balance_wide, _SHORT_DEBT_NAMES))
    current_assets = aligned(_get_series_by_candidates(balance_wide, _CURRENT_ASSETS_NAMES))
    current_liabilities = aligned(_get_series_by_candidates(balance_wide, _CURRENT_LIABILITIES_NAMES))
    cash_and_eq = aligned(_get_series_by_candidates(balance_wide, _CASH_EQ_NAMES))
    accounts_receivable = aligned(_get_series_by_candidates(balance_wide, _AR_NAMES))
    inventory = aligned(_get_series_by_candidates(balance_wide, _INVENTORY_NAMES))
    cfo = aligned(_get_series_by_candidates(cash_wide, _CFO_NAMES))
    capex = aligned(_get_series_by_candidates(cash_wide, _CAPEX_NAMES))
    shares_outstanding = aligned(_get_series_by_candidates(balance_wide, _SHARES_NAMES))

    ratios = pd.DataFrame(index=index)

    if gross_profit is not None and revenues is not None:
        ratios["gross_margin_pct"] = _safe_div(gross_profit, revenues) * 100.0
    if operating_income is not None and revenues is not None:
        ratios["operating_margin_pct"] = _safe_div(operating_income, revenues) * 100.0
    if net_income is not None and revenues is not None:
        ratios["net_margin_pct"] = _safe_div(net_income, revenues) * 100.0
    if net_income is not None and assets is not None:
        ratios["return_on_assets_pct"] = _safe_div(net_income, assets) * 100.0
    if net_income is not None and equity is not None:
        ratios["return_on_equity_pct"] = _safe_div(net_income, equity) * 100.0
    if revenues is not None and assets is not None:
        ratios["asset_turnover"] = _safe_div(revenues, assets)

    total_debt: pd.Series | None = None
    if long_debt is not None and short_debt is not None:
        total_debt = long_debt.fillna(0) + short_debt.fillna(0)
    elif long_debt is not None:
        total_debt = long_debt
    elif short_debt is not None:
        total_debt = short_debt

    if total_debt is not None and equity is not None:
        ratios["debt_to_equity"] = _safe_div(total_debt, equity)
    if liabilities is not None and assets is not None:
        ratios["debt_ratio_pct"] = _safe_div(liabilities, assets) * 100.0
    if equity is not None and assets is not None:
        ratios["equity_ratio_pct"] = _safe_div(equity, assets) * 100.0

    if current_assets is not None and current_liabilities is not None:
        ratios["current_ratio"] = _safe_div(current_assets, current_liabilities)
        ratios["working_capital"] = current_assets - current_liabilities
    if cash_and_eq is not None and accounts_receivable is not None and current_liabilities is not None:
        ratios["quick_ratio"] = _safe_div(cash_and_eq + accounts_receivable, current_liabilities)
    if cash_and_eq is not None and current_liabilities is not None:
        ratios["cash_ratio"] = _safe_div(cash_and_eq, current_liabilities)

    if cfo is not None and net_income is not None:
        ratios["operating_cash_flow_to_net_income"] = _safe_div(cfo, net_income)
    if cfo is not None and revenues is not None:
        ratios["operating_cash_flow_margin_pct"] = _safe_div(cfo, revenues) * 100.0
    if cfo is not None and capex is not None:
        capex_outflow = capex.abs()
        free_cash_flow = cfo - capex_outflow
        ratios["free_cash_flow"] = free_cash_flow
        if revenues is not None:
            ratios["free_cash_flow_margin_pct"] = _safe_div(free_cash_flow, revenues) * 100.0

    if equity is not None and shares_outstanding is not None:
        ratios["book_value_per_share"] = _safe_div(equity, shares_outstanding)
    if inventory is not None and revenues is not None:
        ratios["inventory_to_revenue_pct"] = _safe_div(inventory, revenues) * 100.0
    if accounts_receivable is not None and revenues is not None:
        ratios["accounts_receivable_to_revenue_pct"] = _safe_div(accounts_receivable, revenues) * 100.0

    # Keep rows with at least one ratio.
    return ratios.dropna(how="all")


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
            # Ignore numeric-only legacy CIK folders; canonical storage is ticker folders.
            if child.name.isdigit():
                continue
            for p in child.glob("*_sec_facts_*.parquet"):
                companies.add(child.name)
                break
    # Flat: prefix before _sec_facts_
    for p in data_dir.glob("*_sec_facts_*.parquet"):
        name = p.stem
        if "_sec_facts_" in name:
            ticker = name.split("_sec_facts_")[0].strip()
            if ticker and not ticker.isdigit():
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

    # Ratio analysis (cross-statement): profitability, liquidity, leverage, efficiency, cash-flow.
    inc_wide = _long_to_wide(inc_long) if inc_long is not None and not inc_long.empty else pd.DataFrame()
    bal_wide = _long_to_wide(bal_long) if bal_long is not None and not bal_long.empty else pd.DataFrame()
    cash_wide = _long_to_wide(cash_long) if cash_long is not None and not cash_long.empty else pd.DataFrame()
    ratios = _compute_ratio_table(inc_wide, bal_wide, cash_wide)
    if not ratios.empty:
        ratios_csv = out_dir / "ratios_financial_health.csv"
        ratios.to_csv(ratios_csv)
        files_written.append(str(ratios_csv))

        ratios_flux = _flux_wide(ratios)
        if not ratios_flux.empty:
            ratios_flux_csv = out_dir / "flux_ratios_financial_health.csv"
            ratios_flux.to_csv(ratios_flux_csv)
            files_written.append(str(ratios_flux_csv))

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
