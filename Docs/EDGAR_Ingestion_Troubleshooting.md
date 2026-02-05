# SEC EDGAR Ingestion – Troubleshooting

This document explains common issues when running the SEC EDGAR ingestion (ticker-based fetch via edgartools) and how to resolve them.

---

## 0. How does “search by ticker” work for EDGAR?

The SEC EDGAR system identifies companies by **CIK** (Central Index Key), not by ticker. To search by ticker you first need **ticker → CIK**.

**Official SEC sources for ticker → CIK:**

| What | URL | Format |
|------|-----|--------|
| Company tickers (full list) | https://www.sec.gov/files/company_tickers.json | JSON: `{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."}, ...}`. Search by `ticker` to get `cik_str`. |
| Same, alternate | https://data.sec.gov/files/company_tickers.json | Same structure. |

So “search by ticker” in EDGAR means: (1) get the ticker→CIK mapping from that JSON (or cache it), (2) use the CIK for all SEC APIs (submissions, company facts, filings). The SEC does not provide a single “search by ticker” API; you resolve ticker to CIK yourself or use a library that does.

**In CommonSense:**

- **Ticker to CIK:** We resolve ticker to CIK via the SEC `company_tickers.json` API first (`sec_api.ticker_to_cik`). If that fails, we try edgartools `Company(ticker)`. Using a numeric **CIK** (e.g. `320193`) skips ticker lookup entirely.
- **Form discovery:** We fetch submissions from `data.sec.gov` for the CIK and read `filings.recent["form"]`. We request only periodic report forms (10-K, 10-Q, 20-F, 40-F) that the company actually files. Then we call `Company(CIK)` and request filings only for those forms.
- **If ticker lookup fails:** Use **CIK** instead of ticker (e.g. `320193` for Apple, `353278` for Novo Nordisk). Ingestion accepts numeric CIKs; the data.sec.gov fallback uses CIK directly and can resolve the ticker from the submissions API for folder naming.
- **CIK lookup:** Use [SEC EDGAR Company Search](https://www.sec.gov/edgar/searchedgar/companysearch) or the `company_tickers.json` file above to find a company’s CIK by name or ticker.

**Form discovery from submissions:** For each company we fetch submissions from `data.sec.gov` and read the `filings.recent["form"]` list. We then request only periodic report forms (10-K, 10-Q, 20-F, 40-F) that the company actually files. So foreign issuers (e.g. NVO) get 20-F automatically; domestic issuers get 10-K/10-Q. This avoids requesting 10-K for companies that only file 20-F and helps bypass form-type mismatches.

---

## 1. Identity (SEC User-Agent)

**What you need:** The SEC requires a contact identity in the HTTP User-Agent when accessing EDGAR. No sign-up or account is required.

**Config:**

- **CommonSense** uses `EDGAR_EMAIL` from `.env` (or the dashboard field) and passes it to edgartools.
- **edgartools** uses `EDGAR_IDENTITY` (e.g. `"Your Name your@email.com"`). If you set only `EDGAR_EMAIL`, we pass that as the identity string.

**If ingestion fails with identity-related errors:** Set `EDGAR_EMAIL` in `.env` to any valid contact email, or set `EDGAR_IDENTITY="Your Name your@email.com"` in the environment.

---

## 2. Ticker lookup: "Both data sources are unavailable"

**Symptom:** Errors like:

- `AAPL: Both data sources are unavailable`
- `Error fetching company tickers from [https://www.sec.gov/...]: module 'hishel' has no attribute 'FileStorage'`

**Cause:** edgartools resolves **ticker symbols** (e.g. AAPL) to a company by downloading the SEC ticker list (e.g. `company_tickers.json` or `ticker.txt`) and caching it. That path can fail if:

- The **hishel** HTTP-cache library has an API mismatch (e.g. `FileStorage` moved or was renamed).
- The cache directory is not writable (see §3).
- The network blocks access to `sec.gov`.

**Fix – pin hishel to a version that provides `FileStorage`:**  
Newer hishel (1.x) removed `FileStorage`; edgartools still expects it. In **requirements.txt** we pin:

`hishel>=0.1.5,<1.0`

Reinstall with `pip install -r requirements.txt` so the cache layer works.

**Workaround – use CIK instead of ticker:**  
edgartools accepts **CIK** (Central Index Key) as well as ticker. Using CIK skips the ticker-file fetch.

- In the **dashboard**, in the tickers field enter the CIK (e.g. `320193` for Apple, `1652044` for Alphabet/GOOG) instead of the ticker.
- From **CLI or code**, call `run_ingestion(tickers=["1652044"], ...)`.

Common CIKs:

| Company | Ticker | CIK    |
|---------|--------|--------|
| Apple   | AAPL   | 320193 |
| Microsoft | MSFT | 789019 |
| Google  | GOOGL  | 1652044 |

You can look up CIKs at [SEC EDGAR Company Search](https://www.sec.gov/edgar/searchedgar/companysearch).

**Code behavior:** If the value you pass is numeric (e.g. `320193` or `0000320193`), the ingestion layer uses `Company(CIK)` so ticker lookup is not used.

---

## 3. PermissionError: `~/.edgar` or `_cache`

**Symptom:**

- `PermissionError: [Errno 1] Operation not permitted: '/Users/.../.edgar'`
- Or: `No such file or directory: '.../.edgar/_cache'`

**Cause:** edgartools uses a local cache for ticker data and other downloads. By default it uses:

- `EDGAR_LOCAL_DATA_DIR` if set, otherwise  
- A default directory under your home (e.g. `~/.edgar`).

Some code paths (e.g. ticker list fetch) may still use the home directory or a `_cache` subdirectory. If the process cannot create or write to that path (e.g. sandbox, read-only home, no write access to `~/.edgar`), you get permission or "no such file" errors.

**What to do:**

1. **Allow write access to the cache:** Ensure the process can create and write to `~/.edgar` (or whatever path edgartools uses). In restricted environments (e.g. some CI/sandboxes), you may need to set `EDGAR_LOCAL_DATA_DIR` to a writable path inside the project, e.g. `data/.edgar`, and ensure that path is used for all edgar operations (behavior may depend on edgartools version).
2. **Use CIK:** To avoid depending on the ticker-file cache, use CIK instead of ticker (see §2).

---

## 4. Where our output goes vs edgartools cache

- **CommonSense Parquet output** is written to `DATA_DIR` (default `data/parquet/`). This is configurable via `DATA_DIR` in `.env`.
- **edgartools’ own cache** (ticker list, etc.) is separate and controlled by `EDGAR_LOCAL_DATA_DIR` or its default (e.g. `~/.edgar`). Problems with "unable to access data" are often due to ticker lookup or cache (§2 and §3), not our output directory.

---

## 5. Environment variables reference

| Variable | Used by | Purpose |
|----------|--------|---------|
| `EDGAR_EMAIL` | CommonSense | SEC contact email; passed to edgartools as identity. |
| `EDGAR_IDENTITY` | edgartools | Full identity string, e.g. `"Name email@example.com"`. |
| `DATA_DIR` | CommonSense | Directory for Parquet output (default `data/parquet`). |
| `EDGAR_LOCAL_DATA_DIR` | edgartools | Directory for edgar cache (ticker list, etc.). |

---

## 6. Quick checklist when ingestion fails

1. **Identity:** Set `EDGAR_EMAIL` (or `EDGAR_IDENTITY`) so the SEC User-Agent is valid.
2. **Ticker vs CIK:** If you see "Both data sources are unavailable" or hishel/`FileStorage` errors, try **CIK** (e.g. `320193` for Apple) instead of ticker.
3. **Cache:** If you see permission or "no such file" for `~/.edgar` or `_cache`, fix write access to that path or set `EDGAR_LOCAL_DATA_DIR` to a writable directory; or again, use CIK to avoid ticker fetch.
4. **Network:** Ensure the host can reach `https://www.sec.gov` and `https://data.sec.gov` (no special VPN required for normal use).

### "Unknown SGML format"

**Symptom:** Ingestion runs but reports errors like `320193 10-K: Unknown SGML format` and no Parquet files are written.

**Does the format differ by company?** Yes. The content edgartools feeds to its SGML parser is the **full submission text** from the filing’s `text_url`. The parser only accepts content that:

- Starts with `<SUBMISSION>`, or  
- Contains `<SEC-DOCUMENT>`, `<IMS-DOCUMENT>`, or `<DOCUMENT>` in the first 1,000 characters.

So the format can differ (and trigger "Unknown SGML format") when:

1. **The SEC serves different submission layouts** – e.g. different index or wrapper format for some filers or periods.  
2. **The requested URL returns something else** – e.g. an HTML index page instead of raw SGML.  
3. **Filing type or era** – e.g. inline XBRL vs legacy SGML, or older vs newer submission structure.

So it’s not necessarily “one format per company”; it can vary by filing, period, or how the submission is built/served.

**How we ensure we can operate on the filings:** We add a **fallback path** that does not rely on SGML at all:

- When edgartools fails (e.g. Unknown SGML, or any exception while processing a company), the ingestion layer calls the **data.sec.gov JSON APIs** for that company’s CIK:
  - **Submissions API:** `https://data.sec.gov/submissions/CIK{cik}.json` → recent filings (metadata: form, date, accession, etc.).  
  - **Company Facts API:** `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` → XBRL financial concepts (us-gaap, dei, etc.) in JSON.
- We normalize those JSON responses to DataFrames and write **Parquet** (submissions list + company facts in a long/concept table). Downstream code can then **operate on the filings** using this Parquet data even when SGML parsing fails.

So: **we ensure we can operate on the filings** by (1) trying edgartools first, and (2) on failure, automatically using the SEC’s JSON APIs and writing the same Parquet output directory. No account or API key is needed for data.sec.gov; only a **User-Agent** header (your `EDGAR_EMAIL` / identity) is required.

---

For more on edgartools, see [EdgarTools documentation](https://edgartools.readthedocs.io/).
