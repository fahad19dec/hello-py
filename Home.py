"""TradingAgents control room — home page.

Run with:  streamlit run Home.py
"""

from __future__ import annotations

import html
from datetime import date

import streamlit as st

from ta_ui import catalog, ui
from ta_ui.settings import env_value, missing_keys

ui.page("Home", "📈")
settings = ui.sidebar_status()

ui.hero("TradingAgents", "a multi-agent LLM trading desk, driven from your browser")

st.markdown(
    "This is a front end for [TauricResearch/TradingAgents]"
    "(https://github.com/TauricResearch/TradingAgents). Pick a ticker and a date, "
    "and a team of analyst, researcher, trader and risk agents debates its way to a "
    "5-tier rating — with every report readable as it is written."
)

# --------------------------------------------------------------------- readiness
module, detail = catalog.framework()
key_env = catalog.api_key_env(settings.llm_provider)
key_ok = not missing_keys(settings)

checks = [
    (
        module is not None,
        "Framework installed",
        f"tradingagents {detail}".strip() if module is not None
        else "run <code>pip install -r requirements.txt</code>",
    ),
    (
        key_ok,
        "LLM credentials",
        f"<code>{key_env}</code> is set" if key_ok
        else (f"<code>{key_env}</code> is missing — add it on Settings"
              if key_env else "this provider needs no API key"),
    ),
    (
        bool(settings.analysts),
        "Analyst team",
        f"{len(settings.analysts)} analyst(s) selected",
    ),
]

st.markdown("### Setup")
for ok, label, note in checks:
    icon = "✅" if ok else "⚠️"
    st.markdown(
        f'<div class="ta-row"><span>{icon} <b>{label}</b></span>'
        f'<span class="ta-team">{note}</span></div>',
        unsafe_allow_html=True,
    )

if not env_value("ALPHA_VANTAGE_API_KEY") and "alpha_vantage" in settings.data_vendors.values():
    st.warning(
        "Alpha Vantage is selected as a data vendor but `ALPHA_VANTAGE_API_KEY` is not set.",
        icon="🔑",
    )

st.write("")
left, right = st.columns([2, 1], gap="large")

with left:
    st.markdown("### Start an analysis")
    with st.form("quick_start"):
        col_a, col_b = st.columns(2)
        ticker = col_a.text_input("Ticker", value="NVDA").strip().upper()
        trade_date = col_b.date_input("Trade date", value=date.today())
        go = st.form_submit_button("Open analysis →", type="primary", use_container_width=True)
    if go:
        st.session_state.prefill = {"ticker": ticker, "trade_date": trade_date}
        st.switch_page("pages/1_New_Analysis.py")

    st.markdown("### How a run flows")
    st.markdown(
        "1. **Analyst team** — market, sentiment, news and fundamentals agents pull data "
        "and each write a report.\n"
        "2. **Research team** — a bull and a bear researcher debate; a research manager rules.\n"
        "3. **Trader** — turns the ruling into a concrete plan.\n"
        "4. **Risk team** — aggressive, conservative and neutral analysts stress the plan.\n"
        "5. **Portfolio manager** — issues the final Buy / Overweight / Hold / Underweight / Sell."
    )

with right:
    st.markdown("### Recent runs")
    reports_root = settings.reports_dir()
    runs = sorted(
        (p for p in reports_root.glob("*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:6] if reports_root.exists() else []

    if not runs:
        st.caption("No saved runs yet. Completed analyses are written here.")
    else:
        for run_dir in runs:
            name = html.escape(run_dir.name)
            st.markdown(
                f'<div class="ta-card"><b>{name}</b><br>'
                f'<span class="ta-muted">{reports_root.name}/{name}</span></div>',
                unsafe_allow_html=True,
            )
    st.page_link("pages/2_Run_History.py", label="Browse all runs", icon="🗂️")
    st.page_link("pages/3_Settings.py", label="Provider, models and keys", icon="⚙️")

st.divider()
st.caption(
    "Research software, not investment advice. Every run costs real LLM tokens — "
    "the deep-thinking model does most of the spending."
)
