"""
fetch_news.py — Third monitoring source for trump-alert skill.

Scans financial news headlines (Yahoo Finance RSS, CNBC RSS) for reports of
Trump mentioning or praising specific companies. Catches "Trump says buy Dell"-type
stories that originate from video events, rallies, or phone calls where no official
White House transcript is published.

Output: same JSON schema as fetch_posts.py and fetch_speeches.py.
"""

import sys
import json
import re
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RSS_FEEDS = [
    # Yahoo Finance — covers most Trump stock/company stories
    "https://finance.yahoo.com/rss/headline?s=AAPL",   # broad market feed via AAPL
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=PLTR&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DELL&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=INTC&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
    # CNBC top news
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    # Reuters business
    "https://feeds.reuters.com/reuters/businessNews",
    # AP business
    "https://rsshub.app/apnews/topics/business",
]

# Signals that Trump is actively promoting / praising a company
BUY_SIGNAL_PATTERNS = [
    r"\btrump\b.{0,60}\b(buy|invest|great company|great investment|going up|strong)\b",
    r"\btrump\b.{0,60}\b(prais|endors|recommend|told.{0,20}buy|urged.{0,20}buy)\b",
    r"\b(buy a|go buy|purchase).{0,40}\btrump\b",
    r"\btrump says.{0,60}(buy|invest|great)\b",
    r"\btrump.{0,30}(boosts?|touts?|pumps?|plugs?)\b",
]

# Signals that Trump is attacking / warning about a company
WARN_SIGNAL_PATTERNS = [
    r"\btrump\b.{0,60}\b(boycott|failing|corrupt|dead|terrible|horrible|worst|tariff|ban|sanction)\b",
    r"\btrump.{0,30}(threatens?|attacks?|targets?|bans?)\b.{0,40}(compan|stock|corp)\b",
    r"\btrump.{0,30}accus\b",
]

# Company name → ticker mapping (S&P 500 focus + known holdings)
COMPANY_TICKERS = {
    "palantir": "PLTR",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "dell": "DELL",
    "apple": "AAPL",
    "aapl": "AAPL",
    "intel": "INTC",
    "intc": "INTC",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "oracle": "ORCL",
    "orcl": "ORCL",
    "servicenow": "NOW",
    "workday": "WDAY",
    "micron": "MU",
    "thermo fisher": "TMO",
    "amazon": "AMZN",
    "meta": "META",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    "synopsys": "SNPS",
    "axom": "AXOM",
    "trump media": "DJT",
    "truth social": "DJT",
    "boeing": "BA",
    "lockheed": "LMT",
    "raytheon": "RTX",
    "northrop": "NOC",
    "halliburton": "HAL",
    "exxon": "XOM",
    "chevron": "CVX",
}

# Common false positives to skip
SKIP_WORDS = {"trump", "the", "and", "for", "but", "not", "buy", "sell"}

# ---------------------------------------------------------------------------
# HTML / RSS parsing helpers
# ---------------------------------------------------------------------------

class RSSItem:
    def __init__(self):
        self.title = ""
        self.link = ""
        self.pub_date = ""
        self.description = ""


class RSSParser(HTMLParser):
    """Minimal RSS 2.0 parser that extracts <item> blocks."""

    def __init__(self):
        super().__init__()
        self.items: list[RSSItem] = []
        self._current: RSSItem | None = None
        self._in_item = False
        self._tag = None
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "item":
            self._in_item = True
            self._current = RSSItem()
        if self._in_item and tag in ("title", "link", "pubdate", "description"):
            self._tag = tag
            self._buf = ""

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._in_item and tag in ("title", "link", "pubdate", "description"):
            value = re.sub(r"<[^>]+>", "", self._buf).strip()
            if self._current:
                setattr(self._current, tag.replace("pubdate", "pub_date"), value)
            self._tag = None
        elif tag == "item" and self._current:
            self.items.append(self._current)
            self._current = None
            self._in_item = False

    def handle_data(self, data):
        if self._in_item and self._tag:
            self._buf += data

    def handle_entityref(self, name):
        if self._in_item and self._tag:
            self._buf += f"&{name};"

    def handle_charref(self, name):
        if self._in_item and self._tag:
            self._buf += f"&#{name};"


def fetch_url(url: str, timeout: int = 10) -> str | None:
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (TrumpAlert/1.0; +https://github.com/saraliu-stack/trump-alert)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return raw.decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        print(f"[fetch_news] {url} failed: {e}", file=sys.stderr)
        return None


def parse_rss_date(date_str: str) -> datetime | None:
    """Parse RFC 2822 date strings from RSS feeds."""
    if not date_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None

# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def detect_signal(text: str) -> str:
    """Return 'BUY', 'WARN', or 'MENTION'."""
    low = text.lower()
    for pat in BUY_SIGNAL_PATTERNS:
        if re.search(pat, low):
            return "BUY"
    for pat in WARN_SIGNAL_PATTERNS:
        if re.search(pat, low):
            return "WARN"
    return "MENTION"


def extract_companies(text: str) -> list[dict]:
    """Extract company mentions and map to tickers."""
    low = text.lower()
    hits = []
    seen = set()
    for name, ticker in COMPANY_TICKERS.items():
        if name in low and ticker not in seen:
            # Find context around the mention
            idx = low.find(name)
            start = max(0, idx - 60)
            end = min(len(text), idx + len(name) + 60)
            snippet = text[start:end].strip()
            signal = detect_signal(text)
            hits.append({
                "company": name.title(),
                "ticker": ticker,
                "signal_type": signal,
                "context_snippet": snippet,
            })
            seen.add(ticker)
    return hits


def is_trump_related(text: str) -> bool:
    """Check whether the headline is about Trump's own statements about companies."""
    low = text.lower()
    if "trump" not in low:
        return False
    # Must mention a company/stock/market context
    market_words = ["stock", "share", "invest", "compan", "corp", "market", "buy", "sell",
                    "mention", "praise", "boost", "touts", "endorses", "says", "tells",
                    "urges", "recommends", "praised", "told", "urged"]
    return any(w in low for w in market_words)

# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_news_mentions(cutoff_dt: datetime, ticker_filter: str | None = None,
                        buy_only: bool = False) -> list[dict]:
    """Fetch RSS feeds and return matched items in the fetch_posts.py output schema."""
    results = []
    seen_links = set()

    for feed_url in RSS_FEEDS:
        xml = fetch_url(feed_url)
        if not xml:
            continue

        parser = RSSParser()
        try:
            parser.feed(xml)
        except Exception as e:
            print(f"[fetch_news] parse error on {feed_url}: {e}", file=sys.stderr)
            continue

        for item in parser.items:
            full_text = f"{item.title} {item.description}"

            # Date filter
            pub_dt = parse_rss_date(item.pub_date)
            if pub_dt and pub_dt < cutoff_dt:
                continue

            # Must be Trump talking about companies
            if not is_trump_related(full_text):
                continue

            # Deduplicate by URL
            if item.link in seen_links:
                continue
            seen_links.add(item.link)

            # Extract company mentions
            mentions = extract_companies(full_text)
            if not mentions:
                continue

            # Optional filters
            if ticker_filter:
                mentions = [m for m in mentions if m["ticker"].upper() == ticker_filter.upper()]
                if not mentions:
                    continue

            if buy_only:
                mentions = [m for m in mentions if m["signal_type"] == "BUY"]
                if not mentions:
                    continue

            # Build output record (same schema as fetch_posts.py)
            pub_str = pub_dt.isoformat() if pub_dt else item.pub_date
            conflict = any(m["ticker"] in CONFLICT_TICKERS for m in mentions)

            results.append({
                "post_id": f"news_{abs(hash(item.link)) % 10**10}",
                "source_type": "news",
                "title": item.title,
                "created_at": pub_str,
                "content": full_text[:500],
                "url": item.link,
                "engagement": {},
                "mentions": mentions,
                "conflict_of_interest": conflict,
            })

    return results


# Tickers with known Trump holdings — for conflict-of-interest flag
CONFLICT_TICKERS = {
    "DELL", "AAPL", "TMO", "MU", "PLTR", "NVDA",
    "NOW", "WDAY", "ORCL", "MSFT", "INTC", "DJT",
}

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch financial news about Trump company mentions")
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--buy-only", action="store_true")
    args = parser.parse_args()

    hours = args.days * 24 if args.days else args.hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    results = fetch_news_mentions(
        cutoff_dt=cutoff,
        ticker_filter=args.ticker,
        buy_only=args.buy_only,
    )

    output = {
        "source": "news_rss",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff.isoformat(),
        "total_matched": len(results),
        "posts": results,
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
