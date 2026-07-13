"""Per-run workspace filesystem helpers: path containment and context-hygiene ignore lists."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUN_DIRS = (
    "input",
    "app",
    "plans",
    "handoffs",
    "reports",
    "reports/errors",
    "metrics",
    "sandbox",
)

CONTEXT_HEAVY_DIRS = frozenset(
    {
        ".git",
        ".cache",
        ".next",
        ".nuxt",
        ".parcel-cache",
        ".pytest_cache",
        ".ruff_cache",
        ".turbo",
        ".vite",
        ".svelte-kit",
        ".angular",
        ".output",
        "coverage",
        "dist",
        "build",
        "node_modules",
    }
)
CONTEXT_HEAVY_FILES = frozenset(
    {
        ".package-lock.json",
        "bun.lockb",
        "bun.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
)
DEFAULT_LIST_FILES_LIMIT = 500
MAX_TOOL_READ_CHARS = 20_000

_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class WorkspaceError(ValueError):
    """Raised when a workspace path or run_id is invalid."""


@dataclass(frozen=True)
class RunWorkspace:
    root: Path
    run_id: str

    @classmethod
    def for_run(cls, run_id: str, workspace_root: Path = Path("workspace")) -> "RunWorkspace":
        if not _RUN_ID_RE.match(run_id):
            raise WorkspaceError(f"Invalid run_id: {run_id!r}")
        root = (workspace_root / "run" / run_id).expanduser().resolve()
        return cls(root=root, run_id=run_id)

    def ensure_layout(self) -> None:
        for directory in RUN_DIRS:
            (self.root / directory).mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        return self._resolve_under(self.root, relative_path)

    def app_resolve(self, relative_path: str | Path) -> Path:
        return self._resolve_under(self.root / "app", relative_path)

    @staticmethod
    def _resolve_under(base: Path, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise WorkspaceError(f"Paths must be relative: {relative}")
        candidate = (base / relative).resolve()
        if candidate != base and base not in candidate.parents:
            raise WorkspaceError(f"Path escapes workspace: {relative}")
        return candidate

    def write_text(self, relative_path: str | Path, content: str) -> Path:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_app_text(self, relative_path: str | Path, content: str) -> Path:
        target = self.app_resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read_text(self, relative_path: str | Path, max_chars: int = MAX_TOOL_READ_CHARS) -> str:
        target = self.resolve(relative_path)
        data = target.read_text(encoding="utf-8", errors="replace")
        clamped = min(max_chars, MAX_TOOL_READ_CHARS)
        if len(data) > clamped:
            return data[:clamped] + "\n...[truncated]"
        return data

    def write_json(self, relative_path: str | Path, payload: dict[str, Any] | list[Any]) -> Path:
        return self.write_text(relative_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def read_json(self, relative_path: str | Path) -> Any:
        return json.loads(self.resolve(relative_path).read_text(encoding="utf-8"))

    def exists(self, relative_path: str | Path) -> bool:
        return self.resolve(relative_path).exists()

    def list_files(
        self,
        base: Path | None = None,
        relative_path: str | Path = ".",
        max_files: int = DEFAULT_LIST_FILES_LIMIT,
    ) -> list[str]:
        root = base if base is not None else self.root
        start = self._resolve_under(root, relative_path)
        if not start.exists():
            return []
        if start.is_file():
            return [str(start.relative_to(root))]

        files: list[str] = []
        for path in sorted(start.rglob("*")):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if any(part in CONTEXT_HEAVY_DIRS for part in relative_parts):
                continue
            if path.name in CONTEXT_HEAVY_FILES:
                continue
            files.append(str(path.relative_to(root)))
            if len(files) >= max_files:
                files.append(f"...[truncated after {max_files} files]")
                break
        return files
