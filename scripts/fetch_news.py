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
#
# Design rules to avoid false positives:
#   • "buy" only counts when Trump is the recommender, not when it describes
#     trade deals ("China will buy 200 planes"), stock purchases ("Trump's buy"),
#     or IBD technical jargon ("near buy points").
#   • Use "to buy" (infinitive) instead of bare "buy" for recommendation context.
#   • Praise verbs (prais*/endors*/touts*) kept but gap capped at 20 chars so
#     "Trump's Palantir buy before praise fuels ethics debate" does NOT match
#     while "Trump praises Micron" still does.
FINANCIAL_BUY_PATTERNS = [
    # Trump explicitly tells/urges people to buy a stock.
    # "says/said" intentionally excluded: "Trump says China will buy 200 planes"
    # (trade deal) also satisfies "says...to buy" and causes false positives.
    r"\btrump\b.{0,60}\b(told|urge[sd]?|ask[ed]?|recommend\w*|advise[sd]?)\b.{0,50}\bto\s+(buy|invest|purchase)\b",
    # "go buy" / "go out and buy" — Trump's classic stock-pump phrase
    r"\btrump\b.{0,120}\bgo(?:\s+out\s+and?)?\s+buy\b",
    r"\bgo(?:\s+out\s+and?)?\s+buy\b.{0,120}\btrump\b",
    # Trump praises/endorses/touts — short gap (≤20 chars) so noun forms don't match
    # Verb stems use \w* to catch all tenses: touts/touted/touting, boosts/boosted, etc.
    r"\btrump\b.{0,20}\b(prais\w+|endors\w+|tout\w*|plug\w*|hail\w*|promot\w*|champion\w*|boost\w*)\b",
    # "President Trump praised/endorsed" — subject-verb in formal reporting
    r"\bpresident\s+trump\b.{0,30}\b(prais\w+|endors\w+|tout\w*|champion\w*|promot\w*)\b",
    # Trump explicitly recommends a stock/company — narrow to avoid matching
    # "Trump's investment policy" or "Trump invest[igates]" as false buy signals.
    r"\btrump\b.{0,30}\b(recommend\w*)\b.{0,50}\b(stock|share|compan|firm)\b",
    # Trade deal: a country agreed to buy company goods — "China agreed to buy 200 aircraft"
    # Requires a country/deal keyword + explicit agreement verb + a quantity.
    # "purchase" and bare "order" removed — they fire on negated contexts like
    # "Beijing won't approve a single H200 purchase" (no agreement = no BUY signal).
    r"\b(china|japan|europe|eu|india|saudi|uae|uk|canada|mexico|korea|trade\s+deal|trade\s+agreement)\b.{0,80}\b(agreed?\s+to\s+buy|will\s+buy|commit\w*\s+to\s+(buy|purchase))\b.{0,50}\b\d+\b",
    # Broader trade deal: Trump announces deal involving purchases of company products
    r"\btrump\b.{0,80}\b(trade\s+deal|trade\s+agreement|signed\s+a\s+deal)\b.{0,100}\b(buy|purchase|order|aircraft|planes?|products?|equipment|billion)\b",
]

FINANCIAL_WARN_PATTERNS = [
    r"\btrump\b.{0,60}\b(boycott|failing|corrupt|dead|terrible|horrible|worst|tariff|ban|sanction)\b",
    r"\btrump.{0,30}(threatens?|attacks?|targets?|bans?)\b.{0,40}(compan|stock|corp)\b",
    r"\btrump.{0,30}accuses?\b",
]

# Mode 2: Speech/event-specific — Trump SAID something AT a WH or public event
WH_SPEECH_BUY_PATTERNS = [
    # Trump said/urged at an event: requires "to buy/invest" (infinitive form)
    r"\btrump\b.{0,60}\b(said|told|urged|asked|called\s+on|recommend\w*)\b.{0,60}\bto\s+(buy|invest|purchase)\b",
    # "go out and buy" at a public event
    r"\bgo(?:\s+out\s+and?)?\s+buy\b.{0,150}\b(trump|white\s+house|rally|speech)\b",
    r"\btrump\b.{0,150}\bgo(?:\s+out\s+and?)?\s+buy\b",
    # Trump praised/endorsed at a public event — short verb gap
    # Verb stems use \w* to catch all tenses: touted/touting, promoted, championed, etc.
    r"\bpresident\s+trump\b.{0,30}\b(prais\w+|endors\w+|tout\w*|promot\w*|champion\w*)\b",
    r"\btrump\b.{0,20}\b(prais\w+|endors\w+|tout\w*)\b.{0,80}\b(compan|stock|invest|firm|product)\b",
    # Event context + praise language
    r"\b(white\s+house|rally|speech|remarks|press\s+conf)\b.{0,100}\b(prais\w+|endors\w+|go\s+buy|buy\s+a\b)\b",
    # Specific known phrasing patterns
    r"\btrump.{0,20}mother.{0,10}day.{0,60}(buy|compan|stock|dell|apple|intel)\b",
    # Trade deal: country agreed to buy company goods — e.g. "China agreed to buy 200 aircraft"
    # Bare "purchase" and "order" removed: they fire on negations like "won't approve a purchase".
    r"\b(china|japan|europe|eu|india|saudi|uae|uk|canada|mexico|korea|trade\s+deal|trade\s+agreement)\b.{0,80}\b(agreed?\s+to\s+buy|will\s+buy|commit\w*\s+to\s+(buy|purchase))\b.{0,50}\b\d+\b",
    r"\btrump\b.{0,80}\b(trade\s+deal|trade\s+agreement|signed\s+a\s+deal)\b.{0,100}\b(buy|purchase|order|aircraft|planes?|products?|equipment|billion)\b",
]

WH_SPEECH_WARN_PATTERNS = [
    r"\btrump\b.{0,30}\b(said|told|warned|threatened).{0,60}\b(boycott|ban|tariff|sanction|shut down|failing)\b",
    r"\b(at the white house|at a rally|speech|remarks).{0,80}\b(boycott|ban|threatened|attacked)\b",
]

# ---------------------------------------------------------------------------
# Company name → ticker mapping (comprehensive)
# ---------------------------------------------------------------------------

# Display-name overrides for entries where .title() gives wrong casing.
# e.g. "ibm".title() == "Ibm", "amd".title() == "Amd"
_DISPLAY_NAMES: dict[str, str] = {
    "ibm": "IBM",
    "amd": "AMD",
    "djt": "DJT",
    "servicenow": "ServiceNow",
    "jp morgan": "JPMorgan",
}

def _company_display(name: str) -> str:
    return _DISPLAY_NAMES.get(name.lower(), name.title())

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
    # "truth social" removed: articles using Truth Social as a platform name
    # (e.g. "Trump praised X on Truth Social") trigger a false DJT BUY signal.
    # DJT is still caught via "trump media" and direct financial coverage of the stock.
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


def _buy_trigger_snippet(text: str, buy_patterns: list, window: int = 180) -> str | None:
    """
    Return a snippet centred on the phrase that actually triggered the BUY signal.

    When the BUY pattern fires on a different sentence from the one that mentions
    the company name (e.g. pattern fires in the description, but the company
    appears in the negative headline), showing this phrase tells the user *why*
    the signal fired instead of showing a misleading negative context.
    Returns None if no pattern matches (should not happen when signal=='buy').
    """
    low = text.lower()
    for pat in buy_patterns:
        m = re.search(pat, low)
        if m:
            start = max(0, m.start() - 40)
            end   = min(len(text), m.end() + window)
            # Snap start forward to a word boundary so snippet doesn't begin mid-word
            if start > 0:
                boundary = text.rfind(' ', 0, start + 1)
                if boundary >= 0:
                    start = boundary + 1
            return text[start:end].strip()
    return None


def _clean_title(title: str) -> str:
    """
    Strip trailing source attribution from RSS headlines.
    Many feeds append ' - Reuters', ' | Yahoo Finance', ' - Investor's Business Daily', etc.
    Removes the last ' - Source' or ' | Source' segment if it looks like a publication name
    (≤40 chars, no sentence punctuation inside).
    """
    title = title.strip()
    m = re.search(r'\s+[-|]\s+([^|.\-]{1,40})\s*$', title)
    if m:
        title = title[:m.start()].strip()
    return title


def _clean_description(description: str, title: str) -> str:
    """
    Strip title duplication from RSS item descriptions.

    Google News and Yahoo Finance RSS embed the article title at the start of
    the <description> field, producing double output like:
        "Trump says China will buy 200 planes from Boeing ... - PBS
         Trump says China will buy 200 planes from Boeing, with a possibility..."

    Strategy: compare the first 60 characters of description (lowercased,
    punctuation-stripped) against the title.  If they overlap significantly,
    drop everything up through the first sentence boundary or dash/pipe that
    follows the title text.
    """
    if not description or not title:
        return description
    # Normalise for comparison
    norm_title = re.sub(r'[^a-z0-9 ]', '', title.lower())[:60].strip()
    norm_desc  = re.sub(r'[^a-z0-9 ]', '', description.lower())[:80].strip()
    # Require at least 30 chars of overlap to act (avoids false positives on
    # very short shared words like "Trump")
    if len(norm_title) < 30:
        return description
    if norm_desc.startswith(norm_title[:30]):
        # Find where the duplicated title ends in the original description.
        # Look for a separator (dash, pipe, newline) or the end of the first sentence.
        cut_pos = len(norm_title)
        sep_match = re.search(r'\s*[-|–—]\s*|\n', description[cut_pos - 5:cut_pos + 40])
        if sep_match:
            cut_pos = cut_pos - 5 + sep_match.end()
        description = description[cut_pos:].strip()
    return description


def extract_companies(text: str, buy_patterns: list, warn_patterns: list,
                      title: str = "") -> list:
    """
    Find company mentions in text. When a clean title is provided it is used
    for the snippet (avoids title/description boundary artefacts like 'Euronews Del').
    """
    low = text.lower()
    # Use the standalone title for snippets when available — cleaner than
    # slicing across the title+description boundary.
    snippet_source = _clean_title(title) if title else text

    hits = []
    seen = set()
    for name, ticker in COMPANY_TICKERS.items():
        if name in low and ticker not in seen:
            # "intel" as a lowercase common noun (intelligence community, ex-intel official)
            # should not match Intel Corp. Require the capitalised form in the original text.
            if name == "intel" and "Intel" not in text:
                continue
            # Build snippet from clean title if company is found there, else from full text
            stl = snippet_source.lower()
            if name in stl:
                idx = stl.find(name)
                start = max(0, idx - 60)
                end = min(len(snippet_source), idx + len(name) + 100)
                # Snap start to word boundary
                if start > 0:
                    boundary = snippet_source.rfind(' ', 0, start + 1)
                    if boundary >= 0:
                        start = boundary + 1
            else:
                idx = low.find(name)
                start = max(0, idx - 60)
                end = min(len(text), idx + len(name) + 100)
                # Snap start to word boundary
                if start > 0:
                    boundary = text.rfind(' ', 0, start + 1)
                    if boundary >= 0:
                        start = boundary + 1
                snippet_source = text
            snippet = snippet_source[start:end].strip()
            signal = detect_signal(text, buy_patterns, warn_patterns)

            # When the BUY pattern fires in the article description but the
            # company name appears in a negative headline, the company-name
            # window misleads the reader.  Prefer the phrase that actually
            # triggered the signal so the user sees *why* it fired.
            if signal == "buy":
                trigger = _buy_trigger_snippet(text, buy_patterns)
                if trigger:
                    snippet = trigger

            hits.append({
                "company": _company_display(name),
                "ticker": ticker,
                "signal_type": signal,
                "context_snippet": snippet,
            })
            seen.add(ticker)
    return hits


def is_trump_related(text: str, speech_mode: bool = False) -> bool:
    """Check whether the story involves Trump making statements."""
    import re as _re
    low = text.lower()
    # Use word-boundary match so "trumps" (verb meaning surpasses) does not
    # trigger Trump detection. "Trump's" still matches because \b fires at
    # the boundary before the apostrophe.
    if not _re.search(r"\btrump\b", low):
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
            # Strip title duplication injected by Google News / Yahoo RSS
            clean_desc = _clean_description(item.description, item.title)
            full_text = f"{item.title} {clean_desc}"

            pub_dt = parse_rss_date(item.pub_date)
            if pub_dt and pub_dt < cutoff_dt:
                continue

            if not is_trump_related(full_text, speech_mode=speech_mode):
                continue

            if item.link in seen_links:
                continue
            seen_links.add(item.link)

            mentions = extract_companies(full_text, buy_patterns, warn_patterns,
                                         title=item.title)
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
