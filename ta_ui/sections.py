"""Report layout: how a run's state maps onto the screen and onto markdown."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

#: Colour per 5-tier rating, used for the decision banner.
RATING_COLORS = {
    "buy": "#0f8f5f",
    "overweight": "#3f9d70",
    "hold": "#9a7b25",
    "underweight": "#c2662d",
    "sell": "#c0392b",
    "review": "#6b7280",
}


def _debate(state: dict, key: str) -> str:
    return (state.get("investment_debate_state") or {}).get(key, "") or ""


def _risk(state: dict, key: str) -> str:
    return (state.get("risk_debate_state") or {}).get(key, "") or ""


@dataclass(frozen=True)
class Section:
    """One report card: where its text comes from and who produced it."""

    key: str
    title: str
    team: str
    icon: str
    getter: Callable[[dict], str]
    #: analyst key this section belongs to, when it is optional
    analyst: str | None = None

    def text(self, state: dict) -> str:
        return (self.getter(state) or "").strip()


SECTIONS: list[Section] = [
    Section("market_report", "Market Analyst", "Analyst Team", "📈",
            lambda s: s.get("market_report", ""), analyst="market"),
    Section("sentiment_report", "Sentiment Analyst", "Analyst Team", "💬",
            lambda s: s.get("sentiment_report", ""), analyst="social"),
    Section("news_report", "News Analyst", "Analyst Team", "📰",
            lambda s: s.get("news_report", ""), analyst="news"),
    Section("fundamentals_report", "Fundamentals Analyst", "Analyst Team", "📊",
            lambda s: s.get("fundamentals_report", ""), analyst="fundamentals"),
    Section("bull_history", "Bull Researcher", "Research Team", "🐂",
            lambda s: _debate(s, "bull_history")),
    Section("bear_history", "Bear Researcher", "Research Team", "🐻",
            lambda s: _debate(s, "bear_history")),
    Section("investment_plan", "Research Manager", "Research Team", "🧭",
            lambda s: _debate(s, "judge_decision") or s.get("investment_plan", "")),
    Section("trader_investment_plan", "Trader", "Trading Team", "💼",
            lambda s: s.get("trader_investment_plan", "")),
    Section("aggressive_history", "Aggressive Analyst", "Risk Team", "🔥",
            lambda s: _risk(s, "aggressive_history")),
    Section("conservative_history", "Conservative Analyst", "Risk Team", "🛡️",
            lambda s: _risk(s, "conservative_history")),
    Section("neutral_history", "Neutral Analyst", "Risk Team", "⚖️",
            lambda s: _risk(s, "neutral_history")),
    Section("final_trade_decision", "Portfolio Manager", "Decision", "🏁",
            lambda s: s.get("final_trade_decision", "")),
]

TEAM_ORDER = ["Analyst Team", "Research Team", "Trading Team", "Risk Team", "Decision"]


def active_sections(selected_analysts: list[str]) -> list[Section]:
    """Sections for a run, dropping analysts the user did not select."""
    chosen = set(selected_analysts)
    return [s for s in SECTIONS if s.analyst is None or s.analyst in chosen]


def progress(state: dict, selected_analysts: list[str], running: bool) -> list[tuple[Section, str]]:
    """Per-agent status: ``done``, ``running`` or ``pending``.

    The graph runs its stages in order, so the first stage without output is
    the one currently working — that is how the CLI's live panel reads too.
    """
    rows: list[tuple[Section, str]] = []
    first_pending = True
    for section in active_sections(selected_analysts):
        if section.text(state):
            rows.append((section, "done"))
        elif first_pending:
            rows.append((section, "running" if running else "pending"))
            first_pending = False
        else:
            rows.append((section, "pending"))
    return rows


def completion(state: dict, selected_analysts: list[str]) -> float:
    sections = active_sections(selected_analysts)
    if not sections:
        return 0.0
    done = sum(1 for s in sections if s.text(state))
    return done / len(sections)


def merge_chunks(chunks: list[dict]) -> dict:
    """Merge streamed per-node deltas into one final state, as the CLI does."""
    final: dict = {}
    for chunk in chunks:
        final.update(chunk)
    return final


def to_markdown(state: dict, ticker: str, trade_date: str, signal: str = "") -> str:
    """Render a full run as a single markdown document for download."""
    lines = [
        f"# TradingAgents report — {ticker}",
        "",
        f"- **Trade date:** {trade_date}",
        f"- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if signal:
        lines.append(f"- **Decision:** {signal}")
    lines.append("")

    for team in TEAM_ORDER:
        team_sections = [s for s in SECTIONS if s.team == team and s.text(state)]
        if not team_sections:
            continue
        lines += [f"## {team}", ""]
        for section in team_sections:
            lines += [f"### {section.title}", "", section.text(state), ""]
    return "\n".join(lines)


_HEADING = re.compile(r"^(#{1,4})\s+", re.MULTILINE)


def demote_headings(text: str) -> str:
    """Push model-written H1–H4 down to H5 so report cards don't shout.

    Agents write their reports as standalone documents with top-level titles;
    inside a card those compete with the page's own headings.
    """
    return _HEADING.sub("##### ", text)
