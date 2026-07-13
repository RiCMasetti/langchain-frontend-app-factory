"""Central configuration: model providers, workspace root, and workflow limits."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


class ConfigurationError(ValueError):
    """Raised when runtime configuration is invalid."""


def load_env_file(path: str | Path = ".env") -> None:
    """Load a simple KEY=VALUE .env file without overriding existing variables."""

    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ConfigurationError(f"{name} must be >= 1")
    return value


BEDROCK_PROVIDERS = frozenset({"bedrock", "bedrock_converse"})


def resolve_bedrock_region() -> str | None:
    return os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")


@dataclass(frozen=True)
class ModelConfig:
    role: str
    provider: str
    model_name: str
    input_usd_per_1m: float = 0.0
    output_usd_per_1m: float = 0.0

    def init_chat_model(self, **kwargs: object):
        """Build a LangChain chat model for this role via init_chat_model().

        For Bedrock providers, wires the AWS_BEARER_TOKEN_BEDROCK env var (Bedrock API
        key auth, no full AWS credentials needed) and a resolved region through
        explicitly rather than relying on langchain-aws/boto3 to pick them up
        implicitly, so misconfiguration surfaces here instead of deep in boto3.
        """

        from langchain.chat_models import init_chat_model

        extra: dict[str, object] = dict(kwargs)
        if self.provider in BEDROCK_PROVIDERS:
            bearer_token = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
            if bearer_token:
                extra.setdefault("bedrock_api_key", bearer_token)
            region = resolve_bedrock_region()
            if region:
                extra.setdefault("region_name", region)

        return init_chat_model(self.model_name, model_provider=self.provider, **extra)


@dataclass(frozen=True)
class Settings:
    planner_model: ModelConfig
    worker_model: ModelConfig
    workspace_dir: Path
    max_agent_iterations: int
    agent_call_timeout_seconds: int
    checkpoint_backend: Literal["sqlite", "memory", "none"]
    langsmith_project: str = field(default="frontend-app-factory")
    app_writer_max_steps: int = 60
    test_writer_max_steps: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        planner_model = ModelConfig(
            role="planner",
            provider=os.getenv("PLANNER_MODEL_PROVIDER", "anthropic"),
            model_name=os.getenv("PLANNER_MODEL_NAME", "claude-sonnet-4-6"),
            input_usd_per_1m=float(os.getenv("PLANNER_INPUT_USD_PER_1M", "0")),
            output_usd_per_1m=float(os.getenv("PLANNER_OUTPUT_USD_PER_1M", "0")),
        )
        worker_model = ModelConfig(
            role="worker",
            provider=os.getenv("WORKER_MODEL_PROVIDER", "anthropic"),
            model_name=os.getenv("WORKER_MODEL_NAME", "claude-haiku-4-5"),
            input_usd_per_1m=float(os.getenv("WORKER_INPUT_USD_PER_1M", "0")),
            output_usd_per_1m=float(os.getenv("WORKER_OUTPUT_USD_PER_1M", "0")),
        )

        checkpoint_backend = os.getenv("CHECKPOINT_BACKEND", "sqlite")
        if checkpoint_backend not in ("sqlite", "memory", "none"):
            raise ConfigurationError(
                f"CHECKPOINT_BACKEND must be one of sqlite|memory|none, got {checkpoint_backend!r}"
            )

        return cls(
            planner_model=planner_model,
            worker_model=worker_model,
            workspace_dir=Path(os.getenv("WORKSPACE_DIR", "./workspace")).expanduser(),
            max_agent_iterations=parse_positive_int("MAX_AGENT_ITERATIONS", 5),
            agent_call_timeout_seconds=parse_positive_int("AGENT_CALL_TIMEOUT_SECONDS", 900),
            checkpoint_backend=checkpoint_backend,  # type: ignore[arg-type]
            langsmith_project=os.getenv("LANGSMITH_PROJECT", "frontend-app-factory"),
            app_writer_max_steps=parse_positive_int("APP_WRITER_MAX_STEPS", 60),
            test_writer_max_steps=parse_positive_int("TEST_WRITER_MAX_STEPS", 60),
        )
