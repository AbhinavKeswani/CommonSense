# CommonSense

A private, localized financial intelligence pipeline. This repo provides **SEC EDGAR data ingestion** and a **dashboard** to trigger it from your browser.

## Quick start (one-command run)

1. Copy environment template and set your SEC identity email:
   ```bash
   cp .env.example .env
   # Edit .env: set EDGAR_EMAIL=your.name@example.com (required by SEC)
   ```

2. Install dependencies and run the dashboard:
   ```bash
   pip install -r requirements.txt
   ./run.sh
   ```
   This starts the Streamlit dashboard and opens `http://localhost:8501`. Use the form to enter tickers (e.g. AAPL, MSFT), select form types (10-K, 10-Q), and click **Run ingestion**. Parquet output is written to `data/parquet/` (or `DATA_DIR` from `.env`).

## Project layout

- `src/commonsense/` – Python package
  - `config.py` – Loads `EDGAR_EMAIL`, `DATA_DIR` from env
  - `edgar/` – SEC EDGAR ingestion (edgartools → Parquet)
  - `dashboard/app.py` – Streamlit UI to trigger ingestion
- `data/parquet/` – Ingestion output (gitignored)
- `Docs/` – Project charter and architecture

## Run locally

- **Dashboard only:** `./run.sh` (or `make run` if you add a Makefile).
- **Alternative:** From project root with `PYTHONPATH=src`:
  ```bash
  streamlit run src/commonsense/dashboard/app.py
  ```
  Then open http://localhost:8501.

## Deploy (online research system)

You can push the same app to get an online URL:

- **Streamlit Community Cloud:** Connect this repo, set `EDGAR_EMAIL` (and optionally `DATA_DIR`) in secrets, and use the app entrypoint `src/commonsense/dashboard/app.py`.
- **Other hosts:** Run `streamlit run src/commonsense/dashboard/app.py` with `PYTHONPATH=src` and expose the port (e.g. Railway, Fly.io).

## Config

| Variable     | Description |
|-------------|-------------|
| `EDGAR_EMAIL` | Your email for SEC User-Agent (required by SEC). |
| `DATA_DIR`    | Parquet output directory (default: `data/parquet`). |

See `.env.example`.
