from pathlib import Path
from types import SimpleNamespace


from app.config import ModelConfig
from app.reporting import ErrorReporter, NodeTimer, RunMetricsWriter, TokenUsageRecorder, instrumented_node
from app.state import initial_state
from app.workspace import RunWorkspace


def _ws(tmp_path: Path) -> RunWorkspace:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    return ws


def test_token_usage_recorder_flushes_after_every_record(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    recorder = TokenUsageRecorder(ws)
    model = ModelConfig(role="planner", provider="anthropic", model_name="claude-sonnet-4-6")
    ai_message = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 50})

    recorder.record_from_message("planner", 1, model, ai_message)

    payload = ws.read_json("reports/token-usage-report.json")
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["prompt_tokens"] == 100
    assert payload["entries"][0]["completion_tokens"] == 50
    assert payload["totals"]["total_tokens"] == 150


def test_token_usage_recorder_handles_missing_usage_metadata(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    recorder = TokenUsageRecorder(ws)
    model = ModelConfig(role="planner", provider="anthropic", model_name="claude-sonnet-4-6")

    recorder.record_from_message("planner", 1, model, None)

    payload = ws.read_json("reports/token-usage-report.json")
    assert payload["entries"][0]["source"] == "no_usage_metadata"
    assert payload["entries"][0]["total_tokens"] == 0


def test_error_reporter_writes_one_file_per_failure(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    reporter = ErrorReporter(ws)
    from app.reporting import ErrorReport

    path = reporter.write(
        ErrorReport(
            node="app_writer",
            iteration=2,
            error_type="ValueError",
            error_signature="ValueError:bad plan",
            message="bad plan",
            traceback_excerpt="...",
        )
    )
    assert ws.exists(path)
    written = ws.read_json(path)
    assert written["node"] == "app_writer"
    assert written["iteration"] == 2


def test_node_timer_records_duration_and_status(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    timer = NodeTimer(ws)
    timer.record("planner", 1, started_at=0.0, ended_at=0.5, status="success")
    records = ws.read_json("metrics/node-timings.json")
    assert records[0]["node"] == "planner"
    assert records[0]["status"] == "success"
    assert records[0]["duration_ms"] == 500.0


def test_instrumented_node_success_path_snapshots_merged_state(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    error_reporter = ErrorReporter(ws)
    timer = NodeTimer(ws)
    metrics_writer = RunMetricsWriter(ws)

    @instrumented_node("planner", error_reporter, timer, metrics_writer)
    def planner_node(state):
        return {"planner_summary": {"ok": True}, "current_node": "planner"}

    state = initial_state("run-1", str(ws.root), "build me a page", max_agent_iterations=5)
    result = planner_node(state)

    assert result["planner_summary"] == {"ok": True}
    snapshot = ws.read_json("metrics/workflow-state.json")
    assert snapshot["planner_summary"] == {"ok": True}
    timings = ws.read_json("metrics/node-timings.json")
    assert timings[0]["status"] == "success"


def test_instrumented_node_catches_exception_and_halts(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    error_reporter = ErrorReporter(ws)
    timer = NodeTimer(ws)
    metrics_writer = RunMetricsWriter(ws)

    @instrumented_node("app_writer", error_reporter, timer, metrics_writer)
    def app_writer_node(state):
        raise RuntimeError("model call timed out")

    state = initial_state("run-1", str(ws.root), "build me a page", max_agent_iterations=5)
    result = app_writer_node(state)

    assert result["halt"] is True
    assert result["status"] == "agent_failed"
    assert "app_writer failed" in result["halt_reason"]

    error_files = [p for p in ws.list_files(base=ws.root / "reports" / "errors") ]
    assert len(error_files) == 1
    timings = ws.read_json("metrics/node-timings.json")
    assert timings[0]["status"] == "error"


def test_run_metrics_writer_finalize_writes_json_reports(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    recorder = TokenUsageRecorder(ws)
    model = ModelConfig(role="planner", provider="anthropic", model_name="claude-sonnet-4-6")
    recorder.record_from_message("planner", 1, model, SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}))
    writer = RunMetricsWriter(ws)

    state = initial_state("run-1", str(ws.root), "build me a page", max_agent_iterations=5)
    state["status"] = "accepted"

    metrics = writer.finalize(state, recorder)

    assert metrics["status"] == "accepted"
    assert metrics["token_totals"]["total_tokens"] == 15
    assert ws.read_json("metrics/run-metrics.json") == metrics
    assert ws.read_json("reports/final-report.json") == metrics
