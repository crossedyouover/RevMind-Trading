"""Adversarial Phase 16 catalyst materialization tests."""

import inspect
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.catalysts.engine as engine_module
import app.catalysts.models as models_module
from app.catalysts import (
    CatalystMaterializationInvalidInputError,
    CatalystMaterializationRequest,
    DeterministicCatalystMaterializationEngine,
    ObservedCatalystFact,
)
from app.core.schemas import AssetClass, CatalystSourceType, Instrument
from app.data.observations import SourceIdentity

_KNOWN = datetime(2025, 1, 10, tzinfo=UTC)
_PUBLISHED = datetime(2025, 1, 9, tzinfo=UTC)
_SOURCE = SourceIdentity(name="wire-service")
_AAPL = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)


def _fact(
    integer: int,
    minute: int,
    *,
    headline: str = "Original",
    record: str | None = "story-1",
    published: datetime | None = _PUBLISHED,
    source: SourceIdentity = _SOURCE,
    instruments: tuple[Instrument, ...] = (_AAPL,),
    source_type: CatalystSourceType = CatalystSourceType.PRIMARY,
) -> ObservedCatalystFact:
    return ObservedCatalystFact(
        observation_id=UUID(f"00000000-0000-4000-8000-{integer:012d}"),
        headline=headline,
        source=source,
        source_type=source_type,
        observed_at=_KNOWN + timedelta(minutes=minute),
        published_at=published,
        source_record_id=record,
        instruments=instruments,
    )


def _request(**changes: object) -> CatalystMaterializationRequest:
    values: dict[str, object] = {"as_of": _KNOWN + timedelta(days=1), "source": _SOURCE}
    values.update(changes)
    return CatalystMaterializationRequest.model_validate(values)


def test_latest_known_source_record_revision_wins_with_provenance() -> None:
    old = _fact(1, 0)
    new = _fact(2, 1, headline="Correction")
    result = DeterministicCatalystMaterializationEngine().materialize((old, new), _request())
    assert result.facts == (new,)
    assert result.inspected_fact_count == 2
    assert result.eligible_fact_count == 2


def test_unkeyed_repeated_facts_remain_distinct_and_unknown_publication_orders_last() -> None:
    known = _fact(1, 0, record=None)
    unknown = _fact(2, 1, record=None, published=None)
    result = DeterministicCatalystMaterializationEngine().materialize(
        (known, unknown), _request()
    )
    assert result.facts == (known, unknown)


def test_explicit_source_instrument_authority_and_half_open_range_filters() -> None:
    other_source = SourceIdentity(name="other")
    facts = (
        _fact(1, 0),
        _fact(2, 1, record="other-source", source=other_source),
        _fact(3, 2, record="no-instrument", instruments=()),
        _fact(4, 3, record="secondary", source_type=CatalystSourceType.SECONDARY),
        _fact(5, 4, record="unknown-time", published=None),
    )
    request = _request(
        instrument=_AAPL,
        source_type=CatalystSourceType.PRIMARY,
        published_start=_PUBLISHED,
        published_end=_PUBLISHED + timedelta(seconds=1),
    )
    result = DeterministicCatalystMaterializationEngine().materialize(facts, request)
    assert result.facts == (facts[0],)


def test_future_known_and_noncanonical_order_fail_closed() -> None:
    first, second = _fact(1, 0), _fact(2, 1, record="story-2")
    with pytest.raises(CatalystMaterializationInvalidInputError, match="knowledge order"):
        DeterministicCatalystMaterializationEngine().materialize((second, first), _request())
    with pytest.raises(CatalystMaterializationInvalidInputError, match="not known"):
        DeterministicCatalystMaterializationEngine().materialize(
            (second,), _request(as_of=_KNOWN)
        )


def test_duplicate_id_and_wrong_types_fail_closed() -> None:
    first = _fact(1, 0)
    duplicate = first.model_copy(update={"observed_at": _KNOWN + timedelta(minutes=1)})
    engine = DeterministicCatalystMaterializationEngine()
    with pytest.raises(CatalystMaterializationInvalidInputError, match="duplicate"):
        engine.materialize((first, duplicate), _request())
    with pytest.raises(CatalystMaterializationInvalidInputError, match="sequence"):
        engine.materialize(cast(tuple[ObservedCatalystFact, ...], iter(())), _request())
    with pytest.raises(CatalystMaterializationInvalidInputError, match="request"):
        engine.materialize((), cast(CatalystMaterializationRequest, object()))


def test_models_are_strict_immutable_and_instruments_are_canonical() -> None:
    fact = _fact(1, 0)
    with pytest.raises(ValidationError, match="frozen"):
        fact.headline = "changed"
    with pytest.raises(ValidationError, match="tuple"):
        _fact(1, 0, instruments=cast(tuple[Instrument, ...], [_AAPL]))
    with pytest.raises(ValidationError, match="canonically ordered"):
        _fact(1, 0, instruments=(_AAPL, _AAPL))
    with pytest.raises(ValidationError, match="earlier"):
        _request(published_start=_PUBLISHED, published_end=_PUBLISHED)


def test_empty_and_identical_runs_are_deterministic() -> None:
    engine = DeterministicCatalystMaterializationEngine()
    first = engine.materialize((), _request())
    second = engine.materialize((), _request())
    assert first == second
    assert first.facts == ()
    assert first.model_dump_json() == second.model_dump_json()


def test_phase16_has_no_forbidden_dependencies_or_authority() -> None:
    source = inspect.getsource(engine_module) + inspect.getsource(models_module)
    forbidden = (
        "sqlite", "httpx", "requests", "socket", "datetime.now", "time.time",
        "random", "secrets", "app.llm", "app.scanner", "app.risk", "app.desks",
        "app.alerts", "Angelo", "except Exception",
    )
    assert not any(item in source for item in forbidden)
