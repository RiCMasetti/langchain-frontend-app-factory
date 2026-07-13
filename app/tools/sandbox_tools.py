"""Sandbox-facing tools: the model may only pick a fixed command slot, never raw argv."""

from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.tools import BaseTool, tool

from app.sandboxing.manager import SandboxManager
from app.sandboxing.templates import TemplateError, get_template
from app.workspace import RunWorkspace


def build_sandbox_prepare_tool(run_ws: RunWorkspace, start: bool = True) -> list[BaseTool]:
    """Tool for the Sandbox Agent node: select a vetted template and render+start it.

    The model supplies the framework key and the three command strings it already read
    from plans/app-plan.json; it never generates Dockerfile/compose content itself.
    """

    @tool
    def sandbox_prepare_tool(
        framework: str,
        install_cmd: str,
        build_cmd: str,
        test_cmd: str,
        node_version: str | None = None,
        port: int | None = None,
    ) -> str:
        """Render and start the sandbox container for one of the vetted framework
        templates (react-vite, vue-vite, nextjs, sveltekit, generic-node)."""

        try:
            template = get_template(framework)
        except TemplateError as exc:
            return json.dumps({"status": "error", "reason": "unknown_framework", "error": str(exc)})

        manager = SandboxManager(run_ws, template, node_version=node_version, port=port)
        prepare_result = manager.prepare(install_cmd, build_cmd, test_cmd)
        if prepare_result.status != "success":
            return json.dumps({"status": "error", "step": "prepare", **asdict(prepare_result)})

        if not start:
            # prepare_result.details is manager.prepare()'s own summary dict, already
            # containing template_key/node_version/port; keep it flat so
            # SandboxManager.load() can read this file back directly.
            summary = {**prepare_result.details, "status": "success", "sandbox_status": "prepared"}
            run_ws.write_json("handoffs/sandbox-summary.json", summary)
            return json.dumps(summary)

        start_result = manager.start()
        summary = {
            "status": "success" if start_result.status == "success" else "error",
            "sandbox_status": "started" if start_result.status == "success" else "failed",
            "template_key": template.template_key,
            "node_version": manager.node_version,
            "port": manager.port,
            "warnings": manager.warnings,
            "start": asdict(start_result),
        }
        run_ws.write_json("handoffs/sandbox-summary.json", summary)
        return json.dumps(summary)

    return [sandbox_prepare_tool]


def build_sandbox_tools(manager: SandboxManager, allowed_slots: frozenset[str]) -> list[BaseTool]:
    """Tools for nodes (e.g. App Writer) allowed to run a subset of command slots
    (typically install/build, never test — that's Test Runner's job alone)."""

    @tool
    def sandbox_command_tool(command_name: str) -> str:
        """Run one allowlisted command slot ("install" or "build") inside this run's sandbox container."""

        if command_name not in allowed_slots:
            return json.dumps(
                {
                    "status": "error",
                    "reason": "not_allowed",
                    "requested": command_name,
                    "allowed": sorted(allowed_slots),
                }
            )
        result = manager.run_allowed(command_name)
        return json.dumps(asdict(result))

    return [sandbox_command_tool]


def build_test_runner_tools(manager: SandboxManager) -> list[BaseTool]:
    """Tools for the Test Runner node: a single fixed install -> build -> test sequence."""

    @tool
    def run_test_tool() -> str:
        """Run install, then build, then test inside this run's sandbox container. Stops at the first failing step."""

        report: dict[str, object] = {}
        for step in ("install", "build", "test"):
            result = manager.run_allowed(step)
            report[step] = asdict(result)
            if result.status != "success":
                report["status"] = "error"
                report["failed_step"] = step
                return json.dumps(report)
        report["status"] = "success"
        return json.dumps(report)

    return [run_test_tool]
