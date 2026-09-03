"""Browse the markdown reports written by finished runs."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from ta_ui import ui

ui.page("Run History", "🗂️")
settings = ui.sidebar_status()
ui.hero("Run history", "every analysis this machine has saved")

reports_root = settings.reports_dir()

st.caption(f"Reading `{reports_root}` — change the results directory on the Settings page.")

runs = (
    sorted(
        (p for p in reports_root.glob("*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if reports_root.exists()
    else []
)

if not runs:
    st.info(
        "No saved runs yet. Finish an analysis with **Save markdown reports** enabled "
        "and it will show up here."
    )
    st.page_link("pages/1_New_Analysis.py", label="Start an analysis", icon="📈")
    st.stop()

labels = [
    f"{p.name}  ·  {datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}"
    for p in runs
]
choice = st.selectbox("Run", options=range(len(runs)), format_func=lambda i: labels[i])
run_dir = runs[choice]

complete = run_dir / "complete_report.md"
parts = sorted(p for p in run_dir.rglob("*.md") if p.name != "complete_report.md")

tab_full, tab_parts, tab_files = st.tabs(["Complete report", "By agent", "Files"])

with tab_full:
    if complete.exists():
        text = complete.read_text(encoding="utf-8")
        st.download_button(
            "Download markdown",
            data=text,
            file_name=f"{run_dir.name}.md",
            mime="text/markdown",
        )
        st.markdown(text)
    else:
        st.warning("This run has no `complete_report.md`.")

with tab_parts:
    if not parts:
        st.caption("No per-agent files in this run.")
    for part in parts:
        with st.expander(f"{part.parent.name} / {part.stem}"):
            st.markdown(part.read_text(encoding="utf-8"))

with tab_files:
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            st.markdown(
                f"- `{path.relative_to(run_dir)}` · {size / 1024:.1f} KB"
            )

st.divider()
if st.button("Delete this run", help=f"Removes {run_dir}"):
    st.session_state.pending_delete = str(run_dir)

pending = st.session_state.get("pending_delete")
if pending == str(run_dir):
    st.warning(f"Delete `{run_dir}` and everything in it?")
    confirm, cancel = st.columns([1, 5])
    if confirm.button("Yes, delete", type="primary"):
        import shutil

        shutil.rmtree(run_dir, ignore_errors=True)
        del st.session_state["pending_delete"]
        st.rerun()
    if cancel.button("Cancel"):
        del st.session_state["pending_delete"]
        st.rerun()
