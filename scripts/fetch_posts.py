#!/usr/bin/env python3
"""
fetch_posts.py — Fetch Trump Truth Social posts and detect company/ticker mentions.

Usage:
    python fetch_posts.py [--hours=48] [--days=N] [--ticker=SYMBOL] [--buy-only]

Outputs JSON to stdout: list of matched post objects.
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Data sources (try in order)
# ---------------------------------------------------------------------------
SOURCES = [
    "https://ix.cnn.io/data/truth-social/truth_archive.json",
    "https://stilesdata.com/trump-truth-social-archive/posts.json",
]

# ---------------------------------------------------------------------------
# BUY signal keywords
# ---------------------------------------------------------------------------
BUY_PHRASES = [
    r"\bbuy\b", r"\bgreat investment\b", r"\bgoing up\b", r"\bstrong buy\b",
    r"\binvest in\b", r"\byou should own\b", r"\bgreat company\b",
    r"\bgreat stock\b", r"\bbullish\b", r"\bsurging\b", r"\bsoaring\b",
    r"\bwinning\b", r"\bcontinues to rise\b", r"\bwar fighting capabilities\b",
    r"\bgreat\b.{0,40}(stock|company|investment)",
    # Explicit buy-signal phrases from known Trump events
    r"\bgo out and buy\b",           # "go out and buy a Dell"
    r"\bis great\b",                  # "Micron is great"
    r"\b's great\b",                  # "Micron's great"
    r"\bone of the hottest\b",        # "one of the hottest companies"
    r"\bbetter than other\b",         # "better than other computers"
    r"\bamazing company\b",
    r"\bincredible company\b",
    r"\bfantastic company\b",
    r"\btremendous company\b",
    r"\bgreat great\b",
    r"\bvery successful\b",           # "very successful CEO"
    r"\bhot company\b",
    r"\bhottest company\b",
]

# ---------------------------------------------------------------------------
# SELL/WARNING signal keywords
# ---------------------------------------------------------------------------
SELL_PHRASES = [
    r"\bboycott\b", r"\bfailing\b", r"\bdead\b", r"\bcorrupt\b",
    r"\bbad company\b", r"\bbad stock\b", r"\bhorrible\b", r"\bnasty\b",
    r"\boverrated\b", r"\bgoing down\b", r"\bcollapse\b",
]

# ---------------------------------------------------------------------------
# Trump's known stock holdings (ticker -> info)
# ---------------------------------------------------------------------------
TRUMP_HOLDINGS = {
    "DELL": {"company": "Dell Technologies", "range": "$1M–$5M",         "date": "February 10, 2026"},
    "AAPL": {"company": "Apple",             "range": "$250K–$500K",     "date": "March 11, 2026"},
    "TMO":  {"company": "Thermo Fisher",     "range": "$15K–$50K",       "date": "March 11, 2026"},
    "MU":   {"company": "Micron",            "range": "$50K–$100K",      "date": "March 25, 2026"},
    "PLTR": {"company": "Palantir",          "range": "$247K–$630K",     "date": "~April 2026"},
    "NVDA": {"company": "Nvidia",            "range": "$1M–$5M",         "date": "February 2026"},
    "NOW":  {"company": "ServiceNow",        "range": "$1M–$5M",         "date": "February 2026"},
    "WDAY": {"company": "Workday",           "range": "$1M–$5M",         "date": "February 2026"},
    "ORCL": {"company": "Oracle",            "range": "$1M–$5M",         "date": "February 2026"},
    "MSFT": {"company": "Microsoft",         "range": "$1M–$5M",         "date": "February 2026"},
    "INTC": {"company": "Intel",             "range": "10% admin stake", "date": "2025–2026"},
    "DJT":  {"company": "Trump Media",       "range": "Majority stake",  "date": "Founder"},
}

# ---------------------------------------------------------------------------
# Major company name → ticker mapping (S&P 500 + notable companies)
# Extend as needed — lowercase keys for case-insensitive matching
# ---------------------------------------------------------------------------
COMPANY_MAP = {
    # Tech
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "google": "GOOGL",
    "alphabet": "GOOGL", "amazon": "AMZN", "meta": "META", "facebook": "META",
    "tesla": "TSLA", "netflix": "NFLX", "intel": "INTC", "amd": "AMD",
    "qualcomm": "QCOM", "broadcom": "AVGO", "oracle": "ORCL",
    "salesforce": "CRM", "servicenow": "NOW", "workday": "WDAY",
    "palantir": "PLTR", "dell": "DELL", "dell technologies": "DELL",
    "thermo fisher": "TMO", "thermo fisher scientific": "TMO",
    "micron": "MU", "micron technology": "MU",
    "corning": "GLW", "openai": None, "anthropic": None,
    "ibm": "IBM", "cisco": "CSCO", "adobe": "ADBE", "paypal": "PYPL",
    "uber": "UBER", "lyft": "LYFT", "airbnb": "ABNB", "spotify": "SPOT",
    "twitter": "X", "x corp": "X", "snap": "SNAP", "pinterest": "PINS",
    # Finance
    "jpmorgan": "JPM", "jp morgan": "JPM", "goldman sachs": "GS",
    "bank of america": "BAC", "citigroup": "C", "wells fargo": "WFC",
    "morgan stanley": "MS", "blackrock": "BLK", "visa": "V",
    "mastercard": "MA", "american express": "AXP",
    # Defense / Aerospace
    "lockheed martin": "LMT", "raytheon": "RTX", "boeing": "BA",
    "northrop grumman": "NOC", "general dynamics": "GD", "l3harris": "LHX",
    # Energy
    "exxon": "XOM", "chevron": "CVX", "conocophillips": "COP",
    "halliburton": "HAL", "schlumberger": "SLB",
    # Healthcare / Pharma
    "pfizer": "PFE", "moderna": "MRNA", "johnson & johnson": "JNJ",
    "johnson and johnson": "JNJ", "eli lilly": "LLY", "abbvie": "ABBV",
    "unitedhealth": "UNH", "cvs": "CVS",
    # Retail / Consumer
    "walmart": "WMT", "target": "TGT", "costco": "COST",
    "home depot": "HD", "lowes": "LOW",
    # Media / Telecom
    "disney": "DIS", "comcast": "CMCSA", "at&t": "T", "verizon": "VZ",
    "trump media": "DJT", "truth social": "DJT",
    # Auto
    "ford": "F", "gm": "GM", "general motors": "GM", "rivian": "RIVN",
    # Other notable
    "spacex": None, "x aerospace": None,
}

# Noise words to ignore when scanning for all-caps tickers
IGNORE_CAPS = {
    "I", "A", "OR", "AND", "IS", "IT", "BE", "DO", "SO", "WE", "US",
    "THE", "FOR", "NOT", "BUT", "ALL", "ANY", "CAN", "OUR", "ARE",
    "HAS", "HAD", "WAS", "ITS", "GET", "GOT", "LET", "PUT", "SET",
    "DID", "HIM", "HER", "HIS", "SHE", "HE", "AM", "AS", "AT", "BY",
    "IF", "IN", "OF", "ON", "TO", "UP", "NO", "MY", "ME", "GO", "NO",
    "NEW", "NOW", "WAY", "BIG", "BAD", "OLD", "TOP", "ONE", "TWO",
    "YES", "ILL", "OK", "AG", "EU", "UN", "UK", "FBI", "CIA", "DOJ",
    "SEC", "FDA", "CDC", "GOP", "DNC", "CEO", "CFO", "COO", "AI",
    "TV", "PC", "GREAT", "HUGE", "FAKE", "SAD", "WIN", "LOST",
    "MAGA", "MAKE", "AMERICA", "AGAIN", "VERY", "MUCH", "MANY", "SOME",
    "TIME", "YEAR", "DEAL", "THEM", "THEY", "WILL", "BEEN", "FROM",
    "WITH", "THAT", "THIS", "HAVE", "WHAT", "WHEN", "THEN", "THAN",
    "ALSO", "JUST", "EVEN", "WELL", "BOTH", "SUCH", "INTO", "OVER",
    "AFTER", "ABOUT", "WHICH", "THEIR", "THERE", "WHERE", "WHILE",
    "THESE", "THOSE", "STILL", "WOULD", "COULD", "SHOULD", "FIRST",
    "LAST", "MOST", "ONLY", "MORE", "LESS", "VERY", "MUCH",
}

# Known valid tickers (subset of most-discussed; used to reduce false positives)
KNOWN_TICKERS = set(TRUMP_HOLDINGS.keys()) | {
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    "NFLX", "INTC", "AMD", "QCOM", "AVGO", "ORCL", "CRM", "NOW",
    "WDAY", "PLTR", "DELL", "TMO", "MU", "GLW", "IBM", "CSCO", "ADBE", "PYPL", "UBER", "LYFT",
    "ABNB", "SPOT", "SNAP", "PINS", "JPM", "GS", "BAC", "C", "WFC",
    "MS", "BLK", "V", "MA", "AXP", "LMT", "RTX", "BA", "NOC", "GD",
    "LHX", "XOM", "CVX", "COP", "HAL", "SLB", "PFE", "MRNA", "JNJ",
    "LLY", "ABBV", "UNH", "CVS", "WMT", "TGT", "COST", "HD", "LOW",
    "DIS", "CMCSA", "T", "VZ", "DJT", "F", "GM", "RIVN", "X",
}


def fetch_archive():
    """Fetch posts from the first available source."""
    for url in SOURCES:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "trump-alert-skill/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                # CNN archive may wrap in {"posts": [...]} or be a bare list
                if isinstance(data, list):
                    return data, url
                if isinstance(data, dict):
                    for key in ("posts", "data", "results", "items"):
                        if key in data and isinstance(data[key], list):
                            return data[key], url
                    # flat dict keyed by id
                    return list(data.values()), url
        except Exception as e:
            print(f"[fetch] {url} failed: {e}", file=sys.stderr)
    return [], None


def parse_dt(s):
    """Parse ISO 8601 timestamp to UTC datetime."""
    if not s:
        return None
    s = s.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def detect_signal(text):
    """Return 'buy', 'sell', or 'neutral' for a text snippet."""
    t = text.lower()
    for pat in BUY_PHRASES:
        if re.search(pat, t, re.IGNORECASE):
            return "buy"
    for pat in SELL_PHRASES:
        if re.search(pat, t, re.IGNORECASE):
            return "sell"
    return "neutral"


def extract_context(text, keyword, window=80):
    """Return a short snippet around the keyword in text."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:150] + ("…" if len(text) > 150 else "")
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(keyword) + window // 2)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def find_mentions(text):
    """Return list of {company, ticker, signal_type, context_snippet, conflict_of_interest}."""
    mentions = []
    seen_tickers = set()

    # 1. Company name matching
    lower_text = text.lower()
    # Single-word ambiguous names that need stricter context to avoid false positives
    NEEDS_CONTEXT = {
        "target": r'\b(target\s+(corp|stock|shares|inc|retail)|buy\s+target|target\s+store)\b',
        "ford":   r'\b(ford\s+(stock|motor|f-150|truck|shares)|buy\s+ford)\b',
        "snap":   r'\b(snap\s+(stock|shares|inc)|snapchat\s+stock)\b',
        "meta":   r'\b(meta\s+(stock|shares|platforms|ai)|buy\s+meta)\b',
        "apple":  r'\b(apple\s+(stock|shares|inc|computer|iphone|ipad|mac)|buy\s+apple|tim\s+cook|apple\s+ceo)\b|tim apple\b',
    }
    for name, ticker in COMPANY_MAP.items():
        if name in NEEDS_CONTEXT:
            if not re.search(NEEDS_CONTEXT[name], lower_text, re.IGNORECASE):
                continue  # ambiguous word — require stricter context
        else:
            if not re.search(r'\b' + re.escape(name) + r'\b', lower_text):
                continue  # name not found

        t = ticker or name.upper().replace(" ", "")
        if t in seen_tickers:
            continue
        seen_tickers.add(t)
        signal = detect_signal(extract_context(text, name, 200))
        mentions.append({
            "company": name.title(),
            "ticker": ticker,
            "signal_type": signal,
            "context_snippet": extract_context(text, name),
            "conflict_of_interest": ticker in TRUMP_HOLDINGS if ticker else False,
            "holding_info": TRUMP_HOLDINGS.get(ticker) if ticker else None,
        })

    # 2. Raw ticker scanning (ALL-CAPS 2–5 letter words; skip single-letter to avoid noise)
    for m in re.finditer(r'\b([A-Z]{2,5})\b', text):
        token = m.group(1)
        if token in seen_tickers or token in IGNORE_CAPS:
            continue
        if token not in KNOWN_TICKERS:
            continue
        seen_tickers.add(token)
        signal = detect_signal(extract_context(text, token, 200))
        # Reverse-lookup company name
        company = next(
            (v.title() for v, t in COMPANY_MAP.items() if t == token),
            token,
        )
        mentions.append({
            "company": company,
            "ticker": token,
            "signal_type": signal,
            "context_snippet": extract_context(text, token),
            "conflict_of_interest": token in TRUMP_HOLDINGS,
            "holding_info": TRUMP_HOLDINGS.get(token),
        })

    return mentions


def main():
    parser = argparse.ArgumentParser(description="Fetch Trump Truth Social posts and detect company mentions.")
    parser.add_argument("--hours", type=int, default=48, help="Look back N hours (default 48)")
    parser.add_argument("--days",  type=int, default=None, help="Look back N days (overrides --hours)")
    parser.add_argument("--ticker", type=str, default=None, help="Filter to specific ticker symbol")
    parser.add_argument("--buy-only", action="store_true", help="Only return BUY signal posts")
    args = parser.parse_args()

    lookback_hours = args.days * 24 if args.days else args.hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    posts, source_url = fetch_archive()
    if not posts:
        print(json.dumps({
            "error": "Could not fetch archive from any source",
            "sources_tried": SOURCES,
        }))
        sys.exit(1)

    # Find newest post timestamp for freshness header
    freshness = None
    for p in posts[:5]:
        dt = parse_dt(p.get("created_at"))
        if dt:
            freshness = dt.strftime("%Y-%m-%d %H:%M UTC")
            break

    results = []
    total_scanned = 0

    for post in posts:
        dt = parse_dt(post.get("created_at"))
        if dt and dt < cutoff:
            continue  # outside window
        total_scanned += 1

        content = post.get("content", "") or ""
        # Strip HTML tags (Truth Social sometimes includes them)
        content_plain = re.sub(r"<[^>]+>", " ", content).strip()
        content_plain = re.sub(r"\s+", " ", content_plain)
        # Remove Trump's signature forms before ticker scanning to avoid
        # false-positive DJT (Trump Media) matches on every post.
        # "President DJT" anywhere in text, and bare "DJT" at the very end
        # of the post (his standard sign-off), are both signatures, not tickers.
        content_for_detection = re.sub(r'\bPresident\s+DJT\b', '', content_plain, flags=re.IGNORECASE)
        content_for_detection = re.sub(r'\bDJT\s*[!?.]*\s*$', '', content_for_detection.strip())

        mentions = find_mentions(content_for_detection)
        if not mentions:
            continue

        # Apply filters
        if args.ticker:
            mentions = [m for m in mentions if m.get("ticker") == args.ticker.upper()]
        if args.buy_only:
            mentions = [m for m in mentions if m["signal_type"] == "buy"]

        if not mentions:
            continue

        results.append({
            "post_id": post.get("id"),
            "created_at": post.get("created_at"),
            "content": content_plain,
            "url": post.get("url") or post.get("uri"),
            "engagement": {
                "favourites": post.get("favourites_count", 0),
                "reblogs":    post.get("reblogs_count", 0),
                "replies":    post.get("replies_count", 0),
            },
            "mentions": mentions,
        })

    output = {
        "meta": {
            "total_scanned": total_scanned,
            "total_matched": len(results),
            "lookback_hours": lookback_hours,
            "archive_freshness": freshness,
            "source_url": source_url,
        },
        "posts": results,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
