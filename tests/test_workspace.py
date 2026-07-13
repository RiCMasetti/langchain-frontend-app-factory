from pathlib import Path

import pytest

from app.workspace import RunWorkspace, WorkspaceError


def test_for_run_rejects_invalid_run_id(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        RunWorkspace.for_run("../../etc", workspace_root=tmp_path)
    with pytest.raises(WorkspaceError):
        RunWorkspace.for_run("has spaces", workspace_root=tmp_path)
    with pytest.raises(WorkspaceError):
        RunWorkspace.for_run("", workspace_root=tmp_path)


def test_for_run_accepts_uuid_like_id(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("a1b2c3d4-1234-5678-9abc-def012345678", workspace_root=tmp_path)
    assert ws.root == (tmp_path / "run" / "a1b2c3d4-1234-5678-9abc-def012345678").resolve()


def test_ensure_layout_creates_expected_dirs(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    for expected in ("input", "app", "plans", "handoffs", "reports", "reports/errors", "metrics", "sandbox"):
        assert (ws.root / expected).is_dir()


def test_resolve_rejects_absolute_and_traversal(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    with pytest.raises(WorkspaceError):
        ws.resolve("/etc/passwd")
    with pytest.raises(WorkspaceError):
        ws.resolve("../../../etc/passwd")
    with pytest.raises(WorkspaceError):
        ws.resolve("plans/../../escape.txt")


def test_app_resolve_scopes_to_app_dir(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    target = ws.app_resolve("src/App.tsx")
    assert target == (ws.root / "app" / "src" / "App.tsx")
    with pytest.raises(WorkspaceError):
        ws.app_resolve("../plans/app-plan.json")


def test_write_and_read_text_roundtrip(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    ws.write_text("plans/notes.md", "hello world")
    assert ws.read_text("plans/notes.md") == "hello world"


def test_read_text_truncates_at_max_chars(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    ws.write_text("plans/notes.md", "x" * 100)
    result = ws.read_text("plans/notes.md", max_chars=10)
    assert result.startswith("x" * 10)
    assert "truncated" in result


def test_write_json_and_read_json_roundtrip(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    payload = {"framework": "react-vite", "install_cmd": "npm install"}
    ws.write_json("plans/app-plan.json", payload)
    assert ws.read_json("plans/app-plan.json") == payload


def test_write_app_text_scopes_to_app_dir(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    ws.write_app_text("src/App.tsx", "export default function App() {}")
    assert (ws.root / "app" / "src" / "App.tsx").read_text() == "export default function App() {}"


def test_list_files_ignores_context_heavy_dirs_and_files(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    ws.write_app_text("src/App.tsx", "content")
    ws.write_app_text("node_modules/pkg/index.js", "noise")
    ws.write_app_text("dist/bundle.js", "noise")
    ws.write_app_text("package-lock.json", "noise")
    ws.write_app_text(".svelte-kit/generated.js", "noise")

    files = ws.list_files(base=ws.root / "app")

    assert "src/App.tsx" in files
    assert not any("node_modules" in f for f in files)
    assert not any("dist" in f for f in files)
    assert "package-lock.json" not in files
    assert not any(".svelte-kit" in f for f in files)


def test_list_files_caps_at_max_files(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    for i in range(5):
        ws.write_app_text(f"src/file{i}.ts", "x")

    files = ws.list_files(base=ws.root / "app", max_files=2)

    assert len(files) == 3  # 2 entries + truncation marker
    assert files[-1].startswith("...[truncated")
