from pathlib import Path

from app.sandboxing.manager import SandboxManager
from app.sandboxing.templates import get_template
from app.workspace import RunWorkspace


def _manager(tmp_path: Path) -> SandboxManager:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    template = get_template("react-vite")
    return SandboxManager(ws, template)


def test_prepare_writes_rendered_dockerfile_and_compose(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    result = manager.prepare("npm install", "npm run build", "npm test")

    assert result.status == "success"
    dockerfile = manager.run_workspace.read_text("sandbox/Dockerfile")
    assert "node:22-bookworm-slim" in dockerfile
    compose = manager.run_workspace.read_text("sandbox/docker-compose.yml")
    assert "5173:5173" in compose
    allowlist = manager.run_workspace.read_json("sandbox/allowlist.json")
    assert allowlist == {"install": ["npm", "install"], "build": ["npm", "run", "build"], "test": ["npm", "test"]}


def test_prepare_rejects_unsafe_command_and_skips_start(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    result = manager.prepare("npm install; rm -rf /", "npm run build", "npm test")

    assert result.status == "error"
    assert manager.allowed_commands() == {}
    assert not manager.run_workspace.exists("sandbox/Dockerfile")


def test_run_allowed_rejects_slot_not_prepared(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    result = manager.run_allowed("install")
    assert result.status == "error"
    assert "not been prepared" in result.message


def test_run_allowed_rejects_unknown_slot(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.prepare("npm install", "npm run build", "npm test")
    result = manager.run_allowed("deploy")
    assert result.status == "error"
    assert "fixed slots" in result.message


def test_node_version_and_port_warnings_surface_in_sandbox_summary(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    template = get_template("react-vite")
    manager = SandboxManager(ws, template, node_version="6", port=80)
    result = manager.prepare("npm install", "npm run build", "npm test")

    assert result.status == "success"
    assert len(result.details["warnings"]) == 2
