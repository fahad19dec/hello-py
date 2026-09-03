"""Runs the TradingAgents graph in a background thread and streams its output.

Streamlit reruns the page script constantly, so the analysis itself must not
live in the script thread. A run owns a worker thread that pushes events onto a
queue; the page drains the queue and repaints. That keeps a long analysis alive
across reruns and lets the page stay responsive while agents work.

The worker mirrors the sequence in ``cli/main.py``: resolve the instrument
identity, build the initial state, stream the graph, merge the streamed deltas,
then process the signal and write the report tree.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .sections import merge_chunks
from .settings import Settings

#: Cap on retained activity-log rows; a long run emits thousands of messages.
MAX_ACTIVITY = 400


class RunCancelled(Exception):
    """Raised inside the worker when the user asks to stop."""


@dataclass
class RunParams:
    ticker: str
    trade_date: str
    asset_type: str
    settings: Settings

    @property
    def analysts(self) -> list[str]:
        return list(self.settings.analysts)


@dataclass
class Activity:
    kind: str          # "System" | "Reasoning" | "Tool" | "Error"
    text: str
    at: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


def _classify(message: Any) -> tuple[str, str]:
    """Turn a LangChain message into a (kind, text) row for the activity log."""
    name = type(message).__name__
    content = getattr(message, "content", "")
    if isinstance(content, list):  # provider blocks (Anthropic, Gemini)
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        content = "\n".join(p for p in parts if p)
    text = str(content or "").strip()
    if name.startswith("Tool"):
        return "Tool", f"{getattr(message, 'name', 'tool')} → {text[:400]}"
    if name.startswith("Human"):
        return "System", text
    return "Reasoning", text


class AnalysisRun:
    """One analysis, executing on its own thread."""

    def __init__(self, params: RunParams):
        self.params = params
        self.state: dict = {}
        self.activity: list[Activity] = []
        self.status: str = "Queued"
        self.error: str | None = None
        self.signal: str = ""
        self.report_path: str | None = None
        self.started_at: float | None = None
        self.started_wall: datetime | None = None
        self.finished_at: float | None = None
        self.cancelled = False

        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_message_ids: set[str] = set()

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        if self._thread is not None:
            return
        self.started_at = time.monotonic()
        self.started_wall = datetime.now()
        self.status = "Starting"
        self._thread = threading.Thread(target=self._work, name="tradingagents-run", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Ask the worker to stop after the node it is currently running."""
        self._stop.set()
        self.status = "Stopping after the current step…"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def finished(self) -> bool:
        return self._thread is not None and not self._thread.is_alive()

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at

    # -------------------------------------------------------------------- drain
    def drain(self) -> bool:
        """Apply queued worker events. Returns True when something changed."""
        changed = False
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            changed = True
            if kind == "status":
                self.status = payload
            elif kind == "chunk":
                self.state.update(payload)
            elif kind == "activity":
                self.activity.append(payload)
                if len(self.activity) > MAX_ACTIVITY:
                    del self.activity[: len(self.activity) - MAX_ACTIVITY]
            elif kind == "done":
                self.state.update(payload.get("state", {}))
                self.signal = payload.get("signal", "")
                self.report_path = payload.get("report_path")
                self.status = payload.get("status", "Complete")
                self.finished_at = time.monotonic()
            elif kind == "error":
                self.error = payload
                self.status = "Failed"
                self.finished_at = time.monotonic()
            elif kind == "cancelled":
                self.cancelled = True
                self.status = "Stopped"
                self.finished_at = time.monotonic()
        return changed

    # ------------------------------------------------------------------- worker
    def _emit(self, kind: str, payload: Any) -> None:
        self._events.put((kind, payload))

    def _log(self, kind: str, text: str) -> None:
        if text and text.strip():
            self._emit("activity", Activity(kind, text.strip()))

    def _check_cancel(self) -> None:
        if self._stop.is_set():
            raise RunCancelled

    def _work(self) -> None:
        try:
            self._run_graph()
        except RunCancelled:
            self._emit("cancelled", True)
        except Exception as exc:  # surfaced in the UI, not swallowed
            detail = traceback.format_exc(limit=6)
            self._emit("error", f"{type(exc).__name__}: {exc}\n\n{detail}")

    def _run_graph(self) -> None:
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        params = self.params
        config = params.settings.to_config()

        self._emit("status", "Building the agent graph…")
        self._log(
            "System",
            f"Provider {config['llm_provider']} · "
            f"deep {config['deep_think_llm']} · quick {config['quick_think_llm']}",
        )
        graph = TradingAgentsGraph(
            selected_analysts=params.analysts,
            debug=False,
            config=config,
        )
        self._check_cancel()

        self._emit("status", f"Resolving {params.ticker}…")
        instrument_context = graph.resolve_instrument_context(params.ticker, params.asset_type)
        if instrument_context:
            self._log("System", instrument_context)

        init_state = graph.propagator.create_initial_state(
            params.ticker,
            params.trade_date,
            asset_type=params.asset_type,
            instrument_context=instrument_context,
        )
        args = graph.propagator.get_graph_args()

        thread_id = graph.begin_checkpoint(params.ticker, params.trade_date, params.asset_type)
        if thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = thread_id

        chunks: list[dict] = []
        try:
            self._emit("status", f"Analyzing {params.ticker} on {params.trade_date}…")
            for chunk in graph.graph.stream(graph.checkpoint_input(init_state), **args):
                self._check_cancel()
                self._record_messages(chunk)
                self._emit("chunk", {k: v for k, v in chunk.items() if k != "messages"})
                chunks.append(chunk)
            graph.clear_checkpoint_on_success(
                params.ticker, params.trade_date, params.asset_type
            )
        finally:
            graph.end_checkpoint()

        final_state = merge_chunks(chunks)
        self._emit("status", "Processing the final decision…")

        signal = ""
        decision = final_state.get("final_trade_decision", "")
        if decision:
            try:
                signal = graph.process_signal(decision)
            except Exception as exc:
                self._log("Error", f"Could not extract a rating: {exc}")

        report_path = None
        if params.settings.save_reports:
            try:
                report_path = str(graph.save_reports(final_state, params.ticker))
                self._log("System", f"Reports written to {report_path}")
            except Exception as exc:
                self._log("Error", f"Could not save reports: {exc}")

        # Record the decision so the framework can reflect on it next run.
        try:
            graph.memory_log.store_decision(
                ticker=params.ticker,
                trade_date=params.trade_date,
                final_trade_decision=decision,
            )
        except Exception as exc:
            self._log("Error", f"Could not update the memory log: {exc}")

        self._emit(
            "done",
            {
                "state": {k: v for k, v in final_state.items() if k != "messages"},
                "signal": signal,
                "report_path": report_path,
                "status": "Complete",
            },
        )

    def _record_messages(self, chunk: dict) -> None:
        """Log new messages and tool calls, deduplicated by message id."""
        for message in chunk.get("messages", []) or []:
            msg_id = getattr(message, "id", None)
            if msg_id is not None:
                if msg_id in self._seen_message_ids:
                    continue
                self._seen_message_ids.add(msg_id)

            kind, text = _classify(message)
            self._log(kind, text)

            for call in getattr(message, "tool_calls", None) or []:
                name = call["name"] if isinstance(call, dict) else getattr(call, "name", "tool")
                call_args = call["args"] if isinstance(call, dict) else getattr(call, "args", {})
                self._log("Tool", f"{name}({call_args})")
