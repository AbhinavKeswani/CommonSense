# Atlas ↔ CommonSense Integration

*Updated 2026-07-04. This is the consumption contract between CommonSense (the
fundamentals engine) and Atlas (the local life dashboard whose **Picks** tab
surfaces stock recommendations). If you rename an artifact, field, or module
listed here, Atlas breaks — update `engine/atlas/commonsense_bridge.py` on the
Atlas side in the same change.*

---

## 1. Architecture

CommonSense is never imported by Atlas. Atlas shells into CommonSense's own venv
(subprocess) and communicates through **JSON/parquet artifacts on disk**:

```
┌─────────────────────────── Atlas (FastAPI, its own venv) ──────────────────────────┐
│  engine/atlas/commonsense_bridge.py                                                │
│    run_screen()      → subprocess: python -m commonsense.screener [--no-ingest]    │
│    lookup_ticker()   → subprocess: _LOOKUP_SNIPPET  (single-ticker pull + score)   │
│    fetch_mdna()      → subprocess: _MDNA_SNIPPET    (on-demand MD&A for one name)  │
│    read_picks()      ← data/parquet/screener/picks.json                            │
│    read_scores(sym)  ← data/parquet/<SYM>/scores.json                              │
│    read_mdna(sym)    ← data/parquet/<SYM>/*_mdna.txt                               │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Invocation invariants (all three subprocess paths):
- **Interpreter:** `<COMMONSENSE_ROOT>/.venv/bin/python`. The venv was created at
  an older path, so its `pip` shebang is broken — always `python -m pip`.
- **cwd:** `COMMONSENSE_ROOT` (so `.env` is found by python-dotenv).
- **env:** `PYTHONPATH=<COMMONSENSE_ROOT>/src` (src-layout package, not installed).
- Atlas locates the project via `ATLAS_COMMONSENSE_ROOT` (defaults to the Desktop
  path) — see `engine/atlas/config.py`.

## 2. Artifacts Atlas reads (the contract)

### 2.1 `data/parquet/screener/picks.json` — ranked universe

Written by `python -m commonsense.screener`. Shape:

```json
{
  "generated_at": "...", "count": 487, "screened": 503,
  "skipped": ["XYZ: no data (ingest=off)"],
  "picks": [{
    "rank": 1, "symbol": "KLAC", "sector": "Information Technology",
    "sub_industry": "Semiconductor Materials & Equipment",
    "quality_score": 72.9, "verdict": "solid",
    "subscores": {"profitability": ..., "growth": ..., "balance_sheet": ..., "cash_conversion": ...},
    "flags": ["strong ROE (>= 20%)"],
    "price": 235.55, "cheapness_metric": 7.6, "cheap_pctile": 98.6,
    "mispricing": true, "pick_score": 80.6,
    "multiples": { "pe": ..., "ps": ..., "pb": ..., "ev_ebitda": ..., "peg": ..., "...": "see scores.json" }
  }]
}
```

Atlas caches this in its `picks` setting and derives **Today's Picks** (top-10
not already held, dismissed names deprioritized) from it.

### 2.2 `data/parquet/<SYM>/scores.json` — per-ticker score

Written by `commonsense.analysis.scoring.score_company(..., write_json=True)`.
Top-level keys Atlas renders directly in the breakout drawer:

| key             | meaning                                                          |
|-----------------|------------------------------------------------------------------|
| `quality_score` | 0–100 weighted mean of the four pillars                          |
| `verdict`       | strong ≥75 · solid ≥60 · watch ≥45 · weak <45 · insufficient-data |
| `subscores`     | per-pillar 0–100 (profitability, growth, balance_sheet, cash_conversion) |
| `methodology`   | **machine-readable definition of the math** (below)              |
| `flags`         | human-readable notable conditions                                 |
| `metrics`       | latest row of `ratios_financial_health.csv`                       |
| `multiples`     | output of `valuation_multiples.compute_multiples` (P/E, P/S, P/B, EV/EBITDA, PEG, CAGRs, price, market cap) |

`methodology` mirrors the `PILLARS` rubric in `src/commonsense/analysis/scoring.py`
(single source of truth: the same structure drives the computation and the doc).
Atlas renders it as the "How this score is computed" accordion, so **every scored
graphic in the UI carries its exact mathematical definition**:

```json
{ "summary": "Each metric maps linearly to 0-100 between its floor (=0) and target (=100)...",
  "verdict_buckets": {"strong": ">= 75", "solid": "60-74", "watch": "45-59", "weak": "< 45"},
  "pillars": [{ "name": "profitability", "weight": 0.30,
                "metrics": [{"label": "Net margin %", "source": "ratio", "definition": "0 → 25"}, ...] }, ...] }
```

### 2.3 Other per-ticker files

- `ratios_valuation_multiples.csv` — one-row multiples snapshot (also inside scores.json).
- `ratios_financial_health.csv`, `flux_*.csv`, `common_size_*.csv` — analysis CSVs.
- `<SYM>_sec_facts_*.parquet`, `<SYM>_sec_submissions.parquet` — raw SEC facts + filing index.
- `<SYM>_10-K_YYYYMMDD_mdna.txt` / `10-Q` — MD&A text, **only after** an on-demand fetch (§3.3).

## 3. Subprocess entry points Atlas uses

### 3.1 Universe screen — `python -m commonsense.screener`
- `--limit N` (subset), `--no-ingest` (re-rank from cached facts only, no network),
  `--force` (re-ingest even if cached).
- Full S&P 500 facts-only ingest ≈ 100 min (SEC-throttled); `--no-ingest` re-rank ≈ 2 min.
- Universe file: `data/universe/sp500.csv` (`symbol,cik,sector,sub_industry`).
  CIK column lets ingestion skip ticker→CIK resolution (more reliable).
- Prices come batched via `market.prices.get_prices_batch` (yfinance
  `yf.download`, ~100 symbols per HTTP call — do not revert to per-ticker quotes).

### 3.2 Single-ticker lookup (Atlas `_LOOKUP_SNIPPET`)
Used by the Picks-tab **ticker lookup bar**: reference-first, pull-on-miss.
Reuses `screener._ensure_ticker_data` + `analysis.score_company` for one symbol —
same pipeline as the bulk screen, ~20 s cold. Contract: prints one JSON line
`{"ok": true, "ticker", "quality_score", "verdict"}` or `{"error": "..."}`.

### 3.3 On-demand MD&A (Atlas `_MDNA_SNIPPET`)
The bulk screen ingests **facts only** (`fetch_mdna=False`) for speed; MD&A for a
name is fetched lazily when its breakout is opened. The snippet reads
`<SYM>_sec_submissions.parquet`, takes the latest 10-K/10-Q/20-F rows, and calls
`edgar.mdna.write_mdna_for_filing` (using `primaryDocument` to skip the index
fetch). Idempotent: existing `*_mdna.*` files are returned, not re-fetched.

## 4. What Atlas builds on top (context for CommonSense changes)

- **Breakout drawer** = scores.json (badge, pillar bars, methodology accordion,
  multiples chips) + Atlas-side price chart (yfinance daily adjusted close, with
  sector-ETF/SPY/peer-basket overlays normalized to %) + Claude analysis
  (thesis grounded ONLY in scores.json — the decision is the math's, Claude
  narrates it), C-suite roster + CEO history, semantic news reads, and the MD&A
  narrative-vs-numbers checks quoting §3.3's text against §2.2's metrics.
- **Daily job** (Atlas cron, 07:30): gap-fills adjusted closes into Atlas's own
  SQLite `price_history`, refreshes picks from `picks.json` if newer.
- **Watchlist/lookup**: watchlist rows are enriched from `read_scores()`;
  the lookup bar checks `scores.json` existence to decide "in our system" vs
  "launch a new pull" (§3.2).

## 5. Stability rules

1. **Paths are API.** `data/parquet/<SYM>/scores.json`,
   `data/parquet/screener/picks.json`, `*_mdna.txt`, and
   `<SYM>_sec_submissions.parquet` names/locations must not move silently.
   (`picks.json` deliberately lives under `DATA_DIR/screener/`, NOT `data/screener/`.)
2. **scores.json keys in §2.2 are load-bearing** (quality_score, verdict,
   subscores, methodology, multiples.{pe,ps,pb,ev_ebitda,peg,price}).
   Additive changes are safe; renames/removals require an Atlas bridge update.
3. **Scoring changes must keep `PILLARS` as the single source of truth** so the
   emitted methodology always matches the computation. Atlas caches its Claude
   analyses keyed on a version prefix (`pick_analysis_v2:`) — bump it there when
   the analysis-relevant shape changes.
4. **SEC fair access:** ingestion is sequential with a descriptive User-Agent
   (`EDGAR_EMAIL`); don't parallelize the EDGAR fetches. Yahoo bursts get the IP
   429-banned — batch (`get_prices_batch`) instead of looping quotes.
