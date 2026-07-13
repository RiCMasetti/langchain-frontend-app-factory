import json
from pathlib import Path

from langchain_core.messages import AIMessage

from app.config import ModelConfig, Settings
from app.nodes.app_writer import build_app_writer_node
from app.nodes.planner import build_planner_node
from app.nodes.sandbox import build_sandbox_node
from app.nodes.test_writer import build_test_writer_node
from app.reporting import TokenUsageRecorder
from app.sandboxing.manager import SandboxManager
from app.sandboxing.templates import get_template
from app.state import initial_state
from app.workspace import RunWorkspace


class FakeChatModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.invocations: list[list] = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.invocations.append(list(messages))
        if not self._responses:
            raise AssertionError("FakeChatModel ran out of canned responses")
        return self._responses.pop(0)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        planner_model=ModelConfig(role="planner", provider="fake", model_name="fake-planner"),
        worker_model=ModelConfig(role="worker", provider="fake", model_name="fake-worker"),
        workspace_dir=tmp_path,
        max_agent_iterations=5,
        agent_call_timeout_seconds=900,
        checkpoint_backend="memory",
    )


def _patch_model(monkeypatch, model_config_attr: str, fake_llm: FakeChatModel) -> None:
    monkeypatch.setattr(ModelConfig, "init_chat_model", lambda self, **kw: fake_llm)


def test_planner_node_writes_valid_plan_and_returns_state_update(tmp_path: Path, monkeypatch) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    app_plan_json = json.dumps(
        {
            "framework": "react-vite",
            "install_cmd": "npm install",
            "build_cmd": "npm run build",
            "test_cmd": "npm test",
            "summary": "a pricing page",
        }
    )
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {"name": "file_writer_tool", "args": {"relative_path": "plans/app-plan.json", "content": app_plan_json}, "id": "c1"},
            {
                "name": "file_writer_tool",
                "args": {"relative_path": "handoffs/planner-summary.json", "content": json.dumps({"summary": "a pricing page"})},
                "id": "c2",
            },
        ],
    )
    final_message = AIMessage(content="Plan written.")
    fake_llm = FakeChatModel([tool_call_message, final_message])
    _patch_model(monkeypatch, "planner_model", fake_llm)

    node = build_planner_node(ws, settings, recorder)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    update = node(state)

    assert update["app_plan"]["framework"] == "react-vite"
    assert update["planner_summary"]["summary"] == "a pricing page"
    assert ws.exists("plans/app-plan.json")


def test_planner_node_halts_after_repeated_invalid_plan(tmp_path: Path, monkeypatch) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    bad_plan_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "file_writer_tool",
                "args": {"relative_path": "plans/app-plan.json", "content": json.dumps({"framework": "react-vite"})},
                "id": "c1",
            }
        ],
    )
    final_message = AIMessage(content="ok")
    # 3 attempts, each does a tool-call round + a final-text round
    fake_llm = FakeChatModel([bad_plan_message, final_message] * 3)
    _patch_model(monkeypatch, "planner_model", fake_llm)

    node = build_planner_node(ws, settings, recorder)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    update = node(state)

    assert update["halt"] is True
    assert update["status"] == "failed"


def test_sandbox_node_prepare_only_succeeds(tmp_path: Path, monkeypatch) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sandbox_prepare_tool",
                "args": {
                    "framework": "react-vite",
                    "install_cmd": "npm install",
                    "build_cmd": "npm run build",
                    "test_cmd": "npm test",
                },
                "id": "c1",
            }
        ],
    )
    final_message = AIMessage(content="Sandbox prepared.")
    fake_llm = FakeChatModel([tool_call_message, final_message])
    _patch_model(monkeypatch, "worker_model", fake_llm)

    node = build_sandbox_node(ws, settings, recorder, start=False)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    state["app_plan"] = {
        "framework": "react-vite", "install_cmd": "npm install", "build_cmd": "npm run build", "test_cmd": "npm test",
    }
    update = node(state)

    assert update["sandbox_summary"]["status"] == "success"
    assert ws.exists("sandbox/Dockerfile")


def test_app_writer_node_writes_app_files(tmp_path: Path, monkeypatch) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    manager = SandboxManager(ws, get_template("react-vite"))
    manager.prepare("npm install", "npm run build", "npm test")

    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {"name": "app_writer_tool", "args": {"relative_path": "src/App.tsx", "content": "export default function App() {}"}, "id": "c1"},
        ],
    )
    final_message = AIMessage(content="App written.")
    fake_llm = FakeChatModel([tool_call_message, final_message])
    _patch_model(monkeypatch, "worker_model", fake_llm)

    node = build_app_writer_node(ws, settings, recorder)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    state["app_plan"] = {"framework": "react-vite", "install_cmd": "npm install", "build_cmd": "npm run build", "test_cmd": "npm test"}
    update = node(state)

    assert (ws.root / "app" / "src" / "App.tsx").exists()
    assert "src/App.tsx" in update["appwriter_summary"]["app_files"]


def test_test_writer_node_writes_test_files_and_summary(tmp_path: Path, monkeypatch) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    manager = SandboxManager(ws, get_template("react-vite"))
    manager.prepare("npm install", "npm run build", "npm test")

    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {"name": "app_writer_tool", "args": {"relative_path": "src/App.test.tsx", "content": "test('x', () => {})"}, "id": "c1"},
            {
                "name": "file_writer_tool",
                "args": {"relative_path": "handoffs/testwriter-summary.json", "content": json.dumps({"notes": "added 1 test", "test_files": ["src/App.test.tsx"]})},
                "id": "c2",
            },
        ],
    )
    final_message = AIMessage(content="Tests written.")
    fake_llm = FakeChatModel([tool_call_message, final_message])
    _patch_model(monkeypatch, "worker_model", fake_llm)

    node = build_test_writer_node(ws, settings, recorder)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    update = node(state)

    assert (ws.root / "app" / "src" / "App.test.tsx").exists()
    assert update["testwriter_summary"]["test_files"] == ["src/App.test.tsx"]


def test_test_writer_node_can_run_test_but_not_install_or_build(tmp_path: Path, monkeypatch) -> None:
    """Test Writer gets the write-run-fix self-check loop (only the "test" slot) --
    it must not be able to reach "install"/"build", those stay App Writer's job."""

    from types import SimpleNamespace

    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    manager = SandboxManager(ws, get_template("react-vite"))
    manager.prepare("npm install", "npm run build", "npm test")

    monkeypatch.setattr(
        "app.sandboxing.manager.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )

    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    run_test_call = AIMessage(
        content="",
        tool_calls=[{"name": "sandbox_command_tool", "args": {"command_name": "test"}, "id": "c1"}],
    )
    install_call = AIMessage(
        content="",
        tool_calls=[{"name": "sandbox_command_tool", "args": {"command_name": "install"}, "id": "c2"}],
    )
    final_message = AIMessage(content="Verified tests pass.")
    fake_llm = FakeChatModel([run_test_call, install_call, final_message])
    _patch_model(monkeypatch, "worker_model", fake_llm)

    node = build_test_writer_node(ws, settings, recorder)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    node(state)

    tool_messages = [m for m in fake_llm.invocations[-1] if m.type == "tool"]
    assert len(tool_messages) == 2
    test_result = json.loads(tool_messages[0].content)
    install_result = json.loads(tool_messages[1].content)

    assert test_result["status"] == "success"
    assert install_result == {
        "status": "error", "reason": "not_allowed", "requested": "install", "allowed": ["test"],
    }


def test_test_writer_node_receives_prior_test_runner_failures_on_retry(tmp_path: Path, monkeypatch) -> None:
    """Regression test: Test Writer used to run blind on retries -- only App Writer saw
    Test Runner's feedback, so a failure caused by the test itself (not the app) could
    never be fixed and would repeat until the circuit breaker gave up."""

    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    manager = SandboxManager(ws, get_template("react-vite"))
    manager.prepare("npm install", "npm run build", "npm test")

    settings = _settings(tmp_path)
    recorder = TokenUsageRecorder(ws)

    final_message = AIMessage(content="Fixed the query.")
    fake_llm = FakeChatModel([final_message])
    _patch_model(monkeypatch, "worker_model", fake_llm)

    node = build_test_writer_node(ws, settings, recorder)
    state = initial_state("run-1", str(ws.root), "build a pricing page", max_agent_iterations=5)
    state["testwriter_summary"] = {"notes": "wrote price assertions"}
    state["testrunner_summary"] = {
        "status": "error",
        "failed_step": "test",
        "failures": ["Unable to find an element with the text: $29"],
    }

    node(state)

    sent_prompt = fake_llm.invocations[0][1].content  # [SystemMessage, HumanMessage]
    assert "Unable to find an element with the text: $29" in sent_prompt
    assert "wrote price assertions" in sent_prompt
