from app.graph import make_router
from app.state import initial_state


def test_router_continues_to_next_node_when_not_halted() -> None:
    router = make_router("sandbox")
    state = initial_state("run-1", "/tmp/run-1", "req", max_agent_iterations=5)
    assert router(state) == "sandbox"


def test_router_goes_to_finalize_when_halted() -> None:
    router = make_router("sandbox")
    state = initial_state("run-1", "/tmp/run-1", "req", max_agent_iterations=5)
    state["halt"] = True
    assert router(state) == "finalize"


def test_router_goes_to_finalize_on_terminal_status_even_without_explicit_halt() -> None:
    router = make_router("app_writer")
    state = initial_state("run-1", "/tmp/run-1", "req", max_agent_iterations=5)
    state["status"] = "accepted"
    assert router(state) == "finalize"


def test_test_runner_loop_router_returns_app_writer_when_still_running() -> None:
    router = make_router("app_writer")
    state = initial_state("run-1", "/tmp/run-1", "req", max_agent_iterations=5)
    state["status"] = "running"
    assert router(state) == "app_writer"
