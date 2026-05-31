# trump-alert

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
- **🚨 BUY ALERT** — flags when Trump explicitly promotes a company he holds stock in
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

## Disclaimer

For informational purposes only. Not financial advice. Always verify via [SEC.gov](https://www.sec.gov), [OGE filings](https://efts.usoge.gov/EFTS/public/search), [Reuters](https://www.reuters.com), and [AP News](https://apnews.com) before acting on any information produced by this tool.
