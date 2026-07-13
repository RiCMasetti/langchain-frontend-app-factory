"""Run lifecycle: mint a run_id, wire it through the workspace dir, the graph, the
checkpointer's thread_id, and the LangSmith trace, then invoke the graph once.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from app.config import Settings
from app.graph import build_graph, recursion_limit_for
from app.reporting import ErrorReporter, NodeTimer, RunMetricsWriter, TokenUsageRecorder
from app.state import FactoryState, initial_state
from app.workspace import RunWorkspace

LANGSMITH_PROJECT_ENV = "LANGSMITH_PROJECT"


@contextmanager
def _checkpointer_context(settings: Settings, run_ws: RunWorkspace) -> Iterator[object | None]:
    if settings.checkpoint_backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = run_ws.root / "metrics" / "checkpoint.sqlite"
        with SqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
    elif settings.checkpoint_backend == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        yield MemorySaver()
    else:
        yield None


def _run_config(run_id: str, run_ws: RunWorkspace, settings: Settings, resumed_from: str | None = None) -> dict:
    langsmith_run_id = str(uuid.uuid4()) if resumed_from else run_id
    metadata = {"run_id": run_id, "run_dir": str(run_ws.root)}
    if resumed_from:
        metadata["resumed_from_run_id"] = resumed_from
    return {
        "run_id": uuid.UUID(langsmith_run_id),
        "run_name": f"frontend-app-factory:{run_id}",
        "tags": ["frontend-app-factory", f"run:{run_id}"],
        "metadata": metadata,
        "configurable": {"thread_id": run_id},
        "recursion_limit": recursion_limit_for(settings.max_agent_iterations),
    }


def _append_run_index(settings: Settings, run_id: str, user_request: str) -> None:
    index_path = settings.workspace_dir / "run" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    entries = json.loads(index_path.read_text()) if index_path.exists() else []
    entries.append(
        {
            "run_id": run_id,
            "user_request_excerpt": user_request[:120],
            "started_at": datetime.now(UTC).isoformat(),
        }
    )
    index_path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")


def create_run_context(user_request: str, settings: Settings) -> tuple[str, RunWorkspace]:
    run_id = str(uuid.uuid4())
    run_ws = RunWorkspace.for_run(run_id, settings.workspace_dir)
    run_ws.ensure_layout()
    run_ws.write_text("input/user-request.md", user_request)
    return run_id, run_ws


def run_once(user_request: str, settings: Settings, sandbox_start: bool = True) -> FactoryState:
    run_id, run_ws = create_run_context(user_request, settings)

    recorder = TokenUsageRecorder(run_ws)
    error_reporter = ErrorReporter(run_ws)
    timer = NodeTimer(run_ws)
    metrics_writer = RunMetricsWriter(run_ws)
    graph_builder = build_graph(
        run_ws, settings, recorder, error_reporter, timer, metrics_writer, sandbox_start=sandbox_start
    )

    with _checkpointer_context(settings, run_ws) as checkpointer:
        compiled = graph_builder.compile(checkpointer=checkpointer)
        config = _run_config(run_id, run_ws, settings)
        state = initial_state(run_id, str(run_ws.root), user_request, settings.max_agent_iterations)
        final_state = compiled.invoke(state, config=config)

    _append_run_index(settings, run_id, user_request)
    return final_state


def resume_run(run_id: str, settings: Settings, sandbox_start: bool = True) -> FactoryState:
    run_ws = RunWorkspace.for_run(run_id, settings.workspace_dir)
    if not run_ws.root.exists():
        raise FileNotFoundError(f"No run directory found for run_id={run_id!r}")
    if settings.checkpoint_backend != "sqlite":
        raise ValueError("resume_run requires checkpoint_backend='sqlite'")

    recorder = TokenUsageRecorder(run_ws)
    error_reporter = ErrorReporter(run_ws)
    timer = NodeTimer(run_ws)
    metrics_writer = RunMetricsWriter(run_ws)
    graph_builder = build_graph(
        run_ws, settings, recorder, error_reporter, timer, metrics_writer, sandbox_start=sandbox_start
    )

    with _checkpointer_context(settings, run_ws) as checkpointer:
        compiled = graph_builder.compile(checkpointer=checkpointer)
        config = _run_config(run_id, run_ws, settings, resumed_from=run_id)
        final_state = compiled.invoke(None, config=config)

    return final_state


def list_runs(settings: Settings) -> list[dict]:
    index_path = settings.workspace_dir / "run" / "index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text())
