---
name: trump-alert
description: >
  Monitor Trump's Truth Social posts for company and stock ticker mentions, fact-check
  the claims against reliable financial sources, and produce formatted alert messages.
  Use this skill whenever the user asks about Trump mentioning stocks, companies, or
  investments on Truth Social — including phrases like "trump stock alert", "did trump
  mention any stocks", "trump truth social companies", "trump pump", "trump buy signal",
  "what companies did trump post about", or any variant of tracking Trump's market-moving
  posts. Also use for scheduled or recurring monitoring requests like "alert me when
  trump mentions a ticker". Trigger even if the user just says "check trump alerts" or
  "/trump-alert".
---

# Trump Truth Social — Company & Stock Alert

Monitor Trump's Truth Social posts for company/ticker mentions. Fact-check each claim.
Flag BUY signals with maximum prominence.

## Data Sources

**Primary (live, updated every 5 min):**
- JSON: `https://ix.cnn.io/data/truth-social/truth_archive.json`
- CSV:  `https://ix.cnn.io/data/truth-social/truth_archive.csv`

**Backup:**
- `https://stilesdata.com/trump-truth-social-archive/posts.json`

Fields per post: `id`, `created_at`, `content`, `url`, `replies_count`, `reblogs_count`, `favourites_count`, `media`

## Invocation

```
/trump-alert [--hours=N] [--days=N] [--ticker=SYMBOL] [--buy-only]
```

- `--hours=N` / `--days=N` — lookback window (default: 48h for spot check, 30d for digest)
- `--ticker=SYMBOL` — filter to a specific ticker
- `--buy-only` — show only BUY signal alerts
- `--digest` — run the full 30-day digest (micro + macro + prices + portfolio)
- `--email` — send the digest via email (requires config setup)
- `--schedule` — set up daily automated run

## Daily Digest Mode

The full digest runs `run_daily.py` and produces two sections:

1. **🚨 BUY ALERTS** — any buy signals in the window, most prominent
2. **📈 MICRO** — per-company mentions with live stock prices + % change since mention
3. **💼 PORTFOLIO** — live prices on Trump's known holdings + any new OGE filings (timestamped)

```bash
# One-off digest (last 30 days, printed to screen + saved to ~/Documents/TrumpAlerts/)
python "C:\Users\saral\.claude\skills\trump-alert\scripts\run_daily.py" --days=30

# With email delivery
python "C:\Users\saral\.claude\skills\trump-alert\scripts\run_daily.py" --days=30 --email

# First-time email setup
python "C:\Users\saral\.claude\skills\trump-alert\scripts\setup_config.py"

# Install live prices (one-time)
pip install yfinance
```

## Workflow

### Step 1: Fetch ALL sources (run all three in parallel)

**Source A — Truth Social posts:**
```bash
python "C:\Users\saral\.claude\skills\trump-alert\scripts\fetch_posts.py" [OPTIONS]
```

**Source B — White House speeches, remarks, press conferences:**
```bash
python "C:\Users\saral\.claude\skills\trump-alert\scripts\fetch_speeches.py" [OPTIONS]
```

**Source C — Financial news headlines (CNBC, Yahoo Finance, Reuters RSS):**
```bash
python "C:\Users\saral\.claude\skills\trump-alert\scripts\fetch_news.py" [OPTIONS]
```
Source C catches Trump company mentions that originate from video events, rallies, and
phone calls where no official WH transcript is published (e.g., the May 8 Dell surge,
the Feb 19 Rome GA rally). It scans RSS headlines for "Trump praises/touts/says buy X".

Pass through any `--hours`, `--days`, `--ticker`, `--buy-only` flags the user provided.

All three scripts output the same JSON schema. Merge their `posts` arrays before processing.
Tag each result with its source type for display:
- Truth Social posts → prefix alert with 📱 **Truth Social**
- WH speeches/events → prefix alert with 🎤 **Speech/Event**
- News headlines → prefix alert with 📰 **News Report**

Each matched item has:
- `post_id` / `title`, `created_at` / `date`, `content`, `url`, `engagement`
- `mentions`: list of `{company, ticker, signal_type, context_snippet}`
- `conflict_of_interest`: true/false (Trump owns stock in this company)

**Why speeches matter:** Trump's most powerful market-moving statements have come from
live speeches and White House events, not just Truth Social. Dell surged 14% after
"go out and buy a Dell" at a May 8 White House event. Apple, Thermo Fisher, and Micron
all jumped after in-person remarks. The speech channel is the higher-volatility signal.

### Step 2: Fact-check each mention

For each mention returned by the script, use WebSearch to verify the claim.
**Only use reliable financial sources:** Reuters, AP News, Bloomberg, WSJ, CNBC, SEC.gov, Yahoo Finance, Financial Times.
**Never cite:** partisan blogs, Truth Social itself, Breitbart, InfoWars, or any site flagged as misinformation.

Search query pattern: `"{company}" stock "{claim_keyword}" site:reuters.com OR site:apnews.com OR site:bloomberg.com OR site:wsj.com OR site:cnbc.com`

Assign a verdict:
- ✅ **VERIFIED** — the claim is corroborated by at least one reliable source
- ⚠️ **UNVERIFIED** — no corroborating source found within 72h of the post
- ❌ **FALSE** — a reliable source directly contradicts the claim
- 🔍 **CONFLICT OF INTEREST** — Trump personally owns stock in this company (see holdings list below)

### Step 3: Format and output alerts

Use the alert templates below. Output ALL alerts for the time window, sorted by signal type: BUY alerts first, then WARNING, then MENTIONS.

---

## Alert Templates

### 🚨 BUY ALERT (highest prominence)

Use when: Trump uses words like "buy", "great investment", "going up", "strong buy",
"invest in", "you should own", or praises a company while tagging its ticker.

```
╔══════════════════════════════════════════════════════════╗
║  🚨🚨  BUY ALERT — {COMPANY} ({TICKER})  🚨🚨           ║
╚══════════════════════════════════════════════════════════╝

📅 Posted: {DATE_TIME} UTC
🔗 Post: {URL}

💬 Trump said:
  "{QUOTE}"

📊 Fact-Check: {VERDICT}
   {VERDICT_DETAIL}
   Source: [{SOURCE_NAME}]({SOURCE_URL})

{IF_CONFLICT}⚠️  CONFLICT OF INTEREST: Trump personally holds {TICKER} stock
   (Disclosed holding: {HOLDING_RANGE}, purchased {PURCHASE_DATE})
{ENDIF}

📈 Engagement: {FAVOURITES} ❤️  {REBLOGS} 🔁  {REPLIES} 💬
```

### 📉 WARNING / NEGATIVE SIGNAL

Use when: Trump criticizes a company, calls for boycotts, threatens regulation, or says a stock is "failing", "dead", "corrupt", etc.

```
┌─────────────────────────────────────────────────────────┐
│  📉  WARNING — {COMPANY} ({TICKER})                     │
└─────────────────────────────────────────────────────────┘

📅 Posted: {DATE_TIME} UTC
🔗 Post: {URL}

💬 Trump said:
  "{QUOTE}"

📊 Fact-Check: {VERDICT}
   {VERDICT_DETAIL}

📈 Engagement: {FAVOURITES} ❤️  {REBLOGS} 🔁  {REPLIES} 💬
```

### 📣 MENTION (neutral)

Use when: company or ticker mentioned without clear directional signal.

```
📣 MENTION — {COMPANY} ({TICKER})
📅 {DATE_TIME} UTC  |  🔗 {URL}
💬 "{QUOTE}"
📊 {VERDICT}  |  📈 {FAVOURITES}❤️  {REBLOGS}🔁
```

---

## Company & Ticker Detection Rules

The fetch script handles detection, but here is the logic for reference:

**Ticker detection:** All-caps sequences of 1–5 letters that match known tickers
(e.g., PLTR, NVDA, MSFT, INTC, AAPL). Ignore common all-caps words: I, A, OR, AND,
IS, IT, BE, DO, SO, WE, US, THE, FOR, NOT, BUT, ALL, ANY, CAN, OUR, ARE, HAS, HAD,
WAS, ITS.

**Company name detection:** Full name matching against the embedded company list in
the script (S&P 500 + major non-listed companies). Case-insensitive.

**Phrase detection:** Patterns like "buy {X}", "invest in {X}", "{X} stock",
"{X} is a great company", "I own {X}", "you should own {X}".

---

## Trump's Known Stock Holdings (Conflict of Interest Registry)

Cross-reference every mention against this list. If matched, always append the
🔍 CONFLICT OF INTEREST notice regardless of verdict.

| Ticker | Company              | Disclosed Range        | How he praised it                          | Purchase date      |
|--------|----------------------|------------------------|--------------------------------------------|--------------------|
| DELL   | Dell Technologies    | $1M – $5M              | "Go out and buy a Dell" (Feb 19 rally; May 8 WH event) | Feb 10, 2026  |
| AAPL   | Apple                | $250K – $500K          | "Apple, a great company" (Mar 11 KY speech)| Mar 11, 2026       |
| TMO    | Thermo Fisher        | $15K – $50K            | "It's a great company" (Mar 11 OH site visit) | Mar 11, 2026    |
| MU     | Micron Technology    | $50K – $100K           | "One of the hottest companies" (Mar 26 Fox call) | Mar 25, 2026  |
| PLTR   | Palantir             | $247K – $630K          | PLTR ticker on Truth Social (May 15)       | ~April 2026        |
| NVDA   | Nvidia               | $1M – $5M              | (holdings only, no public praise yet)      | February 2026      |
| NOW    | ServiceNow           | $1M – $5M              | (holdings only)                            | February 2026      |
| WDAY   | Workday              | $1M – $5M              | (holdings only)                            | February 2026      |
| ORCL   | Oracle               | $1M – $5M              | (holdings only)                            | February 2026      |
| MSFT   | Microsoft            | $1M – $5M              | (holdings only)                            | February 2026      |
| INTC   | Intel                | 10% admin stake        | "Intel stock continues to rise" (Apr 30 Truth Social) | 2025–2026  |
| DJT    | Trump Media          | Majority stake         | Signs all posts "President DJT"            | Founder            |

> **Source:** U.S. Office of Government Ethics periodic transaction reports, May 2026.
> Trump made 3,711 individual trades in Q1 2026 ($220M–$750M volume, ~60 trades/day).
> Pattern: in EVERY case where Trump praised a company publicly, he had already purchased
> stock in it. Always flag conflict of interest on any company he mentions positively.

---

## Known Historical Alerts (embedded context)

Use these as fact-check anchors when the same companies appear again:

| Date       | Company        | Ticker | Signal  | Source      | What happened |
|------------|----------------|--------|---------|-------------|---------------|
| 2026-05-15 | Palantir       | PLTR   | 🚨 BUY  | 📱 Truth Social | "PLTR has proven to have great war fighting capabilities" — reversed 16% freefall in minutes. Held $247K–$630K PLTR. |
| 2026-05-08 | Dell           | DELL   | 🚨 BUY  | 🎤 WH Speech | "Go out and buy a Dell" at Mother's Day event — +14% that day. Held $1M–$5M DELL purchased Feb 10. Pentagon gave Dell $9.7B contract weeks later. |
| 2026-04-30 | Intel          | INTC   | 📣 MENTION | 📱 Truth Social | "Intel stock continues to rise" — +3% after-hours. Admin holds ~10% stake. |
| 2026-04-09 | Market (broad) | —      | 🚨 BUY  | 📱 Truth Social | "THIS IS A GREAT TIME TO BUY!!!" hours before tariff pause — market surged. Under SEC/DOJ review. |
| 2026-03-26 | Micron         | MU     | 🚨 BUY  | 🎤 Fox call  | "One of the hottest companies" on Fox News — held $50K–$100K MU purchased day before. |
| 2026-03-11 | Apple          | AAPL   | 🚨 BUY  | 🎤 Speech    | "Apple, a great company" at KY rally — bought $250K–$500K AAPL same day. |
| 2026-03-11 | Thermo Fisher  | TMO    | 🚨 BUY  | 🎤 Site visit | "It's a great company" during OH facility tour — bought $15K–$50K TMO same day. |
| 2026-02-19 | Dell           | DELL   | 🚨 BUY  | 🎤 Rally     | "Go out and buy a Dell computer" at Rome, GA rally — bought $1M–$5M DELL nine days earlier. |

---

## Output Summary Header

Always open the response with a summary line before listing alerts:

```
🔍 Trump Company Alert Scan — Last {N} hours
   📱 Truth Social: {X} posts scanned
   🎤 Speeches/Events: {Y} items scanned
   📰 News headlines: {N2} Trump+company stories matched
   ─── {Z} company mentions found  |  {W} BUY alerts ───
   Data freshness: {ARCHIVE_TIMESTAMP}
   ─────────────────────────────────────────────────────
```

If zero mentions found:
```
✅ No company or ticker mentions found in Trump's last {N} hours.
   📱 {X} Truth Social posts + 🎤 {Y} speeches/events + 📰 {N2} news stories scanned.
   Data as of {ARCHIVE_TIMESTAMP}.
```

---

## Fact-Check Quality Rules

- Never cite Truth Social itself as a source for a fact-check.
- Never use opinion columns as verification — only news reporting or primary data.
- If a post contains a price claim (e.g., "Intel stock continues to rise"), verify
  against actual market data from Yahoo Finance or Google Finance.
- If the claim is about military/government use of a company's product, check SEC
  filings and DoD contract databases (usaspending.gov) as primary sources.
- When in doubt, mark ⚠️ UNVERIFIED rather than guessing.
- Add a brief plain-English explanation of WHY the verdict was assigned — not just
  the label. Users need to understand the evidence, not just trust a checkmark.
