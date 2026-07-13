"""The one hard-schema artifact: app-plan.json.

Every other agent handoff is parsed leniently via jsonutil.parse_llm_json() and never
gets a Pydantic model, per the project's explicit preference to avoid strict validation
on low-determinism LLM output. AppPlan is the exception: Test Runner's framework-
agnosticism depends on install_cmd/build_cmd/test_cmd always being present, so this is
the one place a hard validation gate is worth the risk of rejecting a plausible-but-
malformed LLM response.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.sandboxing.templates import FRAMEWORK_TEMPLATES


class AppPlan(BaseModel):
    # Only framework/install_cmd/build_cmd/test_cmd are load-bearing (Test Runner
    # depends on them existing). Everything else here is descriptive content for
    # downstream agents, so it's typed loosely (Any) rather than forced into a flat
    # shape -- a model describing components as {name, description} objects instead
    # of bare strings is a reasonable, more useful plan, not an invalid one.
    model_config = ConfigDict(extra="allow")

    framework: str = Field(description="One of the vetted sandbox template keys, e.g. react-vite, generic-node.")
    install_cmd: str
    build_cmd: str
    test_cmd: str
    start_cmd: str | None = None
    node_version: str | None = None
    port: int | None = None
    summary: str = ""
    pages: list[Any] = Field(default_factory=list)
    components: list[Any] = Field(default_factory=list)
    acceptance_criteria: list[Any] = Field(default_factory=list)
    stack: dict[str, Any] = Field(default_factory=dict)


def validate_app_plan(raw: dict) -> tuple[AppPlan | None, list[str]]:
    """Validate a lenient dict against AppPlan. Returns (plan, errors); plan is None on failure."""

    try:
        plan = AppPlan.model_validate(raw)
    except ValidationError as exc:
        return None, [str(err) for err in exc.errors()]

    if plan.framework not in FRAMEWORK_TEMPLATES:
        return None, [
            f"framework {plan.framework!r} is not one of the vetted templates: "
            f"{sorted(FRAMEWORK_TEMPLATES)}. Use 'generic-node' as a fallback."
        ]

    return plan, []
