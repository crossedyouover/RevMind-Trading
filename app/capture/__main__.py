"""Explicit simulated-clock CLI; no live provider or credentials."""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.capture.coordinator import OfflineCaptureCoordinator
from app.capture.models import CycleRequest


@dataclass(frozen=True)
class SimulatedClock:
    at: datetime

    def now(self) -> datetime:
        return self.at


async def main() -> None:
    parser = argparse.ArgumentParser(description="Offline capture demo; no live acquisition")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--allow-policy", required=True)
    parser.add_argument(
        "--at",
        type=datetime.fromisoformat,
        required=True,
        help="explicit simulated receipt/processing time, including timezone",
    )
    args = parser.parse_args()
    with args.request.open("rb") as stream:
        payload = stream.read(10_000_001)
    if len(payload) > 10_000_000:
        raise ValueError("request file exceeds offline input byte cap")
    request = CycleRequest.model_validate_json(payload)
    capture = OfflineCaptureCoordinator(
        args.directory,
        clock=SimulatedClock(args.at),
        observation_id_factory=uuid4,
        allowed_policy_digests=(args.allow_policy,),
    )
    try:
        result = await capture.execute(request)
        print(
            json.dumps(
                {
                    "cycle_id": str(result.cycle_id),
                    "state": "COMPLETE",
                    "sealed_digest": result.sealed_digest,
                    "bars": len(result.research.request.history.bars),
                    "completed_at": result.completed_at.isoformat(),
                    "mode": "OFFLINE_CAPTURE_RESEARCH",
                }
            )
        )
    finally:
        capture.close()


if __name__ == "__main__":
    asyncio.run(main())
