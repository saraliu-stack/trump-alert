# 📊 Trump Market Alert

[![Daily Digest](https://github.com/saraliu-stack/trump-alert/actions/workflows/daily-alert.yml/badge.svg)](https://github.com/saraliu-stack/trump-alert/actions/workflows/daily-alert.yml)
&nbsp;
[![Use this template](https://img.shields.io/badge/Use_this_template-2ea44f?style=flat-square&logo=github&logoColor=white)](https://github.com/saraliu-stack/trump-alert/generate)

> **⚠️ Informational only. Not financial advice.** See [Legal Disclaimer](#️-legal-disclaimer).

A self-hosted tool that monitors Trump's Truth Social posts, White House speeches, financial news, and community research **every day at 7 AM** — and emails you a digest of every company he mentions, with live prices, conflict-of-interest flags, and dated quotes.

**You deploy it once. It runs itself forever. No API keys required. Free.**

---

## Why it exists

A pattern documented in federal ethics filings and major outlets: **Trump publicly praised companies within days of buying their stock** — every time.

| Date | What Trump said | Company | Move |
|------|----------------|---------|------|
| May 8, 2026 | "Go out and buy a Dell" (White House event) | DELL | +14.6% intraday, all-time high |
| May 15, 2026 | "PLTR has great war fighting capabilities" (Truth Social) | PLTR | Reversed 16% freefall |
| Mar 26, 2026 | "One of the hottest companies" (Fox News call) | MU | Jumped |
| Apr 30, 2026 | "Intel stock continues to rise" (Truth Social) | INTC | +3% after-hours |
| Apr 9, 2026 | "THIS IS A GREAT TIME TO BUY!!!" (Truth Social) | Market | Surged hours later |

An [OGE Form 278-T](https://www.cnbc.com/2026/05/15/trump-stock-trade-tech-oge.html) filed May 8, 2026 logged **3,642 individual securities transactions** in Q1 2026 alone — roughly 60 trades per day. Every prior holding has been publicly praised within weeks of purchase.

---

## What you get

A daily HTML email that includes:

- 🚨 **BUY ALERTS** — companies Trump explicitly praised, with the direct quote and date
- ⚠️ **Conflict-of-interest flags** — cross-referenced against his 12 OGE-disclosed holdings
- 📈 **Live prices** — % change since first mention, % change today
- 📰 **Four sources** — Truth Social, White House transcripts, financial news RSS, Reddit community research
- 💼 **Portfolio tracker** — daily prices on all known Trump holdings
- 🆕 **New OGE filing alerts** — notified the moment a new disclosure is detected

---

## Set up your own — 5 minutes

No server. No API keys. Runs free on GitHub Actions.

### Step 1 — Create your copy

Click **[Use this template](https://github.com/saraliu-stack/trump-alert/generate)** → give it any name → **Create repository**.

> This gives you your own private copy. Your credentials never touch anyone else's repo.

### Step 2 — Get a Gmail App Password

1. Enable [2-Step Verification](https://myaccount.google.com/security) on your Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Select **Mail** → **Other** → name it `trump-alert` → click **Generate**
4. Copy the 16-character password (e.g. `abcd efgh ijkl mnop`)

### Step 3 — Add your secrets

In your new repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | What to put |
|--------|-------------|
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASS` | The App Password from Step 2 (no spaces) |
| `ALERT_TO` | Where to send the digest — your email, or a comma-separated list |

> **Sharing with friends?** Set `ALERT_TO` to `you@gmail.com,friend@gmail.com,colleague@yahoo.com` — everyone gets the same daily digest from your one deployment.

### Step 4 — Enable Actions and test

1. Go to the **Actions** tab in your repo
2. Click **Trump Market Alert — Daily Digest** → **Run workflow**
3. Wait ~2 minutes → check your inbox

That's it. The digest runs automatically every morning at 7 AM EDT.

---

## How it works

Four sources run in parallel on every digest:

| Source | Tag | What it catches |
|--------|-----|-----------------|
| Truth Social | 📱 | Posts Trump writes himself (via CNN live archive) |
| White House transcripts | 🎤 | Official remarks and briefings from whitehouse.gov |
| Financial news RSS | 📰 | CNBC, Reuters, Yahoo Finance, Google News — Trump company praise in the press |
| Community research | 🔍 | Reddit finance communities (r/stocks, r/wallstreetbets, r/investing) — Fox calls, hallway scrums, off-camera mentions |

**Sliding-window cache** — the first run fetches 30 days of posts (~90 seconds). Every run after that only fetches the last 25 hours and merges into the cache, keeping warm runs fast (~15 seconds).

**Signal quality** — buy signals require Trump to be the actual recommender ("go out and buy", "praises", "touts", "endorses"). Trade deal coverage ("China agreed to buy 200 planes") and financial jargon ("near buy points") are filtered out.

---

## Trump's known holdings (conflict-of-interest registry)

As of May 2026 OGE filings:

| Ticker | Company | Disclosed range | Purchase period |
|--------|---------|-----------------|-----------------|
| DELL | Dell Technologies | $1M – $5M | Feb 10, 2026 |
| AAPL | Apple | $250K – $500K | Mar 11, 2026 |
| TMO | Thermo Fisher | $15K – $50K | Mar 11, 2026 |
| MU | Micron Technology | $50K – $100K | Mar 25, 2026 |
| PLTR | Palantir | $247K – $630K | Q1 2026 |
| NVDA | Nvidia | $1M – $5M | February 2026 |
| NOW | ServiceNow | $1M – $5M | February 2026 |
| WDAY | Workday | $1M – $5M | February 2026 |
| ORCL | Oracle | $1M – $5M | February 2026 |
| MSFT | Microsoft | $1M – $5M | February 2026 |
| INTC | Intel | ~10% admin stake | 2025–2026 |
| DJT | Trump Media | Majority stake | Founder |

Sources: [CNBC](https://www.cnbc.com/2026/05/15/trump-stock-trade-tech-oge.html) · [TheStreet](https://www.thestreet.com/investing/stocks/trump-brokerage-bought-broadcom-synopsys-dell-intel-in-q1) · [NOTUS](https://www.notus.org/money/donald-trump-stock-investments-palantir-axom-nvidia)

---

## FAQ

**Does it need any API keys?**
No. All sources use public endpoints — the CNN Truth Social archive, whitehouse.gov, public RSS feeds, and Reddit's public search RSS.

**Does it work on GitHub's free tier?**
Yes. Each run takes under 3 minutes and uses well within the free 2,000 minutes/month allowance.

**Can I run it locally instead of on GitHub?**
Yes. `pip install yfinance` then `python scripts/run_daily.py --days=30 --email`. Run `python scripts/setup_config.py` to configure Gmail and optionally register a Windows Task Scheduler job.

**Can I add more subscribers later?**
Yes — just update the `ALERT_TO` secret with a comma-separated list of addresses. The next run will send to all of them.

**Will it alert me on weekends?**
Yes, it runs every day at 7 AM EDT including weekends. Stock prices show as unavailable when markets are closed but all text mentions still appear.

**How do I adjust the schedule?**
Edit `.github/workflows/daily-alert.yml` and change the `cron` line. The current value `0 11 * * *` = 11:00 UTC = 7 AM EDT.

**Is the digest always 30 days?**
Yes by default. The 30-day window means you see the full pattern even if Trump mentioned a company weeks ago. Change `--days=30` in the workflow to `--days=7` if you want a shorter window.

---

## Scripts reference

| Script | Purpose |
|--------|---------|
| `scripts/run_daily.py` | Main orchestrator — fetches all sources, merges cache, builds digest, sends email |
| `scripts/fetch_posts.py` | Truth Social via CNN live archive |
| `scripts/fetch_speeches.py` | whitehouse.gov remarks and briefings |
| `scripts/fetch_news.py` | Financial news RSS + WH speech news supplement |
| `scripts/fetch_last30days.py` | Reddit RSS community research (falls back gracefully if unavailable) |
| `scripts/fetch_prices.py` | Live prices via yfinance |
| `scripts/setup_config.py` | Local setup wizard (Gmail + Windows Task Scheduler) |

---

## Legal context

Under the [STOCK Act of 2012](https://www.congress.gov/bill/112th-congress/senate-bill/2038), the president must disclose individual securities transactions but is **not prohibited** from making them. Presidents are explicitly exempt from federal conflict-of-interest statutes that apply to other executive-branch employees. The White House has denied any conflict of interest.

---

## ⚖️ Legal disclaimer

**READ BEFORE USE.**

This software and all output it produces — including alerts, BUY signals, conflict-of-interest notices, stock prices, and any other content — is provided **for informational and educational purposes only**. Nothing here constitutes investment advice, a recommendation to buy or sell any security, or financial guidance of any kind.

**Past price movements following Trump's statements are historical observations only and do not predict future results.** Markets move for many reasons; correlation is not causation.

This software is provided **"AS IS"** without warranty of any kind. The authors:
- Do not guarantee alerts are complete, accurate, or timely
- Do not guarantee all Trump statements are captured — video-only events, private calls, and off-the-record remarks may be missed
- Do not guarantee OGE data reflects current holdings — filings lag actual trades by weeks
- Accept no liability for decisions made based on this tool's output

**You assume full responsibility for any action you take based on this tool's output.**

This tool monitors only **publicly available information**: published Truth Social posts, published White House transcripts, public RSS feeds, and publicly filed OGE disclosures.

Always verify independently:
[SEC.gov](https://www.sec.gov) · [OGE filings](https://efts.usoge.gov) · [Reuters](https://reuters.com/business) · [AP News](https://apnews.com/hub/financial-markets) · [whitehouse.gov](https://www.whitehouse.gov/briefings-statements/)

Released under the MIT License.

---

*Questions or issues? Open a GitHub issue.*
