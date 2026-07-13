"""LangGraph wiring: five instrumented nodes + finalize, all edges conditional.

Any node's failure (instrumented_node sets halt=True) routes straight to finalize
instead of five separate try/except blocks. The only loop is test_runner -> app_writer,
gated by the retry/circuit-breaker bookkeeping done in apply_test_runner_outcome
(inside the test_runner node itself, since LangGraph router functions can't mutate
state).
"""

from __future__ import annotations


from langgraph.graph import END, StateGraph

from app.config import Settings
from app.nodes.app_writer import build_app_writer_node
from app.nodes.finalize import build_finalize_node
from app.nodes.planner import build_planner_node
from app.nodes.sandbox import build_sandbox_node
from app.nodes.test_runner import build_test_runner_node
from app.nodes.test_writer import build_test_writer_node
from app.reporting import ErrorReporter, NodeTimer, RunMetricsWriter, TokenUsageRecorder, instrumented_node
from app.state import FactoryState, should_halt
from app.workspace import RunWorkspace


def make_router(next_node: str):
    def router(state: FactoryState) -> str:
        return "finalize" if should_halt(state) else next_node

    return router


def build_graph(
    run_ws: RunWorkspace,
    settings: Settings,
    recorder: TokenUsageRecorder,
    error_reporter: ErrorReporter,
    timer: NodeTimer,
    metrics_writer: RunMetricsWriter,
    sandbox_start: bool = True,
):
    def instrumented(name: str):
        return instrumented_node(name, error_reporter, timer, metrics_writer)

    planner = instrumented("planner")(build_planner_node(run_ws, settings, recorder))
    sandbox = instrumented("sandbox")(build_sandbox_node(run_ws, settings, recorder, start=sandbox_start))
    app_writer = instrumented("app_writer")(build_app_writer_node(run_ws, settings, recorder))
    test_writer = instrumented("test_writer")(build_test_writer_node(run_ws, settings, recorder))
    test_runner = instrumented("test_runner")(build_test_runner_node(run_ws))
    finalize = instrumented("finalize")(build_finalize_node(run_ws, metrics_writer, recorder))

    graph = StateGraph(FactoryState)
    graph.add_node("planner", planner)
    graph.add_node("sandbox", sandbox)
    graph.add_node("app_writer", app_writer)
    graph.add_node("test_writer", test_writer)
    graph.add_node("test_runner", test_runner)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", make_router("sandbox"), ["sandbox", "finalize"])
    graph.add_conditional_edges("sandbox", make_router("app_writer"), ["app_writer", "finalize"])
    graph.add_conditional_edges("app_writer", make_router("test_writer"), ["test_writer", "finalize"])
    graph.add_conditional_edges("test_writer", make_router("test_runner"), ["test_runner", "finalize"])
    graph.add_conditional_edges("test_runner", make_router("app_writer"), ["app_writer", "finalize"])
    graph.add_edge("finalize", END)

    return graph


def recursion_limit_for(max_agent_iterations: int) -> int:
    # Each retry cycle visits app_writer -> test_writer -> test_runner (3 nodes),
    # plus planner/sandbox/finalize -- LangGraph's own default (25) would trip before
    # max_agent_iterations ever does for anything but the smallest configs.
    return max_agent_iterations * 3 + 5
