"""Full graph run with fake LLMs and a faked docker subprocess -- exercises the whole
planner -> sandbox -> app_writer -> test_writer -> test_runner -> finalize wiring
mechanically, without hitting a real model API or Docker daemon.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.config import ModelConfig, Settings
from app.run import run_once


class FakeChatModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if not self._responses:
            raise AssertionError("FakeChatModel ran out of canned responses")
        return self._responses.pop(0)


def _fake_completed(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_full_run_accepts_on_first_pass(tmp_path: Path, monkeypatch) -> None:
    planner_llm = FakeChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "file_writer_tool",
                        "args": {
                            "relative_path": "plans/app-plan.json",
                            "content": json.dumps(
                                {
                                    "framework": "react-vite",
                                    "install_cmd": "npm install",
                                    "build_cmd": "npm run build",
                                    "test_cmd": "npm test",
                                    "summary": "a pricing page",
                                }
                            ),
                        },
                        "id": "c1",
                    },
                    {
                        "name": "file_writer_tool",
                        "args": {
                            "relative_path": "handoffs/planner-summary.json",
                            "content": json.dumps({"summary": "a pricing page"}),
                        },
                        "id": "c2",
                    },
                ],
            ),
            AIMessage(content="Plan written."),
        ]
    )
    worker_llm = FakeChatModel(
        [
            # sandbox node
            AIMessage(
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
                        "id": "s1",
                    }
                ],
            ),
            AIMessage(content="Sandbox started."),
            # app_writer node
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "app_writer_tool",
                        "args": {"relative_path": "src/App.tsx", "content": "export default function App() {}"},
                        "id": "a1",
                    }
                ],
            ),
            AIMessage(content="App written."),
            # test_writer node
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "app_writer_tool",
                        "args": {"relative_path": "src/App.test.tsx", "content": "test('x', () => {})"},
                        "id": "t1",
                    },
                    {
                        "name": "file_writer_tool",
                        "args": {
                            "relative_path": "handoffs/testwriter-summary.json",
                            "content": json.dumps({"notes": "added 1 test", "test_files": ["src/App.test.tsx"]}),
                        },
                        "id": "t2",
                    },
                ],
            ),
            AIMessage(content="Tests written."),
        ]
    )

    def fake_init_chat_model(self, **kwargs):
        return planner_llm if self.role == "planner" else worker_llm

    monkeypatch.setattr(ModelConfig, "init_chat_model", fake_init_chat_model)
    monkeypatch.setattr("app.sandboxing.manager.subprocess.run", lambda *a, **kw: _fake_completed(0, stdout="ok"))

    settings = Settings(
        planner_model=ModelConfig(role="planner", provider="fake", model_name="fake-planner"),
        worker_model=ModelConfig(role="worker", provider="fake", model_name="fake-worker"),
        workspace_dir=tmp_path,
        max_agent_iterations=5,
        agent_call_timeout_seconds=900,
        checkpoint_backend="memory",
    )

    final_state = run_once("build me a pricing page", settings, sandbox_start=False)

    assert final_state["status"] == "accepted"
    run_dir = Path(final_state["run_dir"])
    assert (run_dir / "app" / "src" / "App.tsx").exists()
    assert (run_dir / "app" / "src" / "App.test.tsx").exists()
    assert json.loads((run_dir / "metrics" / "run-metrics.json").read_text())["status"] == "accepted"
    assert json.loads((run_dir / "reports" / "test-report.json").read_text())["status"] == "success"
