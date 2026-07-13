# DESIGN_SPEC.md

## Overview

This project implements a LangGraph/LangSmith based multi-agent workflow that plans,
writes, and tests a frontend application of the user's choice of framework, inside a
per-run Docker sandbox mounted into the shared run workspace.

The graph has five nodes -- Planner, Sandbox Agent, App Writer, Test Writer, Test
Runner -- plus a deterministic Finalize step. Unlike a single merged "build and verify"
agent, Test Writer and Test Runner are still separate: Test Writer authors test files
and Test Runner executes install/build/test with no file-writing rights for source.
Test Writer additionally has the "test" execution slot only (never install/build) so it
can run its own write-run-fix self-check loop before handing off -- a lighter-weight
alternative to a full merge that keeps Test Runner's official, retry-loop-driving run
deterministic and LLM-free, and keeps App Writer with no path to quietly "fix" a test
that's actually testing correct behavior. Each node runs its own short-lived
tool-calling loop -- there is no shared `messages` history across nodes, only compact
JSON handoff summaries -- so token usage stays bounded across retries regardless of how
many iterations a run takes.

Model provider is configurable per role (`planner_model`, `worker_model`) via
LangChain's `init_chat_model()`; this repo is routinely run against Anthropic models
through AWS Bedrock (API-key/bearer-token auth), but is not hardcoded to any provider.

## Graph & Retry Loop

```
planner -> sandbox -> app_writer -> test_writer -> test_runner -> (retry: app_writer | finalize)
```

- Every edge is conditional on `should_halt(state)`; any node's failure routes straight
  to `finalize` instead of five separate try/except blocks in the orchestration layer.
- The only loop is `test_runner -> app_writer`. Retry/circuit-breaker bookkeeping
  (`apply_test_runner_outcome` in `app/nodes/test_runner.py`) lives inside the node,
  not the router, because LangGraph conditional-edge functions cannot mutate state.
- Stop conditions: `status="accepted"` (tests passed), `status="failed"` (same error
  signature 3 iterations in a row, or Planner/Sandbox failed outright), or
  `status="max_iterations_reached"` (`MAX_AGENT_ITERATIONS`, default 5).
- `run_id` (one `uuid4()` per user request) is simultaneously the
  `workspace/run/<run_id>` folder name, the LangGraph checkpointer's `thread_id`, and
  the LangSmith trace id -- so a run's on-disk artifacts and its trace correlate 1:1 on
  the happy path. A resumed run keeps the same folder/thread_id but mints a fresh
  LangSmith trace id (crash recovery shouldn't reuse a dangling root run).

## Tools Required

- LangGraph + LangSmith for orchestration and tracing.
- A LangChain chat-model integration matching whichever provider is configured
  (`langchain-anthropic`, `langchain-openai`, `langchain-aws`, ...).
- Docker with Compose for the per-run frontend sandbox.
- Node.js tooling inside the sandbox image (version pinned per template, see below).

## Constraints & Safety Rules

- **No freeform Dockerfiles.** Sandbox Agent selects one of five vetted templates
  (`react-vite`, `vue-vite`, `nextjs`, `sveltekit`, `generic-node` fallback) under
  `sandbox/templates/`. The only substitutable values are `NODE_VERSION` and `PORT`
  (via stdlib `string.Template.safe_substitute`), both validated against the template's
  own allowlist before rendering -- never LLM-generated Dockerfile content.
- **No freeform shell execution.** The model can only pick a fixed command slot
  (`install`/`build`/`test`); the actual argv was tokenized and validated once, from
  `app-plan.json`, at `prepare()` time (`app/sandboxing/allowlist.py`): no shell
  metacharacters, first token must be in the template's `allowed_command_binaries`, no
  absolute paths. All `docker compose`/`exec` calls use argv-list form, never
  `shell=True`.
- **Every filesystem tool is scoped to one run.** `RunWorkspace.resolve()`/
  `app_resolve()` reject absolute paths and enforce containment under
  `workspace/run/<run_id>/` (optionally further scoped to `.../app/`). `run_id` is
  never an LLM-visible tool argument -- it's bound once per run via closures, and is
  itself regex-validated before any path math happens.
- **App Writer never touches tests; Test Writer can only run tests, never
  install/build.** Enforced by which tools each node is given, not by prompt
  discipline alone (see Tool Binding) -- Test Writer's `sandbox_command_tool` is built
  with `allowed_slots=frozenset({"test"})`, so `install`/`build` are rejected the same
  way an unrelated tool name would be.
- **Only one Pydantic-validated artifact.** `AppPlan` (`app/schemas.py`) is the sole
  hard schema, and only because Test Runner's framework-agnosticism depends on
  `framework`/`install_cmd`/`build_cmd`/`test_cmd` always being present. Everything
  else in the plan (`pages`, `components`, `acceptance_criteria`, `stack`) is typed
  loosely (`list[Any]`/`dict[str, Any]`, `extra="allow"`) -- a model describing a
  component as a structured `{name, description}` object instead of a bare string is a
  better plan, not an invalid one. Every other agent handoff is parsed leniently via
  `app/jsonutil.py::parse_llm_json()` and never gets a Pydantic model at all. **When a
  validated field keeps failing, check whether it's actually load-bearing before
  assuming the LLM is wrong** -- twice now the bug was an over-strict schema, not a
  misbehaving model.
- **No generated summary/report clutter.** App Writer and Test Writer are explicitly
  told not to write markdown status files (`SUMMARY.md`, `FIXES.md`, etc.) -- early
  runs accumulated dozens of these, wasting tokens and turns on files nobody reads.
- **Every retrying node needs the feedback it would need to fix its own failure
  class.** Test Runner's failure feedback (`compact_test_feedback()`,
  `app/nodes/feedback.py`) goes to *both* App Writer and Test Writer. Feeding only one
  of them left the other blind on retries, so a failure caused by the test itself
  (not the app) could never be fixed and would repeat until the circuit breaker gave up.
- **The repeated-error circuit breaker's fingerprint must be genuinely stable.** It
  anchors on a `file:line` test-location match first, then a line naming an `Error`
  type, only falling back to "first line of the tail" last -- a raw last-line fallback
  is fragile (ANSI codes, wrapped output, incidental formatting drift) and can silently
  defeat the breaker even when the same test is failing the same way every time.
- **Token usage must be recorded for every LLM turn, not just the last one of a node
  call** (`AgentLoopResult.ai_messages`, `TokenUsageRecorder.record_from_messages()`),
  including the turns that happened before a loop error
  (`AgentLoopError.ai_messages`) -- otherwise a multi-step tool-calling loop silently
  drops most of its real, paid cost from the report, and a loop that errors out drops
  all of it.

## Tool Binding

| Node | Tools |
|---|---|
| Planner | `file_reader_tool`, `file_writer_tool` |
| Sandbox Agent | `file_reader_tool`, `file_writer_tool`, `sandbox_prepare_tool` |
| App Writer | `file_reader_tool`, `app_reader_tool`, `app_writer_tool`, `file_patch_tool`, `sandbox_command_tool` (install/build only, never test) |
| Test Writer | `app_reader_tool`, `app_writer_tool`, `file_patch_tool`, `sandbox_command_tool` (test only, never install/build) |
| Test Runner | `run_test_tool` (fixed install→build→test sequence), `file_writer_tool` -- deterministic, no LLM call at all: there's no judgment call in running the commands the plan already specified |

## Reporting

Everything under `workspace/run/<run_id>/` is JSON (not Markdown, per explicit
preference): `reports/token-usage-report.json` (flushed after every recorded turn),
`reports/errors/<node>-<iteration>-<ts>.json` (one file per node failure, for root-cause
analysis), `reports/test-report.json`, `reports/final-report.json`,
`metrics/{workflow-state,node-timings,run-metrics}.json`,
`metrics/checkpoint.sqlite` (LangGraph checkpointer).

## Success Criteria

- `uv sync --extra dev [--extra anthropic|openai|bedrock]` installs cleanly.
- `app-factory run "<request>"` produces either `status: accepted` with a working app
  under `workspace/run/<id>/app/`, or a clearly attributable `failed` /
  `max_iterations_reached` / `agent_failed` status with a populated error/test report.
- `workspace/run/index.json` and `app-factory runs list` make past runs discoverable
  without needing to know a `run_id` in advance.
- The full test suite (currently 109+ tests) runs without Docker or a real model API
  call -- LLM calls are exercised via a fake chat model, sandbox execution via a faked
  `subprocess.run` -- but this does **not** substitute for the manual smoke test against
  a real model + Docker described in `README.md`.

## Known Limitations / Edge Cases Observed in Real Runs

- **Ambiguous test queries are the dominant real-world failure mode so far.**
  `getByText`/`getByRole` with a loose string can fail two distinct ways: "not found"
  (text visually looks combined but is split across sibling elements, e.g. a currency
  symbol and amount in separate `<span>`s) and "multiple found" (the same text appears
  in more than one place, e.g. a brand name in both header and footer). Test Writer's
  prompt now calls out both patterns explicitly.
- **CSS Modules + `toHaveClass()` is a recurring trap.** A component applying a class
  via a `*.module.css` import (e.g. `styles.selected`) gets that class name hashed at
  build time -- asserting the literal unhashed string never matches and isn't a real
  app bug. Seen independently in two unrelated runs (one self-diagnosed it mid-run in a
  now-removed throwaway summary doc; a later run hit the identical trap with no memory
  of the first). Reactive per-run feedback alone didn't reliably fix it even after the
  feedback-wiring fix above, so it's now a standing rule in Test Writer's prompt rather
  than something left to be rediscovered every run -- **a failure pattern that recurs
  across independent runs belongs in the prompt, not just in the retry feedback.** This
  is also the motivating case for giving Test Writer its own `test`-only execution slot
  (see Overview): it can now catch this class of mistake itself, immediately, instead of
  waiting for Test Runner's official pass and a full retry cycle.
- **Test file pileup.** Without explicit guidance, Test Writer tended to add a new,
  overlapping test file each retry iteration rather than fixing the existing one (one
  real run accumulated 18 test files across 7 iterations). Prompted to check for and
  fix/extend existing coverage first.
- **Concurrent/parallel runs are explicitly out of scope for now.** `SandboxManager`
  already namespaces its Docker Compose project as `aff-<run_id>`, which is the noted
  extension point if multi-run isolation (port/network collisions) is ever revisited --
  no further work has been done to actually support it.
- **`AGENT_CALL_TIMEOUT_SECONDS` is configured but not yet enforced** anywhere in the
  agent loop or node wrappers; the only current bound on a stuck tool-calling loop is
  `max_steps` (`APP_WRITER_MAX_STEPS`/`TEST_WRITER_MAX_STEPS`).
