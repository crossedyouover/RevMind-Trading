"""Tests for the append-only imported-research summary journal."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.core.schemas import Timeframe
from app.dashboard.import_history import ImportHistoryError, ImportHistoryRecord, ImportHistoryStore


def record() -> ImportHistoryRecord:
    return ImportHistoryRecord(
        import_id=UUID("00000000-0000-4000-8000-000000000001"),
        symbol="EURUSD",
        asset_class="FX",
        timeframe=Timeframe.FIVE_MINUTES,
        source="manual-export",
        received_at=datetime(2026, 9, 13, 12, tzinfo=UTC),
        latest_event_at=datetime(2026, 9, 13, 11, 55, tzinfo=UTC),
        content_digest="a" * 64,
        imported_bar_count=500,
        analyzed_bar_count=500,
        readiness="WAIT",
        trend="UPWARD",
        active_setups=(),
    )


def test_appends_and_reads_validated_record(tmp_path: Path) -> None:
    store = ImportHistoryStore(tmp_path / "imports.db")
    store.append(record())
    assert store.recent() == (record(),)


def test_duplicate_identity_is_rejected(tmp_path: Path) -> None:
    store = ImportHistoryStore(tmp_path / "imports.db")
    store.append(record())
    with pytest.raises(ImportHistoryError):
        store.append(record())


def test_limit_is_strict_and_bounded(tmp_path: Path) -> None:
    store = ImportHistoryStore(tmp_path / "imports.db")
    with pytest.raises(ValueError):
        store.recent(0)
    with pytest.raises(ValueError):
        store.recent(True)
