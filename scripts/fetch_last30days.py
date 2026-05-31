"""
fetch_last30days.py — Source D for trump-alert: community + web research via last30days.

Uses the last30days skill engine to find Trump company mentions from events that are:
  - NOT published on whitehouse.gov (no transcript)
  - NOT on Truth Social
  - NOT in a structured financial RSS feed

This catches:
  - Fox News phone call interviews  (e.g. Micron, Mar 26 — said on-air, no WH record)
  - Reporter press-pool scrums in hallways
  - Cabinet meeting / CEO meeting leaks
  - Rally ad-libs not in the official speech transcript
  - Reddit/investor community posts that break the news first

Strategy: run last30days with --search reddit (keyless, uses public RSS)
targeting r/stocks, r/investing, r/wallstreetbets, r/StockMarket.
Those communities post "Trump just mentioned [company] on Fox" within minutes.

Falls back gracefully if last30days is not installed or has no results.

Output: same JSON schema as fetch_posts.py and fetch_speeches.py.

Usage:
    python fetch_last30days.py [--hours=48] [--days=N] [--ticker=SYMBOL] [--buy-only]
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Path to last30days engine (relative to this skill)
LAST30DAYS_SCRIPT = Path(__file__).parent.parent.parent / "last30days" / "scripts" / "last30days.py"

# Fallback: also check a few common sibling install locations
_FALLBACK_PATHS = [
    Path.home() / ".claude" / "skills" / "last30days" / "scripts" / "last30days.py",
    Path(__file__).parents[3] / "last30days" / "scripts" / "last30days.py",
]

# ---------------------------------------------------------------------------
# Company name → ticker (must mirror fetch_posts.py for consistent COI tagging)
# ---------------------------------------------------------------------------
COMPANY_TICKERS = {
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
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "broadcom": "AVGO",
    "qualcomm": "QCOM",
    "ibm": "IBM",
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
    "pfizer": "PFE",
    "eli lilly": "LLY",
    "walmart": "WMT",
    "disney": "DIS",
    "ford": "F",
    "general motors": "GM",
}

CONFLICT_TICKERS = {
    "DELL", "AAPL", "TMO", "MU", "PLTR", "NVDA",
    "NOW", "WDAY", "ORCL", "MSFT", "INTC", "DJT",
}

BUY_WORDS = {
    "buy", "great company", "great investment", "invest", "strong", "hot", "soaring",
    "surging", "going up", "bullish", "prais", "endors", "recommend", "touts", "boosts",
    "plugs", "champions", "fantastic", "best", "love",
}
WARN_WORDS = {
    "boycott", "fail", "corrupt", "dead", "terrible", "horrible", "ban", "sanction",
    "tariff", "threaten", "attack", "accus", "worst", "avoid",
}


def _find_last30days() -> Path | None:
    """Locate the last30days engine script."""
    candidates = [LAST30DAYS_SCRIPT] + _FALLBACK_PATHS
    for p in candidates:
        if p.exists():
            return p
    return None


def _build_query_plan(days: int) -> dict:
    """
    Pre-built query plan targeting Reddit finance/investing communities
    for Trump company mention discussions — catches events missed by all
    other sources (Fox calls, press scrums, cabinet meetings, rally ad-libs).

    Uses only the keyless 'reddit' source so no API keys are required.
    Subreddit names embedded in the search query guide last30days toward
    r/stocks, r/wallstreetbets, r/investing, r/StockMarket.
    """
    return {
        "intent": "breaking_news",
        "freshness_mode": "strict_recent",
        "cluster_mode": "story",
        "raw_topic": "Trump company stock mention praise event interview rally press conference",
        "subqueries": [
            {
                "label": "Trump praises company on Fox News or in interview",
                "search_query": (
                    f"Trump praised company stock buy Fox News interview said told "
                    f"r/stocks r/wallstreetbets last {days} days"
                ),
                "ranking_query": "Trump explicitly praises or recommends a company in a media interview",
                "sources": ["reddit"],
                "weight": 1.0,
            },
            {
                "label": "Trump company mention at rally or press scrum",
                "search_query": (
                    f"Trump company stock mentioned rally press conference reporters "
                    f"r/investing r/StockMarket last {days} days"
                ),
                "ranking_query": "Trump mentions a company at a public event not published on whitehouse.gov",
                "sources": ["reddit"],
                "weight": 0.9,
            },
            {
                "label": "Trump CEO meeting company stock",
                "search_query": (
                    f"Trump met CEO praised company stock White House "
                    f"r/stocks r/wallstreetbets"
                ),
                "ranking_query": "Trump praises a company after meeting its CEO",
                "sources": ["reddit"],
                "weight": 0.8,
            },
        ],
        "source_weights": {
            "reddit": 1.0,
        },
        "notes": [
            "Focus on events NOT on whitehouse.gov transcripts or Truth Social.",
            "Target: Fox News calls, press scrums, rallies, cabinet meetings, signing ceremonies.",
            "Reddit communities r/stocks, r/wallstreetbets, r/investing post Trump company "
            "mentions within minutes of any TV appearance.",
        ],
    }


def _detect_signal(text: str) -> str:
    low = text.lower()
    if any(w in low for w in BUY_WORDS):
        return "buy"
    if any(w in low for w in WARN_WORDS):
        return "warn"
    return "neutral"


def _extract_companies(text: str) -> list[dict]:
    low = text.lower()
    hits = []
    seen = set()
    for name, ticker in COMPANY_TICKERS.items():
        if name in low and ticker not in seen:
            idx = low.find(name)
            start = max(0, idx - 80)
            end = min(len(text), idx + len(name) + 80)
            snippet = text[start:end].strip()
            signal = _detect_signal(text)
            hits.append({
                "company": name.title(),
                "ticker": ticker,
                "signal_type": signal,
                "context_snippet": snippet,
            })
            seen.add(ticker)
    return hits


def _parse_l30d_json(raw_json: str, cutoff_dt: datetime, ticker_filter: str | None, buy_only: bool) -> list[dict]:
    """Parse last30days --emit=json output into trump-alert standard schema items."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        print(f"[fetch_last30days] JSON parse error: {e}", file=sys.stderr)
        return []

    # last30days Report JSON has: topic, clusters, ranked_candidates, items_by_source, ...
    # We work from ranked_candidates (the flat scored list) for simplicity.
    candidates = data.get("ranked_candidates", [])
    results = []
    seen_urls = set()

    for cand in candidates:
        title = cand.get("title", "")
        url = cand.get("url", "")
        snippet = cand.get("snippet", "")
        published_at = cand.get("source_items", [{}])[0].get("published_at") if cand.get("source_items") else None
        full_text = f"{title} {snippet}"

        # Must mention Trump
        if "trump" not in full_text.lower():
            continue

        # Date filter
        if published_at:
            try:
                pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                if pub_dt < cutoff_dt:
                    continue
            except (ValueError, TypeError):
                pass  # Keep if we can't parse the date

        if url in seen_urls:
            continue
        seen_urls.add(url)

        mentions = _extract_companies(full_text)
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

        conflict = any(m["ticker"] in CONFLICT_TICKERS for m in mentions)
        results.append({
            "post_id": f"l30d_{abs(hash(url)) % 10**10}",
            "source": "last30days",
            "title": title,
            "created_at": published_at or "",
            "content": full_text[:600],
            "url": url,
            "engagement": {
                "score": cand.get("final_score", 0),
            },
            "mentions": mentions,
            "conflict_of_interest": conflict,
        })

    return results


def fetch_via_last30days(hours: int, ticker_filter=None, buy_only=False) -> list[dict]:
    """Run last30days engine with Reddit RSS and return trump-alert items."""
    engine = _find_last30days()
    if not engine:
        print(
            "[fetch_last30days] last30days engine not found — skipping Source D. "
            "Install it at ~/.claude/skills/last30days/ to enable.",
            file=sys.stderr,
        )
        return []

    days = max(1, hours // 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    plan = _build_query_plan(days)

    # Write plan to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(plan, f)
        plan_path = f.name

    try:
        cmd = [
            sys.executable,
            str(engine),
            plan["raw_topic"],
            "--plan", plan_path,
            "--search", "reddit",
            "--emit", "json",
            f"--days={days}",
        ]
        print(f"[fetch_last30days] Running last30days (Reddit RSS, keyless)...", file=sys.stderr)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(f"[fetch_last30days] last30days exited {result.returncode}: {result.stderr[:300]}", file=sys.stderr)
            return []

        return _parse_l30d_json(result.stdout, cutoff, ticker_filter, buy_only)

    except subprocess.TimeoutExpired:
        print("[fetch_last30days] last30days timed out (120s) — skipping Source D.", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[fetch_last30days] unexpected error: {e}", file=sys.stderr)
        return []
    finally:
        try:
            os.unlink(plan_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Source D: last30days Reddit research for Trump company mentions at non-recorded events"
    )
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--days",  type=int, default=None)
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--buy-only", action="store_true")
    args = parser.parse_args()

    hours = args.days * 24 if args.days else args.hours
    results = fetch_via_last30days(
        hours=hours,
        ticker_filter=args.ticker,
        buy_only=args.buy_only,
    )

    output = {
        "source": "last30days_reddit",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_matched": len(results),
        "posts": results,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
