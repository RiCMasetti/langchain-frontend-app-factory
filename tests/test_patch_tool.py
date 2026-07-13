import json
from pathlib import Path

from app.tools.patch_tools import build_patch_tools
from app.workspace import RunWorkspace


def _tool(tmp_path: Path):
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    (file_patch_tool,) = build_patch_tools(ws)
    return ws, file_patch_tool


def test_patch_replaces_unique_match(tmp_path: Path) -> None:
    ws, file_patch_tool = _tool(tmp_path)
    ws.write_text("plans/notes.md", "hello world")
    result = json.loads(
        file_patch_tool.invoke({"relative_path": "plans/notes.md", "old_string": "world", "new_string": "there"})
    )
    assert result == {"status": "success", "path": "plans/notes.md", "replacements": 1}
    assert ws.read_text("plans/notes.md") == "hello there"


def test_patch_reports_old_string_not_found(tmp_path: Path) -> None:
    ws, file_patch_tool = _tool(tmp_path)
    ws.write_text("plans/notes.md", "hello world")
    result = json.loads(
        file_patch_tool.invoke({"relative_path": "plans/notes.md", "old_string": "missing", "new_string": "x"})
    )
    assert result["status"] == "error"
    assert result["reason"] == "old_string_not_found"


def test_patch_reports_ambiguous_match_without_replace_all(tmp_path: Path) -> None:
    ws, file_patch_tool = _tool(tmp_path)
    ws.write_text("plans/notes.md", "foo foo foo")
    result = json.loads(
        file_patch_tool.invoke({"relative_path": "plans/notes.md", "old_string": "foo", "new_string": "bar"})
    )
    assert result["status"] == "error"
    assert result["reason"] == "ambiguous_match"
    assert result["occurrences"] == 3


def test_patch_replace_all_replaces_every_occurrence(tmp_path: Path) -> None:
    ws, file_patch_tool = _tool(tmp_path)
    ws.write_text("plans/notes.md", "foo foo foo")
    result = json.loads(
        file_patch_tool.invoke(
            {"relative_path": "plans/notes.md", "old_string": "foo", "new_string": "bar", "replace_all": True}
        )
    )
    assert result["status"] == "success"
    assert ws.read_text("plans/notes.md") == "bar bar bar"


def test_patch_rejects_missing_file(tmp_path: Path) -> None:
    _, file_patch_tool = _tool(tmp_path)
    result = json.loads(
        file_patch_tool.invoke({"relative_path": "plans/missing.md", "old_string": "a", "new_string": "b"})
    )
    assert result["status"] == "error"
    assert result["reason"] == "not_found"


def test_patch_rejects_no_op_change(tmp_path: Path) -> None:
    ws, file_patch_tool = _tool(tmp_path)
    ws.write_text("plans/notes.md", "hello")
    result = json.loads(
        file_patch_tool.invoke({"relative_path": "plans/notes.md", "old_string": "hello", "new_string": "hello"})
    )
    assert result["status"] == "error"
    assert result["reason"] == "no_changes"
