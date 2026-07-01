"""Price-based valuation multiples (P/E, P/S, P/B, EV/EBITDA, PEG).

The rest of the pipeline is fundamentals-only (SEC facts). Multiples additionally
need the current market price, which we pull via `commonsense.market.prices`. We
combine the latest annual fundamentals with a live quote to produce a one-row
"as of today" snapshot per ticker, written as `ratios_valuation_multiples.csv`
and returned as a dict for scoring/screening.

Reuses concept-matching + fact-loading helpers from common_size_flux so we speak
the same company-supplied concept names as the ratio engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from commonsense.analysis.common_size_flux import (
    _load_facts_for_company,
    _normalize_concept_for_match,
    _CAPEX_NAMES,
    _CASH_EQ_NAMES,
    _CFO_NAMES,
    _EQUITY_NAMES,
    _LONG_DEBT_NAMES,
    _NET_INCOME_NAMES,
    _OPERATING_INCOME_NAMES,
    _REVENUE_NAMES,
    _SHARES_NAMES,
    _SHORT_DEBT_NAMES,
)
from commonsense.market.prices import get_quote

# Depreciation & amortization live in the cash-flow statement; needed for EBITDA.
_DA_NAMES = frozenset({
    "depreciationdepletionandamortization",
    "depreciationamortizationandaccretionnet",
    "depreciationandamortization",
    "depreciation",
    "amortizationofintangibleassets",
})

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F"}


def _matches(concept: str, names: frozenset[str]) -> bool:
    norm = _normalize_concept_for_match(concept)
    if norm in names:
        return True
    return any(cand in norm or norm in cand for cand in names)


def _annual_series(long_df: pd.DataFrame | None, names: frozenset[str]) -> pd.Series:
    """Annual (FY) values for the first matching concept, indexed by period end (sorted)."""
    if long_df is None or long_df.empty or "concept" not in long_df.columns:
        return pd.Series(dtype="float64")
    df = long_df.copy()
    mask = df["concept"].map(lambda c: _matches(str(c), names))
    df = df[mask]
    if df.empty:
        return pd.Series(dtype="float64")
    if "fp" in df.columns:
        annual = df[df["fp"].astype(str).str.upper() == "FY"]
        if not annual.empty:
            df = annual
    elif "form" in df.columns:
        annual = df[df["form"].astype(str).str.upper().isin(_ANNUAL_FORMS)]
        if not annual.empty:
            df = annual
    df = df.dropna(subset=["end", "value"])
    if df.empty:
        return pd.Series(dtype="float64")
    # One value per period end (take the largest |value| to prefer the fullest restatement).
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    grouped = df.sort_values("value").groupby("end")["value"].last()
    return grouped.sort_index()


def _latest(series: pd.Series) -> float | None:
    return float(series.iloc[-1]) if series is not None and not series.empty else None


def _latest_any(long_df: pd.DataFrame | None, names: frozenset[str]) -> float | None:
    """Most recent value (any period, not just annual) — for point-in-time balance items."""
    if long_df is None or long_df.empty or "concept" not in long_df.columns:
        return None
    df = long_df.copy()
    df = df[df["concept"].map(lambda c: _matches(str(c), names))]
    df = df.dropna(subset=["end", "value"])
    if df.empty:
        return None
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df = df.sort_values("end")
    return float(df["value"].iloc[-1])


def _cagr(series: pd.Series) -> float | None:
    """Annualized growth (%) between first and last positive annual points."""
    s = series.dropna()
    if len(s) < 2:
        return None
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    if first <= 0 or last <= 0:
        return None
    years = (s.index[-1] - s.index[0]).days / 365.25
    if years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def compute_multiples(
    company_ticker: str,
    data_dir: str | Path,
    *,
    price: float | None = None,
    write_csv: bool = True,
) -> dict[str, Any]:
    """Compute valuation multiples for one company. Returns a dict; optionally writes CSV.

    `price` overrides the live quote (useful for the screener's batched quotes/tests).
    """
    data_dir = Path(data_dir)
    inc, bal, cash = _load_facts_for_company(data_dir, company_ticker)

    revenue_s = _annual_series(inc, _REVENUE_NAMES)
    net_income_s = _annual_series(inc, _NET_INCOME_NAMES)
    ebit = _latest(_annual_series(inc, _OPERATING_INCOME_NAMES))
    d_a = _latest(_annual_series(cash, _DA_NAMES))
    cfo = _latest(_annual_series(cash, _CFO_NAMES))
    capex = _latest(_annual_series(cash, _CAPEX_NAMES))

    revenue = _latest(revenue_s)
    net_income = _latest(net_income_s)
    equity = _latest_any(bal, _EQUITY_NAMES)
    cash_eq = _latest_any(bal, _CASH_EQ_NAMES)
    long_debt = _latest_any(bal, _LONG_DEBT_NAMES) or 0.0
    short_debt = _latest_any(bal, _SHORT_DEBT_NAMES) or 0.0
    total_debt = long_debt + short_debt
    shares = _latest_any(bal, _SHARES_NAMES)

    # Live quote (price + market cap), preferring the SEC-derived share count.
    quote = get_quote(company_ticker, shares_outstanding=shares) if price is None else None
    px = price if price is not None else (quote.price if quote else None)
    if shares is None and quote is not None:
        shares = quote.shares_outstanding
    market_cap = (px * shares) if (px is not None and shares) else (quote.market_cap if quote else None)

    ebitda = (ebit + d_a) if (ebit is not None and d_a is not None) else None
    ev = None
    if market_cap is not None:
        ev = market_cap + total_debt - (cash_eq or 0.0)

    earnings_cagr = _cagr(net_income_s)
    revenue_cagr = _cagr(revenue_s)
    fcf = (cfo - abs(capex)) if (cfo is not None and capex is not None) else None

    def _div(a: float | None, b: float | None) -> float | None:
        if a is None or b is None or b == 0:
            return None
        return a / b

    pe = _div(market_cap, net_income) if (net_income and net_income > 0) else None
    ps = _div(market_cap, revenue)
    pb = _div(market_cap, equity) if (equity and equity > 0) else None
    ev_ebitda = _div(ev, ebitda) if (ebitda and ebitda > 0) else None
    ev_ebit = _div(ev, ebit) if (ebit and ebit > 0) else None
    peg = _div(pe, earnings_cagr) if (pe is not None and earnings_cagr and earnings_cagr > 0) else None

    as_of = str(revenue_s.index[-1].date()) if not revenue_s.empty else ""

    result: dict[str, Any] = {
        "ticker": company_ticker.upper(),
        "as_of_fiscal": as_of,
        "price": px,
        "shares_outstanding": shares,
        "market_cap": market_cap,
        "enterprise_value": ev,
        "pe": pe,
        "ps": ps,
        "pb": pb,
        "ev_ebitda": ev_ebitda,
        "ev_ebit": ev_ebit,
        "peg": peg,
        "earnings_cagr_pct": earnings_cagr,
        "revenue_cagr_pct": revenue_cagr,
        "fcf": fcf,
        "net_income": net_income,
        "revenue": revenue,
        "equity": equity,
        "total_debt": total_debt,
        "cash": cash_eq,
        "ebitda": ebitda,
        "price_source": (quote.source if quote else ("override" if price is not None else "")),
    }

    if write_csv and px is not None:
        out_dir = data_dir / company_ticker
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "ratios_valuation_multiples.csv"
        pd.DataFrame([result]).to_csv(csv_path, index=False)
        result["csv_path"] = str(csv_path)

    return result
