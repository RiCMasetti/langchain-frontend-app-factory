import json
from pathlib import Path

from app.sandboxing.manager import SandboxManager
from app.sandboxing.templates import get_template
from app.tools.sandbox_tools import build_sandbox_prepare_tool, build_sandbox_tools
from app.workspace import RunWorkspace


def test_sandbox_prepare_tool_rejects_unknown_framework(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    (sandbox_prepare_tool,) = build_sandbox_prepare_tool(ws, start=False)
    result = json.loads(
        sandbox_prepare_tool.invoke(
            {
                "framework": "angular-nonexistent",
                "install_cmd": "npm install",
                "build_cmd": "npm run build",
                "test_cmd": "npm test",
            }
        )
    )
    assert result["status"] == "error"
    assert result["reason"] == "unknown_framework"


def test_sandbox_prepare_tool_rejects_unsafe_command(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    (sandbox_prepare_tool,) = build_sandbox_prepare_tool(ws, start=False)
    result = json.loads(
        sandbox_prepare_tool.invoke(
            {
                "framework": "react-vite",
                "install_cmd": "npm install; rm -rf /",
                "build_cmd": "npm run build",
                "test_cmd": "npm test",
            }
        )
    )
    assert result["status"] == "error"
    assert result["step"] == "prepare"


def test_sandbox_prepare_tool_prepare_only_succeeds_without_docker(tmp_path: Path) -> None:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    ws.ensure_layout()
    (sandbox_prepare_tool,) = build_sandbox_prepare_tool(ws, start=False)
    result = json.loads(
        sandbox_prepare_tool.invoke(
            {
                "framework": "react-vite",
                "install_cmd": "npm install",
                "build_cmd": "npm run build",
                "test_cmd": "npm test",
            }
        )
    )
    assert result["status"] == "success"
    assert ws.exists("sandbox/Dockerfile")


def _prepared_manager(tmp_path: Path) -> SandboxManager:
    ws = RunWorkspace.for_run("run-1", workspace_root=tmp_path)
    manager = SandboxManager(ws, get_template("react-vite"))
    manager.prepare("npm install", "npm run build", "npm test")
    return manager


def test_sandbox_command_tool_rejects_slot_outside_allowed(tmp_path: Path) -> None:
    manager = _prepared_manager(tmp_path)
    (sandbox_command_tool,) = build_sandbox_tools(manager, allowed_slots=frozenset({"install", "build"}))
    result = json.loads(sandbox_command_tool.invoke({"command_name": "test"}))
    assert result["status"] == "error"
    assert result["reason"] == "not_allowed"


def test_manager_load_reconstructs_from_disk(tmp_path: Path) -> None:
    manager = _prepared_manager(tmp_path)
    reloaded = SandboxManager.load(manager.run_workspace)
    assert reloaded.allowed_commands() == manager.allowed_commands()
    assert reloaded.template.template_key == "react-vite"
