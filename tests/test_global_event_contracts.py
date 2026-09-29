from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.schemas import AssetClass, Instrument
from app.data.observations import SourceIdentity
from app.intelligence.events import (
    GlobalEventCategory,
    GlobalEventReceiptBatch,
    ObservedGlobalEvent,
)

NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
SOURCE = SourceIdentity(name="example-public-source")
FIRST_ID = UUID("00000000-0000-4000-8000-000000000001")
SECOND_ID = UUID("00000000-0000-4000-8000-000000000002")


def event(**changes: object) -> ObservedGlobalEvent:
    values: dict[str, object] = {
        "observation_id": FIRST_ID,
        "observed_at": NOW,
        "source": SOURCE,
        "category": GlobalEventCategory.MARITIME_DISRUPTION,
        "headline": "Port authority reports a temporary channel closure",
        "occurred_at": NOW - timedelta(hours=2),
        "published_at": NOW - timedelta(minutes=30),
        "source_event_id": "notice-42",
        "source_revision_id": "revision-1",
        "countries": ("ES",),
        "regions": ("EUROPE",),
        "instruments": (
            Instrument(symbol="brn", asset_class=AssetClass.FUTURE, exchange="ICE"),
        ),
    }
    values.update(changes)
    return ObservedGlobalEvent.model_validate(values)


def test_global_event_preserves_event_publication_and_knowledge_time() -> None:
    observed = event()

    assert observed.occurred_at == NOW - timedelta(hours=2)
    assert observed.published_at == NOW - timedelta(minutes=30)
    assert observed.observed_at == NOW
    assert observed.instruments[0].symbol == "BRN"


def test_future_or_unknown_source_times_do_not_change_knowledge_time() -> None:
    future = event(occurred_at=NOW + timedelta(days=1), published_at=NOW + timedelta(hours=1))
    unknown = event(occurred_at=None, published_at=None)

    assert future.observed_at == NOW
    assert unknown.observed_at == NOW
    assert unknown.occurred_at is None


@pytest.mark.parametrize("field", ["countries", "regions", "instruments"])
def test_python_collections_must_be_explicit_tuples(field: str) -> None:
    with pytest.raises(ValidationError, match="supplied as tuples"):
        event(**{field: []})


def test_revision_identity_requires_provider_event_identity() -> None:
    with pytest.raises(ValidationError, match="requires source_event_id"):
        event(source_event_id=None, source_revision_id="revision-2")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("countries", ("US", "US"), "countries must be unique"),
        ("regions", ("MIDDLE_EAST", "EUROPE"), "regions must be unique"),
        (
            "instruments",
            (
                Instrument(symbol="WTI", asset_class=AssetClass.FUTURE),
                Instrument(symbol="WTI", asset_class=AssetClass.FUTURE),
            ),
            "instruments must be unique",
        ),
    ],
)
def test_collections_reject_duplicates_or_noncanonical_order(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        event(**{field: value})


def test_receipt_batch_requires_one_source_one_time_and_knowledge_order() -> None:
    first = event()
    second = event(observation_id=SECOND_ID, headline="Second report")

    batch = GlobalEventReceiptBatch(source=SOURCE, observed_at=NOW, events=(first, second))

    assert batch.events == (first, second)
    with pytest.raises(ValidationError, match="batch source"):
        GlobalEventReceiptBatch(
            source=SOURCE,
            observed_at=NOW,
            events=(first.model_copy(update={"source": SourceIdentity(name="other")}),),
        )
    with pytest.raises(ValidationError, match="batch observed_at"):
        GlobalEventReceiptBatch(
            source=SOURCE,
            observed_at=NOW,
            events=(first.model_copy(update={"observed_at": NOW + timedelta(seconds=1)}),),
        )
    with pytest.raises(ValidationError, match="canonical knowledge order"):
        GlobalEventReceiptBatch(source=SOURCE, observed_at=NOW, events=(second, first))


def test_repeated_provider_receipts_remain_distinct() -> None:
    first = event()
    correction = event(
        observation_id=SECOND_ID,
        source_revision_id="revision-2",
        headline="Port authority corrects the expected reopening time",
    )

    batch = GlobalEventReceiptBatch(source=SOURCE, observed_at=NOW, events=(first, correction))

    assert len(batch.events) == 2
    assert {item.source_revision_id for item in batch.events} == {"revision-1", "revision-2"}


def test_contract_is_immutable_and_rejects_unknown_fields() -> None:
    observed = event()
    with pytest.raises(ValidationError):
        observed.headline = "changed"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ObservedGlobalEvent.model_validate(
            {
                **observed.model_dump(mode="python"),
                "market_direction": "BUY",
            }
        )


def test_python_contract_rejects_string_uuid_and_naive_time() -> None:
    with pytest.raises(ValidationError, match="actual UUID4"):
        event(observation_id=str(FIRST_ID))
    with pytest.raises(ValidationError, match="timezone"):
        event(observed_at=datetime(2026, 9, 29, 8, 0))
