"""Filesystem tools bound to a single run's workspace via closures.

run_id is never an LLM-visible tool argument: each node builds its tool list once per
run from a RunWorkspace already scoped to that run, so a model has no parameter through
which it could address another run's files.
"""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from app.workspace import MAX_TOOL_READ_CHARS, RunWorkspace, WorkspaceError


def _read_or_list(run_ws: RunWorkspace, app_scoped: bool, relative_path: str, max_chars: int) -> str:
    resolver = run_ws.app_resolve if app_scoped else run_ws.resolve
    base = run_ws.root / "app" if app_scoped else run_ws.root

    try:
        path = resolver(relative_path)
    except WorkspaceError as exc:
        return json.dumps({"status": "error", "error": str(exc)})

    if path.is_dir():
        entries = run_ws.list_files(base=base, relative_path=relative_path)
        return json.dumps({"status": "success", "type": "directory", "entries": entries})

    if not path.is_file():
        return json.dumps({"status": "error", "error": "not_found", "path": relative_path})

    clamped = min(max_chars, MAX_TOOL_READ_CHARS)
    data = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(data) > clamped
    content = data[:clamped] + "\n...[truncated]" if truncated else data
    return json.dumps({"status": "success", "type": "file", "content": content, "truncated": truncated})


def build_fs_tools(run_ws: RunWorkspace) -> list[BaseTool]:
    @tool
    def file_writer_tool(relative_path: str, content: str) -> str:
        """Write UTF-8 text to a path under this run's workspace (plans/, handoffs/, reports/, metrics/, sandbox/, app/)."""

        try:
            target = run_ws.write_text(relative_path, content)
            return json.dumps({"status": "success", "path": str(target.relative_to(run_ws.root))})
        except (WorkspaceError, OSError) as exc:
            return json.dumps({"status": "error", "path": relative_path, "error": str(exc)})

    @tool
    def app_writer_tool(relative_path: str, content: str) -> str:
        """Write UTF-8 text to a path under this run's app/ directory only (generated frontend source)."""

        try:
            target = run_ws.write_app_text(relative_path, content)
            return json.dumps({"status": "success", "path": str(target.relative_to(run_ws.root / "app"))})
        except (WorkspaceError, OSError) as exc:
            return json.dumps({"status": "error", "path": relative_path, "error": str(exc)})

    @tool
    def file_reader_tool(relative_path: str = ".", max_chars: int = MAX_TOOL_READ_CHARS) -> str:
        """Read a file (bounded) or list a directory anywhere under this run's workspace."""

        return _read_or_list(run_ws, app_scoped=False, relative_path=relative_path, max_chars=max_chars)

    @tool
    def app_reader_tool(relative_path: str = ".", max_chars: int = MAX_TOOL_READ_CHARS) -> str:
        """Read a file (bounded) or list a directory, restricted to this run's app/ directory."""

        return _read_or_list(run_ws, app_scoped=True, relative_path=relative_path, max_chars=max_chars)

    return [file_writer_tool, app_writer_tool, file_reader_tool, app_reader_tool]
