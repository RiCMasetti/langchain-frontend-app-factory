import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent_loop import AgentLoopError, run_tool_agent, run_tool_agent_recorded
from app.config import ModelConfig
from app.reporting import TokenUsageRecorder


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


@tool
def echo_tool(value: str) -> str:
    """Echo a value back as JSON."""

    return f'{{"echoed": "{value}"}}'


def test_immediate_final_answer_no_tool_calls() -> None:
    llm = FakeChatModel([AIMessage(content="all done")])
    result = run_tool_agent(llm, [echo_tool], "system", "user")
    assert result.final_text == "all done"
    assert result.steps_used == 1


def test_one_tool_call_then_final_answer() -> None:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"value": "hi"}, "id": "call_1"}],
    )
    final_message = AIMessage(content='{"status": "success"}')
    llm = FakeChatModel([tool_call_message, final_message])

    result = run_tool_agent(llm, [echo_tool], "system", "user")

    assert result.final_text == '{"status": "success"}'
    assert result.steps_used == 2
    tool_messages = [m for m in result.messages if m.type == "tool"]
    assert len(tool_messages) == 1
    assert "hi" in tool_messages[0].content


def test_unknown_tool_call_reports_error_and_continues() -> None:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "does_not_exist", "args": {}, "id": "call_1"}],
    )
    final_message = AIMessage(content="recovered")
    llm = FakeChatModel([tool_call_message, final_message])

    result = run_tool_agent(llm, [echo_tool], "system", "user")

    assert result.final_text == "recovered"
    tool_messages = [m for m in result.messages if m.type == "tool"]
    assert "unknown_tool" in tool_messages[0].content


def test_raises_after_max_steps_without_final_answer() -> None:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"value": "x"}, "id": "call_1"}],
    )
    llm = FakeChatModel([tool_call_message] * 3)

    with pytest.raises(AgentLoopError):
        run_tool_agent(llm, [echo_tool], "system", "user", max_steps=3)


def test_agent_loop_error_carries_partial_ai_messages() -> None:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"value": "x"}, "id": "call_1"}],
    )
    llm = FakeChatModel([tool_call_message] * 3)

    with pytest.raises(AgentLoopError) as excinfo:
        run_tool_agent(llm, [echo_tool], "system", "user", max_steps=3)

    assert len(excinfo.value.ai_messages) == 3


def test_result_ai_messages_includes_every_turn() -> None:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"value": "x"}, "id": "call_1"}],
    )
    final_message = AIMessage(content="done")
    llm = FakeChatModel([tool_call_message, final_message])

    result = run_tool_agent(llm, [echo_tool], "system", "user")

    assert result.ai_messages == [tool_call_message, final_message]
    assert result.last_ai_message is final_message


def _model() -> ModelConfig:
    return ModelConfig(role="worker", provider="fake", model_name="fake-worker")


def test_run_tool_agent_recorded_records_every_turn_on_success(tmp_path) -> None:
    from app.workspace import RunWorkspace

    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    recorder = TokenUsageRecorder(ws)

    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"value": "x"}, "id": "call_1"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    final_message = AIMessage(
        content="done", usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55}
    )
    llm = FakeChatModel([tool_call_message, final_message])

    run_tool_agent_recorded(llm, [echo_tool], "system", "user", recorder, "app_writer", 0, _model())

    assert len(recorder.entries) == 2
    assert recorder.totals()["total_tokens"] == 175


def test_run_tool_agent_recorded_records_partial_usage_on_error(tmp_path) -> None:
    from app.workspace import RunWorkspace

    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    recorder = TokenUsageRecorder(ws)

    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"value": "x"}, "id": "call_1"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    llm = FakeChatModel([tool_call_message] * 3)

    with pytest.raises(AgentLoopError):
        run_tool_agent_recorded(
            llm, [echo_tool], "system", "user", recorder, "app_writer", 0, _model(), max_steps=3
        )

    assert len(recorder.entries) == 3
    assert recorder.totals()["total_tokens"] == 360
