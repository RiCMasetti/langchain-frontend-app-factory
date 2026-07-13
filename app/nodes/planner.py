"""Planner node: analyzes the user request and writes plans/app-plan.json + a handoff summary.

app-plan.json is the one artifact validated against a hard Pydantic schema (AppPlan),
because Test Runner's framework-agnosticism depends on install_cmd/build_cmd/test_cmd
always being present. On validation failure the node re-prompts with the specific
missing/invalid fields, up to PLANNER_MAX_ATTEMPTS total attempts, before halting.
"""

from __future__ import annotations

import json
from typing import Callable

from app.agent_loop import run_tool_agent_recorded
from app.config import Settings
from app.reporting import TokenUsageRecorder
from app.schemas import validate_app_plan
from app.state import FactoryState
from app.tools.fs_tools import build_fs_tools
from app.tools.util import select_tools
from app.workspace import RunWorkspace

PLANNER_MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """You are the Planner agent in a frontend app factory.

Analyze the user's request and produce an execution plan for the agents that come
after you (Sandbox Agent, App Writer, Test Writer, Test Runner). You must:

1. Choose a `framework` from exactly these vetted sandbox templates: react-vite,
   vue-vite, nextjs, sveltekit, generic-node. Use generic-node if none of the named
   frameworks fit the request.
2. Decide install_cmd, build_cmd, and test_cmd -- the exact shell commands (e.g.
   "npm install", "npm run build", "npm test") the sandbox will run later. These must
   use only binaries appropriate for the chosen framework (npm/npx/node/vite/vitest/
   next, or pnpm/yarn for generic-node).
3. Call file_writer_tool to write the plan as JSON to "plans/app-plan.json" with keys:
   framework, install_cmd, build_cmd, test_cmd, optional start_cmd/node_version/port,
   summary, pages, components, acceptance_criteria, stack.
4. Call file_writer_tool to write a short handoff summary as JSON to
   "handoffs/planner-summary.json" (keys: summary, pages, acceptance_criteria).
5. Reply with a one-sentence confirmation once both files are written.

Use file_reader_tool if you need to check what's already in the workspace. Do not
invent files outside these two paths.
"""


def build_planner_node(
    run_ws: RunWorkspace, settings: Settings, recorder: TokenUsageRecorder
) -> Callable[[FactoryState], dict]:
    def planner_node(state: FactoryState) -> dict:
        fs_tools = select_tools(build_fs_tools(run_ws), ["file_reader_tool", "file_writer_tool"])
        llm = settings.planner_model.init_chat_model()

        feedback = ""
        for attempt in range(1, PLANNER_MAX_ATTEMPTS + 1):
            user_prompt = f"User request:\n{state['user_request']}\n{feedback}".strip()
            result = run_tool_agent_recorded(
                llm, fs_tools, SYSTEM_PROMPT, user_prompt,
                recorder, "planner", state.get("iteration", 0), settings.planner_model,
            )

            if not run_ws.exists("plans/app-plan.json"):
                feedback = "\nYou did not write plans/app-plan.json. Write it now via file_writer_tool."
                continue

            raw_plan = run_ws.read_json("plans/app-plan.json")
            plan, errors = validate_app_plan(raw_plan)
            if plan is not None:
                planner_summary = (
                    run_ws.read_json("handoffs/planner-summary.json")
                    if run_ws.exists("handoffs/planner-summary.json")
                    else {"summary": result.final_text}
                )
                return {
                    "planner_summary": planner_summary,
                    "app_plan": plan.model_dump(),
                    "current_node": "planner",
                    "last_completed_node": "planner",
                }

            feedback = (
                "\nYour previous plans/app-plan.json was invalid: "
                f"{json.dumps(errors)}. Fix these specific fields and rewrite the file."
            )

        return {
            "halt": True,
            "status": "failed",
            "halt_reason": f"Planner could not produce a valid app-plan.json after {PLANNER_MAX_ATTEMPTS} attempts.",
            "errors": [*state.get("errors", []), "planner_invalid_app_plan"],
            "current_node": "planner",
        }

    return planner_node
