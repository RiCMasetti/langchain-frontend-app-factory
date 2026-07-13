"""Test Runner node: executes install -> build -> test inside the sandbox and reports.

Deterministic, not an LLM tool-calling loop -- there's no judgment call in "run the
three commands app-plan.json already specified and report what happened." Keeping it
LLM-free also means retries cost zero extra tokens on the mechanical part.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from app.sandboxing.manager import SandboxManager
from app.state import FactoryState
from app.tools.sandbox_tools import build_test_runner_tools
from app.workspace import RunWorkspace

MAX_REPEATED_ERROR_SIGNATURE = 3

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
# Prefer a line naming the failing test file/location (stable across otherwise-noisy
# reruns) as the circuit-breaker fingerprint anchor, e.g. "src/App.test.jsx:196:21".
_TEST_LOCATION_RE = re.compile(r"\S+\.(?:test|spec)\.[jt]sx?:\d+(?::\d+)?")
_ERROR_KEYWORD_RE = re.compile(r"\b\w*Error\w*\b")


def build_test_runner_node(run_ws: RunWorkspace) -> Callable[[FactoryState], dict]:
    def test_runner_node(state: FactoryState) -> dict:
        manager = SandboxManager.load(run_ws)
        (run_test_tool,) = build_test_runner_tools(manager)
        report = run_test_tool.invoke({})
        report_dict = json.loads(report)
        run_ws.write_json("reports/test-report.json", report_dict)

        testrunner_summary = {
            "status": report_dict.get("status", "error"),
            "failed_step": report_dict.get("failed_step"),
            "failures": _extract_failures(report_dict),
        }
        run_ws.write_json("handoffs/testrunner-summary.json", testrunner_summary)

        return apply_test_runner_outcome(state, testrunner_summary)

    return test_runner_node


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _extract_failures(report_dict: dict) -> list[str]:
    failed_step = report_dict.get("failed_step")
    if not failed_step:
        return []
    step_result = report_dict.get(failed_step, {})
    details = step_result.get("details", {})
    stderr = _strip_ansi(details.get("stderr", ""))
    return [line for line in stderr.splitlines() if line.strip()][-20:]


def _signature_fingerprint(failures: list[str]) -> str:
    """Pick a stable anchor line from the failure output for the repeated-error
    circuit breaker. A raw "last line of stderr" is fragile -- it can drift between
    otherwise-identical failures (differing stack depth, wrapped output, etc.) and
    silently defeat the circuit breaker. A test file:line reference, or else a line
    naming an Error type, is far more likely to repeat verbatim across iterations
    when the same underlying test is still failing the same way.
    """

    for line in failures:
        if _TEST_LOCATION_RE.search(line):
            return _TEST_LOCATION_RE.search(line).group(0)
    for line in failures:
        if _ERROR_KEYWORD_RE.search(line):
            return line.strip()
    return failures[0] if failures else "no_output"


def _error_signature(testrunner_summary: dict) -> str | None:
    if testrunner_summary.get("status") == "success":
        return None
    failed_step = testrunner_summary.get("failed_step", "unknown")
    failures = testrunner_summary.get("failures", [])
    fingerprint = _signature_fingerprint(failures)
    return f"{failed_step}:{fingerprint}"[:200]


def apply_test_runner_outcome(state: FactoryState, testrunner_summary: dict) -> dict:
    """Pure function: given state + this iteration's Test Runner summary, decide
    whether to accept the run, retry (route back to App Writer), or halt.

    Unit-testable in isolation from the graph -- LangGraph conditional-edge functions
    cannot mutate state, so all of this bookkeeping happens here, in the node, not in
    a router.
    """

    iteration = state.get("iteration", 0) + 1
    max_iterations = state.get("max_agent_iterations", 5)
    update: dict = {
        "testrunner_summary": testrunner_summary,
        "iteration": iteration,
        "current_node": "test_runner",
        "last_completed_node": "test_runner",
    }

    if testrunner_summary.get("status") == "success":
        update["status"] = "accepted"
        update["halt"] = True
        return update

    signature = _error_signature(testrunner_summary)
    previous_signature = state.get("repeated_error_signature")
    if signature is not None and signature == previous_signature:
        repeated_count = state.get("repeated_error_count", 0) + 1
    else:
        repeated_count = 1
    update["repeated_error_signature"] = signature
    update["repeated_error_count"] = repeated_count

    if repeated_count >= MAX_REPEATED_ERROR_SIGNATURE:
        update["status"] = "failed"
        update["halt"] = True
        update["halt_reason"] = f"Same error ({signature}) repeated {repeated_count} times consecutively."
        return update

    if iteration >= max_iterations:
        update["status"] = "max_iterations_reached"
        update["halt"] = True
        update["halt_reason"] = f"Reached max_agent_iterations={max_iterations} without a passing test run."
        return update

    update["status"] = "running"
    update["halt"] = False
    return update
