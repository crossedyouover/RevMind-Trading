"""Delivery tests reuse the frozen end-to-end decision fixture."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_head_of_desk import NOW, request, run

from app.alerts.outbox import (
    AlertDeliveryCoordinator,
    AlertEnvelope,
    DeliveryStatus,
    RecordingAlertTransport,
    SQLiteAlertOutbox,
    TransportOutcome,
)


@pytest.fixture
def envelope():
    return AlertEnvelope(
        decision=run(request()), destination="local-research", expires_at=NOW + timedelta(minutes=1)
    )


def test_delivered_duplicate_reopen_and_audit(tmp_path, envelope):
    path = tmp_path / "outbox.db"
    store = SQLiteAlertOutbox(path)
    sink = RecordingAlertTransport()
    coordinator = AlertDeliveryCoordinator(store, sink, ("local-research",))
    first = coordinator.dispatch(envelope, NOW)
    assert first.status == DeliveryStatus.DELIVERED
    assert coordinator.dispatch(envelope, NOW) == first
    assert len(sink.envelopes) == 1
    assert [e.status for e in store.events(envelope.key())] == [
        DeliveryStatus.STARTED,
        DeliveryStatus.DELIVERED,
    ]
    store.close()
    store = SQLiteAlertOutbox(path)
    assert (
        AlertDeliveryCoordinator(store, sink, ("local-research",)).dispatch(envelope, NOW) == first
    )
    assert len(sink.envelopes) == 1
    store.close()


def test_crash_claim_does_not_resend(tmp_path, envelope):
    path = tmp_path / "outbox.db"
    store = SQLiteAlertOutbox(path)
    claim, send = store.claim(envelope, NOW, False)
    assert send
    store.close()
    store = SQLiteAlertOutbox(path)
    sink = RecordingAlertTransport()
    assert (
        AlertDeliveryCoordinator(store, sink, ("local-research",)).dispatch(
            envelope, NOW, retry_failed=True
        )
        == claim
    )
    assert not sink.envelopes
    store.close()


@pytest.mark.parametrize("outcome", ["exception", "malformed", "not_sent"])
def test_transport_failure_semantics(tmp_path, envelope, outcome):
    class Transport:
        calls = 0

        def send(self, value):
            self.calls += 1
            if outcome == "exception":
                raise TimeoutError()
            if outcome == "malformed":
                return "DELIVERED"
            return TransportOutcome.DEFINITELY_NOT_SENT

    store = SQLiteAlertOutbox(tmp_path / "outbox.db")
    transport = Transport()
    coordinator = AlertDeliveryCoordinator(store, transport, ("local-research",))
    result = coordinator.dispatch(envelope, NOW)
    expected = (
        DeliveryStatus.DEFINITELY_NOT_SENT if outcome == "not_sent" else DeliveryStatus.UNCERTAIN
    )
    assert result.status == expected
    coordinator.dispatch(envelope, NOW)
    assert transport.calls == 1
    coordinator.dispatch(envelope, NOW, retry_failed=True)
    assert transport.calls == (2 if outcome == "not_sent" else 1)
    store.close()


def test_eligibility_authority_expiry_and_collision(tmp_path, envelope):
    quiet = run(request().model_copy(update={"risk": None}))
    with pytest.raises(ValidationError):
        AlertEnvelope(decision=quiet, destination="local-research", expires_at=NOW)
    store = SQLiteAlertOutbox(tmp_path / "outbox.db")
    sink = RecordingAlertTransport()
    coordinator = AlertDeliveryCoordinator(store, sink, ("local-research",))
    with pytest.raises(ValueError):
        coordinator.dispatch(envelope.model_copy(update={"destination": "other"}), NOW)
    with pytest.raises(ValueError):
        coordinator.dispatch(envelope, NOW - timedelta(microseconds=1))
    assert not sink.envelopes
    coordinator.dispatch(envelope, envelope.expires_at)
    assert len(sink.envelopes) == 1
    with pytest.raises(ValueError):
        coordinator.dispatch(envelope.model_copy(update={"expires_at": NOW}), envelope.expires_at)
    with pytest.raises(ValueError):
        coordinator.dispatch(envelope, NOW)
    store.close()


def test_expired_never_sends(tmp_path, envelope):
    store = SQLiteAlertOutbox(tmp_path / "outbox.db")
    sink = RecordingAlertTransport()
    result = AlertDeliveryCoordinator(store, sink, ("local-research",)).dispatch(
        envelope, envelope.expires_at + timedelta(microseconds=1)
    )
    assert result.status == DeliveryStatus.EXPIRED
    assert not sink.envelopes
    store.close()


def test_two_connections_only_one_claim(tmp_path, envelope):
    path = tmp_path / "outbox.db"
    SQLiteAlertOutbox(path).close()

    def claim():
        store = SQLiteAlertOutbox(path)
        try:
            return store.claim(envelope, NOW, False)[1]
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: claim(), range(2))) == [False, True]


def test_database_failure_prevents_transport(tmp_path, envelope):
    store = SQLiteAlertOutbox(tmp_path / "outbox.db")
    sink = RecordingAlertTransport()
    store.close()
    with pytest.raises(sqlite3.Error):
        AlertDeliveryCoordinator(store, sink, ("local-research",)).dispatch(envelope, NOW)
    assert not sink.envelopes
