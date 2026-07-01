"""Composite fundamental quality score per ticker.

Implements the quality-first rubric from
`Docs/Research_Plan_Fundamental_Stock_Selection.md`: score *current fundamental
quality* across four pillars (profitability, growth, balance-sheet strength, cash
conversion), then combine into a 0-100 quality score. Valuation multiples are
attached raw — the *relative* mispricing signal (cheap vs. peers with intact
quality) is computed cross-sectionally by the screener, which has peer context.

Output: `scores.json` per ticker + a returned dict (Atlas's consumption contract).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from commonsense.analysis.valuation_multiples import compute_multiples


def _score_linear(value: float | None, lo: float, hi: float, invert: bool = False) -> float | None:
    """Map value to 0-100 linearly between lo (=0) and hi (=100), clamped. invert flips it."""
    if value is None or pd.isna(value):
        return None
    if hi == lo:
        return None
    frac = (value - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    if invert:
        frac = 1.0 - frac
    return round(frac * 100.0, 1)


def _latest_ratio_row(data_dir: Path, ticker: str) -> dict[str, float]:
    """Latest row of ratios_financial_health.csv as {metric: value}."""
    path = data_dir / ticker / "ratios_financial_health.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, index_col=0)
    except Exception:
        return {}
    if df.empty:
        return {}
    row = df.iloc[-1]
    out: dict[str, float] = {}
    for k, v in row.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not pd.isna(fv):
            out[str(k)] = fv
    return out


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 1) if present else None


def _weighted(subscores: dict[str, float | None], weights: dict[str, float]) -> float | None:
    num = 0.0
    den = 0.0
    for key, w in weights.items():
        s = subscores.get(key)
        if s is not None:
            num += s * w
            den += w
    return round(num / den, 1) if den else None


def score_company(
    company_ticker: str,
    data_dir: str | Path,
    *,
    price: float | None = None,
    write_json: bool = True,
) -> dict[str, Any]:
    """Compute the composite quality score + attach valuation multiples for one ticker."""
    data_dir = Path(data_dir)
    ticker = company_ticker.upper()

    m = _latest_ratio_row(data_dir, ticker)
    mult = compute_multiples(ticker, data_dir, price=price, write_csv=True)

    # --- Pillar sub-scores (0-100) ---
    profitability = _mean([
        _score_linear(m.get("net_margin_pct"), 0, 25),
        _score_linear(m.get("return_on_equity_pct"), 5, 30),
        _score_linear(m.get("return_on_assets_pct"), 2, 15),
        _score_linear(m.get("gross_margin_pct"), 20, 70),
    ])
    growth = _mean([
        _score_linear(mult.get("revenue_cagr_pct"), 0, 20),
        _score_linear(mult.get("earnings_cagr_pct"), 0, 25),
    ])
    balance_sheet = _mean([
        _score_linear(m.get("debt_to_equity"), 0.0, 2.0, invert=True),
        _score_linear(m.get("current_ratio"), 1.0, 2.5),
    ])
    cash_conversion = _mean([
        _score_linear(m.get("free_cash_flow_margin_pct"), 0, 25),
        _score_linear(m.get("operating_cash_flow_to_net_income"), 0.6, 1.3),
    ])

    subscores = {
        "profitability": profitability,
        "growth": growth,
        "balance_sheet": balance_sheet,
        "cash_conversion": cash_conversion,
    }
    # Quality-gated growth: profitability + cash conversion carry the most weight.
    quality_score = _weighted(subscores, {
        "profitability": 0.30,
        "growth": 0.25,
        "balance_sheet": 0.20,
        "cash_conversion": 0.25,
    })

    # --- Human-readable flags ---
    flags: list[str] = []
    if m.get("debt_to_equity") is not None and m["debt_to_equity"] > 2.0:
        flags.append("high leverage (D/E > 2)")
    if mult.get("fcf") is not None and mult["fcf"] < 0:
        flags.append("negative free cash flow")
    if m.get("return_on_equity_pct") is not None and m["return_on_equity_pct"] >= 20:
        flags.append("strong ROE (>= 20%)")
    if mult.get("revenue_cagr_pct") is not None and mult["revenue_cagr_pct"] >= 15:
        flags.append("high revenue growth (>= 15% CAGR)")
    if m.get("current_ratio") is not None and m["current_ratio"] < 1.0:
        flags.append("current ratio < 1 (liquidity watch)")

    if quality_score is None:
        verdict = "insufficient-data"
    elif quality_score >= 75:
        verdict = "strong"
    elif quality_score >= 60:
        verdict = "solid"
    elif quality_score >= 45:
        verdict = "watch"
    else:
        verdict = "weak"

    result: dict[str, Any] = {
        "ticker": ticker,
        "as_of_fiscal": mult.get("as_of_fiscal", ""),
        "quality_score": quality_score,
        "verdict": verdict,
        "subscores": subscores,
        "flags": flags,
        "metrics": m,
        "multiples": mult,
    }

    if write_json:
        out_dir = data_dir / ticker
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "scores.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        result["json_path"] = str(out_dir / "scores.json")

    return result
