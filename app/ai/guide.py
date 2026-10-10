"""Where things are in the app and how to do them, for the AI Lab's `app_guide` tool (0.43.1).

The user asks the chat for things they do not know how to do; the agent reads this to
answer with the right page and steps, or to propose the change itself (`propose`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TOPICS: dict[str, str] = {
    "pages": (
        "Pages in the side bar: Dashboard (account, today, open risk), Market (quotes, "
        "spreads, sessions), Signals (every signal with its checks and trace), Positions & "
        "Trades (open bot trades, the operating mode, close buttons), Analytics, Journal "
        "(notes and reports), Backtest, Model, AI Lab (this chat, the Signal desk, order "
        "cards), Strategies (on/off, parameters, filters, the Go-Live checklist), Risk (the "
        "account's limits), Logs, Health (connection, soak report, Demo test) and Settings "
        "(tabs: Account & connection, Data & cloud sync, Notifications, Telegram channels, "
        "Updates). Simple mode shows one Home page instead."
    ),
    "modes": (
        "Operating modes (Positions & Trades page): Analysis-only places no orders; Paper "
        "(the default) fills orders in the app only; Semi-auto sends real orders, each after "
        "your approval; Auto sends without asking, only after typing AUTO and, on a REAL "
        "account, the Go-Live approval of every strategy that is on. The AI never changes "
        "the mode."
    ),
    "signal_desk": (
        "Signal desk: paste a signal (Persian or English) into this chat, e.g. 'XAUUSD buy "
        "4190 SL 4180 TP 4205 4220'. The AI only fills an order card (symbol, side, market, "
        "limit or stop, entry, SL, TPs); the app sizes it and runs every risk check; the "
        "order goes only after you hold the card's button. Quick card or full analysis."
    ),
    "channels": (
        "Telegram channels (Settings > Telegram channels): log in with your own account "
        "(needs the VPN as a system proxy), name the Telegram folder, tick the channels to "
        "read. A channel starts in Paper trial: signals are counted and followed as shadow "
        "trades, no order card. Set it to Live with a budget (money it may use) and each "
        "signal becomes an order card in the AI Lab, sized with the budget's risk percent, "
        "still waiting for your hold. Daily loss and drawdown stops per channel; the "
        "account's limits apply on top. The chat can do it: 'give gg 100 dollars and set "
        "it to Live' shows a card to hold. Follow-ups (close, break-even, new SL) of a "
        "signal you took show a card too."
    ),
    "strategies": (
        "Strategies page: turn each strategy on or off, change its parameters and the "
        "signal filters, and run the Go-Live checklist (approval per strategy before Auto "
        "on a REAL account). Test a change on the Backtest page first. The chat can propose "
        "on/off, a parameter or a filter (only in Paper or Analysis-only mode)."
    ),
    "risk": (
        "Risk page: risk per trade, daily loss, drawdown, open risk and open trades limits. "
        "Every order passes these checks; a hit limit pauses new trades. Only you change "
        "them on the Risk page; the AI can read them (risk_settings) and explain."
    ),
    "positions": (
        "Positions & Trades page: open bot trades with close buttons; the bot only manages "
        "its own trades (its magic numbers), never your manual ones. The chat can propose "
        "closing a bot trade or a tighter stop loss (a card to hold); the kill switch "
        "closes everything and stops new entries."
    ),
    "backtest": (
        "Backtest page: pick a symbol, dates and the strategies; it uses the MT5 history "
        "(set MT5 Tools > Options > Charts > Max bars to Unlimited for long tests). The AI "
        "Lab can compare a suggestion with your current settings on the same history."
    ),
    "notifications": (
        "Settings > Notifications: the Telegram bot (token and chat id) sends notices and "
        "reports. 'WinError 10060' or 'getaddrinfo failed' means api.telegram.org is not "
        "reachable: turn on the VPN as a system proxy. The bot is separate from the "
        "channel reader."
    ),
    "logs": (
        "Logs page: every line with filters, a trace timeline per signal and export. The "
        "files are in the profile's logs folder; the chat reads them with logs and "
        "log_summary."
    ),
    "updates": (
        "Settings > Updates: the app updates itself from GitHub Releases (Velopack); a "
        "small delta package each time."
    ),
}


def guide(args: Mapping[str, Any]) -> str:
    """One topic, or the list of topics with the pages overview."""
    topic = str(args.get("topic") or "").strip().lower().replace(" ", "_")
    if topic in TOPICS:
        return TOPICS[topic]
    found = [name for name in TOPICS if topic and topic in TOPICS[name].lower()]
    if found:
        return "\n\n".join(f"{name}: {TOPICS[name]}" for name in found[:3])
    return TOPICS["pages"] + "\n\nTopics: " + ", ".join(TOPICS)
