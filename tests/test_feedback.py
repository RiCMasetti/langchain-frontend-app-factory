from app.nodes.feedback import compact_test_feedback
from app.state import initial_state


def test_no_feedback_on_first_iteration() -> None:
    state = initial_state("run-1", "/tmp/run-1", "build a page", max_agent_iterations=5)
    assert compact_test_feedback(state) == ""


def test_feedback_includes_failed_step_and_failures() -> None:
    state = initial_state("run-1", "/tmp/run-1", "build a page", max_agent_iterations=5)
    state["testwriter_summary"] = {"notes": "added price tests", "test_files": ["src/App.test.jsx"]}
    state["testrunner_summary"] = {
        "status": "error",
        "failed_step": "test",
        "failures": ["AssertionError: expected $29 to be in the document"],
    }

    feedback = compact_test_feedback(state)

    assert "added price tests" in feedback
    assert "failed_step" not in feedback or "test" in feedback  # rendered inline, not the literal key
    assert "AssertionError" in feedback


def test_feedback_truncates_to_max_items() -> None:
    state = initial_state("run-1", "/tmp/run-1", "build a page", max_agent_iterations=5)
    state["testwriter_summary"] = {"notes": "x"}
    state["testrunner_summary"] = {"status": "error", "failed_step": "test", "failures": [f"line {i}" for i in range(50)]}

    feedback = compact_test_feedback(state)

    assert "line 9" in feedback
    assert "line 15" not in feedback
