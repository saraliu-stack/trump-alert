"""
fetch_news.py — Financial news and WH speech supplement for trump-alert skill.

Runs in two modes, selectable via --wh-supplement:

MODE 1 (default): General financial news
    Scans financial RSS feeds (Yahoo Finance, CNBC, Reuters Business) for stories
    about Trump mentioning or praising specific companies. Catches "Trump says buy
    Dell"-type stories from rallies, video events, and phone calls where no official
    WH transcript is published.
    Source tag: "news"  →  rendered as 📰 in digests

MODE 2 (--wh-supplement): White House speech news supplement
    Scans political/general news RSS feeds (Reuters US News, AP Top News, Axios,
    Politico, The Hill) for stories explicitly about what Trump SAID at a WH event,
    press conference, rally, or speech — specifically looking for company/stock
    mentions within those speech reports. This fills the gap where whitehouse.gov
    publishes only a video page with no text transcript.
    Source tag: "wh_supplement"  →  rendered as 🎤 in digests (treated as speech)

Both modes output the same JSON schema as fetch_posts.py and fetch_speeches.py so
results can be merged directly.

Usage:
    python fetch_news.py [--hours=48] [--days=N] [--ticker=SYMBOL] [--buy-only]
    python fetch_news.py --wh-supplement [--hours=48] [--days=N] [--ticker=SYMBOL]
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
# RSS feed lists
# ---------------------------------------------------------------------------

# Mode 1: financial news — Trump company mentions from market-focused outlets
FINANCIAL_RSS_FEEDS = [
    # Yahoo Finance per-ticker feeds
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=PLTR&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DELL&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=INTC&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MU&region=US&lang=en-US",
    # CNBC: top news and markets
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    # Reuters business
    "https://feeds.reuters.com/reuters/businessNews",
    # Google News search feeds — reliable from any network / CI runner
    # Catches "Trump praises X" stories that don't appear in per-ticker feeds
    "https://news.google.com/rss/search?q=Trump+stock+company+praised+OR+endorsed+OR+buy&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+Palantir+OR+Dell+OR+Micron+OR+Intel+OR+Apple+OR+Nvidia+stock&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+says+buy+OR+praised+OR+invest+OR+%22great+company%22&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+Goldman+Sachs+OR+Microsoft+OR+Tesla+OR+Amazon+stock&hl=en-US&gl=US&ceid=US:en",
]

# Mode 2: political/general news — what Trump said at WH events and speeches
WH_SUPPLEMENT_RSS_FEEDS = [
    # Reuters US politics and general US news
    "https://feeds.reuters.com/Reuters/PoliticsNews",
    "https://feeds.reuters.com/reuters/domesticNews",
    # The Hill — strong WH/Congress coverage
    "https://thehill.com/feed/",
    # Politico
    "https://www.politico.com/rss/politicopicks.xml",
    # CNBC politics
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000113",
    # Google News — WH speech / event coverage (reliable from GitHub Actions)
    "https://news.google.com/rss/search?q=Trump+White+House+speech+OR+remarks+company+stock&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+said+praised+OR+endorsed+OR+recommended+company&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+rally+OR+press+conference+stock+OR+company+buy&hl=en-US&gl=US&ceid=US:en",
]

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Mode 1: Trump actively promoting / praising a company in general coverage
FINANCIAL_BUY_PATTERNS = [
    r"\btrump\b.{0,60}\b(buy|invest|great company|great investment|going up|strong)\b",
    r"\btrump\b.{0,60}\b(prais|endors|recommend|told.{0,20}buy|urged.{0,20}buy)\b",
    r"\b(buy a|go buy|go out and buy)\b.{0,40}\btrump\b",
    r"\btrump says.{0,60}(buy|invest|great)\b",
    r"\btrump.{0,30}(boosts?|touts?|pumps?|plugs?|champions?)\b",
    r"\btrump.{0,30}calls.{0,30}(great|hot|strong|best)\b",
]

FINANCIAL_WARN_PATTERNS = [
    r"\btrump\b.{0,60}\b(boycott|failing|corrupt|dead|terrible|horrible|worst|tariff|ban|sanction)\b",
    r"\btrump.{0,30}(threatens?|attacks?|targets?|bans?)\b.{0,40}(compan|stock|corp)\b",
    r"\btrump.{0,30}accuses?\b",
]

# Mode 2: Speech/event-specific — Trump SAID something AT a WH or public event
WH_SPEECH_BUY_PATTERNS = [
    # Event-grounded "Trump said X at Y" structures
    r"\btrump\b.{0,30}\b(said|told|urged|asked|called on|recommended).{0,60}\b(buy|invest|great|purchase)\b",
    r"\b(at the white house|at a rally|at an event|during.{0,20}speech|during.{0,20}remarks|during.{0,20}press conf|at.{0,20}event).{0,80}\b(buy|invest|praised|compan|stock)\b",
    r"\btrump\b.{0,60}\b(white house|rally|speech|remarks|event|press conf).{0,60}\b(buy|invest|praised|prais)\b",
    r"\bpresident trump.{0,60}\b(prais|endors|touts?|promoted|championed)\b",
    r"\btrump remarks.{0,60}(compan|stock|buy|invest)\b",
    r"\btrump.{0,20}\bmother.{0,10}day\b.{0,60}(buy|compan|stock|dell|apple|intel)\b",
    r"\btrump.{0,20}white house.{0,60}(buy|compan|stock|great)\b",
]

WH_SPEECH_WARN_PATTERNS = [
    r"\btrump\b.{0,30}\b(said|told|warned|threatened).{0,60}\b(boycott|ban|tariff|sanction|shut down|failing)\b",
    r"\b(at the white house|at a rally|speech|remarks).{0,80}\b(boycott|ban|threatened|attacked)\b",
]

# ---------------------------------------------------------------------------
# Company name → ticker mapping (comprehensive)
# ---------------------------------------------------------------------------

COMPANY_TICKERS = {
    # Known Trump holdings (always check these)
    "palantir": "PLTR",
    "dell": "DELL",
    "dell technologies": "DELL",
    "apple": "AAPL",
    "thermo fisher": "TMO",
    "micron": "MU",
    "micron technology": "MU",
    "nvidia": "NVDA",
    "servicenow": "NOW",
    "workday": "WDAY",
    "oracle": "ORCL",
    "microsoft": "MSFT",
    "intel": "INTC",
    "trump media": "DJT",
    "truth social": "DJT",
    # Broader market
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    "synopsys": "SNPS",
    "qualcomm": "QCOM",
    "salesforce": "CRM",
    "ibm": "IBM",
    "cisco": "CSCO",
    "adobe": "ADBE",
    "amd": "AMD",
    "axom": "AXOM",
    "boeing": "BA",
    "lockheed martin": "LMT",
    "raytheon": "RTX",
    "northrop grumman": "NOC",
    "general dynamics": "GD",
    "exxon": "XOM",
    "chevron": "CVX",
    "halliburton": "HAL",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "goldman sachs": "GS",
    "bank of america": "BAC",
    "morgan stanley": "MS",
    "pfizer": "PFE",
    "moderna": "MRNA",
    "johnson & johnson": "JNJ",
    "eli lilly": "LLY",
    "unitedhealth": "UNH",
    "walmart": "WMT",
    "disney": "DIS",
    "ford": "F",
    "general motors": "GM",
}

# Tickers with known Trump holdings — for conflict-of-interest flag
CONFLICT_TICKERS = {
    "DELL", "AAPL", "TMO", "MU", "PLTR", "NVDA",
    "NOW", "WDAY", "ORCL", "MSFT", "INTC", "DJT",
}

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
        self.items = []
        self._current = None
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


def fetch_url(url: str, timeout: int = 12) -> str | None:
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
    if not date_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
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

def detect_signal(text: str, buy_patterns: list, warn_patterns: list) -> str:
    low = text.lower()
    for pat in buy_patterns:
        if re.search(pat, low):
            return "buy"
    for pat in warn_patterns:
        if re.search(pat, low):
            return "warn"
    return "neutral"


def extract_companies(text: str, buy_patterns: list, warn_patterns: list) -> list:
    low = text.lower()
    hits = []
    seen = set()
    for name, ticker in COMPANY_TICKERS.items():
        if name in low and ticker not in seen:
            idx = low.find(name)
            start = max(0, idx - 80)
            end = min(len(text), idx + len(name) + 80)
            snippet = text[start:end].strip()
            signal = detect_signal(text, buy_patterns, warn_patterns)
            hits.append({
                "company": name.title(),
                "ticker": ticker,
                "signal_type": signal,
                "context_snippet": snippet,
            })
            seen.add(ticker)
    return hits


def is_trump_related(text: str, speech_mode: bool = False) -> bool:
    """Check whether the story involves Trump making statements."""
    low = text.lower()
    if "trump" not in low:
        return False
    if speech_mode:
        # Need to be about a WH event / speech / public statement
        event_words = [
            "white house", "speech", "remarks", "rally", "press conf", "briefing",
            "said", "told", "urged", "called on", "event", "ceremony", "signing",
            "address", "spoke", "speaking", "statement", "announced",
        ]
        if not any(w in low for w in event_words):
            return False
    # Must reference a market / company context (company names count directly)
    market_words = [
        "stock", "share", "invest", "compan", "corp", "market", "buy", "sell",
        "praise", "boost", "touts", "endorses", "says", "tells", "urges",
        "recommends", "praised", "told", "urged", "great", "hails", "promotes",
        "champion", "fantastic", "tremendous", "incredible", "phenomenal",
        # Major company name fragments so "Trump + Dell" passes without needing "stock"
        "dell", "apple", "palantir", "nvidia", "micron", "intel", "microsoft",
        "goldman", "tesla", "amazon", "google", "meta", "boeing", "lockheed",
    ]
    return any(w in low for w in market_words)

# ---------------------------------------------------------------------------
# Core fetch function (shared by both modes)
# ---------------------------------------------------------------------------

def fetch_from_feeds(
    feeds: list,
    cutoff_dt: datetime,
    buy_patterns: list,
    warn_patterns: list,
    source_tag: str,
    speech_mode: bool = False,
    ticker_filter: str | None = None,
    buy_only: bool = False,
) -> list:
    results = []
    seen_links = set()

    for feed_url in feeds:
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

            pub_dt = parse_rss_date(item.pub_date)
            if pub_dt and pub_dt < cutoff_dt:
                continue

            if not is_trump_related(full_text, speech_mode=speech_mode):
                continue

            if item.link in seen_links:
                continue
            seen_links.add(item.link)

            mentions = extract_companies(full_text, buy_patterns, warn_patterns)
            if not mentions:
                continue

            if ticker_filter:
                mentions = [m for m in mentions if m["ticker"].upper() == ticker_filter.upper()]
                if not mentions:
                    continue

            if buy_only:
                mentions = [m for m in mentions if m["signal_type"] == "BUY"]
                if not mentions:
                    continue

            pub_str = pub_dt.isoformat() if pub_dt else item.pub_date
            conflict = any(m["ticker"] in CONFLICT_TICKERS for m in mentions)

            results.append({
                "post_id": f"{source_tag}_{abs(hash(item.link)) % 10**10}",
                "source": source_tag,
                "title": item.title,
                "created_at": pub_str,
                "content": full_text[:600],
                "url": item.link,
                "engagement": {},
                "mentions": mentions,
                "conflict_of_interest": conflict,
            })

    return results

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_news_mentions(cutoff_dt: datetime, ticker_filter=None, buy_only=False):
    """Mode 1: general financial news (📰)."""
    return fetch_from_feeds(
        feeds=FINANCIAL_RSS_FEEDS,
        cutoff_dt=cutoff_dt,
        buy_patterns=FINANCIAL_BUY_PATTERNS,
        warn_patterns=FINANCIAL_WARN_PATTERNS,
        source_tag="news",
        speech_mode=False,
        ticker_filter=ticker_filter,
        buy_only=buy_only,
    )


def fetch_wh_speech_supplement(cutoff_dt: datetime, ticker_filter=None, buy_only=False):
    """Mode 2: WH speech/event news supplement (🎤).

    Finds news REPORTS about what Trump said at White House events, rallies, and
    speeches — specifically to catch company/stock mentions where whitehouse.gov
    only published a video page and no transcript.
    """
    return fetch_from_feeds(
        feeds=WH_SUPPLEMENT_RSS_FEEDS,
        cutoff_dt=cutoff_dt,
        buy_patterns=WH_SPEECH_BUY_PATTERNS,
        warn_patterns=WH_SPEECH_WARN_PATTERNS,
        source_tag="wh_supplement",
        speech_mode=True,
        ticker_filter=ticker_filter,
        buy_only=buy_only,
    )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch financial news or WH speech supplement for Trump company mentions"
    )
    parser.add_argument("--hours",          type=int, default=48)
    parser.add_argument("--days",           type=int, default=None)
    parser.add_argument("--ticker",         type=str, default=None)
    parser.add_argument("--buy-only",       action="store_true")
    parser.add_argument("--wh-supplement",  action="store_true",
                        help="Run in WH-speech supplement mode (political RSS, event patterns)")
    args = parser.parse_args()

    hours = args.days * 24 if args.days else args.hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if args.wh_supplement:
        results = fetch_wh_speech_supplement(
            cutoff_dt=cutoff,
            ticker_filter=args.ticker,
            buy_only=args.buy_only,
        )
        source_label = "wh_speech_supplement"
    else:
        results = fetch_news_mentions(
            cutoff_dt=cutoff,
            ticker_filter=args.ticker,
            buy_only=args.buy_only,
        )
        source_label = "financial_news_rss"

    output = {
        "source": source_label,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff.isoformat(),
        "total_matched": len(results),
        "posts": results,
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
