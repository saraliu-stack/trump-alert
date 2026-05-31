"""
trump_alert._api — Core programmatic interface.

All public functions run the existing fetch scripts as subprocesses and return
plain Python dicts / lists. This keeps the package thin (no duplicated logic)
while letting any agent or framework call it without touching the CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Scripts live one level up from this package directory
_SCRIPTS = Path(__file__).parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Trump's known OGE-disclosed holdings — exposed as a constant so agents
# can cross-reference without running any script.
# ---------------------------------------------------------------------------

TRUMP_HOLDINGS: dict[str, dict] = {
    "DELL": {"company": "Dell Technologies", "range": "$1M–$5M",         "purchased": "Feb 10, 2026"},
    "AAPL": {"company": "Apple",             "range": "$250K–$500K",     "purchased": "Mar 11, 2026"},
    "TMO":  {"company": "Thermo Fisher",     "range": "$15K–$50K",       "purchased": "Mar 11, 2026"},
    "MU":   {"company": "Micron",            "range": "$50K–$100K",      "purchased": "Mar 25, 2026"},
    "PLTR": {"company": "Palantir",          "range": "$247K–$630K",     "purchased": "~Apr 2026"},
    "NVDA": {"company": "Nvidia",            "range": "$1M–$5M",         "purchased": "Feb 2026"},
    "NOW":  {"company": "ServiceNow",        "range": "$1M–$5M",         "purchased": "Feb 2026"},
    "WDAY": {"company": "Workday",           "range": "$1M–$5M",         "purchased": "Feb 2026"},
    "ORCL": {"company": "Oracle",            "range": "$1M–$5M",         "purchased": "Feb 2026"},
    "MSFT": {"company": "Microsoft",         "range": "$1M–$5M",         "purchased": "Feb 2026"},
    "INTC": {"company": "Intel",             "range": "~10% admin stake", "purchased": "2025–2026"},
    "DJT":  {"company": "Trump Media",       "range": "Majority stake",   "purchased": "Founder"},
}

CONFLICT_TICKERS: set[str] = set(TRUMP_HOLDINGS.keys())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_script(script_name: str, extra_args: list[str] | None = None, timeout: int = 120) -> Any | None:
    """Run a fetch script and return its parsed JSON output, or None on failure."""
    cmd = [sys.executable, str(_SCRIPTS / script_name)] + (extra_args or [])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


def _build_args(hours: int | None, days: int | None, ticker: str | None, buy_only: bool) -> list[str]:
    args: list[str] = []
    if days is not None:
        args.append(f"--days={days}")
    elif hours is not None:
        args.append(f"--hours={hours}")
    if ticker:
        args.append(f"--ticker={ticker.upper()}")
    if buy_only:
        args.append("--buy-only")
    return args


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan(
    hours: int = 48,
    days: int | None = None,
    ticker: str | None = None,
    buy_only: bool = False,
) -> dict:
    """
    Scan all four sources for Trump company/ticker mentions.

    Runs fetch_posts, fetch_speeches, fetch_news (both modes), and
    fetch_last30days in parallel and returns a merged result dict.

    Returns
    -------
    {
        "scanned_at": str,           # ISO timestamp
        "lookback_hours": int,
        "sources": {
            "truth_social":   {...meta, "posts": [...]},
            "wh_speeches":    {...meta, "posts": [...]},
            "wh_supplement":  {"total_matched": N, "posts": [...]},
            "financial_news": {"total_matched": N, "posts": [...]},
            "community":      {"total_matched": N, "posts": [...]},
        },
        "all_mentions": [...],        # merged, deduplicated list of mention dicts
        "buy_alerts":   [...],        # subset where signal_type == "buy"
        "coi_alerts":   [...],        # subset where conflict_of_interest == True
    }
    """
    effective_hours = (days * 24) if days is not None else hours
    args = _build_args(hours, days, ticker, buy_only)

    # Run all sources
    ts      = _run_script("fetch_posts.py",      args) or {"meta": {}, "posts": []}
    wh      = _run_script("fetch_speeches.py",   args) or {"meta": {}, "posts": []}
    wh_supp = _run_script("fetch_news.py",       args + ["--wh-supplement"]) or {"total_matched": 0, "posts": []}
    news    = _run_script("fetch_news.py",       args) or {"total_matched": 0, "posts": []}
    community = _run_script("fetch_last30days.py", args) or {"total_matched": 0, "posts": []}

    all_posts = (
        ts.get("posts", [])
        + wh.get("posts", [])
        + wh_supp.get("posts", [])
        + news.get("posts", [])
        + community.get("posts", [])
    )

    # Flatten all mentions across all posts
    all_mentions: list[dict] = []
    for post in all_posts:
        for mention in post.get("mentions", []):
            all_mentions.append({
                **mention,
                "source": post.get("source", "unknown"),
                "source_url": post.get("url", ""),
                "posted_at": post.get("created_at", ""),
                "content_snippet": post.get("content", "")[:200],
            })

    buy_alerts = [m for m in all_mentions if m.get("signal_type") in ("buy", "BUY")]
    coi_alerts = [m for m in all_mentions if m.get("conflict_of_interest")]

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": effective_hours,
        "sources": {
            "truth_social":   {"meta": ts.get("meta", {}), "posts": ts.get("posts", [])},
            "wh_speeches":    {"meta": wh.get("meta", {}), "posts": wh.get("posts", [])},
            "wh_supplement":  wh_supp,
            "financial_news": news,
            "community":      community,
        },
        "all_mentions": all_mentions,
        "buy_alerts":   buy_alerts,
        "coi_alerts":   coi_alerts,
    }


def digest(days: int = 30, no_prices: bool = False) -> dict:
    """
    Build a full Trump market alert digest.

    Equivalent to running run_daily.py --json.

    Returns the raw digest dict with keys:
        scan_time, days, ts_meta, wh_meta, wh_supp_count, news_count,
        l30d_count, company_mentions, price_data, price_timestamp,
        new_oge_filings
    """
    args = [f"--days={days}", "--json"]
    if no_prices:
        args.append("--no-prices")
    return _run_script("run_daily.py", args, timeout=300) or {
        "error": "digest failed — check scripts/run_daily.py",
        "days": days,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def portfolio() -> dict:
    """
    Get live prices for Trump's known OGE-disclosed holdings.

    Returns
    -------
    {
        "fetched_at": str,
        "holdings": {
            "DELL": {"company": str, "range": str, "purchased": str,
                     "price": float, "change_pct_today": float, ...},
            ...
        }
    }
    """
    tickers = list(TRUMP_HOLDINGS.keys())
    price_data = get_prices(tickers)

    holdings_out: dict[str, dict] = {}
    for ticker, info in TRUMP_HOLDINGS.items():
        entry = dict(info)
        price_entry = price_data.get(ticker, {})
        entry.update({
            "price": price_entry.get("price"),
            "change_pct_today": price_entry.get("change_pct_today"),
            "price_error": price_entry.get("error"),
        })
        holdings_out[ticker] = entry

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "holdings": holdings_out,
        "disclaimer": "OGE filings lag actual trades by weeks. Holdings may be incomplete.",
    }


def get_prices(tickers: list[str], since_date: str | None = None) -> dict:
    """
    Get live prices for a list of tickers via yfinance.

    Parameters
    ----------
    tickers : list of ticker symbols
    since_date : optional ISO date string (YYYY-MM-DD) to compute % change since

    Returns
    -------
    { "TICKER": {"price": float, "change_pct_today": float, "change_pct_since": float | None} }
    """
    try:
        # Import inline — yfinance is an optional dependency
        from scripts import fetch_prices  # type: ignore
        return fetch_prices.get_prices(tickers, since_date=since_date)
    except ImportError:
        pass

    # Fallback: call the script
    args = tickers[:]
    if since_date:
        args += ["--since", since_date]
    result = _run_script("fetch_prices.py", args)
    return result or {}
