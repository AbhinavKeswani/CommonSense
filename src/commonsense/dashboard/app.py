"""Streamlit dashboard: trigger SEC EDGAR ingestion and show status."""

import sys
from pathlib import Path

# Ensure project src is on path when running as: streamlit run src/commonsense/dashboard/app.py
_src = Path(__file__).resolve().parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import streamlit as st

from commonsense.config import DATA_DIR, EDGAR_EMAIL
from commonsense.edgar.ingestion import run_ingestion

st.set_page_config(page_title="CommonSense – SEC EDGAR Ingestion", layout="wide")
st.title("CommonSense – SEC EDGAR Ingestion")
st.caption("Trigger EDGAR data ingestion; output is written to Parquet for later analysis.")

tickers_raw = st.text_area(
    "Tickers (comma- or space-separated)",
    value="AAPL, MSFT",
    help="e.g. AAPL, MSFT, GOOGL",
)
forms = st.multiselect(
    "Form types",
    options=["10-K", "10-Q", "8-K", "10-K/A", "10-Q/A"],
    default=["10-K", "10-Q"],
    help="Select one or more SEC form types to fetch.",
)
email_override = st.text_input(
    "SEC identity email (optional override)",
    value=EDGAR_EMAIL or "",
    type="default",
    help="SEC requires a User-Agent. Set EDGAR_EMAIL in .env or enter here.",
)

if st.button("Run ingestion", type="primary"):
    tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]
    if not tickers:
        st.error("Enter at least one ticker.")
    elif not forms:
        st.error("Select at least one form type.")
    else:
        email = (email_override or EDGAR_EMAIL or "").strip()
        if not email:
            st.error("SEC identity email is required. Set EDGAR_EMAIL in .env or enter it above.")
        else:
            with st.spinner("Running ingestion…"):
                summary = run_ingestion(
                    tickers=tickers,
                    forms=forms,
                    output_dir=DATA_DIR,
                    email=email,
                )
            st.success("Ingestion finished.")
            st.subheader("Summary")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Tickers processed", summary["tickers_processed"])
            with col2:
                st.metric("Filings processed", summary["filings_count"])
            with col3:
                st.metric("Files written", len(summary["files_written"]))
            if summary["files_written"]:
                with st.expander("Files written"):
                    for p in summary["files_written"]:
                        st.code(p, language=None)
            if summary["errors"]:
                st.warning("Some errors occurred:")
                for err in summary["errors"]:
                    st.text(err)
            st.info(f"Output directory: {DATA_DIR.resolve()}")

st.sidebar.header("Config")
st.sidebar.text(f"Data dir: {DATA_DIR.resolve()}")
st.sidebar.caption("Set EDGAR_EMAIL and DATA_DIR in .env (see .env.example).")
