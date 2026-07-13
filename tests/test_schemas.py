from app.schemas import validate_app_plan


def _valid_raw(**overrides: object) -> dict:
    base = {
        "framework": "react-vite",
        "install_cmd": "npm install",
        "build_cmd": "npm run build",
        "test_cmd": "npm test",
    }
    base.update(overrides)
    return base


def test_valid_plan_passes() -> None:
    plan, errors = validate_app_plan(_valid_raw())
    assert errors == []
    assert plan is not None
    assert plan.framework == "react-vite"
    assert plan.install_cmd == "npm install"


def test_missing_required_field_fails() -> None:
    raw = _valid_raw()
    del raw["test_cmd"]
    plan, errors = validate_app_plan(raw)
    assert plan is None
    assert errors


def test_unknown_framework_fails_with_helpful_message() -> None:
    plan, errors = validate_app_plan(_valid_raw(framework="angular-nonexistent"))
    assert plan is None
    assert any("generic-node" in e for e in errors)


def test_optional_fields_default_sensibly() -> None:
    plan, errors = validate_app_plan(_valid_raw())
    assert errors == []
    assert plan.pages == []
    assert plan.components == []
    assert plan.acceptance_criteria == []
    assert plan.node_version is None
    assert plan.port is None


def test_structured_pages_and_components_are_accepted() -> None:
    """Regression test: a model describing pages/components as {name, description}
    objects instead of bare strings is a more useful plan, not an invalid one --
    only framework/install_cmd/build_cmd/test_cmd are load-bearing."""

    raw = _valid_raw(
        pages=[{"route": "/", "name": "PricingPage", "description": "The sole page."}],
        components=[
            {"name": "App", "description": "Root component."},
            {"name": "PricingCard", "description": "One tier card."},
        ],
        stack={"framework": "React 18 + Vite", "dependencies": ["react", "vite"]},
    )
    plan, errors = validate_app_plan(raw)
    assert errors == []
    assert plan is not None
    assert plan.pages[0]["name"] == "PricingPage"
    assert plan.components[1]["name"] == "PricingCard"


def test_unexpected_extra_top_level_field_is_tolerated() -> None:
    raw = _valid_raw(dependencies="react, react-dom, vitest")
    plan, errors = validate_app_plan(raw)
    assert errors == []
    assert plan is not None
