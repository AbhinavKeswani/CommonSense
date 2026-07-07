"""Market data (prices, shares, market cap) for valuation multiples.

Fundamentals come from SEC filings; valuation multiples additionally need the
current market price. This package provides a small, keyless price fetch.
"""

from commonsense.market.prices import PriceQuote, get_quote, get_quotes

__all__ = ["PriceQuote", "get_quote", "get_quotes"]
