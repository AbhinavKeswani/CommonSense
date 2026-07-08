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

# EBIT derivation chain for filers that don't tag an operating-income subtotal
# (e.g. KLAC stopped tagging OperatingIncomeLoss in 2015).
_GROSS_PROFIT_NAMES_VM = frozenset({"grossprofit"})
_OPEX_NAMES = frozenset({"operatingexpenses"})
_PRETAX_NAMES = frozenset({
    "incomelossfromcontinuingoperationsbeforeincometaxesextraordinaryitemsnoncontrollinginterest",
    "incomelossfromcontinuingoperationsbeforeincometaxesminorityinterestandincomelossfromequitymethodinvestments",
})
_TAX_NAMES = frozenset({"incometaxexpensebenefit"})
_INTEREST_NAMES = frozenset({"interestexpense", "interestexpensenonoperating", "interestanddebtexpense"})

# A flow series older than this (vs. the company's latest revenue period) is
# treated as missing — a filer that stopped tagging a concept must not
# contribute a years-stale number to a current multiple.
_MAX_STALENESS_DAYS = 550

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


def _latest_fresh(series: pd.Series, ref_end: pd.Timestamp | None) -> float | None:
    """Latest value of an annual series, or None if it's stale vs. ref_end."""
    if series is None or series.empty:
        return None
    if ref_end is not None and (ref_end - series.index[-1]).days > _MAX_STALENESS_DAYS:
        return None
    return float(series.iloc[-1])


def _derive_ebit(inc: pd.DataFrame | None, ref_end: pd.Timestamp | None) -> float | None:
    """EBIT with fallbacks, freshness-guarded at every step:

    1. OperatingIncomeLoss (direct tag)
    2. GrossProfit − OperatingExpenses
    3. Pretax income + interest expense
    4. Net income + tax + interest expense
    """
    direct = _latest_fresh(_annual_series(inc, _OPERATING_INCOME_NAMES), ref_end)
    if direct is not None:
        return direct
    gp_s = _annual_series(inc, _GROSS_PROFIT_NAMES_VM)
    opex_s = _annual_series(inc, _OPEX_NAMES)
    if not gp_s.empty and not opex_s.empty:
        common = gp_s.index.intersection(opex_s.index)
        if len(common) and (ref_end is None or (ref_end - common[-1]).days <= _MAX_STALENESS_DAYS):
            return float(gp_s.loc[common[-1]] - opex_s.loc[common[-1]])
    pretax = _latest_fresh(_annual_series(inc, _PRETAX_NAMES), ref_end)
    interest = _latest_fresh(_annual_series(inc, _INTEREST_NAMES), ref_end)
    if pretax is not None:
        return pretax + (interest or 0.0)
    net = _latest_fresh(_annual_series(inc, _NET_INCOME_NAMES), ref_end)
    tax = _latest_fresh(_annual_series(inc, _TAX_NAMES), ref_end)
    if net is not None and tax is not None:
        return net + tax + (interest or 0.0)
    return None


def _ebitda_from_yfinance(symbol: str) -> float | None:
    """Trailing EBITDA from Yahoo — fallback only, when SEC D&A can't be classified."""
    try:
        import yfinance as yf
        v = (yf.Ticker(symbol).info or {}).get("ebitda")
        return float(v) if v else None
    except Exception:  # noqa: BLE001 - fallback must never break the SEC path
        return None


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
    # Revenue is the always-tagged reference period: any flow whose latest point
    # is much older than this is a discontinued tag, not current data.
    ref_end = revenue_s.index[-1] if not revenue_s.empty else None
    ebit = _derive_ebit(inc, ref_end)
    d_a = _latest_fresh(_annual_series(cash, _DA_NAMES), ref_end)
    cfo = _latest_fresh(_annual_series(cash, _CFO_NAMES), ref_end)
    capex = _latest_fresh(_annual_series(cash, _CAPEX_NAMES), ref_end)

    revenue = _latest(revenue_s)
    net_income = _latest_fresh(net_income_s, ref_end)
    equity = _latest_any(bal, _EQUITY_NAMES)
    cash_eq = _latest_any(bal, _CASH_EQ_NAMES)
    long_debt = _latest_any(bal, _LONG_DEBT_NAMES) or 0.0
    short_debt = _latest_any(bal, _SHORT_DEBT_NAMES) or 0.0
    total_debt = long_debt + short_debt
    shares = _latest_any(bal, _SHARES_NAMES)

    # Live quote (price + market cap), preferring the SEC-derived share count.
    # Fetch the quote even when a batch price override is supplied if we still
    # need a share count — otherwise market cap (and every multiple) dies here.
    quote = None
    if price is None or shares is None:
        quote = get_quote(company_ticker, shares_outstanding=shares)
    px = price if price is not None else (quote.price if quote else None)
    if shares is None and quote is not None:
        shares = quote.shares_outstanding
    market_cap = (px * shares) if (px is not None and shares) else (quote.market_cap if quote else None)

    ebitda = (ebit + d_a) if (ebit is not None and d_a is not None) else None
    ebitda_source = "sec" if ebitda is not None else ""
    if ebitda is None:
        # Last resort: Yahoo's own trailing EBITDA. SEC facts stay the primary
        # source; this only fills filers whose D&A tag we still can't classify.
        ebitda = _ebitda_from_yfinance(company_ticker)
        if ebitda is not None:
            ebitda_source = "yfinance"
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
        "ebitda_source": ebitda_source,
        "price_source": (quote.source if quote else ("override" if price is not None else "")),
    }

    if write_csv and px is not None:
        out_dir = data_dir / company_ticker
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "ratios_valuation_multiples.csv"
        pd.DataFrame([result]).to_csv(csv_path, index=False)
        result["csv_path"] = str(csv_path)

    return result
