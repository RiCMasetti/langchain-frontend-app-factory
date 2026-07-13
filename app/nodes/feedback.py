"""Shared retry-iteration feedback compaction, used by both App Writer and Test Writer.

Both nodes need to see what Test Runner reported last time -- App Writer to fix app
bugs, Test Writer to fix test-authoring bugs (e.g. a query that assumes markup App
Writer didn't actually produce). Only feeding this to App Writer left Test Writer
blind on retries, so a failure caused by the test itself (not the app) could never be
fixed and would repeat until the circuit breaker gave up.
"""

from __future__ import annotations

import json

from app.state import FactoryState

MAX_FEEDBACK_ITEMS = 10


def compact_test_feedback(state: FactoryState) -> str:
    testwriter_summary = state.get("testwriter_summary") or {}
    testrunner_summary = state.get("testrunner_summary") or {}
    if not testwriter_summary and not testrunner_summary:
        return ""

    failures = testrunner_summary.get("failures", [])[:MAX_FEEDBACK_ITEMS]
    return (
        "\nPrevious iteration feedback:\n"
        f"Test Writer notes: {json.dumps(testwriter_summary.get('notes', ''))}\n"
        f"Test Runner status: {testrunner_summary.get('status', 'unknown')}\n"
        f"Test Runner failed step: {testrunner_summary.get('failed_step', 'unknown')}\n"
        f"Test Runner failures (truncated): {json.dumps(failures)}"
    )
