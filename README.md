# trump-alert

> **⚠️ DISCLAIMER:** This tool is for informational purposes only. Nothing here is financial advice. Do not make investment decisions based solely on its output. See the full [Legal Disclaimer](#️-legal-disclaimer) at the bottom of this page.

A Claude Code skill that monitors Trump's Truth Social posts and White House speeches for company/stock ticker mentions, fact-checks claims against reliable financial sources, and delivers formatted alerts.

---

## Why This Exists

A pattern documented in federal ethics filings and major financial outlets: **Trump publicly praised companies within days of buying their stock** — in every case confirmed so far.

A 113-page [OGE Form 278-T disclosure](https://www.cnbc.com/2026/05/15/trump-stock-trade-tech-oge.html) certified by Trump on May 8, 2026, logged **3,642 individual securities transactions** in Q1 2026 alone ($220M–$750M cumulative value, roughly 60 trades per day). [Source: Moneywise / Benzinga](https://moneywise.com/news/top-stories/trump-eric-trump-nvidia-palantir-stock-trades-insider-trading-accusations)

### Documented Cases

| Date | Company | Ticker | What Trump Said | Price Move | Source |
|------|---------|--------|-----------------|------------|--------|
| 2026-05-08 | Dell | DELL | "Go out and buy a Dell" at White House Mother's Day event | +14.6% intraday, all-time high $263.99 | [Yahoo Finance](https://finance.yahoo.com/markets/stocks/articles/dell-family-gave-6-25b-173000510.html) · [TheStreet](https://www.thestreet.com/markets/trump-praised-dell-at-the-white-house-and-the-stock-soared) |
| 2026-05-15 | Palantir | PLTR | "PLTR has proven to have great war fighting capabilities" on Truth Social | Reversed 16% freefall | [CNBC](https://www.cnbc.com/2026/05/15/trump-palantir-stock-truth-social.html) · [Yahoo Finance](https://finance.yahoo.com/markets/stocks/articles/trump-praises-palantir-pltr-truth-044917332.html) |
| 2026-04-30 | Intel | INTC | "Intel stock continues to rise" on Truth Social | +3% after-hours | [Washington Examiner](https://www.washingtonexaminer.com/news/white-house/4583562/trump-praised-companies-within-days-of-buying-their-stock/) |
| 2026-04-09 | Broad market | — | "THIS IS A GREAT TIME TO BUY!!!" hours before tariff pause | Market surged | [CNBC](https://www.cnbc.com/2026/05/15/trump-stock-trade-tech-oge.html) |
| 2026-03-26 | Micron | MU | "One of the hottest companies" on Fox News | Jumped | [Washington Examiner](https://www.washingtonexaminer.com/news/white-house/4583562/trump-praised-companies-within-days-of-buying-their-stock/) |
| 2026-02-19 | Dell | DELL | "Go out and buy a Dell computer" at Rome, GA rally | — | [TheStreet](https://www.thestreet.com/investing/stocks/trump-brokerage-bought-broadcom-synopsys-dell-intel-in-q1) |

**Additional reporting:** Dell scored a [$9.7 billion Pentagon contract](https://www.detroitnews.com/story/news/politics/2026/05/28/dell-inks-9-7-billion-pentagon-contract-after-trump-acquires-stock/90304079007/) weeks after Trump promoted the company. Trump bought $1M–$5M in DELL on Feb 10 — nine days before the first "buy a Dell" rally. [More: NOTUS](https://www.notus.org/money/donald-trump-stock-investments-palantir-axom-nvidia)

---

## Features

- **Two live sources**: Truth Social (CNN live archive, updated every 5 min) + White House remarks/speeches/briefings
- **🚨 BUY ALERT** — flags when Trump explicitly promotes any company (conflict-of-interest notice added separately when he also holds that stock)
- **Conflict of interest registry** — cross-references all 12 known Trump stock holdings (OGE-disclosed)
- **Live prices** — shows % change since Trump's mention date (via yfinance)
- **Daily digest** — automated email + Windows Task Scheduler support
- **Fact-checking** — Reuters, AP, Bloomberg, WSJ, CNBC only

---

## Data Sources

### Truth Social (Primary — live, updated every 5 min)

Maintained by CNN Visuals:
- JSON: `https://ix.cnn.io/data/truth-social/truth_archive.json`
- CSV: `https://ix.cnn.io/data/truth-social/truth_archive.csv`

Community backup maintained by Matt Stiles:
- `https://stilesdata.com/trump-truth-social-archive/posts.json`
- [GitHub: stiles/trump-truth-social-archive](https://github.com/stiles/trump-truth-social-archive)

### White House Speeches & Briefings

Scraped from:
- `https://www.whitehouse.gov/remarks/` (formal remarks)
- `https://www.whitehouse.gov/briefings-statements/` (press briefings and official statements)

The skill fetches up to 7 pages of each listing (roughly 70+ speeches) to cover a 30-day lookback window. Note: video-only events (e.g., some Mother's Day remarks) have no published transcript — these are the hardest to catch.

### Conflict of Interest Registry

All holdings sourced from the U.S. Office of Government Ethics:
- [CNBC: Trump went big on tech stocks in Q1 2026](https://www.cnbc.com/2026/05/15/trump-stock-trade-tech-oge.html)
- [Benzinga: Trump Q1 2026 Trade Disclosure](https://www.benzinga.com/news/politics/26/05/52576337/trump-q1-2026-trade-disclosure-nvidia-amd-palantir-microsoft-oracle)
- [IBTimes: Trump bought Nvidia one week before chip deal approval](https://www.ibtimes.co.uk/trump-nvidia-stock-trades-ethics-concerns-1797424)

---

## Quick Start

```bash
# Run a spot check (last 48h)
/trump-alert

# Run a 30-day digest
python scripts/run_daily.py --days=30

# Set up daily email delivery
python scripts/setup_config.py

# Install price dependency (one-time)
pip install yfinance
```

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_posts.py` | Fetch & scan Truth Social posts via CNN archive |
| `scripts/fetch_speeches.py` | Fetch & scan WH remarks and briefings (paginated) |
| `scripts/fetch_prices.py` | Live stock prices via yfinance |
| `scripts/run_daily.py` | Full digest orchestrator (BUY alerts + MICRO + PORTFOLIO) |
| `scripts/setup_config.py` | Interactive email + Windows Task Scheduler setup wizard |

---

## Trump's Known Stock Holdings (Conflict of Interest Registry)

As of May 2026 OGE filings:

| Ticker | Company | Disclosed Range | Purchase Period |
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

> **Sources:** [CNBC OGE filing coverage](https://www.cnbc.com/2026/05/15/trump-stock-trade-tech-oge.html) · [TheStreet Q1 trades](https://www.thestreet.com/investing/stocks/trump-brokerage-bought-broadcom-synopsys-dell-intel-in-q1) · [NOTUS investigative piece](https://www.notus.org/money/donald-trump-stock-investments-palantir-axom-nvidia)

---

## Config

Run `python scripts/setup_config.py` to set up Gmail delivery and/or a Windows Task Scheduler job. Config is stored in `~/.config/trump-alert/.env`.

---

## Legal Context

Under the [STOCK Act of 2012](https://www.congress.gov/bill/112th-congress/senate-bill/2038), the president is required to disclose individual securities transactions but is **not prohibited** from making them. Presidents are explicitly exempt from federal conflict-of-interest statutes that bar other executive-branch employees from acting on matters where they hold a financial stake. The White House has denied any conflict of interest.

---

## ⚖️ Legal Disclaimer

> **READ BEFORE USE. BY USING THIS SOFTWARE YOU ACKNOWLEDGE AND ACCEPT THE TERMS BELOW.**

### No Financial Advice

This software and all output it produces — including alerts, summaries, BUY signals, conflict-of-interest notices, stock prices, and any other content — is provided **for informational and educational purposes only**. Nothing in this repository, its scripts, its output, or its documentation constitutes, or should be construed as:

- Investment advice, financial advice, trading advice, or any other type of financial guidance
- A recommendation or solicitation to buy, sell, hold, or otherwise transact in any security, financial instrument, or asset
- An endorsement of any company, stock, or investment strategy

**Past price movements following Trump's statements are historical observations only and are not predictive of future results.** Markets can move for many reasons; correlation between a public statement and a price change does not establish causation or guarantee any future outcome.

### No Warranty; Use At Your Own Risk

This software is provided **"AS IS"**, without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, accuracy, completeness, timeliness, or non-infringement. The authors and contributors:

- Do not guarantee that alerts are complete, accurate, or delivered in time to be actionable
- Do not guarantee that all Trump statements, posts, or speeches are captured — some events (video-only, private calls, off-the-record remarks) may not appear in the sources this tool monitors
- Do not guarantee that conflict-of-interest data reflects Trump's current holdings — OGE filings are periodic and may lag actual trades by weeks or months
- Accept no responsibility for errors, omissions, or inaccuracies in third-party data sources (CNN archive, whitehouse.gov, RSS feeds)

### Limitation of Liability

To the maximum extent permitted by applicable law, the authors, contributors, and maintainers of this software shall not be liable for any direct, indirect, incidental, special, consequential, punitive, or exemplary damages arising from:

- Your use of or reliance on this software or its output
- Any trading, investment, or financial decision you make based on alerts produced by this tool
- Financial losses of any kind, including but not limited to lost profits, lost savings, or loss of capital
- Missed alerts, delayed alerts, or incorrect alerts
- Any third-party data being inaccurate, incomplete, or unavailable

**You assume full and sole responsibility for any action you take based on this tool's output.**

### No Insider Information

This tool monitors only **publicly available information**: published Truth Social posts, published White House transcripts, public RSS news feeds, and publicly filed OGE disclosures. It does not have access to, and does not purport to provide, any non-public or insider information. Any apparent predictive value derives entirely from publicly observable patterns in public records.

### Not a Trading System

This tool is a **monitoring and research aid**, not an automated trading system. It does not execute trades, connect to any brokerage, or place any orders. Do not build automated trading logic that acts directly on the output of this tool without independent human review and verification.

### Third-Party Sources

This tool aggregates content from third-party sources including CNN, Yahoo Finance, CNBC, Reuters, the White House website, and the U.S. Office of Government Ethics. The authors are not affiliated with any of these organizations. Their content is their own; accuracy and completeness are their responsibility. Always verify any claim via primary sources:

- SEC filings: [sec.gov/cgi-bin/browse-edgar](https://www.sec.gov/cgi-bin/browse-edgar)
- OGE disclosures: [efts.usoge.gov](https://efts.usoge.gov/EFTS/public/search)
- White House: [whitehouse.gov/briefings-statements](https://www.whitehouse.gov/briefings-statements/)
- Reuters: [reuters.com/business](https://www.reuters.com/business/)
- AP News: [apnews.com/hub/financial-markets](https://apnews.com/hub/financial-markets)

### Open Source License

This software is released under the MIT License. See `LICENSE` for full terms. The MIT License does not limit or modify the disclaimers above — it governs copyright and distribution only.

---

*If you have questions about securities law, conflict-of-interest rules, or the legality of trading around public political statements, consult a licensed attorney or registered financial advisor.*
