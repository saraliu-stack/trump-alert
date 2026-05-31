"""
trump_alert — Python package API.

Importable by any Python agent, script, or notebook.

    from trump_alert import scan, digest, portfolio

Functions return plain dicts/lists so they're easy to consume
regardless of what framework the caller uses.
"""

from ._api import scan, digest, portfolio, get_prices, TRUMP_HOLDINGS, CONFLICT_TICKERS

__all__ = ["scan", "digest", "portfolio", "get_prices", "TRUMP_HOLDINGS", "CONFLICT_TICKERS"]
__version__ = "1.0.0"
