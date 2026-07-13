# langchain-frontend-app-factory

A LangGraph/LangSmith multi-agent system that plans, writes, and tests frontend
applications inside a per-run Docker sandbox -- generalized to any frontend framework
(not just React). A LangGraph/LangSmith port of
[google-adk-app-factory](https://github.com/RiCMasetti/google-adk-app-factory).

## Architecture

Five LangGraph nodes plus a deterministic finalize step:

```
planner -> sandbox -> app_writer -> test_writer -> test_runner -> (retry: app_writer | finalize)
```

- **Planner** -- analyzes the user request, writes `plans/app-plan.json` (the one
  Pydantic-validated artifact: `framework`, `install_cmd`, `build_cmd`, `test_cmd`, ...)
  and a handoff summary.
- **Sandbox Agent** -- picks one of five vetted Docker templates
  (`react-vite`, `vue-vite`, `nextjs`, `sveltekit`, `generic-node`) and starts the
  per-run container. Never generates Dockerfile content itself.
- **App Writer** -- writes the frontend app inside the sandbox's `app/` directory only,
  can run `install`/`build` (never `test`) to check its work compiles.
- **Test Writer** -- authors test files; can also run `test` (never `install`/`build`)
  to catch and fix its own mistakes before handing off.
- **Test Runner** -- deterministically runs `install -> build -> test` inside the
  sandbox and reports results; no LLM call, since there's no judgment call in running
  the commands the plan already specified.

Every agent failure halts the run and routes to `finalize`, which writes the final
JSON report/metrics and tears down the sandbox container.

## Reporting

Everything lives under `workspace/run/<run_id>/`, all JSON:

```
input/user-request.md
plans/app-plan.json
handoffs/{planner,sandbox,appwriter,testwriter,testrunner}-summary.json
reports/token-usage-report.json      # flushed after every LLM call
reports/errors/<node>-<iter>-<ts>.json
reports/test-report.json
reports/final-report.json
metrics/{workflow-state,node-timings,run-metrics}.json
metrics/checkpoint.sqlite            # LangGraph checkpointer (resumable runs)
sandbox/{Dockerfile,docker-compose.yml,allowlist.json}
app/                                  # generated frontend, bind-mounted into the sandbox
```

`run_id` is a single `uuid4()` reused as the folder name, the checkpointer's
`thread_id`, and the LangSmith trace id, so a run's on-disk artifacts and its
LangSmith trace correlate 1:1 on the happy path.

## Security boundaries

- Every filesystem tool (`file_writer_tool`, `app_writer_tool`, `file_reader_tool`,
  `app_reader_tool`, `file_patch_tool`) is scoped to one run's directory via
  `RunWorkspace`'s path-containment check; `run_id` is never an LLM-visible argument.
- Sandbox commands are never freeform: the model can only pick a fixed slot
  (`install`/`build`/`test`); the actual argv was validated once (no shell
  metacharacters, binary in the template's allowlist) from `app-plan.json` at
  `prepare()` time.
- Dockerfiles/compose files are rendered from fixed, vetted templates with exactly two
  substitutable placeholders (`NODE_VERSION`, `PORT`) -- never LLM-generated content.

## Setup

```bash
uv venv && uv pip install -e ".[dev,anthropic]"
cp .env.example .env   # set your model provider's API key and LangSmith key
```

## Usage

```bash
app-factory run "Build a single-page pricing page with three tiers"
app-factory runs list
app-factory runs resume <run_id>   # requires CHECKPOINT_BACKEND=sqlite
```

## Testing

```bash
pytest
```

Unit tests (the whole suite) run without Docker or any real model API call -- LLM
calls are exercised via a fake chat model, and sandbox command execution is exercised
by faking `subprocess.run`. This validates every layer's wiring (path containment,
command allowlisting, patch-tool matching, JSON repair, graph routing/retry logic,
and the full node-to-node handoff), but it does **not** confirm a real model actually
produces a working app, or that a real Docker build succeeds.

To verify that end-to-end, run the CLI against `examples/sample-request.md` with
Docker running locally and a real model API key configured -- confirm the sandbox
container builds, the generated app appears under `workspace/run/<id>/app/`, and
`reports/`/`metrics/` are populated with non-empty JSON.
