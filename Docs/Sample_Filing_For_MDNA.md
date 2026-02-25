# Sample filing for MD&A extraction

To capture **all** of the MD&A everywhere it exists for any company, you need the same raw filing our script sees. Use either the helper script or the URLs below.

---

## 1. Script (recommended): same access as the pipeline

From the project root:

```bash
# Default: Apple 10-K (2024) — downloads index + all main docs into data/sample_filing/
PYTHONPATH=src python scripts/fetch_sample_filing.py
```

This uses the **same URLs and User-Agent** as `mdna.py` / `run_ticker.py` and saves:

- **Index:** `data/sample_filing/0000320193_24_000106/0000320193_24_000106-index.htm`  
  (lists all documents in the filing)

- **Main doc(s):** `data/sample_filing/.../part1_*.htm`, `part2_*.htm`, …  
  (the HTML we run MD&A extraction on; MD&A can span multiple files)

You can then open these in an editor, search for “Item 7”, “Management’s Discussion”, etc., and see exactly where content lives and how to parse it.

**Other filings:**

```bash
# Apple 10-Q
PYTHONPATH=src python scripts/fetch_sample_filing.py 320193 0000320193-24-000123 10-Q

# Another company: CIK, accession, form (get accession from data.sec.gov/submissions/CIK{cik}.json)
PYTHONPATH=src python scripts/fetch_sample_filing.py <CIK> <accession-with-dashes> 10-K
```

---

## 2. URL pattern (how the script builds URLs)

The pipeline uses **SEC Archives** (not data.sec.gov) for the filing HTML:

| Piece | Formula | Example (Apple 10-K 2024) |
|--------|----------|----------------------------|
| Base | `https://www.sec.gov/Archives/edgar/data/{CIK}/{accession-no-dashes}/` | `https://www.sec.gov/Archives/edgar/data/320193/000032019324000106/` |
| Index | `{base}{accession-with-dashes}-index.htm` | `https://www.sec.gov/Archives/edgar/data/320193/000032019324000106/0000320193-24-000106-index.htm` |
| Documents | Listed in the index as `<a href="...">`; we use .htm/.html links that are not exhibits. | e.g. `aapl-20240928.htm` or `aapl-10k_20240928.htm` |

- **CIK:** numeric, no leading zeros in the path (e.g. `320193`).
- **Accession in path:** dashes **removed** (e.g. `0000320193-24-000106` → `000032019324000106`).
- **Accession in index filename:** dashes **kept** (e.g. `0000320193-24-000106-index.htm`).

---

## 3. Download with curl (same as the script)

The SEC expects a **User-Agent** that identifies you (e.g. your email). Use the same when downloading manually:

```bash
# Set your email (required by SEC)
UA="YourName your@email.com"

# Index
curl -o sample-index.htm -H "User-Agent: $UA" \
  "https://www.sec.gov/Archives/edgar/data/320193/000032019324000106/0000320193-24-000106-index.htm"

# Primary document (exact name from index; Apple 2024 10-K example)
curl -o sample-10k.htm -H "User-Agent: $UA" \
  "https://www.sec.gov/Archives/edgar/data/320193/000032019324000106/aapl-20240928.htm"
```

Open `sample-index.htm` in a browser or editor to see the list of documents; then curl the URL of the main filing doc(s) you care about.

---

## 4. What to look for in the saved files

- **Item 7 (10-K), Item 2 (10-Q), Item 5 (20-F):** where MD&A starts.
- **Item 7A, Item 8, etc.:** where we want to stop so we don’t pull in the next section.
- **Table of contents vs real section:** TOC often has “Item 7” / “Item 7A” close together; the real MD&A is a long block of text.
- **Multi-part filings:** if the index lists `part1.htm`, `part2.htm`, etc., MD&A may start in one file and continue in the next — our pipeline fetches all main docs and concatenates before extracting.

Use the saved sample files to decide how to reliably find the start/end of MD&A for any company and form.
