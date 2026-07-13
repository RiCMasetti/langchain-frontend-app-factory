from app.jsonutil import parse_llm_json


def test_parses_clean_json() -> None:
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_strips_code_fences() -> None:
    text = '```json\n{"a": 1}\n```'
    assert parse_llm_json(text) == {"a": 1}


def test_strips_code_fences_without_language_tag() -> None:
    text = '```\n{"a": 1}\n```'
    assert parse_llm_json(text) == {"a": 1}


def test_repairs_trailing_comma() -> None:
    text = '{"a": 1, "b": 2,}'
    result = parse_llm_json(text)
    assert result == {"a": 1, "b": 2}


def test_repairs_missing_closing_brace() -> None:
    text = '{"a": 1, "b": 2'
    result = parse_llm_json(text)
    assert result.get("a") == 1
    assert result.get("b") == 2


def test_unparseable_text_returns_parse_error_dict_not_raise() -> None:
    result = parse_llm_json("this is not json at all, just prose")
    assert "_parse_error" in result
    assert "_raw_text" in result


def test_list_json_wrapped_in_value_key() -> None:
    result = parse_llm_json("[1, 2, 3]")
    assert result == {"value": [1, 2, 3]}
