"""Validate app-plan.json command strings before they're allowlisted for a run.

This is the actual command-injection security boundary: the LLM never supplies raw
argv to the sandbox, it only ever supplies a fixed slot name (install/build/test)
whose argv was validated once here, at prepare() time, from app-plan.json.
"""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath

_SHELL_METACHARACTERS = set(";|&$`<>\n")


class CommandValidationError(ValueError):
    """Raised when an app-plan.json command string is unsafe or not allowlisted."""


def validate_command(raw: str, allowed_binaries: frozenset[str]) -> list[str]:
    """Tokenize and validate a command string. Returns the argv list on success."""

    if not raw or not raw.strip():
        raise CommandValidationError("command must not be empty")
    if any(ch in raw for ch in _SHELL_METACHARACTERS):
        raise CommandValidationError(
            f"command {raw!r} contains disallowed shell metacharacters"
        )

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise CommandValidationError(f"command {raw!r} could not be tokenized: {exc}") from exc

    if not tokens:
        raise CommandValidationError("command must not be empty")

    binary = tokens[0]
    if PurePosixPath(binary).is_absolute():
        raise CommandValidationError(f"binary {binary!r} must not be an absolute path")
    if binary not in allowed_binaries:
        raise CommandValidationError(
            f"binary {binary!r} is not in the allowlist for this template "
            f"(allowed: {sorted(allowed_binaries)})"
        )

    return tokens
