"""Shared Streamlit helpers: page chrome, styling and session wiring."""

from __future__ import annotations

import html

import streamlit as st

from . import catalog, sections
from .settings import Settings, load_dotenv_if_present

CSS = """
<style>
  .block-container { padding-top: 2.2rem; max-width: 1200px; }
  .ta-hero { display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap; margin-bottom:.2rem; }
  .ta-hero h1 { margin:0; font-size:1.9rem; letter-spacing:-.02em; }
  .ta-hero span { color:var(--text-color); opacity:.55; font-size:.95rem; }
  .ta-pill { display:inline-block; padding:.12rem .55rem; border-radius:999px;
             font-size:.75rem; font-weight:600; border:1px solid currentColor; }
  .ta-row { display:flex; align-items:center; gap:.55rem; padding:.3rem 0;
            border-bottom:1px solid rgba(128,128,128,.18); font-size:.92rem; }
  .ta-row .ta-team { opacity:.5; font-size:.78rem; margin-left:auto; }
  .ta-dot { width:.55rem; height:.55rem; border-radius:50%; flex:none; }
  .ta-done .ta-dot { background:#0f8f5f; }
  .ta-running .ta-dot { background:#e0a12c; box-shadow:0 0 0 4px rgba(224,161,44,.18); }
  .ta-pending .ta-dot { background:rgba(128,128,128,.4); }
  .ta-pending { opacity:.55; }
  .ta-banner { border-radius:14px; padding:1.1rem 1.4rem; color:#fff; margin:.4rem 0 1rem; }
  .ta-banner .ta-label { opacity:.85; font-size:.8rem; text-transform:uppercase;
                         letter-spacing:.08em; }
  .ta-banner .ta-value { font-size:2rem; font-weight:700; line-height:1.15; }
  .ta-card { border:1px solid rgba(128,128,128,.25); border-radius:12px;
             padding:.8rem 1rem; margin-bottom:.6rem; }
  .ta-muted { opacity:.6; font-size:.85rem; }
</style>
"""


def page(title: str, icon: str = "📈") -> None:
    """Standard page setup — call once at the top of every page."""
    st.set_page_config(page_title=f"{title} · TradingAgents", page_icon=icon, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    load_dotenv_if_present()


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="ta-hero"><h1>{title}</h1><span>{subtitle}</span></div>',
        unsafe_allow_html=True,
    )


def get_settings() -> Settings:
    """Settings for this session, loaded from disk on first use."""
    if "settings" not in st.session_state:
        st.session_state.settings = Settings.load()
    return st.session_state.settings


def set_settings(settings: Settings) -> None:
    st.session_state.settings = settings


def framework_guard() -> bool:
    """Render an install panel when the framework is missing. True when ready."""
    module, detail = catalog.framework()
    if module is not None:
        return True
    st.error("The `tradingagents` package is not installed in this environment.")
    st.markdown(
        "Install it once, then reload this page:\n"
        "```bash\n"
        "pip install -r requirements.txt\n"
        "```\n"
        "`requirements.txt` pulls the framework straight from GitHub. "
        "The rest of the UI still works — you can set your provider, models and "
        "API keys on the **Settings** page in the meantime."
    )
    st.caption(f"Import error: {detail}")
    return False


def sidebar_status() -> Settings:
    """Provider / key summary shown on every page."""
    settings = get_settings()
    with st.sidebar:
        st.markdown("### Configuration")
        st.markdown(
            f"**Provider** · {catalog.provider_label(settings.llm_provider)}  \n"
            f"**Deep** · `{settings.deep_think_llm}`  \n"
            f"**Quick** · `{settings.quick_think_llm}`  \n"
            f"**Analysts** · {len(settings.analysts)} selected"
        )
        from .settings import missing_keys

        missing = missing_keys(settings)
        if missing:
            st.warning(f"Missing key: `{missing[0]}`", icon="🔑")
        else:
            st.success("API key detected", icon="🔑")
        st.caption("Change any of this on the Settings page.")
    return settings


def decision_banner(signal: str, ticker: str, trade_date: str) -> None:
    """Coloured banner carrying the run's 5-tier rating."""
    key = (signal or "review").strip().lower()
    color = sections.RATING_COLORS.get(key, sections.RATING_COLORS["review"])
    label = html.escape(signal or "REVIEW")
    note = "" if signal and key != "review" else " — no parseable rating, read the decision below"
    st.markdown(
        f'<div class="ta-banner" style="background:{color}">'
        f'<div class="ta-label">{html.escape(ticker)} · {html.escape(trade_date)}</div>'
        f'<div class="ta-value">{label}</div>'
        f'<div class="ta-label">Portfolio decision{note}</div></div>',
        unsafe_allow_html=True,
    )


def progress_rows(state: dict, analysts: list[str], running: bool) -> None:
    """The per-agent status list used during and after a run."""
    for section, status in sections.progress(state, analysts, running):
        icon = {"done": "✓", "running": "•", "pending": "○"}[status]
        st.markdown(
            f'<div class="ta-row ta-{status}"><span class="ta-dot"></span>'
            f'<span>{section.icon} {section.title}</span>'
            f'<span class="ta-team">{icon} {section.team}</span></div>',
            unsafe_allow_html=True,
        )


def render_reports(state: dict, analysts: list[str]) -> None:
    """Report cards grouped into one tab per team."""
    available = [s for s in sections.active_sections(analysts) if s.text(state)]
    if not available:
        st.info("No report content yet.")
        return

    teams = [t for t in sections.TEAM_ORDER if any(s.team == t for s in available)]
    for tab, team in zip(st.tabs(teams), teams, strict=True):
        with tab:
            for section in [s for s in available if s.team == team]:
                st.markdown(f"#### {section.icon} {section.title}")
                st.markdown(sections.demote_headings(section.text(state)))
                st.divider()
