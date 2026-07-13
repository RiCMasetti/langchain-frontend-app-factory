.PHONY: install run test lint clean-workspace

install:
	uv sync --extra dev --extra bedrock

run:
	uv run app-factory run "$$(cat examples/complex-frontend.md)"

test:
	uv run --extra dev pytest

lint:
	uv run --extra dev ruff check .

clean-workspace:
	find workspace/run -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
