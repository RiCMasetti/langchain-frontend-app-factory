"""Small shared helper for picking a subset of tools by name."""

from __future__ import annotations

from langchain_core.tools import BaseTool


def select_tools(tools: list[BaseTool], names: list[str]) -> list[BaseTool]:
    by_name = {t.name: t for t in tools}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise KeyError(f"tool(s) not found: {missing} (available: {sorted(by_name)})")
    return [by_name[n] for n in names]
