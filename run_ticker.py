#!/usr/bin/env python3
from __future__ import annotations

"""
Test runner: pass a ticker as the only argument to run ingestion + analysis and verify storage.

Usage (from project root, with PYTHONPATH=src or via .venv):
  python run_ticker.py AAPL
  python run_ticker.py GOOGL
  .venv/bin/python run_ticker.py MSFT

Runs:
  1. SEC EDGAR ingestion for the ticker (10-K, 10-Q, 20-F) → facts Parquet + MD&A .txt under data/parquet/<ticker>/
  2. Common-size and flux analysis → CSV only under data/parquet/<ticker>/

Prints a short summary and paths to analysis CSVs and MD&A files. Optionally runs Gemini AI analysis (see ENABLE_GEMINI_ANALYSIS in script).
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Project root (run_ticker.py lives here)
_PROJECT_ROOT = Path(__file__).resolve().parent

# Force EDGAR cache under project (used by some dependencies/utilities).
os.environ.setdefault("EDGAR_LOCAL_DATA_DIR", str(_PROJECT_ROOT / "data" / ".edgar"))

# Ensure project root and src on path
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

# Create cache dir immediately.
Path(os.environ.get("EDGAR_LOCAL_DATA_DIR", "")).mkdir(parents=True, exist_ok=True)

from commonsense.config import DATA_DIR, EDGAR_EMAIL, GEMINI_API_KEY
from commonsense.edgar.ingestion import run_ingestion
from commonsense.analysis import run_analysis_all

# Set to True to run Gemini AI analysis after ingestion (MD&A + common-size + flux → analysis).
ENABLE_GEMINI_ANALYSIS = True
GEMINI_MODEL = "gemini-2.5-flash"

# -----------------------------------------------------------------------------
# EDIT ONLY THE PROMPT BELOW — context (MD&A + common-size + flux) is attached automatically.
# -----------------------------------------------------------------------------
AI_ANALYSIS_PROMPT = """### ROLE AND CONTEXT ###
You are acting as a Senior Equity Research Analyst with 20 years of experience in forensic accounting and sector-relative benchmarking. Your goal is to provide a "CommonSense" assessment of a company's financial health by synthesizing hard quantitative data with management's qualitative narrative.

### OBJECTIVE ###
Conduct a comprehensive financial health and operational outlook assessment for [TICKER]. You must bridge the gap between the provided Common-Sized Analysis, Flux Data (Horizontal Analysis), Ratio Analysis (levels + fluctuations), and the MD&A (Item 7) narrative.

### ANALYST METHODOLOGY AND LOGIC ###
    1. The Narrative-Financial Bridge:
        - Deep-read the MD&A for management's stated "Growth Drivers" and "Cost Optimization" claims.
        - Cross-reference these claims with the Flux Analysis.
        - Logic: If management claims efficiency gains but SG&A as a % of Revenue (Common-Sized) is rising, flag the obfuscation.

    2. Line-Item Interactivity (The "Forensic" Lens):
        - You must look for interactions between specific line items:
            - Inventory vs. COGS: Is a buildup in inventory masking a drop in demand or obsolescence?
            - Accounts Receivable vs. Revenue: If AR grows significantly faster than Sales, investigate aggressive revenue recognition or credit quality issues.
            - Capex vs. FCF: Based on the "Trends and Uncertainties" in the MD&A, is the current Free Cash Flow sufficient to fund the stated 12-month capital requirements?

    3. Sector & Competitive Positioning:
        - Use the provided Industry Benchmarks to determine if the company’s margins and flux are Idiosyncratic (company-specific) or Systemic (industry-wide).
        - A "bad" flux is acceptable if it is better than the sector average; a "good" flux is a red flag if the company is falling behind its peers.
    4. Reconciliation Discipline (Required):
        - Use ONLY values present in the provided CSV context (common-size, flux, ratios, ratio-flux).
        - Do not invent values.
        - For each major MD&A claim, cite at least one numeric metric from our calculated outputs and classify support as Match, Partial, or Mismatch.

### REQUIRED OUTPUT STRUCTURE ###
Return your full answer in valid Markdown, using clear headings, bullets, and the required table format.

    I. EXECUTIVE SUMMARY (The "CommonSense" Take):
        - Provide a 3-sentence definitive bottom line on the company's financial trajectory and overall sentiment.
        
    II. NARRATIVE VS. REALITY TABLE:
        | Management Claim (from MD&A) | Financial Evidence (from Data) | Analyst Verdict (Consistent/Inconsistent) |
        | :--- | :--- | :--- |
        | [Claim] | [Supporting or Refuting Metric] | [Verdict] |

    III. THE "RED FLAG" INTERACTION ANALYSIS:
        - Identify at least 3 specific interactions between line items (e.g., Debt-to-Equity vs. Interest Expense Flux) that suggest hidden operational or liquidity stress.

    IV. FORWARD-LOOKING OUTLOOK:
        - Based on the "Known Trends" in the MD&A and current liquidity ratios, provide a 12-month risk assessment.
    V. RECONCILIATION CHECK (Required):
        - Provide a short table with at least 5 checks:
          | Metric / Claim | Value from our CSV output | MD&A statement | Match status (Match / Partial / Mismatch) |

"""


def _collect_analysis_inputs(company_dir: Path) -> tuple[list[Path], list[Path], list[Path], list[Path], list[Path]]:
    """Return (mdna_files, common_size_csvs, statement_flux_csvs, ratio_level_csvs, ratio_flux_csvs)."""
    mdna_files = sorted(company_dir.glob("*_mdna.txt")) + sorted(company_dir.glob("*_mdna.md"))
    common_size_csvs = sorted(company_dir.glob("common_size_*.csv"))
    statement_flux_csvs = [p for p in sorted(company_dir.glob("flux_*.csv")) if not p.name.startswith("flux_ratios_")]
    ratio_level_csvs = sorted(company_dir.glob("ratios_*.csv"))
    ratio_flux_csvs = sorted(company_dir.glob("flux_ratios_*.csv"))
    return mdna_files, common_size_csvs, statement_flux_csvs, ratio_level_csvs, ratio_flux_csvs


def _build_analysis_context(company_dir: Path) -> str:
    """Gather MD&A files and common-size/flux CSVs under company_dir into one context string."""
    parts: list[str] = []

    mdna_files, common_size_csvs, statement_flux_csvs, ratio_level_csvs, ratio_flux_csvs = _collect_analysis_inputs(company_dir)
    if mdna_files:
        parts.append("## MD&A (Management's Discussion and Analysis)\n")
        for f in mdna_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    parts.append(f"### {f.name}\n{text}\n")
            except Exception:
                parts.append(f"### {f.name}\n[Could not read file.]\n")

    for label, files in [("Common-size", common_size_csvs), ("Flux", statement_flux_csvs)]:
        if files:
            parts.append(f"## {label}\n")
            for f in files:
                try:
                    text = f.read_text(encoding="utf-8", errors="replace").strip()
                    if text:
                        parts.append(f"### {f.name}\n{text}\n")
                except Exception:
                    parts.append(f"### {f.name}\n[Could not read file.]\n")
    if ratio_level_csvs:
        parts.append("## Ratios (Levels)\n")
        for f in ratio_level_csvs:
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    parts.append(f"### {f.name}\n{text}\n")
            except Exception:
                parts.append(f"### {f.name}\n[Could not read file.]\n")
    if ratio_flux_csvs:
        parts.append("## Ratio Fluctuations (Flux)\n")
        for f in ratio_flux_csvs:
            try:
                text = f.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    parts.append(f"### {f.name}\n{text}\n")
            except Exception:
                parts.append(f"### {f.name}\n[Could not read file.]\n")

    return "\n".join(parts) if parts else ""


def _run_ai_analysis(ticker: str, company_dir: Path, prompt: str, api_key: str) -> str | None:
    """Call Gemini with prompt + context (MD&A + common-size + flux). Returns response text or None on error."""
    context = _build_analysis_context(company_dir)
    if not context.strip():
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL)
        full_content = f"{prompt}\n\n--- Context for {ticker} ---\n\n{context}"
        response = model.generate_content(full_content)
        if response and response.text:
            return response.text.strip()
        return None
    except Exception as e:
        print(f"  AI analysis error: {e}", file=sys.stderr)
        return None


def _write_ai_analysis_markdown(company_dir: Path, ticker: str, analysis_text: str) -> Path:
    """Write AI analysis output to company_dir/Analysis as markdown and return path."""
    analysis_dir = company_dir / "Analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    out_path = analysis_dir / f"{ticker}_gemini_analysis_{ts}.md"
    out_path.write_text(analysis_text.strip() + "\n", encoding="utf-8")
    return out_path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_ticker.py <TICKER>", file=sys.stderr)
        print("Example: python run_ticker.py AAPL", file=sys.stderr)
        sys.exit(1)

    ticker = sys.argv[1].strip().upper()
    if not ticker:
        print("Error: provide a non-empty ticker (e.g. AAPL, GOOGL).", file=sys.stderr)
        sys.exit(1)

    if not EDGAR_EMAIL:
        print("Error: EDGAR_EMAIL is required (set in .env or environment).", file=sys.stderr)
        sys.exit(1)

    # 20-F included so foreign private issuers (e.g. NVO, BHP) get annual data
    forms = ["10-K", "10-Q", "20-F"]
    print(f"Running ingestion for {ticker} ({', '.join(forms)})...")
    ingest = run_ingestion(
        tickers=[ticker],
        forms=forms,
        output_dir=DATA_DIR,
        email=EDGAR_EMAIL,
    )
    print(f"  Tickers processed: {ingest['tickers_processed']}")
    print(f"  Filings: {ingest.get('filings_count', 0)}")
    print(f"  Files written: {len(ingest['files_written'])}")
    if ingest["errors"]:
        for e in ingest["errors"]:
            print(f"  Error: {e}")

    print("\nRunning common-size and flux analysis (CSV only)...")
    analysis = run_analysis_all(DATA_DIR, write_csv=True)
    print(f"  Companies processed: {analysis['companies_processed']}")
    print(f"  Analysis files written: {len(analysis['files_written'])}")
    if analysis["errors"]:
        for e in analysis["errors"]:
            print(f"  Error: {e}")

    # Show where data landed: prefer ticker dir; if input was CIK, resolve by metadata written this run.
    company_dir = DATA_DIR / ticker
    if not company_dir.is_dir():
        meta_files = [Path(p) for p in ingest.get("files_written", []) if str(p).endswith("_meta.parquet")]
        if meta_files:
            company_dir = meta_files[0].parent
            if company_dir.is_dir():
                print(f"\nNote: CIK resolved to ticker folder {company_dir.name}/.")
    if not company_dir.is_dir():
        print(f"\nNo company directory found for requested input '{ticker}'.")

    if company_dir.is_dir():
        csvs = sorted(company_dir.glob("*.csv"))
        print(f"\nAnalysis CSVs under {company_dir}:")
        for f in csvs:
            print(f"  {f}")
        if not csvs:
            print("  (no CSV files yet; check that fact Parquets exist in this directory)")
        mdna_files = sorted(company_dir.glob("*_mdna.txt")) + sorted(company_dir.glob("*_mdna.md"))
        if mdna_files:
            print(f"\nMD&A files under {company_dir}:")
            for f in mdna_files:
                print(f"  {f}")
        mdna_inputs, common_size_inputs, statement_flux_inputs, ratio_level_inputs, ratio_flux_inputs = _collect_analysis_inputs(company_dir)
        print("\nAI context file coverage:")
        print(f"  MD&A files: {len(mdna_inputs)}")
        print(f"  Common-size CSVs: {len(common_size_inputs)}")
        print(f"  Statement flux CSVs: {len(statement_flux_inputs)}")
        print(f"  Ratio level CSVs: {len(ratio_level_inputs)}")
        print(f"  Ratio flux CSVs: {len(ratio_flux_inputs)}")
        if not mdna_inputs:
            print("  Warning: no MD&A files found for context.")
        if not common_size_inputs or not statement_flux_inputs or not ratio_level_inputs or not ratio_flux_inputs:
            print("  Warning: common-size/flux/ratio-level/ratio-flux context is incomplete.")

        # AI analysis: Gemini with editable prompt + MD&A + common-size + flux context (disabled for MD&A troubleshooting)
        if ENABLE_GEMINI_ANALYSIS and GEMINI_API_KEY:
            print(f"\nRunning AI analysis (Gemini: {GEMINI_MODEL})...")
            analysis_text = _run_ai_analysis(ticker, company_dir, AI_ANALYSIS_PROMPT, GEMINI_API_KEY)
            if analysis_text:
                md_out = _write_ai_analysis_markdown(company_dir, company_dir.name, analysis_text)
                print("\n--- AI Analysis ---")
                print(analysis_text)
                print("---")
                print(f"\nSaved AI analysis markdown: {md_out}")
            else:
                ctx = _build_analysis_context(company_dir)
                if not ctx.strip():
                    print("  (Skipped: no MD&A or analysis CSVs to send as context.)")
                else:
                    print("  (No analysis returned; check API key and context.)")
        elif not ENABLE_GEMINI_ANALYSIS:
            print("\n(AI analysis disabled: set ENABLE_GEMINI_ANALYSIS = True in run_ticker.py to enable)")
    else:
        print(f"\nNo company directory at {DATA_DIR / ticker}; check ingestion errors above.")

    print("\nDone.")


if __name__ == "__main__":
    main()
