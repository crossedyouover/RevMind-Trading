"""Adversarial point-in-time global-event materialization tests."""

import inspect
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.intelligence.events.engine as engine_module
import app.intelligence.events.models as models_module
from app.core.schemas import AssetClass, Instrument
from app.data.observations import SourceIdentity
from app.intelligence.events import (
    DeterministicGlobalEventMaterializationEngine,
    GlobalEventCategory,
    GlobalEventMaterializationInvalidInputError,
    GlobalEventMaterializationRequest,
    ObservedGlobalEvent,
)

KNOWN = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
OCCURRED = KNOWN - timedelta(hours=2)
PUBLISHED = KNOWN - timedelta(hours=1)
SOURCE = SourceIdentity(name="public-source")
BRENT = Instrument(symbol="BRN", asset_class=AssetClass.FUTURE, exchange="ICE")


def event(
    integer: int,
    minute: int,
    *,
    source_event_id: str | None = "event-1",
    revision: str | None = "revision-1",
    headline: str = "Initial report",
    source: SourceIdentity = SOURCE,
    category: GlobalEventCategory = GlobalEventCategory.ENERGY_INFRASTRUCTURE,
    occurred_at: datetime | None = OCCURRED,
    published_at: datetime | None = PUBLISHED,
    countries: tuple[str, ...] = ("NO",),
    regions: tuple[str, ...] = ("EUROPE",),
    instruments: tuple[Instrument, ...] = (BRENT,),
) -> ObservedGlobalEvent:
    return ObservedGlobalEvent(
        observation_id=UUID(f"00000000-0000-4000-8000-{integer:012d}"),
        observed_at=KNOWN + timedelta(minutes=minute),
        source=source,
        category=category,
        headline=headline,
        occurred_at=occurred_at,
        published_at=published_at,
        source_event_id=source_event_id,
        source_revision_id=revision if source_event_id is not None else None,
        countries=countries,
        regions=regions,
        instruments=instruments,
    )


def request(**changes: object) -> GlobalEventMaterializationRequest:
    values: dict[str, object] = {"as_of": KNOWN + timedelta(days=1), "source": SOURCE}
    values.update(changes)
    return GlobalEventMaterializationRequest.model_validate(values)


def test_latest_known_provider_revision_wins_by_receipt_order() -> None:
    original = event(1, 0)
    correction = event(2, 1, revision="revision-2", headline="Corrected report")

    result = DeterministicGlobalEventMaterializationEngine().materialize(
        (original, correction), request()
    )

    assert result.events == (correction,)
    assert result.inspected_event_count == 2
    assert result.eligible_event_count == 2


def test_revision_label_never_overrides_knowledge_order() -> None:
    first = event(1, 0, revision="z-final")
    later = event(2, 1, revision="a-draft")

    result = DeterministicGlobalEventMaterializationEngine().materialize(
        (first, later), request()
    )

    assert result.events == (later,)


def test_ineligible_later_revision_does_not_erase_eligible_receipt() -> None:
    eligible = event(1, 0)
    outside_filter = event(
        2,
        1,
        revision="revision-2",
        category=GlobalEventCategory.CYBER_INCIDENT,
    )

    result = DeterministicGlobalEventMaterializationEngine().materialize(
        (eligible, outside_filter),
        request(category=GlobalEventCategory.ENERGY_INFRASTRUCTURE),
    )

    assert result.events == (eligible,)
    assert result.eligible_event_count == 1


def test_unkeyed_repeated_receipts_remain_distinct_and_unknown_times_sort_last() -> None:
    known = event(1, 0, source_event_id=None)
    unknown = event(2, 1, source_event_id=None, occurred_at=None, published_at=None)

    result = DeterministicGlobalEventMaterializationEngine().materialize(
        (known, unknown), request()
    )

    assert result.events == (known, unknown)


def test_explicit_source_and_all_filters_are_applied_before_revision_selection() -> None:
    other_source = SourceIdentity(name="other")
    facts = (
        event(1, 0),
        event(2, 1, source_event_id="wrong-source", source=other_source),
        event(3, 2, source_event_id="wrong-category", category=GlobalEventCategory.CYBER_INCIDENT),
        event(4, 3, source_event_id="wrong-country", countries=("US",)),
        event(5, 4, source_event_id="wrong-region", regions=("NORTH_AMERICA",)),
        event(6, 5, source_event_id="no-instrument", instruments=()),
        event(7, 6, source_event_id="unknown-times", occurred_at=None, published_at=None),
    )
    selected = DeterministicGlobalEventMaterializationEngine().materialize(
        facts,
        request(
            category=GlobalEventCategory.ENERGY_INFRASTRUCTURE,
            country="NO",
            region="EUROPE",
            instrument=BRENT,
            occurred_start=OCCURRED,
            occurred_end=OCCURRED + timedelta(seconds=1),
            published_start=PUBLISHED,
            published_end=PUBLISHED + timedelta(seconds=1),
        ),
    )

    assert selected.events == (facts[0],)


def test_future_known_noncanonical_and_duplicate_inputs_fail_closed() -> None:
    first = event(1, 0)
    second = event(2, 1, source_event_id="event-2")
    engine = DeterministicGlobalEventMaterializationEngine()
    with pytest.raises(GlobalEventMaterializationInvalidInputError, match="knowledge order"):
        engine.materialize((second, first), request())
    with pytest.raises(GlobalEventMaterializationInvalidInputError, match="not known"):
        engine.materialize((second,), request(as_of=KNOWN))
    duplicate = first.model_copy(update={"observed_at": KNOWN + timedelta(minutes=1)})
    with pytest.raises(GlobalEventMaterializationInvalidInputError, match="duplicate"):
        engine.materialize((first, duplicate), request())


def test_wrong_input_types_fail_closed() -> None:
    engine = DeterministicGlobalEventMaterializationEngine()
    with pytest.raises(GlobalEventMaterializationInvalidInputError, match="sequence"):
        engine.materialize(cast(tuple[ObservedGlobalEvent, ...], iter(())), request())
    with pytest.raises(GlobalEventMaterializationInvalidInputError, match="canonical"):
        engine.materialize(cast(tuple[ObservedGlobalEvent, ...], (object(),)), request())
    with pytest.raises(GlobalEventMaterializationInvalidInputError, match="request"):
        engine.materialize((), cast(GlobalEventMaterializationRequest, object()))


def test_invalid_ranges_and_output_count_claims_are_rejected() -> None:
    with pytest.raises(ValidationError, match="occurred_start"):
        request(occurred_start=OCCURRED, occurred_end=OCCURRED)
    with pytest.raises(ValidationError, match="published_start"):
        request(published_start=PUBLISHED, published_end=PUBLISHED)


def test_empty_and_identical_runs_are_deterministic() -> None:
    engine = DeterministicGlobalEventMaterializationEngine()
    first = engine.materialize((), request())
    second = engine.materialize((), request())

    assert first == second
    assert first.events == ()
    assert first.model_dump_json() == second.model_dump_json()


def test_phase103_has_no_forbidden_dependencies_or_authority() -> None:
    source = inspect.getsource(engine_module) + inspect.getsource(models_module)
    forbidden = (
        "sqlite",
        "httpx",
        "requests",
        "socket",
        "datetime.now",
        "time.time",
        "random",
        "secrets",
        "app.llm",
        "app.scanner",
        "app.risk",
        "app.desks",
        "app.alerts",
        "Angelo",
        "except Exception",
        "BUY",
        "SELL",
    )
    assert not any(item in source for item in forbidden)
