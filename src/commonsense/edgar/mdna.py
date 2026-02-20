"""
Extract MD&A (Management's Discussion and Analysis) from SEC EDGAR filings.
Fetches the primary filing document from Archives and parses Item 7 (10-K), Item 2 (10-Q), or Item 5 (20-F).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from commonsense.edgar.sec_api import _headers


SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# Section markers for MD&A by form (regex patterns for start; we extract until next Item).
# 10-K: Item 7 (MD&A); 10-Q: Item 2 (MD&A); 20-F: Item 5 (Operating and Financial Review).
# Allow optional punctuation after Item number (e.g. "Item 7." or "Item 7—")
MDNA_START_PATTERNS = {
    "10-K": [
        r"Item\s+7\.?\s*[:\-]?\s*Management'?s?\s+Discussion\s+and\s+Analysis",
        r"ITEM\s+7\.?\s*[:\-]?\s*MANAGEMENT'?S?\s+DISCUSSION\s+AND\s+ANALYSIS",
        r"Item\s+7\b",
        r"ITEM\s+7\b",
        r"Management'?s?\s+Discussion\s+and\s+Analysis\s+of\s+Financial",
    ],
    "10-Q": [
        r"Item\s+2\.?\s*[:\-]?\s*Management'?s?\s+Discussion\s+and\s+Analysis",
        r"ITEM\s+2\.?\s*[:\-]?\s*MANAGEMENT'?S?\s+DISCUSSION\s+AND\s+ANALYSIS",
        r"Item\s+2\b",
        r"ITEM\s+2\b",
    ],
    "20-F": [
        r"Item\s+5\.?\s*[:\-]?\s*Operating\s+and\s+Financial\s+Review",
        r"ITEM\s+5\.?\s*[:\-]?\s*OPERATING\s+AND\s+FINANCIAL\s+REVIEW",
        r"Item\s+5\b",
        r"ITEM\s+5\b",
    ],
}

# Stop at next Item (so we don't include Item 7A, Item 8, etc.).
MDNA_END_PATTERNS = [
    r"\bItem\s+7A\b",
    r"\bItem\s+8\b",
    r"\bItem\s+3\b",
    r"\bItem\s+6\b",
    r"\bITEM\s+7A\b",
    r"\bITEM\s+8\b",
    r"\bITEM\s+3\b",
    r"\bITEM\s+6\b",
]
# Ignore end markers in the first N chars (avoids TOC/nav that list "Item 7", "Item 7A", "Item 8" right after the heading).
MIN_CHARS_BEFORE_END_MARKER = 800


def _accession_no_dashes(accession_no: str) -> str:
    """Return accession number with dashes removed for SEC path."""
    return (accession_no or "").strip().replace("-", "")


def _cik_to_int(cik: int | str | None) -> int | None:
    """Coerce CIK to int for SEC URLs. Returns None if not a valid integer."""
    if cik is None:
        return None
    if isinstance(cik, int):
        return cik
    s = (cik if isinstance(cik, str) else str(cik)).strip()
    if not s or not s.isdigit():
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _filing_base_url(cik: int | str, accession_no: str) -> str:
    """Base URL for a filing directory (e.g. .../data/320193/000032019324000106/)."""
    cik_int = _cik_to_int(cik)
    if cik_int is None:
        return ""
    acc = _accession_no_dashes(accession_no)
    if not acc:
        return ""
    return f"{SEC_ARCHIVES_BASE}/{cik_int}/{acc}/"


def _index_url(cik: int | str, accession_no: str) -> str:
    """URL for the index page of a filing (e.g. .../000032019324000106-index.htm)."""
    base = _filing_base_url(cik, accession_no)
    acc_raw = (accession_no or "").strip()
    if not base or not acc_raw:
        return ""
    # SEC uses accession-with-dashes + -index.htm or -index.html
    return base + acc_raw + "-index.htm"


def fetch_index_html(cik: int | str, accession_no: str, user_agent: str) -> str | None:
    """Fetch the index HTML for a filing. Returns HTML string or None."""
    import urllib.request
    url = _index_url(cik, accession_no)
    if not url:
        return None
    req = urllib.request.Request(url, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        # Try .html variant
        base = _filing_base_url(cik, accession_no)
        acc_raw = (accession_no or "").strip()
        url2 = base + acc_raw + "-index.html"
        try:
            req2 = urllib.request.Request(url2, headers=_headers(user_agent))
            with urllib.request.urlopen(req2, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None


def _primary_doc_from_index(html: str, base_url: str, form: str) -> str | None:
    """Parse index HTML and return the URL of the primary filing document (.htm/.html)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    candidates = []
    form_lower = (form or "").strip().upper()
    for a in links:
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or "index" in href.lower():
            continue
        if not (href.lower().endswith(".htm") or href.lower().endswith(".html")):
            continue
        if "exhibit" in href.lower() or "ex_" in href.lower():
            continue
        full_url = urljoin(base_url, href)
        name = (a.get_text() or "").strip().lower()
        # Prefer document that looks like the main form (e.g. 10k, 10-k, 10q)
        score = 0
        if "10k" in href.lower() or "10-k" in href.lower() or (form_lower == "10-K" and "10k" in name):
            score = 2
        elif "10q" in href.lower() or "10-q" in href.lower() or (form_lower == "10-Q" and "10q" in name):
            score = 2
        elif "20f" in href.lower() or "20-f" in href.lower() or (form_lower == "20-F" and "20f" in name):
            score = 2
        elif "exhibit" not in href.lower():
            score = 1
        candidates.append((score, full_url))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1]


def fetch_document(url: str, user_agent: str) -> str | None:
    """Fetch a single document (HTML) from SEC. Returns HTML string or None."""
    import urllib.request
    req = urllib.request.Request(url, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _section_end_pos(remainder: str) -> int:
    """First end-pattern position at or after MIN_CHARS_BEFORE_END_MARKER, or len(remainder)."""
    end_pos = len(remainder)
    for pat in MDNA_END_PATTERNS:
        for m in re.finditer(pat, remainder, re.IGNORECASE):
            if m.start() >= MIN_CHARS_BEFORE_END_MARKER and m.start() < end_pos:
                end_pos = m.start()
                break
    return end_pos


def extract_mdna_from_html(html: str, form: str) -> str:
    """
    Extract MD&A section from filing HTML as plain text.
    Uses regex to find Item 7 (10-K), Item 2 (10-Q), or Item 5 (20-F) and text until next Item.
    Picks the *longest* matching section so we get the real MD&A body, not the Table of Contents.
    """
    if not html or not form:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n+", "\n", text)
    form_upper = form.strip().upper()
    start_patterns = MDNA_START_PATTERNS.get(form_upper) or MDNA_START_PATTERNS.get("10-K", [])
    # Collect start positions that look like section headings (at line start), not in-sentence refs like "Item 7, 'Management's Discussion...'"
    starts: list[int] = []
    for pat in start_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            pos = m.start()
            if pos == 0 or text[pos - 1] == "\n":
                starts.append(pos)
    if not starts:
        return ""
    starts = sorted(set(starts))
    # For each start, section length = from start to next end marker (or end of doc).
    # Pick the start that yields the longest section (that's the real MD&A, not the TOC).
    best_start = starts[0]
    best_len = 0
    for s in starts:
        remainder = text[s:]
        end_pos = _section_end_pos(remainder)
        length = end_pos
        if length > best_len:
            best_len = length
            best_start = s
    remainder = text[best_start:]
    end_pos = _section_end_pos(remainder)
    section = remainder[:end_pos]
    section = re.sub(r"\n{3,}", "\n\n", section).strip()
    return section


def fetch_and_extract_mdna(
    cik: int | str,
    accession_no: str,
    form: str,
    user_agent: str,
    delay_seconds: float = 0.2,
    primary_document: str | None = None,
) -> str | None:
    """
    Fetch the primary filing document and extract MD&A text.
    If primary_document (e.g. from submissions API) is given, fetch that file directly;
    otherwise fetch the index page and pick the primary doc from it.
    Returns extracted MD&A string or None on failure.
    """
    form = (form or "").strip() or "10-K"
    cik_int = _cik_to_int(cik)
    if cik_int is None:
        return None
    base_url = _filing_base_url(cik_int, accession_no)
    if not base_url:
        return None
    doc_url = None
    if primary_document and (primary_document.endswith(".htm") or primary_document.endswith(".html")):
        doc_url = base_url + primary_document.strip()
    if not doc_url:
        index_html = fetch_index_html(cik_int, accession_no, user_agent)
        time.sleep(delay_seconds)
        if not index_html:
            return None
        doc_url = _primary_doc_from_index(index_html, base_url, form)
        if not doc_url:
            return None
    time.sleep(delay_seconds)
    doc_html = fetch_document(doc_url, user_agent)
    if not doc_html:
        return None
    return extract_mdna_from_html(doc_html, form)


def write_mdna_for_filing(
    cik: int | str,
    accession_no: str,
    form: str,
    user_agent: str,
    company_dir: Path,
    base_name: str,
    delay_seconds: float = 0.2,
    use_md: bool = False,
    primary_document: str | None = None,
) -> Path | None:
    """
    Fetch MD&A for a filing, write to company_dir, and return the path written (or None).
    base_name is e.g. AAPL_10-K_20240928. File will be base_name_mdna.txt or .md.
    If primary_document is set (from SEC submissions API), the main doc is fetched directly.
    """
    text = fetch_and_extract_mdna(cik, accession_no, form, user_agent, delay_seconds, primary_document=primary_document)
    if not text or len(text) < 50:
        return None
    ext = ".md" if use_md else ".txt"
    out_path = company_dir / f"{base_name}_mdna{ext}"
    out_path.write_text(text, encoding="utf-8")
    return out_path
