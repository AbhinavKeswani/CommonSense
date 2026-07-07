# CommonSense

A private, localized financial intelligence pipeline. CommonSense ingests SEC EDGAR data, runs common-size and flux analysis, extracts MD&A from filings, and stores results per company so you can feed them into a local LLM for analyst-style output—all without leaving your machine.

**Current state (v1, reality check):** SEC ingestion, per-ticker storage, MD&A extraction (Item 7 / 2 / 5), common-size and flux analysis (CSV), and a one-command test runner plus a Streamlit dashboard all run end-to-end. The major gap is MD&A quality: extraction is still inconsistent and often captures TOC/adjacent sections instead of the clean MD&A body.

---

## What’s implemented

### 1. SEC EDGAR ingestion

- **Ticker or CIK:** You can pass a ticker (e.g. `AAPL`, `MSFT`) or a CIK (e.g. `320193`). The pipeline resolves ticker/CIK via SEC data endpoints and always writes under a ticker folder.
- **Form discovery:** For each company we fetch submissions from data.sec.gov and read the filings.recent form list. We request only periodic report forms (10-K, 10-Q, 20-F, 40-F) that the company actually files. Foreign issuers (e.g. NVO) get 20-F automatically; domestic issuers get 10-K and 10-Q.
- **Single path:** SEC JSON endpoints for submissions/companyfacts + SEC Archives HTML for MD&A extraction. No SGML parsing path is required in the ingestion flow.
- **Output:** All output is under **`data/parquet/<ticker>/`** (e.g. `data/parquet/AAPL/`, `data/parquet/NVO/`). For each company you get:
  - `{ticker}_sec_submissions.parquet` (filing list; used for form discovery)
  - `{ticker}_sec_facts_income_statement.parquet`
  - `{ticker}_sec_facts_balance_sheet.parquet`
  - `{ticker}_sec_facts_cash_flow.parquet`
  (or `_sec_facts_all_concepts.parquet` when fallback buckets do not match.)
  - **MD&A:** `{base}_mdna.txt` per filing (Management’s Discussion and Analysis: 10-K Item 7, 10-Q Item 2, 20-F Item 5), for use as narrative context alongside the numbers.
- **Identity:** SEC requires a User-Agent. Set `EDGAR_EMAIL` in `.env`; it’s used as the contact identity (no sign-up). The edgartools cache is pointed at **`data/.edgar`** inside the project by default so you don’t need write access to `~/.edgar`.

### 1.1 Current validation snapshot (Feb 2026)

- **Run attempted on new SPX ticker not in local data (`NVDA`):**
  - Command: `python run_ticker.py NVDA`
  - Result: failed before ingestion with `could not resolve to CIK`.
  - Observed errors included SEC ticker fetch failures and cache writes to `~/.edgar/_cache` (permission blocked), so no `data/parquet/NVDA/` was created.
- **Run attempted on new SPX company by CIK (`731766`, UnitedHealth/UNH):**
  - Command: `python run_ticker.py 731766`
  - Result: fallback path succeeded and created `data/parquet/UNH/` with:
    - `UNH_sec_submissions.parquet`
    - `UNH_sec_facts_{income_statement,balance_sheet,cash_flow}.parquet`
    - `common_size_*.csv` and `flux_*.csv`
    - `UNH_*_mdna.txt`
  - edgartools form loop still emitted `~/.edgar/_cache` permission errors, but fallback data was written.

### 2. Common-size and flux analysis

- **Input:** The analysis module reads the fact Parquets for each company (income, balance sheet, cash flow). Line items are taken **directly from each company’s data** (concept names in the Parquet)—no separate mapping file.
- **Logic:**
  - Pivot long-form facts to wide (one row per period, one column per concept).
  - **Common-size:** Each line item as a percentage of a denominator (e.g. revenue for income, total assets for balance sheet). Denominator is chosen from the company’s own concept names (e.g. “Revenues”, “Assets”).
  - **Flux:** Period-over-period percent change.
- **Output:** Analysis is written as **CSV only** (no Parquet for analysis), under the same company folder:
  - `common_size_income_statement.csv`, `common_size_balance_sheet.csv`, `common_size_cash_flow.csv`
  - `flux_income_statement.csv`, `flux_balance_sheet.csv`, `flux_cash_flow.csv`  
  These are intended for review and for feeding into local models (we’re working on that integration).
- **Important behavior:** `run_ticker.py` currently runs `run_analysis_all(DATA_DIR)`, so it recomputes analysis for all companies in `data/parquet/`, not just the ticker passed on the command line.

### 2.1 Financial-health ratios + ratio flux

Cross-statement ratio suite (profitability, efficiency, liquidity, leverage, cash
flow, per-share) computed from the same wide fact tables → `ratios_financial_health.csv`
and period-over-period `flux_ratios_financial_health.csv` per company. These feed
both the Gemini context and the quality score below.

### 2.2 Valuation multiples (price-based)

`src/commonsense/analysis/valuation_multiples.py` joins the latest fiscal-year SEC
facts with a live market quote (`src/commonsense/market/prices.py`: yfinance primary,
keyless Yahoo-chart fallback; shares outstanding preferred from SEC facts) to compute
**P/E, P/S, P/B, EV/EBITDA, EV/EBIT, PEG** plus revenue/earnings CAGRs and FCF →
`ratios_valuation_multiples.csv` per company.

### 2.3 Composite quality score (`scores.json`)

`src/commonsense/analysis/scoring.py` scores each company 0–100 across four pillars
(profitability 0.30, growth 0.25, balance-sheet 0.20, cash-conversion 0.25). The
rubric lives in one data structure (`PILLARS`) that drives **both** the computation
and a `methodology` block emitted into `scores.json`, so the math and its rendered
definition can never drift. Verdicts: strong ≥75 · solid ≥60 · watch ≥45 · weak <45.
`scores.json` is the machine-readable contract consumed by Atlas
(see `Docs/Atlas_Integration.md`).

### 2.4 Universe screener

`python -m commonsense.screener` scores a whole universe (bundled
`data/universe/sp500.csv`: symbol, CIK, GICS sector/sub-industry) and ranks it
cross-sectionally → `data/parquet/screener/picks.json`:

- **Batched prices:** one yfinance `download` per ~100 symbols (not one request per name).
- **Facts-only ingest** (`fetch_mdna=False`) by CIK, sequential + SEC-throttled;
  cached names are skipped, so re-runs are incremental. `--no-ingest` re-ranks from
  cache in ~2 min; a cold full S&P 500 ingest is ~100 min.
- **Mispricing flag:** quality ≥ 60 AND valuation in the cheapest third of the GICS
  sector (EV/EBITDA preferred, else P/E, else P/S). `pick_score` blends quality (65%)
  with sector-relative cheapness (35%).
- CLI: `--limit N`, `--no-ingest`, `--force`.

MD&A is deliberately **not** fetched during bulk screens; it is pulled on demand per
company (latest 10-K/10-Q via the cached submissions parquet) when a deeper report
is opened — see `Docs/Atlas_Integration.md` §3.3.

### 3. Dashboard and test runner

- **Streamlit dashboard:** Run `./run.sh` to start the app and open the browser. Enter tickers (or CIKs), optionally form types (used only when form discovery returns no forms), and click “Run ingestion.” Results and errors are shown; data lands in `data/parquet/<ticker>/`.
- **Test runner:** From the project root, run:
  ```bash
  python run_ticker.py AAPL
  python run_ticker.py 353278   # CIK for NVO (Novo Nordisk)
  ```
  This runs ingestion using discovered forms per company (10-K/10-Q for domestic, 20-F for foreign issuers, etc.), then runs analysis for all companies in `DATA_DIR`, and prints where the analysis CSVs and MD&A files were written. Use it to confirm that data is stored correctly after setup.

### 3.1 Example execution (AAPL + Gemini Markdown output)

Use CIK if ticker lookup is flaky in your environment:

```bash
python3 run_ticker.py 320193
```

What this run produces under `data/parquet/AAPL/`:

- MD&A files (10-K/10-Q): `AAPL_*_mdna.txt`
- Structured analysis:
  - `common_size_income_statement.csv`
  - `common_size_balance_sheet.csv`
  - `common_size_cash_flow.csv`
  - `flux_income_statement.csv`
  - `flux_balance_sheet.csv`
  - `flux_cash_flow.csv`
- Gemini report (Markdown) under:
  - `data/parquet/AAPL/Analysis/AAPL_gemini_analysis_20260226_155904Z.md`

The AI context for this run included:

- MD&A files: 10
- Common-size CSVs: 3
- Flux CSVs: 3

---

## Project layout

```
CommonSense/
├── README.md
├── requirements.txt
├── run.sh              # Start Streamlit dashboard
├── run_ticker.py       # Ingest one ticker + run analysis, print CSV paths
├── .env.example        # EDGAR_EMAIL, DATA_DIR, optional EDGAR_LOCAL_DATA_DIR
├── src/commonsense/
│   ├── config.py       # EDGAR_EMAIL, DATA_DIR; sets EDGAR_LOCAL_DATA_DIR to data/.edgar
│   ├── screener.py     # Universe screener: ingest → score → cross-sectional rank → picks.json
│   ├── edgar/
│   │   ├── ingestion.py   # run_ingestion(tickers, forms, fetch_mdna=...); CIK + form discovery
│   │   ├── mdna.py        # MD&A extraction (Item 7/2/5) from filing HTML, size-bounded
│   │   ├── sec_api.py     # ticker_to_cik (+aliases/ticker.txt fallback), submissions, companyfacts
│   │   └── models.py      # Table name constants
│   ├── market/
│   │   └── prices.py    # get_quote (yfinance→Yahoo-chart), get_prices_batch (batched downloads)
│   ├── analysis/
│   │   ├── common_size_flux.py    # common-size, flux, financial-health ratios → CSV per company
│   │   ├── valuation_multiples.py # P/E, P/S, P/B, EV/EBITDA, PEG from facts + live quote
│   │   └── scoring.py             # PILLARS rubric → quality score + methodology → scores.json
│   └── dashboard/
│       └── app.py       # Streamlit UI
├── data/
│   ├── .edgar/          # edgartools cache (default; gitignored)
│   ├── universe/
│   │   └── sp500.csv    # Screener universe: symbol, CIK, GICS sector, sub-industry (tracked)
│   └── parquet/         # Output root (DATA_DIR; gitignored)
│       ├── screener/
│       │   └── picks.json            # Ranked universe (quality + mispricing)
│       └── <ticker>/    # e.g. AAPL/, MSFT/
│           ├── {ticker}_sec_facts_*.parquet
│           ├── {ticker}_sec_submissions.parquet
│           ├── *_mdna.txt            # fetched on demand, not during bulk screens
│           ├── common_size_*.csv, flux_*.csv
│           ├── ratios_financial_health.csv, flux_ratios_financial_health.csv
│           ├── ratios_valuation_multiples.csv
│           ├── scores.json           # quality score + methodology (Atlas contract)
│           └── Analysis/
│               └── *_gemini_analysis_*.md
└── Docs/                # Charter, research plan, Atlas integration, troubleshooting
```

---

## Fresh setup (first time on a new device)

1. **Virtual environment and dependencies**
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **Environment**
   ```bash
   cp .env.example .env
   # Edit .env: set EDGAR_EMAIL=your.name@example.com
   ```
   No sign-up; the SEC only needs a contact email in the User-Agent.

3. **Run the dashboard**
   ```bash
   ./run.sh
   ```
   This uses `.venv` if present, sets the edgartools cache to `data/.edgar`, and opens http://localhost:8501.

---

## Run locally

- **Dashboard:** `./run.sh` (or `python3 -m streamlit run src/commonsense/dashboard/app.py` with `PYTHONPATH=src`).
- **Test run by ticker:** Ingest one ticker and run analysis, then print where CSVs were written:
  ```bash
  python3 run_ticker.py AAPL
  # or
  .venv/bin/python run_ticker.py MSFT
  ```
  Run from the project root so `data/.edgar` and `data/parquet` resolve correctly.
- **Universe screener** (score + rank the S&P 500 → `data/parquet/screener/picks.json`):
  ```bash
  PYTHONPATH=src .venv/bin/python -m commonsense.screener               # full run (ingests missing names)
  PYTHONPATH=src .venv/bin/python -m commonsense.screener --limit 25    # first 25 names
  PYTHONPATH=src .venv/bin/python -m commonsense.screener --no-ingest   # re-rank from cached facts (~2 min)
  ```
  A cold full-universe ingest takes ~100 min (sequential, SEC fair-access); re-runs
  are incremental because cached facts are skipped.

---

## Config

| Variable | Description |
|----------|-------------|
| `EDGAR_EMAIL` | Your email for the SEC User-Agent (required). |
| `DATA_DIR` | Parquet output root (default: `data/parquet`). |
| `EDGAR_LOCAL_DATA_DIR` | Optional. Default is `data/.edgar` inside the project so edgartools doesn’t use `~/.edgar`. |

See `.env.example`.

---

## Example AAPL analysis (Gemini)

Generated from a real local run (`python3 run_ticker.py 320193`) and saved under:

`data/parquet/AAPL/Analysis/AAPL_gemini_analysis_20260226_155904Z.md`

### Executive summary

Apple's financial foundation remains exceptionally strong due to robust Services growth and formidable operational cash generation. However, a pattern of aggressive capital returns (primarily share repurchases) is steadily drawing down its once-massive cash reserves, introducing a subtle but growing liquidity vulnerability. Persistent macroeconomic headwinds and increasing tariff costs are also beginning to exert pressure on core product profitability, hinting at future operational challenges despite management's generally optimistic narrative.

### Narrative vs reality (sample)

| Management Claim (from MD&A) | Financial Evidence (from Data) | Analyst Verdict |
|---|---|---|
| **2023:** "Selling, general and administrative expense was relatively flat in 2023 compared to 2022." | Absolute SG&A decreased from $25,094M (2022) to $24,198M (2023), a -3.6% decline. | **Inconsistent** |
| **2023:** "Products gross margin percentage increased during 2023 compared to 2022..." | Products gross margin increased from 35.88% (2022) to 36.63% (2023). | **Consistent** |
| **2024:** "The growth in R&D expense during 2024 compared to 2023 was driven primarily by increases in headcount-related expenses." | R&D flux in 2024 vs 2023: +32.89%; R&D % of revenue increased from 7.80% to 8.02%. | **Consistent** |
| **2025:** "Products gross margin percentage decreased during 2025 compared to 2024..." | Products gross margin decreased from 36.70% (2024) to 35.61% (2025). | **Consistent** |

### Red-flag interactions (sample)

- Accounts receivable grew while total net sales declined in 2023, suggesting potential collection/counterparty stress.
- Cash and marketable securities declined materially over multiple years while buybacks/dividends stayed aggressive.
- Product gross margin in 2025 showed pressure from tariff and mix effects despite internal cost actions.

### Forward-looking outlook (sample)

Risk assessment from the generated report: **Medium-Low** over the next 12 months, with strong Services and liquidity offset by continued margin/cost and macro-tariff pressures.

---

## Deploy (online research system)

- **Streamlit Community Cloud:** Connect the repo, set `EDGAR_EMAIL` (and optionally `DATA_DIR`, `EDGAR_LOCAL_DATA_DIR`) in secrets, entrypoint `src/commonsense/dashboard/app.py`.
- **Other hosts:** Run `streamlit run src/commonsense/dashboard/app.py` with `PYTHONPATH=src` and expose the port.

---

## Troubleshooting

- **“Both data sources are unavailable” / hishel / ticker lookup:** We resolve ticker to CIK via the SEC company_tickers.json API first; if that fails, edgartools is tried. If both fail, use a **CIK** instead (e.g. `320193` for Apple, `353278` for Novo Nordisk/NVO). The project sets `EDGAR_LOCAL_DATA_DIR` to `data/.edgar` by default. See **Docs/EDGAR_Ingestion_Troubleshooting.md** for more.
- **NVO or other foreign tickers:** We discover forms from submissions, so 20-F is requested automatically when the company files it. If the ticker still does not resolve, use the company CIK (e.g. `run_ticker.py 353278` for Novo Nordisk).
- **Permission errors on `~/.edgar`:** Ensure `EDGAR_LOCAL_DATA_DIR` is set (e.g. in `.env` to `data/.edgar`) or let the default in `config.py` apply. See **Docs/EDGAR_Ingestion_Troubleshooting.md** for more detail.

---

## Atlas integration

CommonSense is the fundamentals engine behind the **Picks** tab of Atlas (the local
life dashboard). Atlas shells into this project's venv and consumes the on-disk
artifacts — `scores.json` per ticker and `data/parquet/screener/picks.json` — plus
two on-demand subprocess entry points (single-ticker lookup, MD&A fetch).
**The full contract, invocation invariants, JSON schemas, and stability rules live
in [`Docs/Atlas_Integration.md`](Docs/Atlas_Integration.md).** Read it before
renaming any output path or `scores.json` field.

---

## Known gaps (highest priority)

- **Industry-specific scoring overlays.** The universal `PILLARS` rubric mis-scores
  financials/REITs (e.g. banks get penalized on leverage that is structural to the
  business — JPM scores 22.6). The sector→metric overlay map is specced in
  `Docs/Research_Plan_Fundamental_Stock_Selection.md` §3 and is the top follow-up.
- **MD&A extraction quality is improved but not perfect across all historical issuer/form variants.**
  - Newer filings are generally clean; oversized "Item 7 spans 100+ pages" filers are
    now size-bounded, but some older filings may still include residual artifacts.
- **Ticker lookup can fail in some environments.**
  - Workaround: run by CIK (the universe CSV carries CIKs so the screener already does this).

---

## Next focus

- Implement the industry overlays (banks/SaaS/REITs/industrials) from the research plan.
- An investment-strategy filter that re-weights or swaps `PILLARS` (growth / value /
  income presets) — the data-driven rubric was built so this is a config change.
- Validate MD&A extraction against saved raw filings for the remaining edge-case issuers.

---

## Roadmap (beyond v1)

- **Local models:** MD&A, common-size, flux, ratio, and multiples outputs feeding a
  local analyst-style narrative (Ollama) instead of the cloud Gemini call.
- Optional context limiting (e.g. last N periods, material variances only) to keep prompts within model context and runtime reasonable.
- Portfolio-level analytics: fundamentals pick the holdings; price action sets the
  weights; tax/horizon-aware lot selection minimizes rebalancing costs (Atlas side).
