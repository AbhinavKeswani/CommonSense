# CommonSense

A private, localized financial intelligence pipeline. CommonSense ingests SEC EDGAR data, runs common-size and flux analysis, and stores results per company so you can feed them into a local LLM (e.g. Ollama) for analyst-style output—all without leaving your machine.

**Current state (v1):** SEC ingestion, per-ticker storage, common-size and flux analysis (CSV), and a one-command test runner plus a Streamlit dashboard to trigger ingestion from the browser.

---

## What’s implemented

### 1. SEC EDGAR ingestion

- **Ticker or CIK:** You can pass a ticker (e.g. `AAPL`, `MSFT`) or a CIK (e.g. `1652044`). The pipeline resolves ticker to CIK via the SEC company_tickers.json API first; if that fails, it tries edgartools. When only a CIK is used, the company ticker is taken from the SEC submissions API for folder and file names.
- **Form discovery:** For each company we fetch submissions from data.sec.gov and read the filings.recent form list. We request only periodic report forms (10-K, 10-Q, 20-F, 40-F) that the company actually files. Foreign issuers (e.g. NVO) get 20-F automatically; domestic issuers get 10-K and 10-Q.
- **Two paths:**
  - **edgartools:** We call Company(CIK) and request filings only for the forms discovered from submissions. Writes metadata and statement Parquets under a per-company directory.
  - **Fallback (data.sec.gov):** If edgartools fails (e.g. “Unknown SGML format”), the code falls back to the SEC JSON APIs: submissions and company facts (XBRL). Facts are flattened into long-form tables and written as Parquet. No SGML parsing, so it works even when filing formats differ.
- **Output:** All output is under **`data/parquet/<ticker>/`** (e.g. `data/parquet/AAPL/`, `data/parquet/NVO/`). For each company you get:
  - `{ticker}_sec_submissions.parquet` (filing list; used for form discovery)
  - `{ticker}_sec_facts_income_statement.parquet`
  - `{ticker}_sec_facts_balance_sheet.parquet`
  - `{ticker}_sec_facts_cash_flow.parquet`
  (or `_sec_facts_all_concepts.parquet` when fallback buckets do not match.)
- **Identity:** SEC requires a User-Agent. Set `EDGAR_EMAIL` in `.env`; it’s used as the contact identity (no sign-up). The edgartools cache is pointed at **`data/.edgar`** inside the project by default so you don’t need write access to `~/.edgar`.

### 2. Common-size and flux analysis

- **Input:** The analysis module reads the fact Parquets for each company (income, balance sheet, cash flow). Line items are taken **directly from each company’s data** (concept names in the Parquet)—no separate mapping file.
- **Logic:**
  - Pivot long-form facts to wide (one row per period, one column per concept).
  - **Common-size:** Each line item as a percentage of a denominator (e.g. revenue for income, total assets for balance sheet). Denominator is chosen from the company’s own concept names (e.g. “Revenues”, “Assets”).
  - **Flux:** Period-over-period percent change.
- **Output:** Analysis is written as **CSV only** (no Parquet for analysis), under the same company folder:
  - `common_size_income_statement.csv`, `common_size_balance_sheet.csv`, `common_size_cash_flow.csv`
  - `flux_income_statement.csv`, `flux_balance_sheet.csv`, `flux_cash_flow.csv`  
  These are intended for review and for feeding into an LLM (e.g. Ollama) later.

### 3. Dashboard and test runner

- **Streamlit dashboard:** Run `./run.sh` to start the app and open the browser. Enter tickers (or CIKs), optionally form types (used only when form discovery returns no forms), and click “Run ingestion.” Results and errors are shown; data lands in `data/parquet/<ticker>/`.
- **Test runner:** From the project root, run:
  ```bash
  python run_ticker.py AAPL
  python run_ticker.py 353278   # CIK for NVO (Novo Nordisk)
  ```
  This runs ingestion using discovered forms per company (10-K/10-Q for domestic, 20-F for foreign issuers, etc.), then runs analysis for all companies in `DATA_DIR`, and prints where the analysis CSVs were written. Use it to confirm that data is stored correctly after setup.

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
│   ├── edgar/
│   │   ├── ingestion.py   # run_ingestion(tickers, forms, ...); CIK + form discovery, edgartools + fallback
│   │   ├── sec_api.py     # ticker_to_cik, fetch_submissions, get_periodic_forms_from_submissions; data.sec.gov fallback
│   │   └── models.py      # Table name constants
│   ├── analysis/
│   │   └── common_size_flux.py   # common-size & flux from facts → CSV per company
│   └── dashboard/
│       └── app.py       # Streamlit UI
├── data/
│   ├── .edgar/         # edgartools cache (default; gitignored)
│   └── parquet/         # Output root (DATA_DIR)
│       └── <ticker>/    # e.g. AAPL/, MSFT/
│           ├── {ticker}_sec_facts_*.parquet
│           ├── common_size_*.csv
│           └── flux_*.csv
└── Docs/                # Charter, troubleshooting, API overview
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

---

## Config

| Variable | Description |
|----------|-------------|
| `EDGAR_EMAIL` | Your email for the SEC User-Agent (required). |
| `DATA_DIR` | Parquet output root (default: `data/parquet`). |
| `EDGAR_LOCAL_DATA_DIR` | Optional. Default is `data/.edgar` inside the project so edgartools doesn’t use `~/.edgar`. |

See `.env.example`.

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

## Roadmap (beyond v1)

- Supply analysis CSVs (or summarized slices) to Ollama for analyst-style narrative.
- Optional context limiting (e.g. last N periods, material variances only) to keep prompts within model context and runtime reasonable.
