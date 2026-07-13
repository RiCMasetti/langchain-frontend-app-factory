import json
from pathlib import Path

from app.tools.fs_tools import build_fs_tools
from app.workspace import RunWorkspace


def _tools(tmp_path: Path):
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    file_writer_tool, app_writer_tool, file_reader_tool, app_reader_tool = build_fs_tools(ws)
    return ws, file_writer_tool, app_writer_tool, file_reader_tool, app_reader_tool


def test_file_writer_tool_writes_under_workspace(tmp_path: Path) -> None:
    ws, file_writer_tool, _, _, _ = _tools(tmp_path)
    result = json.loads(file_writer_tool.invoke({"relative_path": "plans/app-plan.json", "content": "{}"}))
    assert result["status"] == "success"
    assert (ws.root / "plans" / "app-plan.json").read_text() == "{}"


def test_file_writer_tool_rejects_path_traversal(tmp_path: Path) -> None:
    _, file_writer_tool, _, _, _ = _tools(tmp_path)
    result = json.loads(file_writer_tool.invoke({"relative_path": "../../escape.txt", "content": "x"}))
    assert result["status"] == "error"


def test_app_writer_tool_scopes_to_app_dir(tmp_path: Path) -> None:
    ws, _, app_writer_tool, _, _ = _tools(tmp_path)
    result = json.loads(app_writer_tool.invoke({"relative_path": "src/App.tsx", "content": "code"}))
    assert result["status"] == "success"
    assert (ws.root / "app" / "src" / "App.tsx").read_text() == "code"


def test_app_writer_tool_cannot_escape_app_dir(tmp_path: Path) -> None:
    _, _, app_writer_tool, _, _ = _tools(tmp_path)
    result = json.loads(app_writer_tool.invoke({"relative_path": "../plans/app-plan.json", "content": "x"}))
    assert result["status"] == "error"


def test_file_reader_tool_reads_back_written_file(tmp_path: Path) -> None:
    _, file_writer_tool, _, file_reader_tool, _ = _tools(tmp_path)
    file_writer_tool.invoke({"relative_path": "plans/notes.md", "content": "hello"})
    result = json.loads(file_reader_tool.invoke({"relative_path": "plans/notes.md"}))
    assert result == {"status": "success", "type": "file", "content": "hello", "truncated": False}


def test_file_reader_tool_lists_directory_and_ignores_noise(tmp_path: Path) -> None:
    ws, _, app_writer_tool, file_reader_tool, _ = _tools(tmp_path)
    app_writer_tool.invoke({"relative_path": "src/App.tsx", "content": "code"})
    app_writer_tool.invoke({"relative_path": "node_modules/pkg/index.js", "content": "noise"})
    result = json.loads(file_reader_tool.invoke({"relative_path": "app"}))
    assert result["status"] == "success"
    assert result["type"] == "directory"
    assert any("App.tsx" in e for e in result["entries"])
    assert not any("node_modules" in e for e in result["entries"])


def test_app_reader_tool_not_found(tmp_path: Path) -> None:
    _, _, _, _, app_reader_tool = _tools(tmp_path)
    result = json.loads(app_reader_tool.invoke({"relative_path": "missing.txt"}))
    assert result["status"] == "error"
    assert result["error"] == "not_found"


def test_file_reader_tool_truncates_long_files(tmp_path: Path) -> None:
    _, file_writer_tool, _, file_reader_tool, _ = _tools(tmp_path)
    file_writer_tool.invoke({"relative_path": "plans/big.md", "content": "x" * 100})
    result = json.loads(file_reader_tool.invoke({"relative_path": "plans/big.md", "max_chars": 10}))
    assert result["truncated"] is True
    assert result["content"].startswith("x" * 10)
