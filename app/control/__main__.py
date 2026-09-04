"""JSON command CLI for trusted local hosts; no network listener."""

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from app.control.local import ControlCommand, ControlGrant, LocalControlPlane
from app.runtime.shadow import ShadowRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description="Local capability-scoped shadow control")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--grants", required=True, help="Host-owned JSON grant array")
    parser.add_argument("--command", required=True, help="Versioned JSON command")
    args = parser.parse_args()
    grants = TypeAdapter(tuple[ControlGrant, ...]).validate_json(
        Path(args.grants).read_text(encoding="utf-8")
    )
    command = ControlCommand.model_validate_json(Path(args.command).read_text(encoding="utf-8"))
    runtime = ShadowRuntime(args.directory)
    control = LocalControlPlane(runtime, Path(args.directory) / "control.db", grants)
    try:
        print(control.execute(command).model_dump_json())
    finally:
        control.close()
        runtime.close()


if __name__ == "__main__":
    main()
