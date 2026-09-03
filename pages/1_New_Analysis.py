"""Configure, launch and watch a single analysis run."""

from __future__ import annotations

import html
import time
from datetime import date

import streamlit as st

from ta_ui import catalog, sections, ui
from ta_ui.engine import AnalysisRun, RunParams
from ta_ui.settings import apply_secrets, missing_keys

REFRESH_SECONDS = 1.0

ui.page("New Analysis", "📈")
settings = ui.sidebar_status()
ui.hero("New analysis", "one ticker, one date, one full agent debate")

run: AnalysisRun | None = st.session_state.get("run")
prefill = st.session_state.pop("prefill", {})


# ------------------------------------------------------------------ run controls
def start_run(ticker: str, trade_date: str, asset_type: str, chosen: list[str]) -> None:
    """Kick off a run with the current settings and the form's overrides."""
    run_settings = ui.get_settings()
    run_settings.analysts = chosen
    ui.set_settings(run_settings)
    apply_secrets(st.session_state.get("secrets", {}))

    params = RunParams(
        ticker=ticker,
        trade_date=trade_date,
        asset_type=asset_type,
        settings=run_settings,
    )
    new_run = AnalysisRun(params)
    st.session_state.run = new_run
    new_run.start()


def render_run(current: AnalysisRun) -> None:
    """Draw the live (or finished) view of a run."""
    params = current.params
    header = st.container()
    with header:
        cols = st.columns([1.2, 1, 1, 1])
        cols[0].metric("Ticker", params.ticker)
        cols[1].metric("Trade date", params.trade_date)
        cols[2].metric("Elapsed", f"{current.elapsed:.0f}s")
        cols[3].metric(
            "Progress",
            f"{sections.completion(current.state, params.analysts) * 100:.0f}%",
        )

    if current.error:
        st.error("The run failed.")
        st.code(current.error, language="text")
    elif current.cancelled:
        st.warning("Run stopped. Partial reports below.")
    elif current.running:
        st.info(current.status, icon="⏳")
    elif current.finished:
        st.success(f"{current.status} in {current.elapsed:.0f}s", icon="✅")

    if current.finished and not current.error:
        ui.decision_banner(current.signal, params.ticker, params.trade_date)

    progress_col, activity_col = st.columns([1, 1.25], gap="large")
    with progress_col:
        st.markdown("#### Agents")
        ui.progress_rows(current.state, params.analysts, current.running)
    with activity_col:
        st.markdown("#### Activity")
        recent = current.activity[-40:][::-1]
        if not recent:
            st.caption("Waiting for the first agent to report in…")
        for item in recent:
            body = item.text if len(item.text) <= 600 else item.text[:600] + " …"
            # Agent output is model text: escape it rather than render it as HTML.
            st.markdown(
                f'<div class="ta-row"><span class="ta-muted">{item.at}</span>'
                f'<span><b>{html.escape(item.kind)}</b> · {html.escape(body)}</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown("#### Reports")
    ui.render_reports(current.state, params.analysts)

    if current.finished and any(
        s.text(current.state) for s in sections.active_sections(params.analysts)
    ):
        markdown = sections.to_markdown(
            current.state, params.ticker, params.trade_date, current.signal
        )
        st.download_button(
            "Download the full report (markdown)",
            data=markdown,
            file_name=f"{params.ticker}_{params.trade_date}_tradingagents.md",
            mime="text/markdown",
        )
        if current.report_path:
            st.caption(f"Also saved to `{current.report_path}`")


# ------------------------------------------------------------------------ layout
if run is not None and run.running:
    top = st.columns([1, 5])
    top[0].button("Stop run", on_click=run.cancel, use_container_width=True)
    if run.started_wall:
        top[1].caption(f"Started {run.started_wall.strftime('%H:%M:%S')}")

    # Poll the worker while it streams. Any widget interaction (the Stop
    # button above) reruns the script and breaks out of this loop.
    slot = st.empty()
    while run.running:
        run.drain()
        with slot.container():
            render_run(run)
        time.sleep(REFRESH_SECONDS)
    run.drain()
    st.rerun()  # redraw with the finished-run controls

elif run is not None and run.finished:
    top = st.columns([1, 5])
    if top[0].button("New run", type="primary", use_container_width=True):
        del st.session_state["run"]
        st.rerun()
    if run.started_wall:
        top[1].caption(f"Started {run.started_wall.strftime('%H:%M:%S')}")
    render_run(run)

else:
    if not ui.framework_guard():
        st.stop()

    blocked = missing_keys(settings)
    if blocked:
        st.warning(
            f"`{blocked[0]}` is not set, so the run would fail on the first model call. "
            "Add it on the Settings page.",
            icon="🔑",
        )

    with st.form("analysis"):
        col_a, col_b, col_c = st.columns([1.2, 1, 1])
        ticker = col_a.text_input(
            "Ticker", value=prefill.get("ticker", "NVDA"),
            help="Any symbol your data vendor knows: NVDA, 0700.HK, BTC-USD, …",
        ).strip().upper()
        trade_date = col_b.date_input(
            "Trade date",
            value=prefill.get("trade_date", date.today()),
            help="Agents analyse the market as of this date.",
        )
        asset_type = col_c.selectbox(
            "Asset type",
            options=[key for key, _ in catalog.ASSET_TYPES],
            format_func=lambda k: dict(catalog.ASSET_TYPES)[k],
        )

        chosen = st.multiselect(
            "Analyst team",
            options=[key for key, _, _ in catalog.ANALYSTS],
            default=settings.analysts,
            format_func=lambda k: next(
                label for key, label, _ in catalog.ANALYSTS if key == k
            ),
            help="Fewer analysts means a cheaper, faster run.",
        )

        with st.expander("Depth and cost"):
            depth_a, depth_b = st.columns(2)
            settings.max_debate_rounds = depth_a.slider(
                "Research debate rounds", 1, 5, settings.max_debate_rounds,
                help="Bull/bear exchanges before the research manager rules.",
            )
            settings.max_risk_discuss_rounds = depth_b.slider(
                "Risk discussion rounds", 1, 5, settings.max_risk_discuss_rounds,
            )
            settings.save_reports = st.checkbox(
                "Save markdown reports to disk when the run finishes",
                value=settings.save_reports,
            )

        submitted = st.form_submit_button(
            "Run analysis", type="primary", use_container_width=True
        )

    if submitted:
        if not ticker:
            st.error("Enter a ticker first.")
        elif not chosen:
            st.error("Select at least one analyst.")
        else:
            ui.set_settings(settings)
            start_run(ticker, trade_date.strftime("%Y-%m-%d"), asset_type, chosen)
            st.rerun()

    st.caption(
        "A full four-analyst run makes dozens of model calls and can take several "
        "minutes. Keep this tab open — the run lives in the server process."
    )
