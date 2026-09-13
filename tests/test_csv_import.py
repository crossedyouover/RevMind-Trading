"""Tests for the bounded provider-neutral CSV adapter."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.core.schemas import AssetClass, Timeframe
from app.data.csv_import import (
    CsvBarImportCoordinator,
    CsvBarImportRequest,
    CsvImportError,
    parse_csv_bars,
)
from app.data.observation_store import SQLiteObservationStore

RECEIVED = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
HEADER = b"timestamp,open,high,low,close,volume\n"
ROW = b"2026-09-13T10:00:00Z,100,101,99,100.5,12\n"


def request() -> CsvBarImportRequest:
    return CsvBarImportRequest(
        symbol="EURUSD",
        asset_class=AssetClass.FX,
        exchange="IDEALPRO",
        currency="USD",
        timeframe=Timeframe.FIVE_MINUTES,
        source_name="user-export",
    )


def test_parses_exact_csv_with_explicit_identity() -> None:
    result = parse_csv_bars(HEADER + ROW, request(), received_at=RECEIVED)
    assert result.bars[0].instrument == request().instrument
    assert result.bars[0].timeframe is Timeframe.FIVE_MINUTES
    assert result.received_at == RECEIVED
    assert len(result.content_digest) == 64


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"time,open,high,low,close,volume\n" + ROW,
        HEADER + b"2026-09-13T10:00:00,100,101,99,100,1\n",
        HEADER + b"2026-09-13T10:00:00Z,100,98,99,100,1\n",
        HEADER + b"2026-09-13T10:00:00Z,100,101,99,NaN,1\n",
        b"\xff\xfe",
    ],
)
def test_rejects_untrusted_csv(payload: bytes) -> None:
    with pytest.raises(CsvImportError):
        parse_csv_bars(payload, request(), received_at=RECEIVED)


def test_rejects_duplicate_or_nonchronological_rows() -> None:
    with pytest.raises(CsvImportError, match="strictly increasing"):
        parse_csv_bars(HEADER + ROW + ROW, request(), received_at=RECEIVED)


def test_rejects_bar_at_or_after_receipt_boundary() -> None:
    payload = HEADER + b"2026-09-13T12:00:00Z,100,101,99,100,1\n"
    with pytest.raises(CsvImportError, match="predate"):
        parse_csv_bars(payload, request(), received_at=RECEIVED)


def test_rejects_naive_receipt_time() -> None:
    with pytest.raises(CsvImportError, match="timezone"):
        parse_csv_bars(HEADER + ROW, request(), received_at=datetime(2026, 9, 13, 12, 0))


class FixedClock:
    def now(self) -> datetime:
        return RECEIVED


def test_coordinator_atomically_persists_shared_receipt_boundary(tmp_path: Path) -> None:
    identifiers = iter(
        (
            UUID("00000000-0000-4000-8000-000000000001"),
            UUID("00000000-0000-4000-8000-000000000002"),
        )
    )
    second = b"2026-09-13T10:05:00Z,100.5,102,100,101,15\n"
    path = tmp_path / "observations.db"
    with SQLiteObservationStore(path) as store:
        receipt = CsvBarImportCoordinator(
            store, clock=FixedClock(), observation_id_factory=lambda: next(identifiers)
        ).import_bars(HEADER + ROW + second, request())
        persisted = store.available_at(RECEIVED)
    assert receipt.count == 2
    assert persisted == list(receipt.observations)
    assert {item.observed_at for item in persisted} == {RECEIVED}
    assert all(item.source.name == "user-export" for item in persisted)


def test_coordinator_rejects_non_uuid4_before_any_append(tmp_path: Path) -> None:
    path = tmp_path / "observations.db"
    with SQLiteObservationStore(path) as store:
        coordinator = CsvBarImportCoordinator(
            store,
            clock=FixedClock(),
            observation_id_factory=lambda: UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
        )
        with pytest.raises(CsvImportError, match="UUID4"):
            coordinator.import_bars(HEADER + ROW, request())
        assert store.available_at(RECEIVED) == []
