#!/usr/bin/env python3
"""
run_daily.py — Daily Trump Market Alert digest.

Sliding-window cache: first run fetches 30 days and saves a cache.
Subsequent runs only fetch the last 25 hours, merge into the cache,
prune posts older than 30 days, and re-analyze. Cold start ~90s,
warm runs ~10–15s.

Usage:
    python run_daily.py [--days=30] [--email] [--save=PATH] [--no-prices] [--cold]

Flags:
    --cold    Force a full 30-day refetch (ignores cache)

Config file: ~/.config/trump-alert/.env
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / ALERT_TO
"""

import argparse
import json
import os
import re
import smtplib
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKILL_DIR   = Path(__file__).parent
CONFIG_FILE = Path.home() / ".config" / "trump-alert" / ".env"
SAVE_DIR    = Path.home() / "Documents" / "TrumpAlerts"
CACHE_FILE  = SAVE_DIR / "post_cache.json"

# Cache is considered "warm" if younger than this many hours.
# We fetch 25h on warm runs (1h overlap avoids gaps at DST boundaries).
CACHE_MAX_AGE_H = 23

OGE_KNOWN_FILINGS = [
    ("2026-04-20", "https://www.whitehouse.gov/wp-content/uploads/2026/04/President-Donald-J.-Trump-Periodic-Transaction-Report-4.20.26.pdf"),
    ("2026-02-26", "https://www.whitehouse.gov/wp-content/uploads/2026/03/President-Donald-J.-Trump-Periodic-Transaction-Report-2.26.26-1.pdf"),
]

# Known COI registry for enrichment
COI_REGISTRY = {
    "DELL": {"range": "$1M–$5M",    "date": "Feb 10, 2026"},
    "AAPL": {"range": "$250K–$500K","date": "Mar 11, 2026"},
    "TMO":  {"range": "$15K–$50K",  "date": "Mar 11, 2026"},
    "MU":   {"range": "$50K–$100K", "date": "Mar 25, 2026"},
    "PLTR": {"range": "$247K–$630K","date": "~Apr 2026"},
    "NVDA": {"range": "$1M–$5M",    "date": "Feb 2026"},
    "NOW":  {"range": "$1M–$5M",    "date": "Feb 2026"},
    "WDAY": {"range": "$1M–$5M",    "date": "Feb 2026"},
    "ORCL": {"range": "$1M–$5M",    "date": "Feb 2026"},
    "MSFT": {"range": "$1M–$5M",    "date": "Feb 2026"},
    "INTC": {"range": "10% admin stake", "date": "2025–2026"},
    "DJT":  {"range": "Majority stake",  "date": "founder"},
}

# Phrases that indicate a genuine Trump buy/praise signal
BUY_PHRASES = [
    "buy", "great company", "great investment", "going up", "strong buy",
    "invest in", "you should own", "i own", "amazing company", "incredible",
    "love", "fantastic", "tremendous", "beautiful", "the best", "great great",
    "very great", "hottest", "great war fighting", "goes up", "very successful",
    "is great", "'s great",
]

# Phrases that indicate a list mention (low relevance — CEO name-drop, meeting list)
LIST_MENTION_PATTERNS = [
    r"\([A-Z][^)]{2,30}\),",   # (Company), Company), in a series
    r"journeying to the",
    r"met with.*ceo",
    r"ceo of",
    r"and many others",
]


# ─────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────
def load_config():
    config = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    for key in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "ALERT_TO"]:
        if key in os.environ:
            config[key] = os.environ[key]
    return config


# ─────────────────────────────────────────────────────────────
#  Sliding-window post cache
# ─────────────────────────────────────────────────────────────
def load_cache():
    """Returns (posts, age_hours). posts=[] and age=9999 if no valid cache."""
    if not CACHE_FILE.exists():
        return [], 9999
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        last_updated = datetime.fromisoformat(data["last_updated"])
        age_h = (datetime.now(timezone.utc) - last_updated).total_seconds() / 3600
        return data.get("posts", []), age_h
    except Exception as e:
        print(f"[cache] load failed: {e}", file=sys.stderr)
        return [], 9999


def save_cache(posts, window_days=30):
    """Deduplicate, prune to window, and save cache."""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    # Deduplicate by post_id / url
    seen, unique = set(), []
    for p in posts:
        key = (p.get("post_id") or p.get("id") or p.get("url") or "").strip()
        if key:
            if key not in seen:
                seen.add(key)
                unique.append(p)
        else:
            unique.append(p)  # keep keyless posts (they won't duplicate in practice)

    # Prune posts older than window
    fresh = []
    for p in unique:
        dt = (p.get("created_at") or p.get("date") or "")[:19]
        if dt >= cutoff[:19]:
            fresh.append(p)

    CACHE_FILE.write_text(json.dumps({
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "post_count": len(fresh),
        "posts": fresh,
    }, indent=2, default=str), encoding="utf-8")
    print(f"[cache] saved {len(fresh)} posts to {CACHE_FILE}", file=sys.stderr)
    return fresh


# ─────────────────────────────────────────────────────────────
#  Subprocess runner
# ─────────────────────────────────────────────────────────────
def run_script(script_name, extra_args=None, timeout=60):
    cmd = [sys.executable, str(SKILL_DIR / script_name)]
    if extra_args:
        cmd.extend(extra_args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        print(f"[run_daily] {script_name} stderr: {result.stderr[:300]}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"[run_daily] {script_name} timed out ({timeout}s) — skipped", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[run_daily] {script_name} failed: {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────
#  Relevance scoring
# ─────────────────────────────────────────────────────────────
def relevance_score(snippet: str, content: str = "") -> int:
    """
    Score a mention's relevance 0–100.
    High = Trump is actually talking about/to the company.
    Low  = company name appears in a list of CEOs or meeting attendees.
    """
    text = (snippet + " " + content).lower()
    score = 30  # base

    # Strong buy/praise language → high relevance
    for phrase in BUY_PHRASES:
        if phrase in text:
            score += 25
            break

    # Explicit ticker in snippet → likely a real mention
    if re.search(r'\b[A-Z]{2,5}\b', snippet):
        score += 10

    # List-context patterns → reduce score
    for pat in LIST_MENTION_PATTERNS:
        if re.search(pat, content, re.IGNORECASE):
            score -= 20
            break

    # News headline (no direct Trump quote) → medium relevance
    if snippet.startswith("http") or len(snippet) < 20:
        score -= 10

    return max(0, min(100, score))


def pick_best_post(posts_for_company: list) -> dict | None:
    """Return the post with the highest-relevance buy-signal snippet."""
    buy_posts = [p for p in posts_for_company if p.get("signal") == "buy"]
    pool = buy_posts if buy_posts else posts_for_company
    if not pool:
        return None
    return max(pool, key=lambda p: relevance_score(p.get("snippet", ""), ""))


# ─────────────────────────────────────────────────────────────
#  Per-company analysis synthesis
# ─────────────────────────────────────────────────────────────
def generate_analysis(ticker: str, company_data: dict, prices: dict) -> str:
    """
    Build a plain-English analysis paragraph for a company.
    Covers: signal pattern, price action, COI context, news reaction.
    """
    c      = company_data
    n      = len(c["posts"])
    buys   = c["buy_count"]
    coi    = COI_REGISTRY.get(ticker)
    p_info = prices.get(ticker, {})
    news   = [p for p in c["posts"] if p.get("source") == "📰"]
    ts     = [p for p in c["posts"] if p.get("source") == "📱"]
    speech = [p for p in c["posts"] if p.get("source") == "🎤"]

    parts = []

    # Signal summary
    if buys > 0:
        sources_str = _sources_label(ts, speech, news)
        parts.append(
            f"Trump has sent <b>{buys} explicit buy signal{'s' if buys > 1 else ''}</b> "
            f"for {c['company']} over the past 30 days, via {sources_str}."
        )
    elif n == 1:
        parts.append(
            f"{c['company']} received a single neutral mention — "
            f"not a direct buy signal, but worth monitoring."
        )
    else:
        parts.append(
            f"{c['company']} has been mentioned <b>{n} times</b> in the window "
            f"without a direct buy signal — likely contextual coverage."
        )

    # Price action
    price      = p_info.get("price")
    chg_since  = p_info.get("change_pct_since")
    chg_today  = p_info.get("change_pct_today")
    if price:
        price_str = f"${price:,.2f}"
        if chg_since is not None:
            direction = "up" if chg_since >= 0 else "down"
            color_tag = "green" if chg_since >= 0 else "red"
            parts.append(
                f"Since first mention the stock is "
                f"<span style='color:{color_tag};font-weight:bold'>"
                f"{direction} {abs(chg_since):.1f}%</span> "
                f"(currently {price_str}"
                + (f", {_arrow(chg_today)}{abs(chg_today):.1f}% today" if chg_today is not None else "")
                + ")."
            )

    # COI
    if coi:
        parts.append(
            f"⚠️ <b>Conflict of interest:</b> Trump personally holds "
            f"{coi['range']} in {ticker} (purchased {coi['date']}). "
            f"In every prior case where Trump praised a company, he had already bought the stock."
        )

    # News / market reaction
    if news:
        headline = news[-1].get("snippet", "")[:160].rstrip(".")
        if headline:
            parts.append(f"📰 <b>Media reaction:</b> \"{headline}…\"")

    return " ".join(parts) if parts else "No analysis available."


def _sources_label(ts, speech, news):
    labels = []
    if ts:     labels.append("Truth Social")
    if speech: labels.append("White House speech")
    if news:   labels.append("press coverage")
    return " and ".join(labels) if labels else "an unspecified source"


def _arrow(val):
    return "▲" if val is not None and val >= 0 else "▼"


# ─────────────────────────────────────────────────────────────
#  OGE filing checker
# ─────────────────────────────────────────────────────────────
def check_new_oge_filings():
    latest_known = max(d for d, _ in OGE_KNOWN_FILINGS)
    new_filings = []
    try:
        url = "https://www.whitehouse.gov/disclosures/"
        req = urllib.request.Request(url, headers={"User-Agent": "trump-alert/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        pdf_links = re.findall(
            r'href="(https://www\.whitehouse\.gov/wp-content/uploads/\d{4}/\d{2}/[^"]*278[^"]*\.pdf)"',
            html, re.IGNORECASE
        )
        for link in pdf_links:
            m = re.search(r'/(\d{4})/(\d{2})/', link)
            if m:
                approx = f"{m.group(1)}-{m.group(2)}-01"
                if approx > latest_known and link not in [u for _, u in OGE_KNOWN_FILINGS]:
                    new_filings.append({"url": link, "approx_date": approx})
    except Exception as e:
        print(f"[run_daily] OGE check failed: {e}", file=sys.stderr)
    return new_filings


# ─────────────────────────────────────────────────────────────
#  Price formatter (plain text)
# ─────────────────────────────────────────────────────────────
def fmt_price(prices, ticker):
    if not prices or ticker not in prices:
        return "N/A"
    d = prices[ticker]
    if "error" in d:
        return "(unavailable)"
    price = d.get("price")
    today = d.get("change_pct_today")
    since = d.get("change_pct_since")
    parts = [f"${price:,.2f}" if price else ""]
    if today is not None:
        parts.append(f"{'▲' if today >= 0 else '▼'}{abs(today):.1f}% today")
    if since is not None:
        parts.append(f"{'▲' if since >= 0 else '▼'}{abs(since):.1f}% since mention")
    return "  ".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────
#  Digest builder  (with cache)
# ─────────────────────────────────────────────────────────────
def build_digest(days=30, include_prices=True, force_cold=False):
    now = datetime.now(timezone.utc)

    # ── Cache decision ──────────────────────────────────────
    cached_posts, cache_age_h = load_cache()
    warm = (not force_cold) and (cache_age_h < CACHE_MAX_AGE_H) and len(cached_posts) > 0

    if warm:
        # Incremental: only pull last 25h from each live source
        fetch_args = ["--hours=25"]
        print(f"[run_daily] Warm cache ({len(cached_posts)} posts, {cache_age_h:.1f}h old) "
              f"— fetching last 25h only", file=sys.stderr)
    else:
        fetch_args = [f"--days={days}"]
        print(f"[run_daily] Cold start — fetching full {days}-day window", file=sys.stderr)

    # ── Fetch all sources ───────────────────────────────────
    print("[run_daily] Source A: Truth Social posts...", file=sys.stderr)
    ts_data = run_script("fetch_posts.py", fetch_args) or {"meta": {}, "posts": []}

    print("[run_daily] Source B1: White House transcripts...", file=sys.stderr)
    wh_data = run_script("fetch_speeches.py", fetch_args, timeout=90) or {"meta": {}, "posts": []}

    print("[run_daily] Source B2: Speech news supplement (RSS)...", file=sys.stderr)
    wh_supp = run_script("fetch_news.py", fetch_args + ["--wh-supplement"]) or {"total_matched": 0, "posts": []}

    print("[run_daily] Source C: Financial news (CNBC, Reuters, Yahoo)...", file=sys.stderr)
    news_data = run_script("fetch_news.py", fetch_args) or {"total_matched": 0, "posts": []}

    print("[run_daily] Source D: Community research (Reddit/Fox calls)...", file=sys.stderr)
    l30d_data = run_script("fetch_last30days.py", fetch_args, timeout=150) or {"total_matched": 0, "posts": []}

    # Tag by source type
    for p in wh_supp.get("posts", []):  p.setdefault("source", "wh_supplement")
    for p in news_data.get("posts", []): p.setdefault("source", "news")
    for p in l30d_data.get("posts", []): p.setdefault("source", "last30days")

    new_posts = (
        ts_data.get("posts", [])
        + wh_data.get("posts", [])
        + wh_supp.get("posts", [])
        + news_data.get("posts", [])
        + l30d_data.get("posts", [])
    )

    # ── Merge with cache and save ───────────────────────────
    all_raw_posts = cached_posts + new_posts
    all_raw_posts = save_cache(all_raw_posts, window_days=days)

    # ── Aggregate company mentions ──────────────────────────
    company_mentions: dict = {}

    for post in all_raw_posts:
        src = post.get("source", "")
        if src in ("whitehouse_speech", "wh_supplement"):
            src_emoji = "🎤"
        elif src == "news":
            src_emoji = "📰"
        elif src == "last30days":
            src_emoji = "🔍"
        else:
            src_emoji = "📱"

        date_str = (post.get("created_at") or post.get("date") or "")[:10]
        url = post.get("url", "")
        content = post.get("content", "")

        for mention in post.get("mentions", []):
            ticker = mention.get("ticker") or mention.get("company", "?").upper()
            if not ticker:
                continue

            snippet = mention.get("context_snippet", "")[:200]
            # Normalize to lowercase — fetch_news / fetch_last30days used to
            # return uppercase "BUY"/"WARN"/"MENTION"; fetch_posts returns
            # lowercase. Accept both.
            signal  = mention.get("signal_type", "neutral").lower()
            if signal == "mention":   signal = "neutral"
            rel     = relevance_score(snippet, content)

            if ticker not in company_mentions:
                company_mentions[ticker] = {
                    "company": mention.get("company", ticker),
                    "ticker": ticker,
                    "posts": [],
                    "buy_count": 0,
                    "sell_count": 0,
                    "earliest_date": date_str,
                    "conflict_of_interest": ticker in COI_REGISTRY,
                    "holding_info": COI_REGISTRY.get(ticker),
                }
            entry = company_mentions[ticker]
            entry["posts"].append({
                "source":    src_emoji,
                "date":      date_str,
                "snippet":   snippet,
                "signal":    signal,
                "url":       url,
                "relevance": rel,
            })
            if signal == "buy":
                entry["buy_count"] += 1
            elif signal in ("sell", "warn"):
                entry["sell_count"] += 1
            if date_str and (not entry["earliest_date"] or date_str < entry["earliest_date"]):
                entry["earliest_date"] = date_str

    # Filter out very low-relevance entries (name-in-list only, no buy signals)
    company_mentions = {
        t: c for t, c in company_mentions.items()
        if c["buy_count"] > 0
        or c["sell_count"] > 0
        or max((p["relevance"] for p in c["posts"]), default=0) >= 40
    }

    # ── Live prices ─────────────────────────────────────────
    price_data = {}
    price_timestamp = None
    portfolio_tickers = ["DELL", "AAPL", "PLTR", "NVDA", "MSFT", "MU", "TMO", "INTC"]
    if include_prices:
        tickers_to_fetch = list(set(
            [t for t in company_mentions] + portfolio_tickers
        ))
        print(f"[run_daily] Fetching prices: {tickers_to_fetch}...", file=sys.stderr)
        try:
            sys.path.insert(0, str(SKILL_DIR))
            from fetch_prices import get_prices
            earliest = min(
                (c["earliest_date"] for c in company_mentions.values() if c["earliest_date"]),
                default=None
            )
            price_data = get_prices(tickers_to_fetch, since_date=earliest)
            price_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception as e:
            print(f"[run_daily] Price fetch failed: {e}", file=sys.stderr)

    # ── OGE filings ─────────────────────────────────────────
    print("[run_daily] Checking OGE filings...", file=sys.stderr)
    new_filings = check_new_oge_filings()

    return {
        "scan_time":       now.isoformat(),
        "days":            days,
        "warm_cache":      warm,
        "cache_age_h":     round(cache_age_h, 1),
        "ts_meta":         ts_data.get("meta", {}),
        "wh_meta":         wh_data.get("meta", {}),
        "wh_supp_count":   wh_supp.get("total_matched", 0),
        "news_count":      news_data.get("total_matched", 0),
        "l30d_count":      l30d_data.get("total_matched", 0),
        "company_mentions": company_mentions,
        "price_data":      price_data,
        "price_timestamp": price_timestamp,
        "new_oge_filings": new_filings,
    }


# ─────────────────────────────────────────────────────────────
#  Plain-text formatter
# ─────────────────────────────────────────────────────────────
def format_digest_text(digest):
    days     = digest["days"]
    ts_meta  = digest["ts_meta"]
    wh_meta  = digest["wh_meta"]
    companies = digest["company_mentions"]
    prices    = digest["price_data"]
    now_str   = digest["scan_time"][:16].replace("T", " ") + " UTC"
    warm      = digest.get("warm_cache", False)
    cache_age = digest.get("cache_age_h", 0)

    mode_str = (f"incremental update ({cache_age:.0f}h cache + 25h fresh)"
                if warm else f"full {days}-day fetch")

    lines = []
    lines.append("=" * 62)
    lines.append("  TRUMP MARKET ALERT — Daily Digest")
    lines.append(f"  {now_str}  |  Mode: {mode_str}")
    lines.append(f"  Truth Social: {ts_meta.get('total_scanned','?')} posts  |  "
                 f"WH Speeches: {wh_meta.get('total_scanned','?')}  |  "
                 f"News: {digest.get('news_count',0)}  |  "
                 f"Community: {digest.get('l30d_count',0)}")
    lines.append("=" * 62)

    buy_companies  = [(t,c) for t,c in companies.items() if c["buy_count"] > 0]
    warn_companies = [(t,c) for t,c in companies.items() if c["sell_count"] > 0]
    neutral        = [(t,c) for t,c in companies.items() if not c["buy_count"] and not c["sell_count"]]

    # BUY alerts
    if buy_companies:
        lines.append("\n  *** BUY ALERTS ***")
        for ticker, c in sorted(buy_companies, key=lambda x: -x[1]["buy_count"]):
            best = pick_best_post(c["posts"])
            lines.append(f"\n  [{ticker}] {c['company']}  {fmt_price(prices, ticker)}")
            if c.get("conflict_of_interest") and c.get("holding_info"):
                h = c["holding_info"]
                lines.append(f"  COI: Trump holds {h['range']} (bought {h['date']})")
            if best:
                lines.append(f"  {best['source']} {best['date']}: \"{best['snippet'][:140]}\"")
                if best["url"]:
                    lines.append(f"  {best['url']}")
    else:
        lines.append(f"\n  No BUY alerts in the last {days} days.")

    # Micro
    lines.append("\n" + "-" * 62)
    lines.append("  COMPANY MENTIONS")
    lines.append("-" * 62)
    all_sorted = sorted(companies.items(), key=lambda x: (-x[1]["buy_count"], -len(x[1]["posts"])))
    for ticker, c in all_sorted:
        sig = "BUY" if c["buy_count"] else ("SELL" if c["sell_count"] else "---")
        coi = " [COI]" if c["conflict_of_interest"] else ""
        lines.append(f"\n  {sig}  {c['company']} ({ticker}){coi}  {fmt_price(prices, ticker)}")
        lines.append(f"       Mentions: {len(c['posts'])}  Buy: {c['buy_count']}  Sell/Warn: {c['sell_count']}")
        best = pick_best_post(c["posts"])
        if best:
            lines.append(f"       {best['source']} {best['date']}: \"{best['snippet'][:120]}\"")
        news_posts = [p for p in c["posts"] if p["source"] == "📰"]
        if news_posts:
            lines.append(f"       Media: \"{news_posts[-1]['snippet'][:120]}\"")

    # Portfolio
    lines.append("\n" + "-" * 62)
    lines.append("  TRUMP PORTFOLIO  (prices: " + (digest.get("price_timestamp","?")) + ")")
    lines.append("-" * 62)
    new_filings = digest["new_oge_filings"]
    if new_filings:
        lines.append(f"  *** {len(new_filings)} NEW OGE FILING(S) ***")
        for f in new_filings:
            lines.append(f"  {f['approx_date']}: {f['url']}")
    else:
        lines.append("  No new OGE filings since 2026-04-20.")
    for ticker in ["DELL","AAPL","PLTR","NVDA","MSFT","MU","TMO","INTC"]:
        lines.append(f"  {ticker:<6} {fmt_price(prices, ticker)}")

    lines.append("\n" + "=" * 62)
    lines.append("  DISCLAIMER: Informational only. Not financial advice.")
    lines.append("  Verify via SEC.gov · OGE (efts.usoge.gov) · Reuters · AP")
    lines.append("=" * 62)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  HTML formatter  (light theme, analysis, media reactions)
# ─────────────────────────────────────────────────────────────
def format_digest_html(digest):
    days       = digest["days"]
    companies  = digest["company_mentions"]
    prices     = digest["price_data"]
    now_str    = digest["scan_time"][:16].replace("T", " ") + " UTC"
    warm       = digest.get("warm_cache", False)
    cache_age  = digest.get("cache_age_h", 0)
    price_ts   = digest.get("price_timestamp", "unknown")
    new_filings= digest["new_oge_filings"]
    ts_meta    = digest["ts_meta"]
    wh_meta    = digest["wh_meta"]

    mode_badge = (
        f"<span style='background:#d1ecf1;color:#0c5460;padding:2px 8px;border-radius:12px;font-size:11px'>"
        f"⚡ incremental ({cache_age:.0f}h cache + 25h fresh)</span>"
        if warm else
        f"<span style='background:#fff3cd;color:#856404;padding:2px 8px;border-radius:12px;font-size:11px'>"
        f"🔄 full {days}-day scan</span>"
    )

    buy_companies  = sorted([(t,c) for t,c in companies.items() if c["buy_count"] > 0],
                             key=lambda x: -x[1]["buy_count"])
    other_companies= sorted([(t,c) for t,c in companies.items() if not c["buy_count"]],
                             key=lambda x: -len(x[1]["posts"]))

    # ── BUY ALERT cards ──────────────────────────────────────
    buy_cards_html = ""
    if buy_companies:
        for ticker, c in buy_companies:
            p_info  = prices.get(ticker, {})
            price   = p_info.get("price")
            chg_t   = p_info.get("change_pct_today")
            chg_s   = p_info.get("change_pct_since")
            best    = pick_best_post(c["posts"])
            analysis= generate_analysis(ticker, c, prices)

            price_html = ""
            if price:
                price_html = f"<span style='font-size:22px;font-weight:bold;color:#212529'>${price:,.2f}</span> "
                if chg_t is not None:
                    col = "#198754" if chg_t >= 0 else "#dc3545"
                    price_html += f"<span style='color:{col};font-weight:bold'>{'▲' if chg_t>=0 else '▼'}{abs(chg_t):.1f}% today</span> "
                if chg_s is not None:
                    col = "#198754" if chg_s >= 0 else "#dc3545"
                    price_html += f"<span style='color:{col}'>{'▲' if chg_s>=0 else '▼'}{abs(chg_s):.1f}% since mention</span>"

            quote_html = ""
            if best and best.get("snippet") and best["relevance"] >= 40:
                quote_html = f"""
                <blockquote style='border-left:4px solid #dc3545;margin:12px 0;
                    padding:10px 16px;background:#fff5f5;border-radius:0 6px 6px 0;
                    font-style:italic;color:#495057;font-size:14px'>
                  "{best['snippet']}"
                  <br><span style='font-size:11px;color:#6c757d;font-style:normal'>
                    {best['source']} {best['date']}
                    {f'&nbsp;·&nbsp;<a href="{best["url"]}" style="color:#0d6efd">source</a>' if best.get("url") else ""}
                  </span>
                </blockquote>"""

            coi_html = ""
            if c.get("conflict_of_interest") and COI_REGISTRY.get(ticker):
                h = COI_REGISTRY[ticker]
                coi_html = f"""
                <div style='background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
                    padding:10px 14px;margin:10px 0;font-size:13px;color:#664d03'>
                  ⚠️ <b>Conflict of Interest:</b> Trump personally holds <b>{h['range']}</b>
                  in {ticker} (purchased {h['date']}).
                  Pattern: he bought before praising — every single time.
                </div>"""

            buy_cards_html += f"""
            <div style='border:2px solid #dc3545;border-radius:10px;margin-bottom:20px;overflow:hidden'>
              <div style='background:#dc3545;padding:14px 20px;display:flex;align-items:center;justify-content:space-between'>
                <div>
                  <span style='color:#fff;font-size:18px;font-weight:bold'>
                    🚨 BUY ALERT — {c['company']} ({ticker})
                  </span>
                  <span style='color:#ffcdd2;font-size:13px;margin-left:12px'>
                    {c['buy_count']} buy signal{'s' if c['buy_count']>1 else ''} · {len(c['posts'])} total mentions
                  </span>
                </div>
              </div>
              <div style='background:#fff;padding:16px 20px'>
                <div style='margin-bottom:12px'>{price_html}</div>
                {quote_html}
                {coi_html}
                <div style='background:#f8f9fa;border-radius:6px;padding:12px 16px;
                    font-size:13px;color:#212529;line-height:1.6;margin-top:10px'>
                  <b>Analysis:</b> {analysis}
                </div>
              </div>
            </div>"""
    else:
        buy_cards_html = """
        <div style='background:#d1e7dd;border:1px solid #a3cfbb;border-radius:8px;
            padding:14px 20px;color:#0a3622;font-size:14px'>
          ✅ <b>No BUY alerts</b> in the last {days} days.
          Monitoring {n} companies with neutral mentions below.
        </div>""".format(days=days, n=len(other_companies))

    # ── Company mention cards (neutral/warn) ──────────────────
    mention_rows = ""
    for ticker, c in other_companies:
        p_info = prices.get(ticker, {})
        price  = p_info.get("price")
        chg_t  = p_info.get("change_pct_today")
        chg_s  = p_info.get("change_pct_since")
        best   = pick_best_post(c["posts"])
        news_p = [p for p in c["posts"] if p["source"] == "📰"]

        sig_color = "#dc3545" if c["sell_count"] else "#6c757d"
        sig_label = "📉 WARN" if c["sell_count"] else "📣 MENTION"
        coi_badge = (
            " <span style='background:#fff3cd;color:#664d03;padding:1px 6px;"
            "border-radius:10px;font-size:11px'>⚠️ COI</span>"
            if c["conflict_of_interest"] else ""
        )

        price_str = ""
        if price:
            price_str = f"${price:,.2f}"
            if chg_t is not None:
                col = "#198754" if chg_t>=0 else "#dc3545"
                price_str += f" <span style='color:{col}'>{'▲' if chg_t>=0 else '▼'}{abs(chg_t):.1f}%</span>"

        best_quote = ""
        if best and best.get("snippet") and best["relevance"] >= 40:
            best_quote = (
                f"<div style='font-style:italic;color:#495057;font-size:13px;"
                f"border-left:3px solid #dee2e6;padding-left:10px;margin:6px 0'>"
                f"\"{best['snippet'][:160]}\"</div>"
            )

        media_quote = ""
        if news_p:
            snip = news_p[-1].get("snippet","")[:160]
            if snip:
                media_quote = (
                    f"<div style='font-size:12px;color:#6c757d;margin-top:4px'>"
                    f"📰 <i>{snip}</i></div>"
                )

        analysis_short = generate_analysis(ticker, c, prices)

        mention_rows += f"""
        <tr style='border-bottom:1px solid #dee2e6'>
          <td style='padding:14px 16px;vertical-align:top;width:160px'>
            <div style='font-weight:bold;font-size:15px;color:#212529'>{c['company']}</div>
            <div style='color:#6c757d;font-size:12px'>{ticker}{coi_badge}</div>
            <div style='font-size:13px;margin-top:4px'>{price_str}</div>
            <div style='margin-top:6px'>
              <span style='background:#f1f3f5;color:{sig_color};padding:2px 8px;
                border-radius:10px;font-size:11px;font-weight:bold'>{sig_label}</span>
            </div>
          </td>
          <td style='padding:14px 16px;vertical-align:top;font-size:13px;color:#212529'>
            {best_quote}
            {media_quote}
            <div style='color:#495057;font-size:12px;margin-top:8px;line-height:1.5'>
              {analysis_short}
            </div>
          </td>
        </tr>"""

    # ── Portfolio table ───────────────────────────────────────
    portfolio_rows = ""
    holdings = [
        ("DELL", "$1M–$5M",    "Feb 10, 2026", "🚨 Praised 2×"),
        ("AAPL", "$250K–$500K","Mar 11, 2026", "🚨 Praised 1×"),
        ("PLTR", "$247K–$630K","~Apr 2026",    "🚨 Praised 2×"),
        ("NVDA", "$1M–$5M",    "Feb 2026",     "👀 Watch — no praise yet"),
        ("MSFT", "$1M–$5M",    "Feb 2026",     "👀 Watch"),
        ("MU",   "$50K–$100K", "Mar 25, 2026", "🚨 Praised 3×"),
        ("TMO",  "$15K–$50K",  "Mar 11, 2026", "🚨 Praised 1×"),
        ("INTC", "10% stake",  "ongoing",      "📣 Mentioned 1×"),
    ]
    for ticker, rng, purchased, status in holdings:
        p_info = prices.get(ticker, {})
        price  = p_info.get("price")
        chg_t  = p_info.get("change_pct_today")
        chg_s  = p_info.get("change_pct_since")
        p_str  = f"${price:,.2f}" if price else "—"
        chg_t_str, chg_s_str = "", ""
        if chg_t is not None:
            col = "#198754" if chg_t>=0 else "#dc3545"
            chg_t_str = f"<span style='color:{col}'>{'▲' if chg_t>=0 else '▼'}{abs(chg_t):.1f}%</span>"
        if chg_s is not None:
            col = "#198754" if chg_s>=0 else "#dc3545"
            chg_s_str = f"<span style='color:{col}'>{'▲' if chg_s>=0 else '▼'}{abs(chg_s):.1f}% since mention</span>"
        highlight = "background:#fff3cd" if "Watch" in status else ""
        portfolio_rows += f"""
        <tr style='border-bottom:1px solid #dee2e6;{highlight}'>
          <td style='padding:8px 12px;font-weight:bold;color:#212529'>{ticker}</td>
          <td style='padding:8px 12px;color:#495057;font-size:13px'>{rng}</td>
          <td style='padding:8px 12px;color:#6c757d;font-size:12px'>{purchased}</td>
          <td style='padding:8px 12px;font-size:14px'>{p_str} {chg_t_str}</td>
          <td style='padding:8px 12px;font-size:12px'>{chg_s_str}</td>
          <td style='padding:8px 12px;font-size:12px;color:#495057'>{status}</td>
        </tr>"""

    oge_html = ""
    if new_filings:
        oge_html = f"<div style='background:#d1e7dd;border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:13px;color:#0a3622'>🆕 <b>{len(new_filings)} new OGE filing(s) detected:</b><br>" + "<br>".join(f"<a href='{f['url']}'>{f['approx_date']}</a>" for f in new_filings) + "</div>"
    else:
        oge_html = "<p style='color:#6c757d;font-size:13px'>No new OGE filings since 2026-04-20. Next filing expected ~May 2026.</p>"

    # ── NVDA watch box ────────────────────────────────────────
    nvda = prices.get("NVDA", {})
    nvda_price = nvda.get("price", "—")
    nvda_chg   = nvda.get("change_pct_today")
    nvda_chg_str = ""
    if nvda_chg is not None:
        col = "#198754" if nvda_chg >= 0 else "#dc3545"
        nvda_chg_str = f"<span style='color:{col}'>{'▲' if nvda_chg>=0 else '▼'}{abs(nvda_chg):.1f}% today</span>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trump Market Alert</title>
</head>
<body style="margin:0;padding:20px;background:#f0f4f8;font-family:Arial,Helvetica,sans-serif;color:#212529">
<table width="640" align="center" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:10px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:100%">

  <!-- HEADER -->
  <tr>
    <td style="background:#1a1a2e;padding:20px 24px">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div>
          <div style="color:#fff;font-size:20px;font-weight:bold">📊 Trump Market Alert</div>
          <div style="color:#adb5bd;font-size:12px;margin-top:4px">
            {now_str} &nbsp;·&nbsp; Last {days} days &nbsp;·&nbsp; {mode_badge}
          </div>
        </div>
      </div>
      <div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap">
        <span style="color:#adb5bd;font-size:12px">📱 {ts_meta.get('total_scanned','?')} Truth Social posts</span>
        <span style="color:#adb5bd;font-size:12px">🎤 {wh_meta.get('total_scanned','?')} WH transcripts + {digest.get('wh_supp_count',0)} news reports</span>
        <span style="color:#adb5bd;font-size:12px">📰 {digest.get('news_count',0)} financial news</span>
        <span style="color:#adb5bd;font-size:12px">🔍 {digest.get('l30d_count',0)} community research</span>
      </div>
    </td>
  </tr>

  <!-- BUY ALERTS -->
  <tr><td style="padding:20px 24px 0">
    <h2 style="margin:0 0 14px;font-size:16px;color:#212529;border-bottom:2px solid #dc3545;padding-bottom:6px">
      🚨 BUY ALERTS
    </h2>
    {buy_cards_html}
  </td></tr>

  <!-- NVDA WATCHLIST BOX -->
  <tr><td style="padding:0 24px 4px">
    <div style="background:#e8f4fd;border:1px solid #b6d4fe;border-radius:8px;padding:12px 16px;font-size:13px;color:#084298">
      👀 <b>NVDA — Next Watch:</b> Trump holds $1M–$5M in Nvidia but has made
      <b>zero</b> public statements about it. Every prior holding has been praised within weeks of purchase.
      Current price: <b>${nvda_price:,.2f} {nvda_chg_str}</b>. Any NVDA mention = immediate signal.
    </div>
  </td></tr>

  <!-- COMPANY MENTIONS -->
  <tr><td style="padding:16px 24px 0">
    <h2 style="margin:0 0 10px;font-size:16px;color:#212529;border-bottom:2px solid #dee2e6;padding-bottom:6px">
      📣 Company Mentions — Analysis
    </h2>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #dee2e6;border-radius:8px;overflow:hidden;border-collapse:collapse">
      {mention_rows if mention_rows else
       '<tr><td style="padding:16px;color:#6c757d;font-size:13px">No neutral mentions in this window.</td></tr>'}
    </table>
  </td></tr>

  <!-- PORTFOLIO -->
  <tr><td style="padding:20px 24px 0">
    <h2 style="margin:0 0 10px;font-size:16px;color:#212529;border-bottom:2px solid #dee2e6;padding-bottom:6px">
      💼 Trump Portfolio — Live Prices
      <span style="font-size:11px;color:#6c757d;font-weight:normal;margin-left:8px">{price_ts}</span>
    </h2>
    {oge_html}
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #dee2e6;border-radius:8px;overflow:hidden;border-collapse:collapse;font-size:13px">
      <tr style="background:#f8f9fa">
        <th style="padding:8px 12px;text-align:left;color:#495057;font-weight:600">Ticker</th>
        <th style="padding:8px 12px;text-align:left;color:#495057;font-weight:600">Holding</th>
        <th style="padding:8px 12px;text-align:left;color:#495057;font-weight:600">Purchased</th>
        <th style="padding:8px 12px;text-align:left;color:#495057;font-weight:600">Price</th>
        <th style="padding:8px 12px;text-align:left;color:#495057;font-weight:600">vs Mention</th>
        <th style="padding:8px 12px;text-align:left;color:#495057;font-weight:600">Status</th>
      </tr>
      {portfolio_rows}
    </table>
  </td></tr>

  <!-- DISCLAIMER -->
  <tr><td style="padding:20px 24px">
    <div style="background:#f8f9fa;border-radius:8px;padding:14px 16px;
        font-size:11px;color:#6c757d;line-height:1.6;border-top:3px solid #dee2e6">
      <b>⚖️ Legal disclaimer.</b> This digest is for <b>informational purposes only</b> and does not
      constitute financial advice, a recommendation to buy or sell any security, or investment guidance
      of any kind. Past price movements following Trump statements are historical observations only and
      do not predict future results. OGE filings lag actual trades by weeks — holdings data may be
      stale or incomplete. Not all Trump statements are captured; video-only events and private calls
      may be missed. <b>You assume full responsibility</b> for any decision made based on this output.
      Always verify independently: <a href="https://www.sec.gov" style="color:#0d6efd">SEC.gov</a> ·
      <a href="https://efts.usoge.gov" style="color:#0d6efd">OGE filings</a> ·
      Reuters · AP News.
    </div>
  </td></tr>

</table>
</body></html>"""

    return html


# ─────────────────────────────────────────────────────────────
#  Email sender
# ─────────────────────────────────────────────────────────────
def send_email(subject, text_body, html_body, config):
    host = config.get("SMTP_HOST", "smtp.gmail.com")
    port = int(config.get("SMTP_PORT", 587))
    user = config.get("SMTP_USER", "")
    pw   = config.get("SMTP_PASS", "")
    to   = config.get("ALERT_TO", user)

    if not user or not pw:
        print("[run_daily] Email not configured — set SMTP_USER and SMTP_PASS", file=sys.stderr)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, pw)
            server.sendmail(user, to, msg.as_string())
        print(f"[run_daily] Email sent to {to}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[run_daily] Email send failed: {e}", file=sys.stderr)
        return False


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Trump Market Alert — daily digest")
    parser.add_argument("--days",      type=int,  default=30)
    parser.add_argument("--email",     action="store_true")
    parser.add_argument("--save",      type=str,  default=None)
    parser.add_argument("--no-prices", action="store_true")
    parser.add_argument("--json",      action="store_true")
    parser.add_argument("--cold",      action="store_true",
                        help="Force full refetch — ignore cache")
    args = parser.parse_args()

    digest = build_digest(
        days=args.days,
        include_prices=not args.no_prices,
        force_cold=args.cold,
    )

    if args.json:
        print(json.dumps(digest, indent=2, default=str))
        return

    text_body = format_digest_text(digest)
    html_body = format_digest_html(digest)

    print(text_body)

    # Save to file
    save_path = args.save
    if not save_path:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        date_str  = datetime.now().strftime("%Y-%m-%d")
        save_path = str(SAVE_DIR / f"trump-alert-{date_str}.txt")
    Path(save_path).write_text(text_body, encoding="utf-8")
    print(f"\n[run_daily] Digest saved → {save_path}", file=sys.stderr)

    # Email
    if args.email:
        config    = load_config()
        companies = digest["company_mentions"]
        buy_count = sum(1 for c in companies.values() if c["buy_count"] > 0)
        warm_tag  = "⚡" if digest.get("warm_cache") else "🔄"
        if buy_count:
            tickers = [t for t, c in companies.items() if c["buy_count"] > 0]
            subject = f"🚨 TRUMP BUY ALERT: {', '.join(tickers)} — {datetime.now().strftime('%b %d')}"
        else:
            subject = f"{warm_tag} Trump Alert — {datetime.now().strftime('%b %d, %Y')} · No new BUY signals"
        send_email(subject, text_body, html_body, config)


if __name__ == "__main__":
    main()
