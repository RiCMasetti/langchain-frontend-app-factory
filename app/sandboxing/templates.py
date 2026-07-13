"""Registry of vetted per-framework Docker sandbox templates.

Templates are fixed Dockerfile/docker-compose content shipped with this repo under
sandbox/templates/<key>/. The only parameterizable knobs are the Node version and the
port, both validated against the template's own allowlist before rendering. The Sandbox
Agent selects a template key; it never generates Dockerfile content itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent.parent / "sandbox" / "templates"

FALLBACK_TEMPLATE_KEY = "generic-node"


class TemplateError(ValueError):
    """Raised when a framework template is unknown or malformed."""


@dataclass(frozen=True)
class FrameworkTemplate:
    template_key: str
    display_name: str
    default_node_version: str
    allowed_node_versions: tuple[str, ...]
    default_port: int
    allowed_command_binaries: frozenset[str]
    dockerfile_template: str
    compose_template: str

    def resolve_node_version(self, requested: str | None) -> tuple[str, str | None]:
        """Return (node_version, warning). Falls back to the default on an invalid request."""

        if requested is None:
            return self.default_node_version, None
        if requested in self.allowed_node_versions:
            return requested, None
        warning = (
            f"node_version {requested!r} is not allowed for template "
            f"{self.template_key!r} (allowed: {sorted(self.allowed_node_versions)}); "
            f"falling back to default {self.default_node_version!r}"
        )
        return self.default_node_version, warning

    def resolve_port(self, requested: int | None) -> tuple[int, str | None]:
        if requested is None:
            return self.default_port, None
        if isinstance(requested, int) and 1024 <= requested <= 65535:
            return requested, None
        warning = (
            f"port {requested!r} is not a valid port in [1024, 65535]; "
            f"falling back to default {self.default_port!r}"
        )
        return self.default_port, warning

    def render_dockerfile(self, node_version: str, port: int) -> str:
        return _render(self.dockerfile_template, node_version, port)

    def render_compose(self, node_version: str, port: int) -> str:
        return _render(self.compose_template, node_version, port)


def _render(template_text: str, node_version: str, port: int) -> str:
    from string import Template

    return Template(template_text).safe_substitute(NODE_VERSION=node_version, PORT=str(port))


def _load_template(template_dir: Path) -> FrameworkTemplate:
    metadata = json.loads((template_dir / "template.json").read_text(encoding="utf-8"))
    return FrameworkTemplate(
        template_key=metadata["template_key"],
        display_name=metadata["display_name"],
        default_node_version=metadata["default_node_version"],
        allowed_node_versions=tuple(metadata["allowed_node_versions"]),
        default_port=int(metadata["default_port"]),
        allowed_command_binaries=frozenset(metadata["allowed_command_binaries"]),
        dockerfile_template=(template_dir / "Dockerfile").read_text(encoding="utf-8"),
        compose_template=(template_dir / "docker-compose.yml").read_text(encoding="utf-8"),
    )


def _discover_templates(root: Path = TEMPLATES_ROOT) -> dict[str, FrameworkTemplate]:
    templates: dict[str, FrameworkTemplate] = {}
    if not root.exists():
        return templates
    for template_dir in sorted(root.iterdir()):
        if not template_dir.is_dir():
            continue
        if not (template_dir / "template.json").exists():
            continue
        template = _load_template(template_dir)
        templates[template.template_key] = template
    return templates


FRAMEWORK_TEMPLATES: dict[str, FrameworkTemplate] = _discover_templates()


def get_template(framework: str) -> FrameworkTemplate:
    try:
        return FRAMEWORK_TEMPLATES[framework]
    except KeyError:
        raise TemplateError(
            f"Unknown framework {framework!r}. Valid frameworks: "
            f"{sorted(FRAMEWORK_TEMPLATES)}. Use {FALLBACK_TEMPLATE_KEY!r} as a fallback."
        ) from None
