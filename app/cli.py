"""CLI entrypoint: `app-factory run "<request>"`, `app-factory runs list`, `app-factory runs resume <id>`."""

from __future__ import annotations

import argparse
import sys

from app.config import Settings, load_env_file


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(prog="app-factory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the factory once for a new user request.")
    run_parser.add_argument("request", help="The frontend app request in plain English.")
    run_parser.add_argument("--no-sandbox-start", action="store_true", help="Prepare the sandbox but don't start Docker.")

    runs_parser = subparsers.add_parser("runs", help="Inspect past runs.")
    runs_subparsers = runs_parser.add_subparsers(dest="runs_command", required=True)
    runs_subparsers.add_parser("list", help="List past runs.")
    resume_parser = runs_subparsers.add_parser("resume", help="Resume a crashed run from its last checkpoint.")
    resume_parser.add_argument("run_id")

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "run":
        from app.run import run_once

        final_state = run_once(args.request, settings, sandbox_start=not args.no_sandbox_start)
        print(f"run_id={final_state['run_id']} status={final_state['status']}")
        return 0 if final_state["status"] == "accepted" else 1

    if args.command == "runs":
        if args.runs_command == "list":
            from app.run import list_runs

            for entry in list_runs(settings):
                print(f"{entry['run_id']}  {entry['started_at']}  {entry['user_request_excerpt']}")
            return 0
        if args.runs_command == "resume":
            from app.run import resume_run

            final_state = resume_run(args.run_id, settings)
            print(f"run_id={final_state['run_id']} status={final_state['status']}")
            return 0 if final_state["status"] == "accepted" else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
