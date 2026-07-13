"""App Writer node: writes the frontend app inside the sandbox only, per app-plan.json.

Only tool with sandbox execution rights here is install/build (never test -- that's
Test Runner's exclusive job). On retry iterations it receives a compact summary of the
previous Test Writer/Test Runner feedback instead of full logs, to keep tokens bounded.
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

SYSTEM_PROMPT = """You are the App Writer agent in a frontend app factory.

Write the frontend application described by app-plan.json using app_writer_tool for
new files and file_patch_tool for targeted edits to existing files. Only write inside
the app/ directory -- app_reader_tool/app_writer_tool/file_patch_tool are already
scoped there. You may call sandbox_command_tool with "install" or "build" to check
your work compiles, but you must never attempt to run tests -- that is the Test
Runner's job.

Do not write test files (anything matching *.test.*, *.spec.*, or under a __tests__/
directory) -- that is Test Writer's exclusive job, done in a later step. Writing tests
yourself wastes your own turn budget on work that will be redone.

Do not write summary, report, or documentation markdown files (e.g. SUMMARY.md,
IMPLEMENTATION_NOTES.md, FIXES.md) -- they are not part of the app and nobody reads
them. Your one-sentence final reply is your summary; no separate file is needed for
it. Every file you write should be something the app actually needs to run or build.

If this is a retry iteration, you will be given a compact summary of what Test Writer
and Test Runner reported last time. Fix the specific issues raised -- but only if the
failure is actually an app bug. If you believe your app's behavior is already correct
and the test itself has a wrong assumption (e.g. it queries for text as one combined
string, but you render it split across multiple elements), say so plainly in your
final reply instead of changing correct behavior to satisfy a broken test -- Test
Writer sees your reply next and can fix the test instead.
"""


def build_app_writer_node(
    run_ws: RunWorkspace, settings: Settings, recorder: TokenUsageRecorder
) -> Callable[[FactoryState], dict]:
    def app_writer_node(state: FactoryState) -> dict:
        fs_tools = select_tools(
            build_fs_tools(run_ws), ["file_reader_tool", "app_reader_tool", "app_writer_tool"]
        )
        patch_tools = build_patch_tools(run_ws, app_scoped=True)
        manager = SandboxManager.load(run_ws)
        sandbox_tools = build_sandbox_tools(manager, allowed_slots=frozenset({"install", "build"}))
        tools = [*fs_tools, *patch_tools, *sandbox_tools]
        llm = settings.worker_model.init_chat_model()

        app_plan = state.get("app_plan", {})
        user_prompt = (
            f"plans/app-plan.json:\n{json.dumps(app_plan, indent=2)}" + compact_test_feedback(state)
        )
        result = run_tool_agent_recorded(
            llm, tools, SYSTEM_PROMPT, user_prompt,
            recorder, "app_writer", state.get("iteration", 0), settings.worker_model,
            max_steps=settings.app_writer_max_steps,
        )

        appwriter_summary = {
            "note": result.final_text,
            "app_files": run_ws.list_files(base=run_ws.root / "app"),
        }
        run_ws.write_json("handoffs/appwriter-summary.json", appwriter_summary)

        return {
            "appwriter_summary": appwriter_summary,
            "current_node": "app_writer",
            "last_completed_node": "app_writer",
        }

    return app_writer_node
