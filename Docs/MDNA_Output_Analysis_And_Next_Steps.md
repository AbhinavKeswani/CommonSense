# MD&A output analysis and next steps

## What we ran

- **Sample filing fetch:** `scripts/fetch_sample_filing.py` — SEC returned **503 Service Unavailable** (temporary). Run again when you have stable internet; no run was possible in this session.
- **Existing outputs:** We inspected current MD&A extractions under `data/parquet/` (AAPL, AVGO, 10-K and 10-Q).

---

## Current output summary

| Filing | Size | What we're actually capturing |
|--------|------|-------------------------------|
| **AAPL 10-K** (2015, 2016) | ~111–124 lines | **Wrong section:** TOC (Item 7, 7A, 8…) + forward-looking boilerplate + **start of PART I Item 1 (Business)**. We never reach the real MD&A body (Results of Operations, Liquidity, Segment Performance). |
| **AAPL 10-Q** (2015) | 2,257 lines | TOC (Item 2, 3, 4…) then **financial statements** (Condensed Consolidated Operations). May include some MD&A later in the file; structure differs from 10-K. |
| **AVGO 10-K** (2018, 2019) | Very long (e.g. 157k+ chars) | Starts correctly with “Item 7” / “Management’s Discussion…” and segment note, then **Business Strategy**, **Products and Markets**, repeated “Table of Contents” headers, **Risk Factors**, etc. So we get a lot of content but it’s a mix of real MD&A and other Part I/II sections; we may be over-including or the filing structure is different. |

**Conclusion:** Extraction is **inconsistent across issuers and forms**. For AAPL 10-K we’re anchored on the **TOC “Item 7”** and then stop at a later “Item 8” (or similar), which gives us TOC + intro + Item 1 Business instead of the actual Item 7 MD&A. For AVGO we get a long block that includes MD&A but also other items.

---

## Root cause (concise)

1. **Start position:** We match “Item 7” / “Management’s Discussion…” at **line start**. The **first** such occurrence is often the **Table of Contents** entry, not the real section header in the body.
2. **Longest-section heuristic:** We pick the longest section among all candidate starts. If the **real** MD&A heading (“Management’s Discussion and Analysis of Financial Condition and Results of Operations”) appears **again** later at line start, we’d get a second candidate; the longer one would then be the real MD&A. So the bug is either:
   - That second heading doesn’t appear at line start in the flattened text (e.g. it’s in a table or has different spacing), or
   - We only have one candidate (TOC), so we’re stuck with the wrong start.
3. **End markers:** We only treat “Item 7A” / “Item 8” as end when they’re at **line start** (to avoid cutting at in-sentence refs). That’s correct; the issue is primarily **where we start**, not where we end.

---

## What “good” MD&A looks like

- **10-K:** Item 7 — “Management’s Discussion and Analysis of Financial Condition and Results of Operations” with subsections such as Overview, **Results of Operations**, **Liquidity and Capital Resources**, Critical Accounting Estimates, Off-Balance Sheet Arrangements, and often **Segment Operating Performance**.
- **10-Q:** Item 2 — same title, typically shorter (quarterly focus).
- **20-F:** Item 5 — “Operating and Financial Review and Prospects.”

So the next steps should make sure we **start** at that real section (after Part I / other items), not at the TOC.

---

## Recommended next steps (in order)

### 1. Get a local copy of the raw filing (when SEC is up)

```bash
PYTHONPATH=src python3 scripts/fetch_sample_filing.py
```

- Saves the **index** and **all main document** HTML for the default Apple 10-K under `data/sample_filing/`.
- Open those files in an editor and search for:
  - “Item 7”, “Management’s Discussion”, “Results of Operations”, “Liquidity and Capital”.
- See **exactly** where the TOC vs the real MD&A section appear (and whether the real one is at line start after `get_text()`).

### 2. Prefer start after “PART II” (10-K) or equivalent — **implemented**

- In many 10-Ks, the **real** Item 7 is under **Part II**. The TOC and Part I (Business, Risk Factors) come first.
- **Done in `mdna.py`:** For 10-K we now restrict candidate starts to those that occur **after** the first “PART II” or “Part II” in the text. So we no longer anchor on the TOC “Item 7”; we use the Item 7 (or “Management’s Discussion…”) that appears in the body under Part II.
- Re-run ingestion for AAPL and re-check `*_mdna.txt` to confirm the section now begins with real MD&A (e.g. “Results of Operations”, “Liquidity”).

### 3. Strengthen “real” section detection

- Require that the chosen section (or candidate start) **looks like** MD&A: e.g. contains phrases like “Results of Operations”, “Liquidity”, or “Liquidity and Capital Resources” within the first N characters. If the longest section never mentions these, consider the next-longest that does, or fall back to the longest.
- This helps when filing structure is odd (e.g. no clear “Part II” or multiple “Item 7” lines).

### 4. Re-run pipeline and validate

- When SEC is available:
  - Run `python3 run_ticker.py AAPL` (and optionally AVGO).
  - Regenerate MD&A with the updated logic.
- **Validation:** Open `data/parquet/AAPL/AAPL_10-K_*_mdna.txt` and confirm the **first substantive heading** is “Management’s Discussion…” and the **first subsection** is something like “Overview” or “Results of Operations”, not “Item 1. Business” or TOC entries.

### 5. Optional: Use saved sample HTML for offline tests

- Once `data/sample_filing/` is populated, add a small test in the repo (e.g. `tests/test_mdna.py`) that:
  - Reads the saved `*-index.htm` and main doc `.htm` from `data/sample_filing/`,
  - Calls `extract_mdna_from_html(html, "10-K")`,
  - Asserts that the result contains “Results of Operations” or “Liquidity” and does **not** start with TOC-only content (e.g. “Item 7.\nManagement’s…\n22\nItem 7A”).
- This lets you iterate on extraction logic without hitting the SEC.

---

## Commands to run when you’re back online

```bash
# 1. Fetch sample filing (index + main docs) for local inspection
PYTHONPATH=src python3 scripts/fetch_sample_filing.py

# 2. Ingest + analyze one ticker (regenerates Parquet, MD&A, common-size, flux)
python3 run_ticker.py AAPL
```

Then inspect:

- `data/sample_filing/` — raw HTML to see exact structure.
- `data/parquet/AAPL/*_mdna.txt` — current vs improved MD&A after you implement steps 2–3.

---

## Summary

| Item | Status |
|------|--------|
| Run fetch_sample_filing | Blocked by SEC 503; run when online. |
| Run run_ticker | Same; run when online. |
| Analyze existing MD&A outputs | Done: AAPL 10-K wrong (TOC + Item 1); AVGO long but mixed. |
| Next steps | Prefer start after “Part II”; require MD&A-like content; validate on AAPL/AVGO; optional offline test on sample HTML. |
