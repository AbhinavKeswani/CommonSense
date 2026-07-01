"""Current market price / shares / market cap for a ticker.

SEC filings give us fundamentals but not the market price, which valuation
multiples (P/E, P/S, P/B, EV/EBITDA, PEG) require. This module fetches a light
quote with two paths:

  1. yfinance (primary) — if installed, gives price + shares outstanding.
  2. Yahoo chart JSON (fallback) — keyless, no deps. Mirrors Atlas's yahoo.py:
     Yahoo 429s Python's urllib fingerprint, so we prefer `curl` and fall back
     to urllib.

Shares outstanding are best sourced from the SEC facts (the caller passes them
in); Yahoo shares are only a fallback when the filing value is missing.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("commonsense.prices")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)


@dataclass
class PriceQuote:
    symbol: str
    price: float | None = None
    shares_outstanding: float | None = None
    market_cap: float | None = None
    source: str = ""

    def with_shares(self, shares: float | None) -> "PriceQuote":
        """Prefer an externally supplied (SEC-derived) share count and recompute market cap."""
        if shares and shares > 0:
            self.shares_outstanding = float(shares)
        if self.price is not None and self.shares_outstanding:
            self.market_cap = float(self.price) * float(self.shares_outstanding)
        return self


def _fetch(url: str, timeout: float = 12.0) -> str | None:
    """GET a URL, preferring curl (Yahoo 429s urllib); fall back to urllib."""
    if shutil.which("curl"):
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", str(int(timeout)), "-A", _UA, url],
                capture_output=True,
                timeout=timeout + 4,
            )
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", "replace")
        except Exception as e:
            log.info("curl fetch failed: %s", e)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as e:
        log.info("urllib fetch failed: %s", e)
        return None


def _quote_from_yfinance(symbol: str) -> PriceQuote | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        t = yf.Ticker(symbol)
        price = None
        shares = None
        # fast_info is cheap and avoids the flaky .info scrape when possible.
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            price = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
            shares = fi.get("shares") if hasattr(fi, "get") else getattr(fi, "shares", None)
        if price is None or shares is None:
            info = t.info or {}
            price = price if price is not None else info.get("currentPrice") or info.get("regularMarketPrice")
            shares = shares if shares is not None else info.get("sharesOutstanding")
        if price is None:
            return None
        q = PriceQuote(symbol=symbol.upper(), price=float(price), source="yfinance")
        return q.with_shares(shares)
    except Exception as e:
        log.info("yfinance quote failed for %s: %s", symbol, e)
        return None


def _quote_from_yahoo_chart(symbol: str) -> PriceQuote | None:
    sym = urllib.parse.quote(symbol.upper().strip())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
    body = _fetch(url)
    if not body:
        return None
    try:
        meta = json.loads(body)["chart"]["result"][0]["meta"]
        px = meta.get("regularMarketPrice")
        if px is None:
            return None
        return PriceQuote(symbol=symbol.upper(), price=float(px), source="yahoo-chart")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
        log.info("yahoo chart parse failed for %s: %s", symbol, e)
        return None


def get_quote(symbol: str, shares_outstanding: float | None = None) -> PriceQuote | None:
    """Return a PriceQuote for `symbol`, preferring the SEC-derived share count.

    Tries yfinance first, then the keyless Yahoo chart endpoint. `shares_outstanding`,
    if given (typically from SEC facts), overrides the fetched share count and drives
    market cap.
    """
    q = _quote_from_yfinance(symbol) or _quote_from_yahoo_chart(symbol)
    if q is None:
        return None
    return q.with_shares(shares_outstanding)


def get_quotes(symbols: list[str], delay: float = 0.4) -> dict[str, PriceQuote]:
    """Best-effort quote per symbol with a small delay to avoid Yahoo burst 429s."""
    out: dict[str, PriceQuote] = {}
    for i, s in enumerate(symbols):
        if i:
            time.sleep(delay)
        q = get_quote(s)
        if q is not None:
            out[s.upper()] = q
    return out
