"""Universe screener: score & rank a candidate universe by quality + mispricing.

For each ticker in a universe file (symbol,sector) it ensures SEC data is present
(ingesting if not cached), runs analysis + scoring, then ranks cross-sectionally:
within each sector it measures how cheap a name is vs. its peers and combines that
with the quality score. The "mispricing" flag marks high-quality names trading
cheap vs. sector peers — the long signal from the research plan.

Output: data/screener/picks.json (ranked). Runtime artifact (data/ is gitignored).

CLI:
    python -m commonsense.screener                    # full default universe
    python -m commonsense.screener --limit 10         # first 10 (bounded test)
    python -m commonsense.screener --no-ingest        # score only what's cached
    python -m commonsense.screener --universe path.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

from commonsense.analysis import run_analysis_for_company, score_company
from commonsense.config import DATA_DIR, EDGAR_EMAIL
from commonsense.edgar.ingestion import run_ingestion
from commonsense.market.prices import get_prices_batch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_UNIVERSE = _PROJECT_ROOT / "universe" / "sp500.csv"
DEFAULT_FORMS = ["10-K", "10-Q", "20-F"]

# Quality gate for the mispricing flag, and how cheap (peer percentile) counts as cheap.
MISPRICING_MIN_QUALITY = 60.0
CHEAP_PERCENTILE = 0.34  # cheapest third within the sector
# Composite rank weighting.
QUALITY_WEIGHT = 0.7
CHEAPNESS_WEIGHT = 0.3


def load_universe(path: str | Path) -> list[dict[str, str]]:
    """Read universe rows from a CSV. Accepts our own (symbol,sector,sub_industry,cik)
    columns or the S&P 500 constituents export (Symbol,Security,GICS Sector,
    GICS Sub-Industry,...,CIK)."""
    path = Path(path)
    out: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            sym = (r.get("symbol") or "").upper()
            if not sym:
                continue
            out.append({
                "symbol": sym,
                "sector": r.get("sector") or r.get("gics sector") or "Unknown",
                "sub_industry": r.get("sub_industry") or r.get("gics sub-industry") or "",
                "cik": (r.get("cik") or "").lstrip("0"),
            })
    return out


def _has_cached_facts(data_dir: Path, ticker: str) -> bool:
    p = data_dir / ticker / f"{ticker}_sec_facts_income_statement.parquet"
    return p.exists()


def _ensure_ticker_data(entry: dict[str, str], data_dir: Path, email: str, *, ingest: bool, force: bool) -> bool:
    """Ensure facts + analysis exist for a universe entry. Returns True if data is available.

    Ingests by CIK when we have it (skips ticker→CIK resolution) and pulls XBRL facts
    only (fetch_mdna=False) — MD&A is fetched on demand when a pick is opened.
    """
    ticker = entry["symbol"]
    if not force and _has_cached_facts(data_dir, ticker):
        run_analysis_for_company(ticker, data_dir)  # cheap, no network
        return True
    if not ingest:
        return _has_cached_facts(data_dir, ticker)
    ref = entry.get("cik") or ticker  # CIK is more reliable than ticker resolution
    res = run_ingestion(tickers=[ref], forms=DEFAULT_FORMS, output_dir=data_dir,
                        email=email, fetch_mdna=False)
    if res.get("errors"):
        for e in res["errors"]:
            print(f"    ingest: {e}", file=sys.stderr)
    if not _has_cached_facts(data_dir, ticker):
        return False
    run_analysis_for_company(ticker, data_dir)
    return True


def _cheapness_metric(mult: dict[str, Any]) -> float | None:
    """The valuation metric used for peer cheapness — prefer EV/EBITDA, then P/E, then P/S."""
    for key in ("ev_ebitda", "pe", "ps"):
        v = mult.get(key)
        if v is not None and v > 0:
            return float(v)
    return None


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add sector-relative cheapness percentile, composite pick_score, and mispricing flag."""
    scored = [r for r in rows if r.get("quality_score") is not None]
    if not scored:
        return []
    df = pd.DataFrame(scored)
    df["cheap_metric"] = df["cheapness_metric"]

    # Percentile of the cheapness metric within sector (0 = cheapest). Fall back to
    # the whole universe when a sector has too few valued names to compare.
    df["cheap_pctile"] = pd.NA
    for sector, grp in df.groupby("sector"):
        valued = grp["cheap_metric"].notna()
        if valued.sum() >= 3:
            df.loc[grp.index[valued], "cheap_pctile"] = grp.loc[valued, "cheap_metric"].rank(pct=True)
    # Universe-wide fallback for names still missing a percentile.
    missing = df["cheap_pctile"].isna() & df["cheap_metric"].notna()
    if missing.any():
        df.loc[missing, "cheap_pctile"] = df.loc[missing, "cheap_metric"].rank(pct=True)

    # cheap_pctile may hold pd.NA (names with no valuation metric) — astype(float)
    # raises on NAType, so coerce through to_numeric instead.
    cheap = pd.to_numeric(df["cheap_pctile"], errors="coerce")
    df["cheapness_score"] = cheap.apply(
        lambda p: round((1.0 - float(p)) * 100.0, 1) if pd.notna(p) else 50.0
    )
    df["pick_score"] = (QUALITY_WEIGHT * df["quality_score"] + CHEAPNESS_WEIGHT * df["cheapness_score"]).round(1)
    df["mispricing"] = (
        (df["quality_score"] >= MISPRICING_MIN_QUALITY)
        & cheap.notna()
        & (cheap <= CHEAP_PERCENTILE)
    )
    df = df.sort_values("pick_score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    df["cheap_pctile"] = df["cheap_pctile"].apply(lambda p: round(float(p), 3) if pd.notna(p) else None)
    return df.to_dict("records")


def screen_universe(
    entries: list[dict[str, str]],
    data_dir: str | Path = DATA_DIR,
    email: str = EDGAR_EMAIL,
    *,
    ingest: bool = True,
    force: bool = False,
    limit: int | None = None,
    write_json: bool = True,
) -> dict[str, Any]:
    """Score every name in `entries`, rank cross-sectionally, write picks.json."""
    data_dir = Path(data_dir)
    if limit is not None:
        entries = entries[:limit]

    # Prices for the whole universe in a few batched requests (not one per name).
    prices = get_prices_batch([e["symbol"] for e in entries])
    print(f"Batched prices for {len(prices)}/{len(entries)} names.")

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    total = len(entries)
    for i, entry in enumerate(entries, 1):
        symbol, sector = entry["symbol"], entry.get("sector", "Unknown")
        print(f"[{i}/{total}] {symbol} ({sector})...")
        try:
            if not _ensure_ticker_data(entry, data_dir, email, ingest=ingest, force=force):
                skipped.append(f"{symbol}: no data (ingest={'on' if ingest else 'off'})")
                continue
            score = score_company(symbol, data_dir, price=prices.get(symbol), write_json=True)
            mult = score.get("multiples", {})
            rows.append({
                "symbol": symbol,
                "sector": sector,
                "sub_industry": entry.get("sub_industry", ""),
                "quality_score": score.get("quality_score"),
                "verdict": score.get("verdict"),
                "subscores": score.get("subscores"),
                "flags": score.get("flags"),
                "price": mult.get("price"),
                "cheapness_metric": _cheapness_metric(mult),
                "multiples": {
                    "pe": mult.get("pe"), "ps": mult.get("ps"), "pb": mult.get("pb"),
                    "ev_ebitda": mult.get("ev_ebitda"), "peg": mult.get("peg"),
                },
            })
            if score.get("quality_score") is None:
                skipped.append(f"{symbol}: scored but insufficient data for quality")
        except Exception as e:
            skipped.append(f"{symbol}: {e!s}")
            print(f"    error: {e}", file=sys.stderr)

    picks = _rank(rows)
    result = {
        "count": len(picks),
        "screened": total,
        "skipped": skipped,
        "picks": picks,
    }

    if write_json:
        out_dir = data_dir / "screener"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "picks.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        result["json_path"] = str(out_dir / "picks.json")

    # Console summary — never silently drop names.
    print(f"\nRanked {len(picks)}/{total}. Skipped {len(skipped)}.")
    for s in skipped:
        print(f"  skip: {s}")
    for p in picks[:10]:
        print(f"  #{p['rank']:>2} {p['symbol']:<6} {p['sector']:<14} "
              f"Q={p['quality_score']} cheap={p['cheapness_score']} pick={p['pick_score']} "
              f"{'MISPRICED' if p['mispricing'] else ''}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Screen a universe by fundamental quality + mispricing.")
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE), help="CSV with symbol,sector columns")
    ap.add_argument("--limit", type=int, default=None, help="Only screen the first N names")
    ap.add_argument("--no-ingest", action="store_true", help="Score only cached tickers (no SEC fetch)")
    ap.add_argument("--force", action="store_true", help="Re-ingest even if cached")
    args = ap.parse_args()

    if not EDGAR_EMAIL and not args.no_ingest:
        print("Error: EDGAR_EMAIL required for ingestion (set in .env) or use --no-ingest.", file=sys.stderr)
        sys.exit(1)

    entries = load_universe(args.universe)
    if not entries:
        print(f"No universe entries in {args.universe}", file=sys.stderr)
        sys.exit(1)
    screen_universe(entries, ingest=not args.no_ingest, force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
