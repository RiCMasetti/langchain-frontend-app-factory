"""Per-run Docker sandbox lifecycle: prepare, start, run allowlisted commands, stop."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.sandboxing.allowlist import CommandValidationError, validate_command
from app.sandboxing.templates import FrameworkTemplate, get_template
from app.workspace import RunWorkspace

SANDBOX_APP_DIR = "/workspace/app"
SERVICE_NAME = "frontend-sandbox"
COMMAND_SLOTS = ("install", "build", "test")


@dataclass
class SandboxResult:
    status: str  # "success" | "error"
    message: str = ""
    details: dict[str, object] = field(default_factory=dict)


class SandboxManager:
    def __init__(
        self,
        run_workspace: RunWorkspace,
        template: FrameworkTemplate,
        node_version: str | None = None,
        port: int | None = None,
    ) -> None:
        self.run_workspace = run_workspace
        self.template = template
        self.node_version, self.node_version_warning = template.resolve_node_version(node_version)
        self.port, self.port_warning = template.resolve_port(port)
        self.compose_project = f"aff-{run_workspace.run_id}"
        self.compose_path = run_workspace.resolve("sandbox/docker-compose.yml")
        self._allowlist: dict[str, list[str]] = {}

    @classmethod
    def load(cls, run_workspace: RunWorkspace) -> "SandboxManager":
        """Reconstruct a SandboxManager for a run that already went through prepare().

        Reads the already-validated allowlist from sandbox/allowlist.json instead of
        re-validating app-plan.json commands, since prepare() already did that once.
        """

        sandbox_summary = run_workspace.read_json("handoffs/sandbox-summary.json")
        template = get_template(sandbox_summary["template_key"])
        manager = cls(
            run_workspace,
            template,
            node_version=sandbox_summary["node_version"],
            port=sandbox_summary["port"],
        )
        manager._allowlist = run_workspace.read_json("sandbox/allowlist.json")
        return manager

    @property
    def warnings(self) -> list[str]:
        return [w for w in (self.node_version_warning, self.port_warning) if w]

    def prepare(self, install_cmd: str, build_cmd: str, test_cmd: str) -> SandboxResult:
        commands = {"install": install_cmd, "build": build_cmd, "test": test_cmd}
        allowlist: dict[str, list[str]] = {}
        for slot, raw_command in commands.items():
            try:
                allowlist[slot] = validate_command(raw_command, self.template.allowed_command_binaries)
            except CommandValidationError as exc:
                return SandboxResult(
                    "error",
                    f"Rejected {slot} command: {exc}",
                    {"slot": slot, "command": raw_command},
                )

        self.run_workspace.ensure_layout()
        self.run_workspace.write_text(
            "sandbox/Dockerfile",
            self.template.render_dockerfile(self.node_version, self.port),
        )
        self.run_workspace.write_text(
            "sandbox/docker-compose.yml",
            self.template.render_compose(self.node_version, self.port),
        )
        self._allowlist = allowlist
        self.run_workspace.write_json("sandbox/allowlist.json", allowlist)

        summary = {
            "status": "prepared",
            "template_key": self.template.template_key,
            "node_version": self.node_version,
            "port": self.port,
            "warnings": self.warnings,
            "allowlist": allowlist,
            "shared_volume_host": str(self.run_workspace.root),
            "shared_volume_container": "/workspace",
            "app_directory": SANDBOX_APP_DIR,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.run_workspace.write_json("handoffs/sandbox-summary.json", summary)
        return SandboxResult("success", "Sandbox files prepared.", summary)

    def start(self) -> SandboxResult:
        if shutil.which("docker") is None:
            result = {"error": "docker executable not found", "timestamp": datetime.now(UTC).isoformat()}
            return SandboxResult("error", "Docker is not available.", result)

        command = [
            "docker", "compose", "-p", self.compose_project, "-f", str(self.compose_path),
            "up", "-d", "--build",
        ]
        completed = subprocess.run(
            command,
            cwd=str(self.compose_path.parent),
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        status = "success" if completed.returncode == 0 else "error"
        details = {
            "command": command,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
        message = "Sandbox container started." if status == "success" else "Sandbox container failed to start."
        return SandboxResult(status, message, details)

    def allowed_commands(self) -> dict[str, list[str]]:
        return dict(self._allowlist)

    def run_allowed(self, command_name: str, timeout: int = 600) -> SandboxResult:
        if command_name not in COMMAND_SLOTS:
            return SandboxResult(
                "error",
                "Requested command slot is not one of the fixed slots.",
                {"requested": command_name, "allowed_slots": list(COMMAND_SLOTS)},
            )
        argv = self._allowlist.get(command_name)
        if argv is None:
            return SandboxResult(
                "error",
                "Command slot has not been prepared for this run.",
                {"requested": command_name, "prepared_slots": list(self._allowlist)},
            )

        command = [
            "docker", "compose", "-p", self.compose_project, "-f", str(self.compose_path),
            "exec", "-T", "-w", SANDBOX_APP_DIR, SERVICE_NAME, *argv,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.compose_path.parent),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                "error",
                f"Command timed out after {timeout}s.",
                {"command_name": command_name, "command": argv, "timeout": timeout, "stdout": exc.stdout or "", "stderr": exc.stderr or ""},
            )

        status = "success" if completed.returncode == 0 else "error"
        details = {
            "command_name": command_name,
            "command": argv,
            "cwd": SANDBOX_APP_DIR,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        return SandboxResult(status, "Sandbox command completed.", details)

    def stop(self, remove_volumes: bool = False) -> SandboxResult:
        if shutil.which("docker") is None:
            return SandboxResult("error", "Docker is not available.", {})
        command = ["docker", "compose", "-p", self.compose_project, "-f", str(self.compose_path), "down"]
        if remove_volumes:
            command.append("-v")
        completed = subprocess.run(
            command,
            cwd=str(self.compose_path.parent),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        status = "success" if completed.returncode == 0 else "error"
        return SandboxResult(status, "Sandbox stopped.", {"returncode": completed.returncode, "stderr_tail": completed.stderr[-2000:]})
