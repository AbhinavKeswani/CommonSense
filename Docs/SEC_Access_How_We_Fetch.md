# How we fetch SEC data (no API — plain HTTP)

We do **not** use a separate SEC “API” or any API key. We use **plain HTTP GET** requests to the same URLs that the SEC’s website serves when you open a filing in a browser.

## What we actually do

| What | How |
|------|-----|
| **Request** | `GET <url>` (e.g. index or filing HTML). |
| **Library** | Python `urllib.request.Request` + `urlopen`. |
| **Headers** | Only **`User-Agent: <your email>`** (from `EDGAR_EMAIL` in `.env`). The SEC asks for a descriptive User-Agent; no API key. |
| **URL** | Built from CIK + accession, e.g. `https://www.sec.gov/Archives/edgar/data/320193/000032019324000106/0000320193-24-000106-index.htm`. |

So it’s the same as opening that URL in a browser, with your email in the User-Agent so the SEC can identify you.

## Where it happens in code

- **MD&A / sample filing:** `src/commonsense/edgar/mdna.py`  
  - `fetch_index_html()` → `urllib.request.Request(url, headers=_headers(user_agent))` then `urlopen(req, timeout=30)`.  
  - `fetch_document()` → same pattern for the main filing `.htm` / `.html`.
- **Headers:** `src/commonsense/edgar/sec_api.py` → `_headers(user_agent)` returns `{"User-Agent": user_agent}`.

## Why the sample script 503'd but run_ticker worked

**run_ticker** often does not request the filing **index** page. It gets the primary document filename from **data.sec.gov** (submissions API), then fetches only that one URL from sec.gov/Archives. So it skips the index; the index is what was returning 503. The sample script (`scripts/fetch_sample_filing.py`) now tries the same path first: submissions API then direct document. It only falls back to the index if that fails.

## Why you might see 503

**503 Service Unavailable** comes from the SEC’s own servers (the same ones that serve www.sec.gov and the EDGAR Archives). Common causes:

- **Load** – Their servers are temporarily overloaded; retry later.
- **Rate limiting** – They allow about 10 requests per second per user; we don’t use a special API, so we’re subject to the same limits as normal web traffic.
- **Network** – Your network (e.g. corporate firewall, VPN, inflight WiFi) might block or throttle `sec.gov`.

So the “service” that’s unavailable is **the SEC’s public EDGAR web site** (the same one you’d use in a browser), not a different “API” service.

## Summary

- **No API key.**  
- **No separate “SEC API” call** — just HTTP GET to `https://www.sec.gov/Archives/edgar/...`.  
- **Only header:** `User-Agent: <EDGAR_EMAIL>`.  
- **503** = SEC’s servers (or something in between) saying “try again later” or “too many requests.”
