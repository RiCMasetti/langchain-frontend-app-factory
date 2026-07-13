from app.nodes.test_runner import _error_signature, _extract_failures, apply_test_runner_outcome
from app.state import initial_state


def _state(**overrides):
    state = initial_state("run-1", "/tmp/run-1", "build me a page", max_agent_iterations=5)
    state.update(overrides)
    return state


def test_success_accepts_and_halts() -> None:
    state = _state(iteration=0)
    update = apply_test_runner_outcome(state, {"status": "success"})
    assert update["status"] == "accepted"
    assert update["halt"] is True
    assert update["iteration"] == 1


def test_failure_retries_when_below_thresholds() -> None:
    state = _state(iteration=0)
    update = apply_test_runner_outcome(
        state, {"status": "error", "failed_step": "test", "failures": ["AssertionError: boom"]}
    )
    assert update["status"] == "running"
    assert update["halt"] is False
    assert update["repeated_error_count"] == 1
    assert update["repeated_error_signature"] == "test:AssertionError: boom"


def test_repeated_identical_error_signature_trips_circuit_breaker() -> None:
    state = _state(iteration=0, repeated_error_signature="test:AssertionError: boom", repeated_error_count=2)
    update = apply_test_runner_outcome(
        state, {"status": "error", "failed_step": "test", "failures": ["AssertionError: boom"]}
    )
    assert update["repeated_error_count"] == 3
    assert update["status"] == "failed"
    assert update["halt"] is True
    assert "repeated" in update["halt_reason"]


def test_different_error_signature_resets_repeat_count() -> None:
    state = _state(iteration=0, repeated_error_signature="test:OldError", repeated_error_count=2)
    update = apply_test_runner_outcome(
        state, {"status": "error", "failed_step": "build", "failures": ["TypeError: nope"]}
    )
    assert update["repeated_error_count"] == 1
    assert update["status"] == "running"


def test_max_iterations_reached_halts() -> None:
    state = _state(iteration=4, max_agent_iterations=5)
    update = apply_test_runner_outcome(
        state, {"status": "error", "failed_step": "test", "failures": ["a"]}
    )
    assert update["iteration"] == 5
    assert update["status"] == "max_iterations_reached"
    assert update["halt"] is True


def test_extract_failures_strips_ansi_color_codes() -> None:
    report = {
        "failed_step": "test",
        "test": {"details": {"stderr": "\x1b[36m❯\x1b[39m src/App.test.jsx:10:5\n\x1b[31mAssertionError\x1b[39m"}},
    }
    failures = _extract_failures(report)
    assert failures == ["❯ src/App.test.jsx:10:5", "AssertionError"]


def test_error_signature_anchors_on_test_location_not_noisy_tail_line() -> None:
    """Regression test for a real run: the last line of stderr was an arbitrary,
    non-representative fragment (closing HTML tags/formatting), which meant the
    circuit breaker never recognized the same test failing the same way across
    iterations. A stable test file:line anchor fixes that."""

    failures_iteration_a = [
        "        </footer>",
        "  ❯ src/integration.test.jsx:196:21",
        "    196|       expect(screen.getByText(/Loomforge/)).toBeInTheDocument()",
    ]
    failures_iteration_b = [
        "    </div>",  # different trailing noise line than iteration a
        "  ❯ src/integration.test.jsx:196:21",  # same failing assertion
        "    196|       expect(screen.getByText(/Loomforge/)).toBeInTheDocument()",
    ]
    sig_a = _error_signature({"status": "error", "failed_step": "test", "failures": failures_iteration_a})
    sig_b = _error_signature({"status": "error", "failed_step": "test", "failures": failures_iteration_b})
    assert sig_a == sig_b
    assert "integration.test.jsx:196" in sig_a


def test_error_signature_falls_back_to_error_keyword_line() -> None:
    signature = _error_signature(
        {"status": "error", "failed_step": "build", "failures": ["some noise", "TypeError: nope"]}
    )
    assert signature == "build:TypeError: nope"


def test_error_signature_falls_back_to_first_line_when_no_anchor_found() -> None:
    signature = _error_signature({"status": "error", "failed_step": "test", "failures": ["just noise"]})
    assert signature == "test:just noise"
