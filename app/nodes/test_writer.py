"""Test Writer node: authors test files for the generated app. Cannot execute anything."""

from __future__ import annotations

import json
from typing import Callable

from app.agent_loop import run_tool_agent_recorded
from app.config import Settings
from app.nodes.feedback import compact_test_feedback
from app.reporting import TokenUsageRecorder
from app.state import FactoryState
from app.tools.fs_tools import build_fs_tools
from app.tools.patch_tools import build_patch_tools
from app.tools.util import select_tools
from app.workspace import RunWorkspace

SYSTEM_PROMPT = """You are the Test Writer agent in a frontend app factory.

Read the generated app (app_reader_tool) and app-plan.json's acceptance_criteria, then
author test files with app_writer_tool/file_patch_tool using whatever test tooling
matches the chosen framework (test_cmd in app-plan.json tells you which). You cannot
execute anything -- Test Runner does that. Finish by calling file_writer_tool to write
"handoffs/testwriter-summary.json" with keys: notes (string), test_files (list of the
test file paths you wrote).

Do not write summary, report, or documentation markdown files -- they are not part of
the app and nobody reads them. The one JSON handoff above is your only required
summary artifact.

If you are given previous iteration feedback below, that means your existing tests are
failing: read the named test file(s) and fix the specific broken assertion or query --
do not add a new, overlapping test file as a workaround. Before writing any test, check
with app_reader_tool whether an existing test file already covers that scenario; extend
or fix it rather than creating a near-duplicate. An accumulating pile of redundant test
files wastes tokens and makes future failures harder to isolate, not easier.

Write queries that can only match exactly one element, and that match how the markup
is actually structured (check with app_reader_tool -- don't assume). Two common,
purely test-side failure modes to avoid:
- Not found: text that looks combined on screen is often split across sibling
  elements (e.g. a "$" currency symbol and the amount in separate <span>s) -- an exact
  getByText('$29') then matches nothing. Query a container and check its combined
  textContent, use a function matcher, or query the parts separately.
- Multiple found: text like a brand name or footer copy often appears in more than one
  place (e.g. a header logo and a footer copyright line) -- getByText/getByRole with a
  loose pattern throws "multiple elements found". Scope queries to a specific container
  (e.g. within(header) / within(footer)), use exact strings instead of loose regexes, or
  add a distinguishing test id.
"""


def build_test_writer_node(
    run_ws: RunWorkspace, settings: Settings, recorder: TokenUsageRecorder
) -> Callable[[FactoryState], dict]:
    def test_writer_node(state: FactoryState) -> dict:
        fs_tools = select_tools(
            build_fs_tools(run_ws), ["app_reader_tool", "app_writer_tool", "file_writer_tool"]
        )
        patch_tools = build_patch_tools(run_ws, app_scoped=True)
        tools = [*fs_tools, *patch_tools]
        llm = settings.worker_model.init_chat_model()

        app_plan = state.get("app_plan", {})
        appwriter_summary = state.get("appwriter_summary", {})
        user_prompt = (
            f"plans/app-plan.json:\n{json.dumps(app_plan, indent=2)}\n"
            f"App Writer's summary:\n{json.dumps(appwriter_summary, indent=2)}"
            + compact_test_feedback(state)
        )
        result = run_tool_agent_recorded(
            llm, tools, SYSTEM_PROMPT, user_prompt,
            recorder, "test_writer", state.get("iteration", 0), settings.worker_model,
            max_steps=settings.test_writer_max_steps,
        )

        testwriter_summary = (
            run_ws.read_json("handoffs/testwriter-summary.json")
            if run_ws.exists("handoffs/testwriter-summary.json")
            else {"notes": result.final_text, "test_files": []}
        )

        return {
            "testwriter_summary": testwriter_summary,
            "current_node": "test_writer",
            "last_completed_node": "test_writer",
        }

    return test_writer_node
