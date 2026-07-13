"""Sandbox Agent node: selects a vetted framework template and starts the run's container.

Never generates Dockerfile/compose content -- it only calls sandbox_prepare_tool with
the framework key and commands already decided by the Planner in app-plan.json.
"""

from __future__ import annotations

import json
from typing import Callable

from app.agent_loop import run_tool_agent_recorded
from app.config import Settings
from app.reporting import TokenUsageRecorder
from app.state import FactoryState
from app.tools.fs_tools import build_fs_tools
from app.tools.sandbox_tools import build_sandbox_prepare_tool
from app.tools.util import select_tools
from app.workspace import RunWorkspace

SYSTEM_PROMPT = """You are the Sandbox Agent in a frontend app factory.

You never write a Dockerfile yourself. You select and start one of a small set of
vetted, pre-built framework templates by calling sandbox_prepare_tool exactly once
with the framework, install_cmd, build_cmd, and test_cmd taken directly from the
provided app-plan.json (and node_version/port only if app-plan.json specifies them).

If sandbox_prepare_tool returns an error, read the error and retry once with a
corrected framework/command if the problem is fixable (e.g. an unknown framework --
fall back to "generic-node"). Otherwise report the failure in one sentence.
"""


def build_sandbox_node(
    run_ws: RunWorkspace, settings: Settings, recorder: TokenUsageRecorder, start: bool = True
) -> Callable[[FactoryState], dict]:
    def sandbox_node(state: FactoryState) -> dict:
        fs_tools = select_tools(build_fs_tools(run_ws), ["file_reader_tool", "file_writer_tool"])
        (prepare_tool,) = build_sandbox_prepare_tool(run_ws, start=start)
        tools = [*fs_tools, prepare_tool]
        llm = settings.worker_model.init_chat_model()

        app_plan = state.get("app_plan", {})
        user_prompt = f"plans/app-plan.json:\n{json.dumps(app_plan, indent=2)}"
        run_tool_agent_recorded(
            llm, tools, SYSTEM_PROMPT, user_prompt,
            recorder, "sandbox", state.get("iteration", 0), settings.worker_model,
        )

        if not run_ws.exists("handoffs/sandbox-summary.json"):
            return {
                "halt": True,
                "status": "failed",
                "halt_reason": "Sandbox Agent did not produce handoffs/sandbox-summary.json.",
                "errors": [*state.get("errors", []), "sandbox_no_summary"],
                "current_node": "sandbox",
            }

        sandbox_summary = run_ws.read_json("handoffs/sandbox-summary.json")
        if sandbox_summary.get("status") != "success":
            return {
                "halt": True,
                "status": "failed",
                "halt_reason": f"Sandbox setup failed: {sandbox_summary}",
                "errors": [*state.get("errors", []), "sandbox_setup_failed"],
                "sandbox_summary": sandbox_summary,
                "current_node": "sandbox",
            }

        return {
            "sandbox_summary": sandbox_summary,
            "current_node": "sandbox",
            "last_completed_node": "sandbox",
        }

    return sandbox_node
