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

# Data-driven rubric — the single source of truth for BOTH the score computation and
# the methodology block emitted in scores.json (so the math and its definition can't
# drift). Each metric maps linearly to 0-100 between floor (=0) and target (=100),
# clamped; a pillar score is the mean of its metrics; the quality score is the
# weighted mean of pillars. `source` is "ratio" (ratios_financial_health.csv) or
# "multiple" (ratios_valuation_multiples.csv).
PILLARS: list[dict[str, Any]] = [
    {"name": "profitability", "weight": 0.30, "metrics": [
        {"key": "net_margin_pct", "label": "Net margin %", "source": "ratio", "floor": 0, "target": 25},
        {"key": "return_on_equity_pct", "label": "Return on equity %", "source": "ratio", "floor": 5, "target": 30},
        {"key": "return_on_assets_pct", "label": "Return on assets %", "source": "ratio", "floor": 2, "target": 15},
        {"key": "gross_margin_pct", "label": "Gross margin %", "source": "ratio", "floor": 20, "target": 70},
    ]},
    {"name": "growth", "weight": 0.25, "metrics": [
        {"key": "revenue_cagr_pct", "label": "Revenue CAGR %", "source": "multiple", "floor": 0, "target": 20},
        {"key": "earnings_cagr_pct", "label": "Earnings CAGR %", "source": "multiple", "floor": 0, "target": 25},
    ]},
    {"name": "balance_sheet", "weight": 0.20, "metrics": [
        {"key": "debt_to_equity", "label": "Debt / equity", "source": "ratio", "floor": 2.0, "target": 0.0, "invert": True},
        {"key": "current_ratio", "label": "Current ratio", "source": "ratio", "floor": 1.0, "target": 2.5},
    ]},
    {"name": "cash_conversion", "weight": 0.25, "metrics": [
        {"key": "free_cash_flow_margin_pct", "label": "Free cash flow margin %", "source": "ratio", "floor": 0, "target": 25},
        {"key": "operating_cash_flow_to_net_income", "label": "OCF / net income", "source": "ratio", "floor": 0.6, "target": 1.3},
    ]},
]

VERDICT_BUCKETS = [(75, "strong"), (60, "solid"), (45, "watch")]


def _verdict(score: float | None) -> str:
    if score is None:
        return "insufficient-data"
    for cutoff, label in VERDICT_BUCKETS:
        if score >= cutoff:
            return label
    return "weak"


def methodology() -> dict[str, Any]:
    """Human-/machine-readable definition of the scoring math (rendered in the report)."""
    return {
        "summary": (
            "Each metric maps linearly to 0-100 between its floor (=0) and target (=100), "
            "clamped. A pillar score is the mean of its metric scores; the quality score is "
            "the weighted mean of the pillars. Higher is better."
        ),
        "verdict_buckets": {"strong": ">= 75", "solid": "60-74", "watch": "45-59", "weak": "< 45"},
        "pillars": [
            {"name": p["name"], "weight": p["weight"], "metrics": [
                {"label": mt["label"], "source": mt["source"],
                 "definition": (f"{mt['floor']} → {mt['target']}" + (" (inverted: lower is better)" if mt.get("invert") else ""))}
                for mt in p["metrics"]
            ]}
            for p in PILLARS
        ],
    }


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

    # --- Pillar sub-scores (0-100), driven by the PILLARS rubric ---
    def _metric_value(mt: dict[str, Any]) -> float | None:
        src = mult if mt["source"] == "multiple" else m
        return src.get(mt["key"])

    subscores: dict[str, float | None] = {}
    weights: dict[str, float] = {}
    for p in PILLARS:
        subscores[p["name"]] = _mean([
            _score_linear(_metric_value(mt), mt["floor"], mt["target"], invert=mt.get("invert", False))
            for mt in p["metrics"]
        ])
        weights[p["name"]] = p["weight"]
    quality_score = _weighted(subscores, weights)

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

    verdict = _verdict(quality_score)

    result: dict[str, Any] = {
        "ticker": ticker,
        "as_of_fiscal": mult.get("as_of_fiscal", ""),
        "quality_score": quality_score,
        "verdict": verdict,
        "subscores": subscores,
        "methodology": methodology(),
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
