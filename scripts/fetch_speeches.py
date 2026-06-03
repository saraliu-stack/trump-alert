#!/usr/bin/env python3
"""
fetch_speeches.py — Fetch Trump White House remarks/speeches and detect company mentions.

Sources:
  - whitehouse.gov/remarks/   (official transcripts)
  - whitehouse.gov/briefings-statements/

Usage:
    python fetch_speeches.py [--hours=72] [--days=N] [--ticker=SYMBOL]

Outputs JSON to stdout: same schema as fetch_posts.py for easy merging.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

# Hard wall-time budget for the entire script (seconds).
# run_daily.py gives us 90s; we self-limit to 80s so we always
# have time to serialize and print results.
WALL_BUDGET_S = 80
_START_TIME = time.monotonic()

def _time_left() -> float:
    return WALL_BUDGET_S - (time.monotonic() - _START_TIME)

def _deadline_ok(need_s: float = 2.0) -> bool:
    """Return True if there is at least need_s seconds left in the budget."""
    return _time_left() > need_s

# ---------------------------------------------------------------------------
# Listing pages to scrape for recent speech links
# ---------------------------------------------------------------------------
WH_LISTING_URLS = [
    "https://www.whitehouse.gov/remarks/",
    "https://www.whitehouse.gov/briefings-statements/",
]

# ---------------------------------------------------------------------------
# Reuse detection tables from fetch_posts (duplicated here for standalone use)
# ---------------------------------------------------------------------------

BUY_PHRASES = [
    r"\bbuy\b", r"\bgreat investment\b", r"\bgoing up\b", r"\bstrong buy\b",
    r"\binvest in\b", r"\byou should own\b", r"\bgreat company\b",
    r"\bgreat stock\b", r"\bbullish\b", r"\bsurging\b", r"\bsoaring\b",
    r"\bgo out and buy\b", r"\bcontinues to rise\b", r"\bone of the hottest\b",
    r"\bgreat\b.{0,40}(stock|company|investment)",
    r"\bis great\b", r"\b's great\b", r"\bbetter than other\b",
    r"\bamazing company\b", r"\bincredible company\b", r"\bfantastic company\b",
    r"\bvery successful\b", r"\bhot company\b", r"\bhottest company\b",
]

SELL_PHRASES = [
    r"\bboycott\b", r"\bfailing\b", r"\bdead\b", r"\bcorrupt\b",
    r"\bbad company\b", r"\bbad stock\b", r"\bhorrible\b", r"\bnasty\b",
    r"\boverrated\b", r"\bgoing down\b", r"\bcollapse\b",
]

# Extended holdings — now includes speech-praise companies
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

_DISPLAY_NAMES: dict[str, str] = {
    "ibm": "IBM", "amd": "AMD", "gm": "GM", "cvs": "CVS",
    "djt": "DJT", "servicenow": "ServiceNow", "jp morgan": "JPMorgan", "at&t": "AT&T",
}

def _company_display(name: str) -> str:
    return _DISPLAY_NAMES.get(name.lower(), name.title())

COMPANY_MAP = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "google": "GOOGL",
    "alphabet": "GOOGL", "amazon": "AMZN", "meta": "META", "facebook": "META",
    "tesla": "TSLA", "netflix": "NFLX", "intel": "INTC", "amd": "AMD",
    "qualcomm": "QCOM", "broadcom": "AVGO", "oracle": "ORCL",
    "salesforce": "CRM", "servicenow": "NOW", "workday": "WDAY",
    "palantir": "PLTR", "dell": "DELL", "dell technologies": "DELL",
    "thermo fisher": "TMO", "thermo fisher scientific": "TMO",
    "micron": "MU", "micron technology": "MU",
    "ibm": "IBM", "cisco": "CSCO", "adobe": "ADBE", "paypal": "PYPL",
    "uber": "UBER", "lyft": "LYFT", "airbnb": "ABNB", "spotify": "SPOT",
    "snap": "SNAP", "pinterest": "PINS",
    "jpmorgan": "JPM", "jp morgan": "JPM", "goldman sachs": "GS",
    "bank of america": "BAC", "citigroup": "C", "wells fargo": "WFC",
    "morgan stanley": "MS", "blackrock": "BLK", "visa": "V",
    "mastercard": "MA", "american express": "AXP",
    "lockheed martin": "LMT", "raytheon": "RTX", "boeing": "BA",
    "northrop grumman": "NOC", "general dynamics": "GD", "l3harris": "LHX",
    "exxon": "XOM", "chevron": "CVX", "conocophillips": "COP",
    "pfizer": "PFE", "moderna": "MRNA", "johnson & johnson": "JNJ",
    "eli lilly": "LLY", "abbvie": "ABBV", "unitedhealth": "UNH", "cvs": "CVS",
    "walmart": "WMT", "target": "TGT", "costco": "COST",
    "home depot": "HD", "lowes": "LOW",
    "disney": "DIS", "comcast": "CMCSA", "at&t": "T", "verizon": "VZ",
    "trump media": "DJT", "truth social": "DJT",
    "ford": "F", "gm": "GM", "general motors": "GM", "rivian": "RIVN",
    "corning": "GLW",
}

IGNORE_CAPS = {
    "I", "A", "OR", "AND", "IS", "IT", "BE", "DO", "SO", "WE", "US",
    "THE", "FOR", "NOT", "BUT", "ALL", "ANY", "CAN", "OUR", "ARE",
    "HAS", "HAD", "WAS", "ITS", "GET", "GOT", "LET", "PUT", "SET",
    "DID", "HIM", "HER", "HIS", "SHE", "HE", "AM", "AS", "AT", "BY",
    "IF", "IN", "OF", "ON", "TO", "UP", "NO", "MY", "ME", "GO",
    "NEW", "NOW", "WAY", "BIG", "BAD", "OLD", "TOP", "ONE", "TWO",
    "YES", "OK", "AG", "EU", "UN", "UK", "FBI", "CIA", "DOJ",
    "SEC", "FDA", "CDC", "GOP", "DNC", "CEO", "CFO", "COO", "AI",
    "TV", "PC", "GREAT", "HUGE", "FAKE", "SAD", "WIN", "LOST",
    "MAGA", "MAKE", "AMERICA", "AGAIN", "VERY", "MUCH", "MANY", "SOME",
    "TIME", "YEAR", "DEAL", "THEM", "THEY", "WILL", "BEEN", "FROM",
    "WITH", "THAT", "THIS", "HAVE", "WHAT", "WHEN", "THEN", "THAN",
    "ALSO", "JUST", "EVEN", "WELL", "BOTH", "SUCH", "INTO", "OVER",
    "AFTER", "ABOUT", "WHICH", "THEIR", "THERE", "WHERE", "WHILE",
}

KNOWN_TICKERS = set(TRUMP_HOLDINGS.keys()) | {
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    "NFLX", "INTC", "AMD", "QCOM", "AVGO", "ORCL", "CRM", "NOW",
    "WDAY", "PLTR", "DELL", "TMO", "MU", "IBM", "CSCO", "ADBE",
    "PYPL", "UBER", "LYFT", "ABNB", "SPOT", "SNAP", "PINS",
    "JPM", "GS", "BAC", "MS", "BLK", "V", "MA", "AXP",
    "LMT", "RTX", "BA", "NOC", "GD", "LHX",
    "XOM", "CVX", "COP", "PFE", "MRNA", "JNJ", "LLY", "ABBV",
    "UNH", "WMT", "TGT", "COST", "HD", "DIS", "T", "VZ",
    "DJT", "F", "GM", "RIVN", "GLW",
}


# ---------------------------------------------------------------------------
# Simple HTML parser to extract text and links
# ---------------------------------------------------------------------------

class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href", "")
            if href.startswith("https://www.whitehouse.gov/") and (
                "/remarks/" in href or "/briefings-statements/" in href or "/videos/" in href
            ) and len(href) > 40:
                self.links.append(href)


class TextExtractor(HTMLParser):
    """Extract visible text from HTML, skipping scripts/styles."""
    def __init__(self):
        super().__init__()
        self._skip = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.chunks.append(text)

    def get_text(self):
        return " ".join(self.chunks)

# Boilerplate phrases common in WH page nav/footer — strip before detection
WH_BOILERPLATE = re.compile(
    r'(Share\s+Icon|Facebook\s+YouTube|TikTok|Instagram|Briefings\s+&\s+Statements'
    r'|Executive\s+Orders|Presidential\s+Actions|Skip\s+to\s+content'
    r'|The\s+White\s+House|whitehouse\.gov|Sign\s+Up\s+for\s+Updates'
    r'|Contact\s+Us|Privacy\s+Policy|Accessibility)',
    re.IGNORECASE
)


def fetch_url(url, timeout=8):
    """Fetch a URL with a tight per-request timeout."""
    if not _deadline_ok(need_s=timeout + 1):
        print(f"[fetch_speeches] wall budget exhausted — skipping {url}", file=sys.stderr)
        return None, None
    req = urllib.request.Request(url, headers={"User-Agent": "trump-alert-skill/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace"), resp.geturl()
    except Exception as e:
        print(f"[fetch_speeches] {url} failed: {e}", file=sys.stderr)
        return None, None


def get_speech_links(cutoff_dt):
    """Scrape WH listing pages and return speech links within the time window.

    Limits to 3 pages per listing URL (≈30 speeches each, covers any 30-day
    window comfortably) and respects the wall-time budget.
    """
    links = []
    for listing_url in WH_LISTING_URLS:
        for page in range(1, 4):   # max 3 pages — enough for 30-day windows
            if not _deadline_ok(need_s=12):
                print("[fetch_speeches] wall budget low — stopping pagination", file=sys.stderr)
                return links
            url = listing_url if page == 1 else f"{listing_url}page/{page}/"
            html, _ = fetch_url(url)
            if not html:
                break
            parser = LinkExtractor()
            parser.feed(html)
            new_links = [l for l in parser.links if l not in links]
            if not new_links:
                break  # no new content on this page
            links.extend(new_links)
            # Stop paginating once the oldest link is before our cutoff
            dates = [extract_date_from_url(l) for l in new_links]
            dated = [d for d in dates if d]
            if dated and min(dated) < cutoff_dt:
                break
    return links


def extract_date_from_url(url):
    """Try to parse a date from WH URL like /remarks-may-8-2026/ or /remarks/2026/05/08/."""
    # Pattern: -month-day-year at end
    m = re.search(
        r'[-/](january|february|march|april|may|june|july|august|september|october|november|december)'
        r'[-/](\d{1,2})[-/](\d{4})',
        url, re.IGNORECASE
    )
    if m:
        month_str, day, year = m.group(1), m.group(2), m.group(3)
        months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
                  "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
        month = months.get(month_str.lower())
        if month:
            try:
                return datetime(int(year), month, int(day), tzinfo=timezone.utc)
            except ValueError:
                pass
    # Pattern: /YYYY/MM/DD/
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def detect_signal(text):
    t = text.lower()
    for pat in BUY_PHRASES:
        if re.search(pat, t, re.IGNORECASE):
            return "buy"
    for pat in SELL_PHRASES:
        if re.search(pat, t, re.IGNORECASE):
            return "sell"
    return "neutral"


def extract_context(text, keyword, window=100):
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:200] + ("…" if len(text) > 200 else "")
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(keyword) + window // 2)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def find_mentions(text):
    mentions = []
    seen_tickers = set()
    lower_text = text.lower()

    NEEDS_CONTEXT = {
        "target": r'\b(target\s+(corp|stock|shares|inc|retail)|buy\s+target|target\s+store)\b',
        "ford":   r'\b(ford\s+(stock|motor|f-150|truck|shares)|buy\s+ford)\b',
        "snap":   r'\b(snap\s+(stock|shares|inc)|snapchat\s+stock)\b',
        "meta":   r'\b(meta\s+(stock|shares|platforms|ai)|buy\s+meta)\b',
        "apple":  r'\b(apple\s+(stock|shares|inc|computer|iphone|ipad|mac)|buy\s+apple|tim\s+cook|apple\s+ceo)\b|tim apple\b',
        "halliburton": r'\b(halliburton\s+(stock|shares|oil|energy)|HAL\b)',
        "intel":  r'\b(intel\s+(stock|shares|corp|chip|semiconductor|processor|ceo|revenue|earnings|pc|computer|results)|buy\s+intel|intel\s+corporation|INTC)\b',
    }
    for name, ticker in COMPANY_MAP.items():
        if name in NEEDS_CONTEXT:
            if not re.search(NEEDS_CONTEXT[name], lower_text, re.IGNORECASE):
                continue
        else:
            if not re.search(r'\b' + re.escape(name) + r'\b', lower_text):
                continue
        t = ticker or name.upper().replace(" ", "")
        if t in seen_tickers:
            continue
        seen_tickers.add(t)
        ctx = extract_context(text, name, 200)
        signal = detect_signal(ctx)
        mentions.append({
                "company": _company_display(name),
                "ticker": ticker,
                "signal_type": signal,
                "context_snippet": extract_context(text, name),
                "conflict_of_interest": ticker in TRUMP_HOLDINGS if ticker else False,
                "holding_info": TRUMP_HOLDINGS.get(ticker) if ticker else None,
            })

    for m in re.finditer(r'\b([A-Z]{2,5})\b', text):
        token = m.group(1)
        if token in seen_tickers or token in IGNORE_CAPS:
            continue
        if token not in KNOWN_TICKERS:
            continue
        seen_tickers.add(token)
        signal = detect_signal(extract_context(text, token, 200))
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
    parser = argparse.ArgumentParser(description="Fetch WH speeches and detect company mentions.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--days",  type=int, default=None)
    parser.add_argument("--ticker", type=str, default=None)
    args = parser.parse_args()

    lookback_hours = args.days * 24 if args.days else args.hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    links = get_speech_links(cutoff)
    print(f"[fetch_speeches] found {len(links)} potential speech links", file=sys.stderr)

    results = []
    freshness = None

    for url in links:
        if not _deadline_ok(need_s=10):
            print(f"[fetch_speeches] wall budget low — stopping after {len(results)} speeches", file=sys.stderr)
            break

        dt = extract_date_from_url(url)
        if dt and dt < cutoff:
            continue

        html, final_url = fetch_url(url)
        if not html:
            continue

        te = TextExtractor()
        te.feed(html)
        raw_text = te.get_text()
        # Strip navigation / boilerplate lines before detection
        lines = [l for l in raw_text.splitlines() if not WH_BOILERPLATE.search(l)]
        text = " ".join(lines)

        # Try to find date in page text if URL date parse failed
        if not dt:
            m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})', text)
            if m:
                months = {"January":1,"February":2,"March":3,"April":4,"May":5,"June":6,
                          "July":7,"August":8,"September":9,"October":10,"November":11,"December":12}
                try:
                    dt = datetime(int(m.group(3)), months[m.group(1)], int(m.group(2)), tzinfo=timezone.utc)
                except (KeyError, ValueError):
                    pass

        if dt and dt < cutoff:
            continue

        if not freshness and dt:
            freshness = dt.strftime("%Y-%m-%d")

        mentions = find_mentions(text)
        if not mentions:
            continue

        if args.ticker:
            mentions = [m for m in mentions if m.get("ticker") == args.ticker.upper()]
        if not mentions:
            continue

        # Extract a title from the page
        title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        title = title_m.group(1).strip() if title_m else url

        results.append({
            "source": "whitehouse_speech",
            "title": title,
            "date": dt.strftime("%Y-%m-%d") if dt else "unknown",
            "url": final_url or url,
            "content": text[:2000],
            "mentions": mentions,
            "engagement": None,
        })

    output = {
        "meta": {
            "total_scanned": len(links),
            "total_matched": len(results),
            "lookback_hours": lookback_hours,
            "archive_freshness": freshness or "unknown",
            "source": "whitehouse.gov/remarks",
        },
        "posts": results,
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
