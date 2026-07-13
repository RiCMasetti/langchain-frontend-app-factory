"""Finalize node: writes the run's final metrics/report and tears down its sandbox container."""

from __future__ import annotations

from typing import Callable

from app.reporting import RunMetricsWriter, TokenUsageRecorder
from app.sandboxing.manager import SandboxManager
from app.state import FactoryState
from app.workspace import RunWorkspace


def build_finalize_node(
    run_ws: RunWorkspace, metrics_writer: RunMetricsWriter, recorder: TokenUsageRecorder
) -> Callable[[FactoryState], dict]:
    def finalize_node(state: FactoryState) -> dict:
        if not state.get("status") or state["status"] == "running":
            state = {**state, "status": "failed", "halt_reason": state.get("halt_reason") or "unknown halt"}

        metrics_writer.finalize(state, recorder)

        if run_ws.exists("handoffs/sandbox-summary.json"):
            try:
                manager = SandboxManager.load(run_ws)
                manager.stop()
            except Exception:  # noqa: BLE001 - teardown must never block finalization
                pass

        return {"current_node": "finalize", "last_completed_node": "finalize"}

    return finalize_node
