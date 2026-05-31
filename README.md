# Trump Alert — Claude Code Skill

A Claude Code skill that monitors Trump's Truth Social posts and White House speeches for company/stock ticker mentions, fact-checks claims against reliable financial sources, and delivers formatted alerts.

## Features

- **Two sources**: Truth Social (CNN live archive, updated every 5 min) + White House remarks/speeches/videos
- **BUY ALERT** 🚨 — flags when Trump explicitly promotes a company he holds stock in
- **Conflict of interest registry** — cross-references all 12 known Trump stock holdings (OGE-disclosed)
- **Live prices** — shows % change since Trump's mention date (via yfinance)
- **Daily digest** — automated email + Windows Task Scheduler support
- **Fact-checking** — Reuters, AP, Bloomberg, WSJ, CNBC only

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

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_posts.py` | Fetch & scan Truth Social posts |
| `scripts/fetch_speeches.py` | Fetch & scan WH remarks/speeches/videos |
| `scripts/fetch_prices.py` | Live stock prices via yfinance |
| `scripts/run_daily.py` | Full digest orchestrator (email + save) |
| `scripts/setup_config.py` | Interactive email + schedule setup wizard |

## Config

Run `python scripts/setup_config.py` to set up Gmail delivery and/or a Windows Task Scheduler job. Config is stored in `~/.config/trump-alert/.env`.

## Disclaimer

For informational purposes only. Not financial advice. Always verify via SEC.gov, OGE filings, Reuters, and AP News before acting on any information produced by this tool.
