from pathlib import Path

from app.nodes.finalize import build_finalize_node
from app.reporting import RunMetricsWriter, TokenUsageRecorder
from app.state import initial_state
from app.workspace import RunWorkspace


def test_finalize_node_writes_metrics_without_sandbox(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    recorder = TokenUsageRecorder(ws)
    metrics_writer = RunMetricsWriter(ws)

    node = build_finalize_node(ws, metrics_writer, recorder)
    state = initial_state("run-1", str(ws.root), "build a page", max_agent_iterations=5)
    state["status"] = "accepted"

    update = node(state)

    assert update["current_node"] == "finalize"
    metrics = ws.read_json("metrics/run-metrics.json")
    assert metrics["status"] == "accepted"


def test_finalize_node_defaults_unset_status_to_failed(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    recorder = TokenUsageRecorder(ws)
    metrics_writer = RunMetricsWriter(ws)

    node = build_finalize_node(ws, metrics_writer, recorder)
    state = initial_state("run-1", str(ws.root), "build a page", max_agent_iterations=5)
    state["status"] = "running"

    node(state)

    metrics = ws.read_json("metrics/run-metrics.json")
    assert metrics["status"] == "failed"
