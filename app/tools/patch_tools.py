"""Find/replace-block patch tool: targeted edits without full-file rewrites.

Chosen over unified-diff patching because it only requires the LLM to reproduce a
substring it already has in context (from a prior reader-tool call), and failures are
reported as plain JSON the agent can act on, never a raised exception.
"""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from app.workspace import RunWorkspace, WorkspaceError


def build_patch_tools(run_ws: RunWorkspace, app_scoped: bool = False) -> list[BaseTool]:
    resolver = run_ws.app_resolve if app_scoped else run_ws.resolve

    @tool
    def file_patch_tool(
        relative_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> str:
        """Replace old_string with new_string inside a file. old_string must match the
        file's current content exactly (including whitespace); it must be unique in the
        file unless replace_all is set."""

        try:
            path = resolver(relative_path)
        except WorkspaceError as exc:
            return json.dumps({"status": "error", "reason": "invalid_path", "error": str(exc)})

        if not path.is_file():
            return json.dumps({"status": "error", "reason": "not_found", "path": relative_path})
        if old_string == new_string:
            return json.dumps({"status": "error", "reason": "no_changes"})

        text = path.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return json.dumps(
                {
                    "status": "error",
                    "reason": "old_string_not_found",
                    "path": relative_path,
                    "hint": "old_string must match the file's current content exactly, including whitespace",
                }
            )
        if count > 1 and not replace_all:
            return json.dumps(
                {
                    "status": "error",
                    "reason": "ambiguous_match",
                    "occurrences": count,
                    "hint": "include more surrounding context to make old_string unique, or pass replace_all=true",
                }
            )

        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        path.write_text(new_text, encoding="utf-8")
        return json.dumps(
            {"status": "success", "path": relative_path, "replacements": count if replace_all else 1}
        )

    return [file_patch_tool]
