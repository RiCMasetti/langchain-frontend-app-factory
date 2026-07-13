"""Test Writer node: authors test files for the generated app.

Also allowed to run the test command itself (and only that slot -- never install/
build, those stay App Writer's job) so it can catch and fix its own mistakes (e.g. a
query that doesn't match the actual markup) in a tight write-run-fix loop, instead of
handing every mistake off to the full App Writer/Test Runner retry cycle. Test Runner
still does the definitive run that the graph's retry/circuit-breaker logic acts on --
this is a cheaper, earlier self-check, not a replacement for it.
"""

from __future__ import annotations

import json
from typing import Callable

from app.agent_loop import run_tool_agent_recorded
from app.config import Settings
from app.nodes.feedback import compact_test_feedback
from app.reporting import TokenUsageRecorder
from app.sandboxing.manager import SandboxManager
from app.state import FactoryState
from app.tools.fs_tools import build_fs_tools
from app.tools.patch_tools import build_patch_tools
from app.tools.sandbox_tools import build_sandbox_tools
from app.tools.util import select_tools
from app.workspace import RunWorkspace

SYSTEM_PROMPT = """You are the Test Writer agent in a frontend app factory.

Read the generated app (app_reader_tool) and app-plan.json's acceptance_criteria, then
author test files with app_writer_tool/file_patch_tool using whatever test tooling
matches the chosen framework (test_cmd in app-plan.json tells you which).

You may call sandbox_command_tool with "test" to run the test suite yourself -- you do
not have "install" or "build" (that's App Writer's job, already done before you run).
Use this to catch your own mistakes early: after writing or changing test files, run
the tests, read the failure, and fix your test if the failure is test-side (a bad
query, a wrong assumption about the markup) before finishing. Don't loop forever --
a couple of write-then-run cycles is normal, running "test" over and over without
changing anything between calls is not. Test Runner will still run the definitive,
official test pass after you hand off; this is a cheaper early check, not a
replacement for it, so it's fine to finish even if you're not 100% certain everything
passes -- just say so honestly in your handoff notes.

Finish by calling file_writer_tool to write "handoffs/testwriter-summary.json" with
keys: notes (string -- include whether you verified tests passing yourself), test_files
(list of the test file paths you wrote).

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

Before asserting on a CSS class name, check whether the component imports a
`*.module.css`/`*.module.scss` file (CSS Modules). If it does, every class name from
that import (e.g. `styles.selected`) is renamed/hashed at build time (e.g. to something
like `_selected_ab12c`) -- `toHaveClass('selected')` or any assertion on the literal,
unhashed class name will never match and is not a real bug. For CSS-Modules-scoped
classes, assert some other way: check `element.className` against a substring/regex of
the unhashed fragment (e.g. `expect(element.className).toMatch(/selected/)`), assert on
a `data-testid`/`aria-*` attribute instead, or import the same `styles` object in the
test and assert `element.classList.contains(styles.selected)`.
"""


def build_test_writer_node(
    run_ws: RunWorkspace, settings: Settings, recorder: TokenUsageRecorder
) -> Callable[[FactoryState], dict]:
    def test_writer_node(state: FactoryState) -> dict:
        fs_tools = select_tools(
            build_fs_tools(run_ws), ["app_reader_tool", "app_writer_tool", "file_writer_tool"]
        )
        patch_tools = build_patch_tools(run_ws, app_scoped=True)
        manager = SandboxManager.load(run_ws)
        sandbox_tools = build_sandbox_tools(manager, allowed_slots=frozenset({"test"}))
        tools = [*fs_tools, *patch_tools, *sandbox_tools]
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
