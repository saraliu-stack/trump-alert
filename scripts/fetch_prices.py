#!/usr/bin/env python3
"""
fetch_prices.py — Get current stock prices and % change for a list of tickers.

Usage:
    python fetch_prices.py AAPL DELL PLTR [--since=YYYY-MM-DD]

Outputs JSON: {ticker: {price, change_pct_today, change_pct_since, currency, name}}
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

def get_prices_yfinance(tickers, since_date=None):
    """Use yfinance to fetch price data."""
    import yfinance as yf

    results = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            hist = t.history(period="5d")
            if hist.empty:
                results[ticker] = {"error": "no data"}
                continue

            current_price = float(hist["Close"].iloc[-1])
            prev_close    = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current_price
            change_today  = round((current_price - prev_close) / prev_close * 100, 2) if prev_close else 0

            change_since = None
            if since_date:
                try:
                    since_dt = datetime.strptime(since_date, "%Y-%m-%d")
                    hist_long = t.history(start=since_date)
                    if not hist_long.empty:
                        price_at_mention = float(hist_long["Close"].iloc[0])
                        change_since = round((current_price - price_at_mention) / price_at_mention * 100, 2)
                except Exception:
                    pass

            results[ticker] = {
                "price":            round(current_price, 2),
                "change_pct_today": change_today,
                "change_pct_since": change_since,
                "currency":         info.get("currency", "USD"),
                "name":             info.get("shortName") or info.get("longName") or ticker,
                "market_cap":       info.get("marketCap"),
                "52w_high":         info.get("fiftyTwoWeekHigh"),
                "52w_low":          info.get("fiftyTwoWeekLow"),
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}
    return results


def get_prices_fallback(tickers, since_date=None):
    """Fallback: scrape Yahoo Finance quote page for basic price data."""
    import urllib.request
    import re

    results = {}
    for ticker in tickers:
        url = f"https://finance.yahoo.com/quote/{ticker}/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract price from meta tag or data attributes
            price_m = re.search(r'"regularMarketPrice"[^}]*?"raw":([\d.]+)', html)
            change_m = re.search(r'"regularMarketChangePercent"[^}]*?"raw":(-?[\d.]+)', html)
            name_m   = re.search(r'<title>([^(]+)\(', html)

            price = float(price_m.group(1)) if price_m else None
            change = round(float(change_m.group(1)), 2) if change_m else None
            name = name_m.group(1).strip() if name_m else ticker

            results[ticker] = {
                "price":            round(price, 2) if price else None,
                "change_pct_today": change,
                "change_pct_since": None,
                "currency":         "USD",
                "name":             name,
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}
    return results


def get_prices(tickers, since_date=None):
    try:
        import yfinance  # noqa: F401
        return get_prices_yfinance(tickers, since_date)
    except ImportError:
        print("[fetch_prices] yfinance not installed, using fallback scraper", file=sys.stderr)
        return get_prices_fallback(tickers, since_date)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD date to compute change since mention")
    args = parser.parse_args()

    results = get_prices(args.tickers, args.since)
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
