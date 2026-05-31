#!/usr/bin/env python3
"""
trump-alert MCP Server

Exposes trump-alert as MCP tools so it can be used inside any MCP-compatible
agent host: Claude Desktop, Cursor, VS Code + Continue.dev, custom agents
built on the Anthropic Agent SDK, etc.

Installation (add to your agent's MCP config):

  Claude Desktop  →  ~/Library/Application Support/Claude/claude_desktop_config.json
  Cursor          →  ~/.cursor/mcp.json
  Claude Code     →  ~/.claude/claude_code_config.json  (or via /mcp add)

Config snippet:
  {
    "mcpServers": {
      "trump-alert": {
        "command": "python",
        "args": ["C:/Users/saral/.claude/skills/trump-alert/mcp_server.py"]
      }
    }
  }

Or if installed via pip:
  {
    "mcpServers": {
      "trump-alert": {
        "command": "trump-alert-mcp"
      }
    }
  }

Tools exposed:
  - scan_trump_mentions   — scan last N hours across all 4 sources
  - get_trump_digest      — full 30-day digest with prices and portfolio
  - get_trump_portfolio   — live prices on Trump's OGE-disclosed holdings
  - check_conflict        — check if a ticker is in Trump's holdings

Requires:  pip install mcp
Optional:  pip install yfinance  (for live prices)
"""

import asyncio
import json
import sys
from pathlib import Path

# Make trump_alert importable when run directly (not installed via pip)
sys.path.insert(0, str(Path(__file__).parent))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    print(
        "MCP package not installed. Run:  pip install mcp\n"
        "Or install with MCP extras:      pip install trump-alert[mcp]",
        file=sys.stderr,
    )
    sys.exit(1)

from trump_alert import scan, digest, portfolio, TRUMP_HOLDINGS, CONFLICT_TICKERS

# ---------------------------------------------------------------------------
# Server definition
# ---------------------------------------------------------------------------

server = Server("trump-alert")

DISCLAIMER = (
    "FOR INFORMATIONAL PURPOSES ONLY. NOT FINANCIAL ADVICE. "
    "Always verify via SEC.gov and OGE filings before acting on any alert."
)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="scan_trump_mentions",
            description=(
                "Scan Trump's Truth Social posts, White House speeches, financial news, "
                "and Reddit finance communities for company/stock ticker mentions. "
                "Returns BUY alerts, WARNINGs, and neutral mentions, each flagged if "
                "Trump holds that stock (conflict of interest). "
                "Use this whenever you need to know if Trump has recently mentioned "
                "or promoted any company."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Lookback window in hours (default: 48)",
                        "default": 48,
                    },
                    "days": {
                        "type": "integer",
                        "description": "Lookback window in days — overrides hours if set",
                    },
                    "ticker": {
                        "type": "string",
                        "description": "Filter results to a specific ticker (e.g. DELL, PLTR, NVDA)",
                    },
                    "buy_only": {
                        "type": "boolean",
                        "description": "Return only BUY signal mentions (default: false)",
                        "default": False,
                    },
                },
            },
        ),
        types.Tool(
            name="get_trump_digest",
            description=(
                "Get a full Trump market alert digest covering the last N days. "
                "Includes: BUY alerts with conflict-of-interest flags, per-company "
                "mention counts with live stock prices, and Trump's known portfolio "
                "holdings with current prices. Use for a comprehensive overview rather "
                "than a quick spot-check."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Lookback window in days (default: 30)",
                        "default": 30,
                    },
                    "no_prices": {
                        "type": "boolean",
                        "description": "Skip live price fetching for faster results (default: false)",
                        "default": False,
                    },
                },
            },
        ),
        types.Tool(
            name="get_trump_portfolio",
            description=(
                "Get live stock prices for Trump's known OGE-disclosed holdings: "
                "DELL, AAPL, PLTR, NVDA, MSFT, MU, TMO, INTC, NOW, WDAY, ORCL, DJT. "
                "Each entry shows the disclosed range and approximate purchase date "
                "alongside the current price. Use when you want to quickly check "
                "Trump's known holdings without scanning for mentions."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="check_conflict",
            description=(
                "Check whether Trump holds stock in a given company (conflict of interest). "
                "Returns the OGE-disclosed holding range and purchase date if found. "
                "Use before citing any Trump company praise to know if he had skin in the game."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Ticker symbol to check (e.g. DELL, PLTR, NVDA)",
                    },
                },
                "required": ["ticker"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _dispatch, name, arguments
        )
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


def _dispatch(name: str, args: dict) -> str:
    """Synchronous dispatch — runs in thread pool so it doesn't block the event loop."""

    if name == "scan_trump_mentions":
        data = scan(
            hours=args.get("hours", 48),
            days=args.get("days"),
            ticker=args.get("ticker"),
            buy_only=args.get("buy_only", False),
        )
        # Format a readable summary alongside the raw data
        buy_count = len(data["buy_alerts"])
        coi_count = len(data["coi_alerts"])
        total = len(data["all_mentions"])
        summary_lines = [
            f"Trump Company Alert Scan — Last {data['lookback_hours']}h",
            f"Scanned at: {data['scanned_at']}",
            f"Total mentions: {total}  |  BUY alerts: {buy_count}  |  COI flags: {coi_count}",
            "",
        ]
        if data["buy_alerts"]:
            summary_lines.append("=== BUY ALERTS ===")
            for m in data["buy_alerts"]:
                coi = " ⚠️ COI" if m.get("conflict_of_interest") else ""
                summary_lines.append(
                    f"🚨 {m.get('company','?')} ({m.get('ticker','?')}){coi}"
                    f" via {m.get('source','?')}"
                    f"\n   \"{m.get('context_snippet','')[:120]}\""
                    f"\n   {m.get('source_url','')}"
                )
        if not data["all_mentions"]:
            summary_lines.append("No company mentions found in this window.")
        summary_lines.append(f"\n⚠️ {DISCLAIMER}")
        return "\n".join(summary_lines) + "\n\n---\nRaw JSON:\n" + json.dumps(data, indent=2, default=str)

    elif name == "get_trump_digest":
        data = digest(
            days=args.get("days", 30),
            no_prices=args.get("no_prices", False),
        )
        if "error" in data:
            return f"Digest failed: {data['error']}\n\n⚠️ {DISCLAIMER}"
        companies = data.get("company_mentions", {})
        buy_cos = [t for t, c in companies.items() if c.get("buy_count", 0) > 0]
        out = [
            f"Trump Market Alert Digest — Last {data.get('days', 30)} days",
            f"Scanned at: {data.get('scan_time', '')}",
            f"BUY alerts: {len(buy_cos)} companies  ({', '.join(buy_cos) or 'none'})",
            f"Total companies mentioned: {len(companies)}",
            f"Prices as of: {data.get('price_timestamp', 'N/A')}",
            "",
            f"⚠️ {DISCLAIMER}",
            "",
            "Raw JSON:",
            json.dumps(data, indent=2, default=str),
        ]
        return "\n".join(out)

    elif name == "get_trump_portfolio":
        data = portfolio()
        holdings = data.get("holdings", {})
        lines = [
            "Trump OGE-Disclosed Portfolio — Live Prices",
            f"Fetched at: {data.get('fetched_at', '')}",
            f"Note: {data.get('disclaimer', '')}",
            "",
        ]
        for ticker, info in holdings.items():
            price = info.get("price")
            chg = info.get("change_pct_today")
            price_str = f"${price:.2f}" if price else "N/A"
            chg_str = (f"  {'▲' if chg >= 0 else '▼'}{abs(chg):.1f}% today" if chg is not None else "")
            lines.append(
                f"  {ticker:<6} {price_str}{chg_str}"
                f"  |  {info['range']}  (bought {info['purchased']})"
            )
        lines.append(f"\n⚠️ {DISCLAIMER}")
        return "\n".join(lines)

    elif name == "check_conflict":
        ticker = args.get("ticker", "").upper()
        if ticker in TRUMP_HOLDINGS:
            h = TRUMP_HOLDINGS[ticker]
            return (
                f"⚠️ CONFLICT OF INTEREST — Trump holds {ticker} ({h['company']})\n"
                f"Disclosed range: {h['range']}\n"
                f"Approximate purchase date: {h['purchased']}\n"
                f"Source: OGE Form 278-T, certified May 8 2026\n\n"
                f"⚠️ {DISCLAIMER}"
            )
        else:
            return (
                f"✅ {ticker} is NOT in Trump's known OGE-disclosed holdings as of May 2026.\n"
                f"Note: OGE filings lag actual trades by weeks — this may not be current.\n\n"
                f"⚠️ {DISCLAIMER}"
            )

    else:
        return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
