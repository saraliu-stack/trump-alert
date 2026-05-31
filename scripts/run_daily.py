#!/usr/bin/env python3
"""
run_daily.py — Daily Trump Market Alert digest.

Orchestrates all sources, produces a rich digest with:
  - BUY / WARNING alerts
  - Micro section: per-company mentions with live stock prices
  - Macro section: inflation / war / tariff / economy mentions
  - Trump portfolio changes (latest OGE filings)

Optionally sends the digest via email or saves to file.

Usage:
    python run_daily.py [--days=30] [--email] [--save=PATH] [--no-prices]

Config file (optional): ~/.config/trump-alert/.env
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=you@gmail.com
    SMTP_PASS=your-app-password
    ALERT_TO=recipient@example.com
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

# Force UTF-8 output on Windows so emoji don't crash the console
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKILL_DIR = Path(__file__).parent
CONFIG_FILE = Path.home() / ".config" / "trump-alert" / ".env"


# OGE filing URLs (whitehouse.gov pattern)
OGE_KNOWN_FILINGS = [
    ("2026-04-20", "https://www.whitehouse.gov/wp-content/uploads/2026/04/President-Donald-J.-Trump-Periodic-Transaction-Report-4.20.26.pdf"),
    ("2026-02-26", "https://www.whitehouse.gov/wp-content/uploads/2026/03/President-Donald-J.-Trump-Periodic-Transaction-Report-2.26.26-1.pdf"),
]


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config():
    config = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    # Also check environment variables
    for key in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "ALERT_TO"]:
        if key in os.environ:
            config[key] = os.environ[key]
    return config


# ---------------------------------------------------------------------------
# Script runner
# ---------------------------------------------------------------------------
def run_script(script_name, extra_args=None):
    cmd = [sys.executable, str(SKILL_DIR / script_name)]
    if extra_args:
        cmd.extend(extra_args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        else:
            print(f"[run_daily] {script_name} stderr: {result.stderr[:500]}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[run_daily] {script_name} failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# OGE filing checker
# ---------------------------------------------------------------------------
def check_new_oge_filings():
    """Scan whitehouse.gov for OGE filings newer than our known list."""
    new_filings = []
    latest_known_date = max(d for d, _ in OGE_KNOWN_FILINGS) if OGE_KNOWN_FILINGS else "2000-01-01"

    try:
        url = "https://www.whitehouse.gov/disclosures/"
        req = urllib.request.Request(url, headers={"User-Agent": "trump-alert/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Look for PDF links matching the OGE pattern
        pdf_links = re.findall(
            r'href="(https://www\.whitehouse\.gov/wp-content/uploads/\d{4}/\d{2}/[^"]*278[^"]*\.pdf)"',
            html, re.IGNORECASE
        )
        for link in pdf_links:
            date_m = re.search(r'/(\d{4})/(\d{2})/', link)
            if date_m:
                year, month = date_m.group(1), date_m.group(2)
                approx_date = f"{year}-{month}-01"
                if approx_date > latest_known_date and link not in [u for _, u in OGE_KNOWN_FILINGS]:
                    new_filings.append({"url": link, "approx_date": approx_date})
    except Exception as e:
        print(f"[run_daily] OGE check failed: {e}", file=sys.stderr)

    return new_filings


# ---------------------------------------------------------------------------
# Price formatter
# ---------------------------------------------------------------------------
def fmt_price(price_data, ticker):
    if not price_data or ticker not in price_data:
        return "N/A"
    d = price_data[ticker]
    if "error" in d:
        return f"(price unavailable)"
    price = d.get("price")
    today = d.get("change_pct_today")
    since = d.get("change_pct_since")
    parts = [f"${price:.2f}" if price else ""]
    if today is not None:
        arrow = "▲" if today >= 0 else "▼"
        parts.append(f"{arrow}{abs(today):.1f}% today")
    if since is not None:
        arrow = "▲" if since >= 0 else "▼"
        parts.append(f"{arrow}{abs(since):.1f}% since mention")
    return "  ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------
def build_digest(days, include_prices=True):
    now = datetime.now(timezone.utc)
    scan_args = [f"--days={days}"]

    print("[run_daily] Fetching Truth Social posts...", file=sys.stderr)
    ts_data = run_script("fetch_posts.py", scan_args) or {"meta": {}, "posts": []}

    print("[run_daily] Fetching White House speeches...", file=sys.stderr)
    wh_data = run_script("fetch_speeches.py", scan_args) or {"meta": {}, "posts": []}

    print("[run_daily] Fetching financial news headlines (CNBC, Yahoo Finance, Reuters)...", file=sys.stderr)
    news_data = run_script("fetch_news.py", scan_args) or {"total_matched": 0, "posts": []}

    # Tag news items so they render with the 📰 emoji in digests
    for post in news_data.get("posts", []):
        post.setdefault("source", "news")

    all_posts = ts_data.get("posts", []) + wh_data.get("posts", []) + news_data.get("posts", [])

    # ---- Collect all company mentions ----
    company_mentions: dict = {}  # ticker -> {company, ticker, posts[], buy_count, sell_count, earliest_date}

    for post in all_posts:
        content = post.get("content", "")
        date_str = post.get("created_at") or post.get("date") or ""
        src = post.get("source", "")
        if src == "whitehouse_speech":
            source_type = "🎤"
        elif src == "news":
            source_type = "📰"
        else:
            source_type = "📱"
        url = post.get("url", "")

        # Company (micro) mentions
        for mention in post.get("mentions", []):
            ticker = mention.get("ticker") or mention.get("company", "?").upper()
            if not ticker:
                continue
            if ticker not in company_mentions:
                company_mentions[ticker] = {
                    "company": mention.get("company", ticker),
                    "ticker": ticker,
                    "posts": [],
                    "buy_count": 0,
                    "sell_count": 0,
                    "earliest_date": date_str[:10] if date_str else None,
                    "conflict_of_interest": mention.get("conflict_of_interest", False),
                    "holding_info": mention.get("holding_info"),
                }
            entry = company_mentions[ticker]
            entry["posts"].append({
                "source": source_type,
                "date": date_str[:10],
                "snippet": mention.get("context_snippet", "")[:200],
                "signal": mention.get("signal_type", "neutral"),
                "url": url,
            })
            if mention.get("signal_type") == "buy":
                entry["buy_count"] += 1
            elif mention.get("signal_type") == "sell":
                entry["sell_count"] += 1
            # Track earliest mention date for price-since calculation
            if date_str and (not entry["earliest_date"] or date_str[:10] < entry["earliest_date"]):
                entry["earliest_date"] = date_str[:10]

    # ---- Fetch live prices ----
    price_data = {}
    price_timestamp = None
    # Always fetch portfolio holdings prices, plus any mentioned tickers
    portfolio_tickers = ["DELL", "AAPL", "PLTR", "NVDA", "MSFT", "MU", "TMO", "INTC"]
    if include_prices:
        tickers_to_fetch = list(set(
            [t for t in company_mentions if not t.startswith("^")] + portfolio_tickers
        ))
        print(f"[run_daily] Fetching prices for {tickers_to_fetch}...", file=sys.stderr)
        try:
            from fetch_prices import get_prices
            earliest = min(
                (v["earliest_date"] for v in company_mentions.values() if v["earliest_date"]),
                default=None
            )
            price_data = get_prices(tickers_to_fetch, since_date=earliest)
            price_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception as e:
            print(f"[run_daily] Price fetch failed: {e}", file=sys.stderr)

    # ---- Check OGE filings ----
    print("[run_daily] Checking for new OGE filings...", file=sys.stderr)
    new_filings = check_new_oge_filings()

    return {
        "scan_time": now.isoformat(),
        "days": days,
        "ts_meta": ts_data.get("meta", {}),
        "wh_meta": wh_data.get("meta", {}),
        "news_count": news_data.get("total_matched", 0),
        "company_mentions": company_mentions,
        "price_data": price_data,
        "price_timestamp": price_timestamp,
        "new_oge_filings": new_filings,
    }


# ---------------------------------------------------------------------------
# Plain-text formatter
# ---------------------------------------------------------------------------
def format_digest_text(digest):
    now_str = digest["scan_time"][:16].replace("T", " ") + " UTC"
    days = digest["days"]
    ts_meta = digest["ts_meta"]
    wh_meta = digest["wh_meta"]
    news_count = digest.get("news_count", 0)
    companies = digest["company_mentions"]
    prices = digest["price_data"]
    price_ts = digest.get("price_timestamp", "unknown")
    new_filings = digest["new_oge_filings"]

    lines = []
    lines.append("=" * 60)
    lines.append("  📊 TRUMP MARKET ALERT — Daily Digest")
    lines.append(f"  Scan time: {now_str}")
    lines.append(f"  Window: Last {days} days")
    lines.append(f"  📱 Truth Social: {ts_meta.get('total_scanned', '?')} posts scanned")
    lines.append(f"  🎤 WH Speeches: {wh_meta.get('total_scanned', '?')} items scanned")
    lines.append(f"  📰 News headlines: {news_count} Trump+company stories matched")
    lines.append("=" * 60)

    # ---- BUY ALERTS ----
    buy_companies = [(t, c) for t, c in companies.items() if c["buy_count"] > 0]
    if buy_companies:
        lines.append("")
        lines.append("┌" + "─" * 58 + "┐")
        lines.append("│  🚨🚨  BUY ALERTS  🚨🚨" + " " * 36 + "│")
        lines.append("└" + "─" * 58 + "┘")
        for ticker, c in sorted(buy_companies, key=lambda x: -x[1]["buy_count"]):
            p = prices.get(ticker, {})
            lines.append(f"\n  ╔══ {c['company']} ({ticker}) ══╗")
            lines.append(f"  Buy signals: {c['buy_count']}  |  Price: {fmt_price(prices, ticker)}")
            if c["conflict_of_interest"] and c.get("holding_info"):
                h = c["holding_info"]
                lines.append(f"  ⚠️  CONFLICT OF INTEREST — Trump holds {ticker}")
                lines.append(f"     Disclosed: {h.get('range','?')} (purchased {h.get('date','?')})")
            for post in c["posts"][:3]:
                if post["signal"] == "buy":
                    lines.append(f"  {post['source']} {post['date']}  \"{post['snippet'][:120]}\"")
                    lines.append(f"     🔗 {post['url']}")
    else:
        lines.append("\n  ✅ No BUY alerts in the last {days} days.")

    # ---- MICRO: All company mentions ----
    lines.append("")
    lines.append("─" * 60)
    lines.append("  📈 MICRO: Company Mentions")
    lines.append("─" * 60)
    if companies:
        for ticker, c in sorted(companies.items(), key=lambda x: -len(x[1]["posts"])):
            signal_emoji = "🚨" if c["buy_count"] > 0 else ("📉" if c["sell_count"] > 0 else "📣")
            price_str = fmt_price(prices, ticker) if ticker in prices else ""
            coi = " ⚠️COI" if c["conflict_of_interest"] else ""
            lines.append(f"\n  {signal_emoji} {c['company']} ({ticker}){coi}")
            if price_str:
                lines.append(f"     💹 {price_str}")
            lines.append(f"     Mentions: {len(c['posts'])}  |  Buy: {c['buy_count']}  |  Sell: {c['sell_count']}")
            # Latest quote
            if c["posts"]:
                latest = max(c["posts"], key=lambda p: p["date"])
                lines.append(f"     Latest {latest['source']} {latest['date']}: \"{latest['snippet'][:120]}\"")
    else:
        lines.append("  No company mentions found.")

    # ---- Portfolio changes ----
    lines.append("")
    lines.append("─" * 60)
    lines.append("  💼 TRUMP PORTFOLIO / OGE FILINGS")
    lines.append(f"  Prices as of: {price_ts}")
    lines.append("─" * 60)
    if new_filings:
        lines.append(f"  🆕 {len(new_filings)} NEW OGE FILING(S) DETECTED:")
        for f in new_filings:
            lines.append(f"     📄 {f['approx_date']}: {f['url']}")
    else:
        lines.append("  No new OGE filings detected since last known report (2026-04-20).")
        lines.append("  Last known filings:")
        for date, url in OGE_KNOWN_FILINGS:
            lines.append(f"     📄 {date}: {url}")
    lines.append("")
    lines.append("  Known holdings (as of May 2026 OGE disclosures):")
    holdings_table = [
        ("DELL", "$1M–$5M",   "Feb 10"),
        ("AAPL", "$250K–$500K","Mar 11"),
        ("PLTR", "$247K–$630K","~Apr"),
        ("NVDA", "$1M–$5M",   "Feb"),
        ("MSFT", "$1M–$5M",   "Feb"),
        ("INTC", "10% stake", "ongoing"),
    ]
    for ticker, amount, date in holdings_table:
        p = prices.get(ticker, {})
        price_str = fmt_price(prices, ticker) if ticker in prices else ""
        lines.append(f"     {ticker:<6} {amount:<18} (bought {date})  {price_str}")

    lines.append("")
    lines.append("─" * 60)
    lines.append("  ⚖️  DISCLAIMER")
    lines.append("─" * 60)
    lines.append("  This alert is for informational purposes only.")
    lines.append("  It does not constitute financial advice.")
    lines.append("  Trump's endorsements have historically been preceded")
    lines.append("  by personal stock purchases — always verify via SEC.gov,")
    lines.append("  OGE filings, and Reuters/AP before acting.")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML formatter (for email)
# ---------------------------------------------------------------------------
def format_digest_html(digest):
    text = format_digest_text(digest)
    # Minimal HTML wrapping — preserve formatting with pre tag
    companies = digest["company_mentions"]
    prices = digest["price_data"]

    buy_rows = ""
    for ticker, c in companies.items():
        if c["buy_count"] > 0:
            price_str = fmt_price(prices, ticker)
            coi = "<span style='color:orange'>⚠️ COI</span>" if c["conflict_of_interest"] else ""
            buy_rows += f"<tr style='background:#fff3cd'><td><b>🚨 {c['company']} ({ticker})</b></td><td>{price_str}</td><td>{c['buy_count']} buy signal(s)</td><td>{coi}</td></tr>"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body {{ font-family: monospace; background: #0d0d0d; color: #e0e0e0; padding: 20px; }}
  .header {{ background: #1a1a2e; border: 2px solid #e63946; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
  .buy-alert {{ background: #2d1b00; border: 3px solid #ff4444; padding: 15px; border-radius: 8px; margin: 10px 0; }}
  .buy-alert h2 {{ color: #ff4444; margin: 0; }}
  .section {{ background: #1a1a2e; border: 1px solid #333; padding: 15px; border-radius: 6px; margin: 10px 0; }}
  .section h3 {{ color: #4fc3f7; margin-top: 0; }}
  .coi {{ color: #ff9800; font-weight: bold; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ padding: 6px 10px; border: 1px solid #333; text-align: left; }}
  th {{ background: #1a1a2e; color: #4fc3f7; }}
  pre {{ white-space: pre-wrap; font-size: 12px; }}
  a {{ color: #4fc3f7; }}
  .disclaimer {{ color: #888; font-size: 11px; border-top: 1px solid #333; padding-top: 10px; }}
</style>
</head>
<body>
<div class="header">
  <h1 style="color:#ff4444;margin:0">📊 TRUMP MARKET ALERT</h1>
  <p style="margin:4px 0;color:#aaa">Daily Digest · {digest['scan_time'][:16].replace('T',' ')} UTC · Last {digest['days']} days</p>
  <p style="margin:2px 0;color:#888;font-size:11px">📱 {digest['ts_meta'].get('total_scanned','?')} Truth Social &nbsp;·&nbsp; 🎤 {digest['wh_meta'].get('total_scanned','?')} WH Speeches &nbsp;·&nbsp; 📰 {digest.get('news_count',0)} News stories</p>
</div>

{"<div class='buy-alert'><h2>🚨🚨 BUY ALERTS 🚨🚨</h2><table><tr><th>Company</th><th>Price</th><th>Signals</th><th>COI</th></tr>" + buy_rows + "</table></div>" if buy_rows else "<div class='section'><p>✅ No BUY alerts in this window.</p></div>"}

<div class="section">
<h3>📈 MICRO — Company Mentions</h3>
<pre>{_micro_text(digest)}</pre>
</div>

<div class="section">
<h3>💼 Trump Portfolio / OGE Filings</h3>
<p style="color:#888;font-size:11px">Prices as of: {digest.get('price_timestamp','unknown')}</p>
<pre>{_portfolio_text(digest)}</pre>
</div>

<p class="disclaimer">
For informational purposes only. Not financial advice. Always verify via
<a href="https://www.sec.gov">SEC.gov</a>,
<a href="https://www.oge.gov">OGE filings</a>,
Reuters, and AP News before acting on any information in this alert.
</p>
</body></html>"""
    return html


def _micro_text(digest):
    lines = []
    companies = digest["company_mentions"]
    prices = digest["price_data"]
    for ticker, c in sorted(companies.items(), key=lambda x: -len(x[1]["posts"])):
        signal = "🚨 BUY" if c["buy_count"] > 0 else ("📉 SELL" if c["sell_count"] > 0 else "📣")
        coi = " ⚠️COI" if c["conflict_of_interest"] else ""
        p_str = fmt_price(prices, ticker)
        lines.append(f"{signal}  {c['company']} ({ticker}){coi}  {p_str}")
        lines.append(f"       Mentions: {len(c['posts'])}  BUY: {c['buy_count']}")
        for post in c["posts"][:2]:
            lines.append(f"       {post['source']} {post['date']}: \"{post['snippet'][:100]}\"")
    return "\n".join(lines) if lines else "No company mentions."


def _portfolio_text(digest):
    lines = []
    prices = digest["price_data"]
    new_filings = digest["new_oge_filings"]
    price_ts = digest.get("price_timestamp", "unknown")
    lines.append(f"Prices as of: {price_ts}")
    lines.append("")
    if new_filings:
        lines.append(f"🆕 {len(new_filings)} NEW OGE FILING(S):")
        for f in new_filings:
            lines.append(f"   {f['approx_date']}: {f['url']}")
    else:
        lines.append("No new OGE filings since 2026-04-20.")
    lines.append("\nKnown holdings with live prices:")
    for ticker in ["DELL", "AAPL", "PLTR", "NVDA", "MSFT", "MU", "TMO", "INTC"]:
        p_str = fmt_price(prices, ticker)
        lines.append(f"  {ticker:<6} {p_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email sender
# ---------------------------------------------------------------------------
def send_email(subject, text_body, html_body, config):
    smtp_host = config.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(config.get("SMTP_PORT", 587))
    smtp_user = config.get("SMTP_USER", "")
    smtp_pass = config.get("SMTP_PASS", "")
    alert_to  = config.get("ALERT_TO", smtp_user)

    if not smtp_user or not smtp_pass:
        print("[run_daily] Email not configured — skipping send. Set SMTP_USER and SMTP_PASS in ~/.config/trump-alert/.env", file=sys.stderr)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = alert_to
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, alert_to, msg.as_string())
        print(f"[run_daily] Email sent to {alert_to}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[run_daily] Email send failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Trump Market Alert — daily digest runner")
    parser.add_argument("--days",       type=int,  default=30,    help="Lookback window in days (default 30)")
    parser.add_argument("--email",      action="store_true",      help="Send digest via email")
    parser.add_argument("--save",       type=str,  default=None,  help="Save digest to this file path")
    parser.add_argument("--no-prices",  action="store_true",      help="Skip live price fetching")
    parser.add_argument("--json",       action="store_true",      help="Output raw JSON digest instead of text")
    args = parser.parse_args()

    digest = build_digest(days=args.days, include_prices=not args.no_prices)

    if args.json:
        print(json.dumps(digest, indent=2, default=str))
        return

    text_body = format_digest_text(digest)
    html_body = format_digest_html(digest)

    # Print to stdout
    print(text_body)

    # Save to file
    save_path = args.save
    if not save_path:
        default_dir = Path.home() / "Documents" / "TrumpAlerts"
        default_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        save_path = str(default_dir / f"trump-alert-{date_str}.txt")
    Path(save_path).write_text(text_body, encoding="utf-8")
    print(f"\n[run_daily] Digest saved to {save_path}", file=sys.stderr)

    # Send email
    if args.email:
        config = load_config()
        companies = digest["company_mentions"]
        buy_count = sum(1 for c in companies.values() if c["buy_count"] > 0)
        subject = f"🚨 Trump Alert [{datetime.now().strftime('%Y-%m-%d')}]"
        if buy_count:
            tickers = [t for t, c in companies.items() if c["buy_count"] > 0]
            subject = f"🚨 TRUMP BUY ALERT: {', '.join(tickers)} — {datetime.now().strftime('%Y-%m-%d')}"
        send_email(subject, text_body, html_body, config)


if __name__ == "__main__":
    main()
