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

# Sections shorter than this are treated as TOC/nav; we prefer a start that yields longer content (real MD&A body).
MIN_MDNA_SECTION_CHARS = 1500


def _clean_mdna_text(section: str) -> str:
    """
    Remove layout noise that can confuse downstream LLM analysis while preserving content.
    """
    if not section:
        return ""

    cleaned_lines: list[str] = []
    previous = ""
    for raw in section.splitlines():
        line = raw.strip()
        if not line:
            cleaned_lines.append("")
            previous = ""
            continue

        lower = line.lower()
        # Common EDGAR rendering noise.
        if lower == "table of contents":
            continue
        # Page number or numeric spill lines (e.g., "25", "1,234", "3.5").
        if re.fullmatch(r"[0-9][0-9,.\-]*", line):
            continue
        # Isolated punctuation artifacts from HTML flattening.
        if re.fullmatch(r"[\"'`.,;:!?()\[\]{}\-–—]+", line):
            continue
        # Single-letter spillover artifacts (e.g., dangling "s" from wrapped words).
        if re.fullmatch(r"[A-Za-z]", line):
            continue
        # Drop immediate duplicates to reduce repeated running headers.
        if line == previous:
            continue

        cleaned_lines.append(line)
        previous = line

    text = "\n".join(cleaned_lines)
    # Normalize excessive whitespace while keeping paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_toc_start(remainder: str) -> bool:
    """
    Heuristic TOC detector near candidate start.
    TOC entries often list adjacent item headers and page numbers immediately after "Item 7/2/5".
    """
    window = remainder[:450]
    if not window:
        return False
    item_lines = len(re.findall(r"(?im)^\s*item\s+[0-9]{1,2}[a-z]?\b", window))
    has_adjacent_items = bool(re.search(r"(?i)\bitem\s+7a\b|\bitem\s+8\b|\bitem\s+3\b|\bitem\s+6\b", window))
    page_number_lines = len(re.findall(r"(?m)^\s*\d{1,4}\s*$", window))
    return (item_lines >= 2 and has_adjacent_items) or (item_lines >= 2 and page_number_lines >= 1)


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
    urls = _all_filing_doc_urls_from_index(html, base_url, form)
    return urls[0] if urls else None


def _all_filing_doc_urls_from_index(html: str, base_url: str, form: str) -> list[str]:
    """
    Parse index HTML and return URLs of all main filing documents (.htm/.html), in order.
    Excludes exhibits. Includes part1, part2, etc. so we can extract full MD&A when it spans files.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    candidates: list[tuple[int, str, str]] = []  # (score, sort_key, full_url)
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
        href_lower = href.lower()
        # Prefer documents that look like the main form; score by relevance.
        score = 0
        if "10k" in href_lower or "10-k" in href_lower or (form_lower == "10-K" and "10k" in name):
            score = 2
        elif "10q" in href_lower or "10-q" in href_lower or (form_lower == "10-Q" and "10q" in name):
            score = 2
        elif "20f" in href_lower or "20-f" in href_lower or (form_lower == "20-F" and "20f" in name):
            score = 2
        elif "exhibit" not in href_lower:
            score = 1
        # Sort key: primary first, then part1, part2, ... (lexicographic so order is stable)
        sort_key = href_lower
        candidates.append((score, sort_key, full_url))
    if not candidates:
        return []
    # Keep only main-filing score (2 or 1), then sort by score desc and by href for stable order (part1 before part2).
    candidates = [c for c in candidates if c[0] >= 1]
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return [url for _, _, url in candidates]


def fetch_document(url: str, user_agent: str) -> str | None:
    """Fetch a single document (HTML) from SEC. Returns HTML string or None."""
    import urllib.request
    req = urllib.request.Request(url, headers=_headers(user_agent))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _is_line_start(text: str, pos: int) -> bool:
    """True if pos is at start of text or immediately after a newline."""
    return pos == 0 or (pos > 0 and text[pos - 1] == "\n")


def _section_end_pos(remainder: str) -> int:
    """
    First end-pattern position at or after MIN_CHARS_BEFORE_END_MARKER, or len(remainder).
    Only counts matches at line start so in-sentence refs (e.g. "See Item 8") don't cut the section.
    """
    end_pos = len(remainder)
    for pat in MDNA_END_PATTERNS:
        for m in re.finditer(pat, remainder, re.IGNORECASE):
            if not _is_line_start(remainder, m.start()):
                continue
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

    # Prefer starts that appear after "Part II" (10-K) so we get the real MD&A in the body, not the TOC.
    part2_markers = ("PART II", "Part II")
    part2_pos = -1
    for marker in part2_markers:
        idx = text.find(marker)
        if idx != -1 and (part2_pos == -1 or idx < part2_pos):
            part2_pos = idx
    if form_upper == "10-K" and part2_pos != -1:
        starts_after_part2 = [s for s in starts if s >= part2_pos]
        if starts_after_part2:
            starts = starts_after_part2
    # 10-Q MD&A lives in Part I Item 2; bias to starts after Part I to avoid TOC.
    if form_upper == "10-Q":
        part1_markers = ("PART I", "Part I")
        part1_pos = -1
        for marker in part1_markers:
            idx = text.find(marker)
            if idx != -1 and (part1_pos == -1 or idx < part1_pos):
                part1_pos = idx
        if part1_pos != -1:
            starts_after_part1 = [s for s in starts if s >= part1_pos]
            if starts_after_part1:
                starts = starts_after_part1

    # For each start, section length = from start to next end marker (or end of doc).
    # Prefer the longest section that looks like real MD&A (not TOC). TOC blocks are short;
    # if any start yields a long section, use the longest of those; otherwise use the longest overall.
    candidates = []
    for s in starts:
        remainder = text[s:]
        if _looks_like_toc_start(remainder):
            continue
        end_pos = _section_end_pos(remainder)
        candidates.append((s, end_pos))
    if not candidates:
        for s in starts:
            remainder = text[s:]
            end_pos = _section_end_pos(remainder)
            candidates.append((s, end_pos))
    long_enough = [(s, L) for s, L in candidates if L >= MIN_MDNA_SECTION_CHARS]
    if long_enough:
        best_start, best_len = max(long_enough, key=lambda x: x[1])
    else:
        best_start, best_len = max(candidates, key=lambda x: x[1])
    remainder = text[best_start:]
    end_pos = _section_end_pos(remainder)
    section = remainder[:end_pos]
    section = re.sub(r"\n{3,}", "\n\n", section).strip()
    return _clean_mdna_text(section)


def fetch_and_extract_mdna(
    cik: int | str,
    accession_no: str,
    form: str,
    user_agent: str,
    delay_seconds: float = 0.2,
    primary_document: str | None = None,
) -> str | None:
    """
    Fetch the filing document(s) and extract full MD&A text.
    If primary_document (e.g. from submissions API) is given, fetch that file only.
    Otherwise fetch the index, get all main filing docs (primary + part2, part3, etc.),
    concatenate their content, and extract MD&A so we capture the entire section when it spans files.
    Returns extracted MD&A string or None on failure.
    """
    form = (form or "").strip() or "10-K"
    cik_int = _cik_to_int(cik)
    if cik_int is None:
        return None
    base_url = _filing_base_url(cik_int, accession_no)
    if not base_url:
        return None

    if primary_document and (primary_document.endswith(".htm") or primary_document.endswith(".html")):
        doc_url = base_url + primary_document.strip()
        time.sleep(delay_seconds)
        doc_html = fetch_document(doc_url, user_agent)
        if not doc_html:
            return None
        return extract_mdna_from_html(doc_html, form)

    index_html = fetch_index_html(cik_int, accession_no, user_agent)
    time.sleep(delay_seconds)
    if not index_html:
        return None
    doc_urls = _all_filing_doc_urls_from_index(index_html, base_url, form)
    if not doc_urls:
        return None

    parts: list[str] = []
    for url in doc_urls:
        time.sleep(delay_seconds)
        html = fetch_document(url, user_agent)
        if html:
            parts.append(html)
    if not parts:
        return None
    combined_html = "\n".join(parts)
    return extract_mdna_from_html(combined_html, form)


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
