from app.config import ModelConfig, resolve_bedrock_region


def test_resolve_bedrock_region_prefers_bedrock_region_env(monkeypatch) -> None:
    monkeypatch.setenv("BEDROCK_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert resolve_bedrock_region() == "eu-central-1"


def test_resolve_bedrock_region_falls_back_through_chain(monkeypatch) -> None:
    monkeypatch.delenv("BEDROCK_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    assert resolve_bedrock_region() == "ap-southeast-2"


def test_resolve_bedrock_region_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("BEDROCK_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert resolve_bedrock_region() is None


def test_init_chat_model_wires_bearer_token_and_region_for_bedrock_provider(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bearer-token")
    monkeypatch.setenv("BEDROCK_REGION", "eu-central-1")

    captured = {}

    def fake_init_chat_model(model_name, model_provider, **kwargs):
        captured["model_name"] = model_name
        captured["model_provider"] = model_provider
        captured.update(kwargs)
        return "fake-model"

    import langchain.chat_models

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init_chat_model)

    model_config = ModelConfig(role="planner", provider="bedrock_converse", model_name="anthropic.claude-x")
    result = model_config.init_chat_model()

    assert result == "fake-model"
    assert captured["model_provider"] == "bedrock_converse"
    assert captured["bedrock_api_key"] == "test-bearer-token"
    assert captured["region_name"] == "eu-central-1"


def test_init_chat_model_does_not_add_bedrock_kwargs_for_other_providers(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bearer-token")

    captured = {}

    def fake_init_chat_model(model_name, model_provider, **kwargs):
        captured.update(kwargs)
        return "fake-model"

    import langchain.chat_models

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init_chat_model)

    model_config = ModelConfig(role="planner", provider="anthropic", model_name="claude-sonnet-4-6")
    model_config.init_chat_model()

    assert "bedrock_api_key" not in captured
    assert "region_name" not in captured


def test_explicit_kwargs_are_not_overridden_by_bedrock_defaults(monkeypatch) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bearer-token")
    monkeypatch.setenv("BEDROCK_REGION", "eu-central-1")

    captured = {}

    def fake_init_chat_model(model_name, model_provider, **kwargs):
        captured.update(kwargs)
        return "fake-model"

    import langchain.chat_models

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init_chat_model)

    model_config = ModelConfig(role="planner", provider="bedrock_converse", model_name="anthropic.claude-x")
    model_config.init_chat_model(region_name="us-west-2")

    assert captured["region_name"] == "us-west-2"
