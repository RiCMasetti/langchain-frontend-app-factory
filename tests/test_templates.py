import pytest

from app.sandboxing.templates import FRAMEWORK_TEMPLATES, TemplateError, get_template


def test_all_five_templates_discovered() -> None:
    assert set(FRAMEWORK_TEMPLATES) == {
        "react-vite", "vue-vite", "nextjs", "sveltekit", "generic-node",
    }


def test_get_template_unknown_framework_raises() -> None:
    with pytest.raises(TemplateError):
        get_template("angular-does-not-exist")


def test_resolve_node_version_valid_passthrough() -> None:
    template = get_template("react-vite")
    version, warning = template.resolve_node_version("20")
    assert version == "20"
    assert warning is None


def test_resolve_node_version_invalid_falls_back_with_warning() -> None:
    template = get_template("react-vite")
    version, warning = template.resolve_node_version("6")
    assert version == template.default_node_version
    assert warning is not None


def test_resolve_port_invalid_falls_back_with_warning() -> None:
    template = get_template("react-vite")
    port, warning = template.resolve_port(80)
    assert port == template.default_port
    assert warning is not None


def test_render_dockerfile_substitutes_placeholders() -> None:
    template = get_template("react-vite")
    rendered = template.render_dockerfile("20", 5173)
    assert "node:20-bookworm-slim" in rendered
    assert "EXPOSE 5173" in rendered
    assert "${NODE_VERSION}" not in rendered
    assert "${PORT}" not in rendered


def test_render_compose_substitutes_placeholders() -> None:
    template = get_template("react-vite")
    rendered = template.render_compose("22", 5173)
    assert "5173:5173" in rendered
    assert "${PORT}" not in rendered
