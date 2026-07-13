"""Per-run reporting: token usage, node timings, error reports, and final metrics.

All JSON (never Markdown), per the project's explicit preference, so post-run root-
cause analysis can be scripted rather than read by eye. Centralized here in one
instrumented_node() decorator rather than repeated try/except blocks per node.
"""

from __future__ import annotations

import functools
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Callable

from app.config import ModelConfig
from app.state import FactoryState
from app.workspace import RunWorkspace


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TokenUsageEntry:
    node: str
    iteration: int
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    source: str
    timestamp: str
    note: str = ""


class TokenUsageRecorder:
    def __init__(self, run_ws: RunWorkspace) -> None:
        self.run_ws = run_ws
        self.entries: list[TokenUsageEntry] = []

    def record_from_message(
        self, node: str, iteration: int, model_config: ModelConfig, ai_message: object | None
    ) -> None:
        usage = getattr(ai_message, "usage_metadata", None) if ai_message is not None else None
        if not usage:
            self.record(node, iteration, model_config, 0, 0, source="no_usage_metadata")
            return
        prompt_tokens = int(usage.get("input_tokens", 0))
        completion_tokens = int(usage.get("output_tokens", 0))
        self.record(node, iteration, model_config, prompt_tokens, completion_tokens, source="usage_metadata")

    def record_from_messages(
        self, node: str, iteration: int, model_config: ModelConfig, ai_messages: list[object]
    ) -> None:
        """Record every turn of a tool-calling loop, not just the last one.

        A multi-step loop (e.g. App Writer writing a dozen files) pays for every turn;
        recording only the final message silently drops the bulk of the real cost.
        """

        if not ai_messages:
            self.record(node, iteration, model_config, 0, 0, source="no_usage_metadata")
            return
        for ai_message in ai_messages:
            self.record_from_message(node, iteration, model_config, ai_message)

    def record(
        self,
        node: str,
        iteration: int,
        model_config: ModelConfig,
        prompt_tokens: int,
        completion_tokens: int,
        source: str,
        note: str = "",
    ) -> None:
        total_tokens = prompt_tokens + completion_tokens
        cost = (
            prompt_tokens / 1_000_000 * model_config.input_usd_per_1m
            + completion_tokens / 1_000_000 * model_config.output_usd_per_1m
        )
        self.entries.append(
            TokenUsageEntry(
                node=node,
                iteration=iteration,
                model=model_config.model_name,
                provider=model_config.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=round(cost, 8),
                source=source,
                timestamp=_now_iso(),
                note=note,
            )
        )
        self.flush()

    def totals(self) -> dict[str, float]:
        return {
            "prompt_tokens": sum(e.prompt_tokens for e in self.entries),
            "completion_tokens": sum(e.completion_tokens for e in self.entries),
            "total_tokens": sum(e.total_tokens for e in self.entries),
            "estimated_cost_usd": round(sum(e.estimated_cost_usd for e in self.entries), 8),
        }

    def flush(self) -> None:
        payload = {"entries": [asdict(e) for e in self.entries], "totals": self.totals()}
        self.run_ws.write_json("reports/token-usage-report.json", payload)


@dataclass
class ErrorReport:
    node: str
    iteration: int
    error_type: str
    error_signature: str
    message: str
    traceback_excerpt: str
    context: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)


class ErrorReporter:
    def __init__(self, run_ws: RunWorkspace) -> None:
        self.run_ws = run_ws

    def write(self, report: ErrorReport) -> str:
        ts = report.timestamp.replace(":", "").replace(".", "")
        relative_path = f"reports/errors/{report.node}-{report.iteration}-{ts}.json"
        self.run_ws.write_json(relative_path, asdict(report))
        return relative_path


def error_signature(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{exc}"[:200]


class NodeTimer:
    def __init__(self, run_ws: RunWorkspace) -> None:
        self.run_ws = run_ws
        self.records: list[dict] = []

    def record(self, node: str, iteration: int, started_at: float, ended_at: float, status: str) -> None:
        self.records.append(
            {
                "node": node,
                "iteration": iteration,
                "duration_ms": round((ended_at - started_at) * 1000, 2),
                "status": status,
                "timestamp": _now_iso(),
            }
        )
        self.run_ws.write_json("metrics/node-timings.json", self.records)


class RunMetricsWriter:
    def __init__(self, run_ws: RunWorkspace) -> None:
        self.run_ws = run_ws

    def write_workflow_state(self, state: FactoryState) -> None:
        snapshot = {k: v for k, v in state.items()}
        snapshot["updated_at"] = _now_iso()
        self.run_ws.write_json("metrics/workflow-state.json", snapshot)

    def finalize(self, state: FactoryState, token_recorder: TokenUsageRecorder) -> dict:
        metrics = {
            "run_id": state.get("run_id"),
            "status": state.get("status"),
            "iteration": state.get("iteration"),
            "halt_reason": state.get("halt_reason", ""),
            "errors": state.get("errors", []),
            "known_limitations": state.get("known_limitations", []),
            "token_totals": token_recorder.totals(),
            "finished_at": _now_iso(),
        }
        self.run_ws.write_json("metrics/run-metrics.json", metrics)
        self.run_ws.write_json("reports/final-report.json", metrics)
        return metrics


def instrumented_node(
    name: str,
    error_reporter: ErrorReporter,
    timer: NodeTimer,
    metrics_writer: RunMetricsWriter,
) -> Callable:
    """Decorator applied to every node function.

    Times execution, catches exceptions and converts them into a structured error
    report plus a halting state update (instead of raising), and unconditionally
    snapshots the live workflow state on the way out.
    """

    def decorator(node_fn: Callable[[FactoryState], dict]) -> Callable[[FactoryState], dict]:
        @functools.wraps(node_fn)
        def wrapped(state: FactoryState) -> dict:
            iteration = state.get("iteration", 0)
            started_at = time.monotonic()
            status = "success"
            result: dict = {}
            try:
                result = node_fn(state)
                return result
            except Exception as exc:  # noqa: BLE001 - node failures must not crash the graph
                report = ErrorReport(
                    node=name,
                    iteration=iteration,
                    error_type=type(exc).__name__,
                    error_signature=error_signature(exc),
                    message=str(exc),
                    traceback_excerpt="".join(traceback.format_exception(exc))[-4000:],
                )
                error_reporter.write(report)
                status = "error"
                result = {
                    "halt": True,
                    "status": "agent_failed",
                    "halt_reason": f"{name} failed: {report.message}",
                    "errors": [*state.get("errors", []), report.error_signature],
                    "current_node": name,
                    "last_completed_node": state.get("last_completed_node"),
                }
                return result
            finally:
                ended_at = time.monotonic()
                timer.record(name, iteration, started_at, ended_at, status)
                metrics_writer.write_workflow_state({**state, **result})

        return wrapped

    return decorator
