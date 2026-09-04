"""Explicit local CLI: python -m app.runtime --help."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.runtime.shadow import ShadowRunPlan, ShadowRuntime


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline shadow runner; no live messages or orders"
    )
    parser.add_argument("--directory", required=True)
    parser.add_argument("action", choices=("register", "start", "pause", "tick", "status", "audit"))
    parser.add_argument("--manifest")
    parser.add_argument("--run-id")
    parser.add_argument("--at", help="Explicit aware ISO time; required for mutations")
    parser.add_argument("--max-jobs", type=int, default=1)
    args = parser.parse_args()
    runtime = ShadowRuntime(args.directory)
    try:
        if args.action == "register":
            if not args.manifest or not args.at:
                parser.error("register requires --manifest and --at")
            plan = ShadowRunPlan.model_validate_json(
                Path(args.manifest).read_text(encoding="utf-8")
            )
            print(runtime.register(plan, datetime.fromisoformat(args.at)).model_dump_json())
        else:
            if not args.run_id:
                parser.error("--run-id is required")
            if args.action == "status":
                print(runtime.status(args.run_id).model_dump_json())
            elif args.action == "audit":
                print(json.dumps(runtime.audit(args.run_id)))
            else:
                if not args.at:
                    parser.error("--at is required")
                at = datetime.fromisoformat(args.at)
                result = (
                    runtime.tick(args.run_id, at, args.max_jobs)
                    if args.action == "tick"
                    else runtime.set_running(args.run_id, args.action == "start", at)
                )
                print(result.model_dump_json())
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
