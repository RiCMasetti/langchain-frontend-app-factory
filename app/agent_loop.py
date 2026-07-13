"""A minimal, per-call tool-calling loop.

Each node builds a fresh message list scoped to just that call (system + task prompt),
binds only the tools that node is allowed to use, and runs this loop to completion. No
message history crosses node boundaries -- see app/state.py for why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

if TYPE_CHECKING:
    from app.config import ModelConfig
    from app.reporting import TokenUsageRecorder

DEFAULT_MAX_STEPS = 8


class AgentLoopError(RuntimeError):
    """Raised when the tool-calling loop can't reach a final answer.

    Carries every AIMessage generated before the loop gave up, so a caller can still
    record token usage for a failed loop instead of silently losing it -- each of
    those messages was a real, paid LLM call.
    """

    def __init__(self, message: str, ai_messages: list[AIMessage]) -> None:
        super().__init__(message)
        self.ai_messages = ai_messages


@dataclass
class AgentLoopResult:
    final_text: str
    last_ai_message: AIMessage | None
    ai_messages: list[AIMessage]
    messages: list[BaseMessage]
    steps_used: int


def run_tool_agent(
    llm,
    tools: list[BaseTool],
    system_prompt: str,
    user_prompt: str,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> AgentLoopResult:
    tools_by_name = {t.name: t for t in tools}
    bound_llm = llm.bind_tools(tools) if tools else llm

    messages: list[BaseMessage] = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    ai_messages: list[AIMessage] = []

    for step in range(1, max_steps + 1):
        ai_message = bound_llm.invoke(messages)
        messages.append(ai_message)
        ai_messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            return AgentLoopResult(
                final_text=_as_text(ai_message.content),
                last_ai_message=ai_message,
                ai_messages=ai_messages,
                messages=messages,
                steps_used=step,
            )

        for call in tool_calls:
            tool = tools_by_name.get(call["name"])
            if tool is None:
                content = f'{{"status": "error", "reason": "unknown_tool", "tool": "{call["name"]}"}}'
            else:
                try:
                    content = tool.invoke(call["args"])
                except Exception as exc:  # noqa: BLE001 - surfaced to the model as a tool error, not a crash
                    content = f'{{"status": "error", "reason": "tool_raised", "error": "{exc}"}}'
            messages.append(ToolMessage(content=str(content), tool_call_id=call["id"]))

    raise AgentLoopError(
        f"Tool-calling loop did not reach a final answer within {max_steps} steps.",
        ai_messages=ai_messages,
    )


def run_tool_agent_recorded(
    llm,
    tools: list[BaseTool],
    system_prompt: str,
    user_prompt: str,
    recorder: "TokenUsageRecorder",
    node: str,
    iteration: int,
    model_config: "ModelConfig",
    max_steps: int = DEFAULT_MAX_STEPS,
) -> AgentLoopResult:
    """run_tool_agent(), recording every turn's token usage -- including the turns
    that happened before an AgentLoopError, which would otherwise be silently lost.
    """

    try:
        result = run_tool_agent(llm, tools, system_prompt, user_prompt, max_steps=max_steps)
    except AgentLoopError as exc:
        recorder.record_from_messages(node, iteration, model_config, exc.ai_messages)
        raise
    recorder.record_from_messages(node, iteration, model_config, result.ai_messages)
    return result


def _as_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(parts)
    return str(content)
