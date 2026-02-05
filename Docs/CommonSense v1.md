# CommonSense v1

CommonSense is a framework for ingesting and processing financial data from the SEC EDGAR database. The goal is to provide analyst-style ratings for companies based on their financial data (and eventually recent news), using a local LLM so the system stays private and low-cost.

---

## Vision

- **Python** for the backend: ingesting and processing data from the SEC EDGAR database.
- **Ollama** for the LLM: used to generate analyst-style ratings from the processed data, with private or open-source models.

This keeps CommonSense free to use and minimizes ongoing maintenance and hosting cost.

---

## Current implementation (as of v1)

The following is implemented and documented in the main [README](../README.md).

### SEC EDGAR ingestion

- **Input:** Tickers (e.g. AAPL, MSFT) or CIKs. Forms: 10-K, 10-Q, etc.
- **Primary path:** edgartools (Company, get_filings, financials) to fetch filings and normalized financials.
- **Fallback:** If edgartools fails (e.g. “Unknown SGML format”), the pipeline uses the SEC’s data.sec.gov JSON APIs (submissions + company facts / XBRL). Facts are flattened to long-form tables and written as Parquet. No SGML parsing.
- **Output:** Per-company directories under `data/parquet/<ticker>/` with:
  - `{ticker}_sec_facts_income_statement.parquet`
  - `{ticker}_sec_facts_balance_sheet.parquet`
  - `{ticker}_sec_facts_cash_flow.parquet`
  (and optionally submissions). When only a CIK is supplied, the ticker is resolved from the SEC submissions API so storage uses ticker names (e.g. GOOGL) instead of numeric IDs.

### Analysis (common-size and flux)

- **Input:** The fact Parquets for each company. Line items are the **company’s own concept names** from the data (no separate mapping).
- **Common-size:** Each line item as a percentage of a denominator (revenue for income, total assets for balance sheet, etc.), chosen from the company’s concepts.
- **Flux:** Period-over-period percent change.
- **Output:** CSV only (no Parquet for analysis), under the same `data/parquet/<ticker>/` folder: `common_size_*.csv` and `flux_*.csv` for income statement, balance sheet, and cash flow. These are suitable for human review and for feeding into an LLM.

### Dashboard and test runner

- **Streamlit dashboard:** Run `./run.sh`; enter tickers/CIKs and optionally form types (used when form discovery returns no forms), click “Run ingestion.” Results and file locations are shown.
- **Test runner:** `python run_ticker.py <TICKER>` or `python run_ticker.py <CIK>` runs ingestion using **discovered forms** per company (10-K/10-Q for domestic, 20-F for foreign issuers, etc.), then runs analysis for all companies in the data dir, and prints the paths to the analysis CSVs so you can confirm storage and outputs.

### Configuration and environment

- **EDGAR_EMAIL:** Required for the SEC User-Agent (any contact email; no sign-up).
- **DATA_DIR:** Parquet output root (default `data/parquet`).
- **EDGAR_LOCAL_DATA_DIR:** Defaults to `data/.edgar` inside the project so edgartools does not require write access to `~/.edgar`, avoiding sandbox and permission issues when running from the project.

---

## Planned (v1 and beyond)

- **Ollama integration:** Use the analysis CSVs (or summarized slices) as context for a local model to produce analyst-style narrative or ratings.
- **Context limiting:** Optional slicing (e.g. last N periods, material variances only) to keep prompts within the model’s context window and runtime reasonable.

For setup, usage, and troubleshooting, see the main [README](../README.md).
