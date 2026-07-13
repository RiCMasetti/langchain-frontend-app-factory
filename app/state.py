"""Shared LangGraph state schema.

Deliberately carries only small structured dicts (handoff summaries) and control-flow
bookkeeping -- never a shared `messages` list across nodes. Each node runs its own
short-lived tool-calling loop internally and only the parsed result (plus the last
AIMessage, for token accounting) crosses back into this state. This keeps token usage
bounded across retries, mirroring the reference ADK repo's per-agent session isolation.
"""

from __future__ import annotations

from typing import Literal, TypedDict

NodeName = Literal["planner", "sandbox", "app_writer", "test_writer", "test_runner", "finalize"]

RunStatus = Literal[
    "running",
    "accepted",
    "failed",
    "agent_failed",
    "max_iterations_reached",
]


class FactoryState(TypedDict, total=False):
    # run identity
    run_id: str
    run_dir: str
    user_request: str

    # control flow
    status: RunStatus
    halt: bool
    halt_reason: str
    current_node: str
    last_completed_node: str | None
    iteration: int
    max_agent_iterations: int
    repeated_error_signature: str | None
    repeated_error_count: int

    # handoffs (compact dicts, mirror workspace/run/<id>/handoffs/*.json 1:1)
    planner_summary: dict
    app_plan: dict
    sandbox_summary: dict
    appwriter_summary: dict
    testwriter_summary: dict
    testrunner_summary: dict

    errors: list[str]
    known_limitations: list[str]


def initial_state(run_id: str, run_dir: str, user_request: str, max_agent_iterations: int) -> FactoryState:
    return FactoryState(
        run_id=run_id,
        run_dir=run_dir,
        user_request=user_request,
        status="running",
        halt=False,
        halt_reason="",
        current_node="",
        last_completed_node=None,
        iteration=0,
        max_agent_iterations=max_agent_iterations,
        repeated_error_signature=None,
        repeated_error_count=0,
        errors=[],
        known_limitations=[],
    )


def should_halt(state: FactoryState) -> bool:
    return bool(state.get("halt")) or state.get("status") in (
        "accepted",
        "failed",
        "agent_failed",
        "max_iterations_reached",
    )
