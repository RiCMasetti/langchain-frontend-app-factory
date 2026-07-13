"""Lenient JSON parsing for LLM-produced text.

Used for every agent handoff dict. Never raises: on total failure it returns a dict
carrying the parse error and the raw text, so a node can decide to re-prompt or halt
rather than crash on a malformed but non-critical field.
"""

from __future__ import annotations

import json
import re

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

MAX_RAW_TEXT_ECHO = 2000


def parse_llm_json(text: str) -> dict:
    stripped = _CODE_FENCE_RE.sub("", text).strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        pass

    try:
        from json_repair import repair_json

        repaired = repair_json(stripped, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
        if isinstance(repaired, list):
            return {"value": repaired}
    except Exception:  # noqa: BLE001 - repair is best-effort, never allowed to raise
        pass

    return {"_parse_error": "could_not_parse_json", "_raw_text": text[:MAX_RAW_TEXT_ECHO]}
