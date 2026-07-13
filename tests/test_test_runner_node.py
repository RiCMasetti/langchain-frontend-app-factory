from pathlib import Path
from types import SimpleNamespace

from app.nodes.test_runner import build_test_runner_node
from app.sandboxing.manager import SandboxManager
from app.sandboxing.templates import get_template
from app.state import initial_state
from app.workspace import RunWorkspace


def _prepared_ws(tmp_path: Path) -> RunWorkspace:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    manager = SandboxManager(ws, get_template("react-vite"))
    manager.prepare("npm install", "npm run build", "npm test")
    return ws


def _fake_completed(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_test_runner_node_success_marks_accepted(tmp_path: Path, monkeypatch) -> None:
    ws = _prepared_ws(tmp_path)
    monkeypatch.setattr(
        "app.sandboxing.manager.subprocess.run", lambda *a, **kw: _fake_completed(0, stdout="ok")
    )

    node = build_test_runner_node(ws)
    state = initial_state("run-1", str(ws.root), "build a page", max_agent_iterations=5)
    update = node(state)

    assert update["status"] == "accepted"
    assert update["halt"] is True
    assert ws.read_json("reports/test-report.json")["status"] == "success"


def test_test_runner_node_failure_reports_and_retries(tmp_path: Path, monkeypatch) -> None:
    ws = _prepared_ws(tmp_path)

    calls = {"n": 0}

    def fake_run(command, **kwargs):
        calls["n"] += 1
        if "install" in command:
            return _fake_completed(0)
        if "build" in command:
            return _fake_completed(0)
        return _fake_completed(1, stderr="AssertionError: expected 2 got 3\n")

    monkeypatch.setattr("app.sandboxing.manager.subprocess.run", fake_run)

    node = build_test_runner_node(ws)
    state = initial_state("run-1", str(ws.root), "build a page", max_agent_iterations=5)
    update = node(state)

    assert update["status"] == "running"
    assert update["halt"] is False
    assert update["repeated_error_count"] == 1
    summary = ws.read_json("handoffs/testrunner-summary.json")
    assert summary["failed_step"] == "test"
    assert any("AssertionError" in line for line in summary["failures"])
