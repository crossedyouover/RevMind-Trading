"""Adversarial tests for PIT insider facts, strict types, and amendment-before-filter order."""

import ast
import inspect
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.insiders.engine as engine_module
import app.insiders.models as models_module
from app.core.schemas import AssetClass, Instrument
from app.data.observations import SourceIdentity
from app.insiders import (
    DeterministicInsiderMaterializationEngine,
    InsiderMaterializationComputationError,
    InsiderMaterializationInvalidInputError,
    InsiderMaterializationRequest,
    MaterializedInsiderHistory,
    ObservedInsiderTransaction,
)

_TIME = datetime(2025, 1, 10, 12, tzinfo=UTC)
_DAY = date(2025, 1, 9)
_SOURCE = SourceIdentity(name="filing-source")
_OTHER_SOURCE = SourceIdentity(name="other-source")
_INSTRUMENT = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
_OTHER_INSTRUMENT = Instrument(
    symbol="AAPL", asset_class=AssetClass.EQUITY, exchange="XNYS", currency="USD"
)


def _fact(index: int = 1, /, **changes: object) -> ObservedInsiderTransaction:
    data: dict[str, object] = {
        "observation_id": UUID(int=index, version=4),
        "observed_at": _TIME + timedelta(microseconds=index),
        "source": _SOURCE,
        "instrument": _INSTRUMENT,
        "reporting_person": "Source Person",
        "transaction_code": "source-code",
        "transaction_date": _DAY,
        "filed_at": _TIME - timedelta(hours=1),
        "source_transaction_id": "transaction-1",
    }
    data.update(changes)
    return ObservedInsiderTransaction.model_validate(data)


def _request(**changes: object) -> InsiderMaterializationRequest:
    data: dict[str, object] = {"as_of": _TIME + timedelta(days=1), "source": _SOURCE}
    data.update(changes)
    return InsiderMaterializationRequest.model_validate(data)


def _run(
    facts: tuple[ObservedInsiderTransaction, ...],
    request: InsiderMaterializationRequest | None = None,
) -> MaterializedInsiderHistory:
    return DeterministicInsiderMaterializationEngine().materialize(facts, request or _request())


def _history(
    facts: tuple[ObservedInsiderTransaction, ...],
    request: InsiderMaterializationRequest | None = None,
    /,
    **changes: object,
) -> MaterializedInsiderHistory:
    data: dict[str, object] = {
        "request": request or _request(),
        "facts": facts,
        "inspected_receipt_count": len(facts),
        "source_receipt_count": len(facts),
        "revision_winner_count": len(facts),
        "matching_winner_count": len(facts),
    }
    data.update(changes)
    return MaterializedInsiderHistory.model_validate(data)


def test_latest_receipt_wins_with_full_provenance_and_all_counts() -> None:
    old = _fact(1, source_revision_id="Z", quantity=Decimal("1.000"))
    independent = _fact(2, source_transaction_id="transaction-2")
    corrected = _fact(
        3,
        source_revision_id="A",
        source_filing_id="amendment",
        reporting_role="Director",
        source_url="https://example.invalid/source",
        quantity=Decimal("2.000"),
        unit_price=Decimal("11.0250"),
        reported_total_value=Decimal("22.0500"),
    )
    excluded_source = _fact(4, source=_OTHER_SOURCE)
    result = _run((old, independent, corrected, excluded_source))
    assert result.facts == (independent, corrected)
    assert result.inspected_receipt_count == 4
    assert result.source_receipt_count == 3
    assert result.revision_winner_count == 2
    assert result.matching_winner_count == 2
    assert result.facts[-1].model_dump_json() == corrected.model_dump_json()
    assert old.quantity == Decimal("1.000")


@pytest.mark.parametrize(
    ("correction", "filters"),
    [
        ({"instrument": _OTHER_INSTRUMENT}, {"instrument": _INSTRUMENT}),
        (
            {"transaction_date": _DAY + timedelta(days=1)},
            {"transaction_end": _DAY + timedelta(days=1)},
        ),
        ({"filed_at": _TIME}, {"filing_end": _TIME}),
        ({"transaction_date": None}, {"transaction_start": _DAY}),
        ({"filed_at": None}, {"filing_start": _TIME - timedelta(hours=2)}),
    ],
)
def test_amendments_are_reduced_before_filters_no_old_version_resurrection(
    correction: dict[str, object], filters: dict[str, object]
) -> None:
    original, amended = _fact(1), _fact(2, **correction)
    result = _run((original, amended), _request(**filters))
    assert result.facts == ()
    assert result.source_receipt_count == 2
    assert result.revision_winner_count == 1
    assert result.matching_winner_count == 0


def test_correction_into_filter_and_same_id_across_sources() -> None:
    wrong = _fact(1, instrument=_OTHER_INSTRUMENT)
    corrected = _fact(2)
    different_source = _fact(3, source=_OTHER_SOURCE, instrument=_OTHER_INSTRUMENT)
    assert _run((wrong, corrected, different_source), _request(instrument=_INSTRUMENT)).facts == (
        corrected,
    )
    assert _run((wrong, corrected, different_source), _request(source=_OTHER_SOURCE)).facts == (
        different_source,
    )


def test_unkeyed_receipts_and_distinct_transactions_in_same_filing_are_preserved() -> None:
    facts = (
        _fact(1, source_transaction_id=None, source_filing_id="same"),
        _fact(2, source_transaction_id=None, source_filing_id="same"),
        _fact(3, source_transaction_id="one", source_filing_id="same"),
        _fact(4, source_transaction_id="two", source_filing_id="same"),
    )
    assert _run(facts).facts == facts


def test_ties_use_uuid_order_and_not_event_time_or_revision_id() -> None:
    first = _fact(1, observed_at=_TIME, source_revision_id="latest")
    last = _fact(2, observed_at=_TIME, source_revision_id="old", transaction_date=None)
    assert _run((first, last)).facts == (last,)
    with pytest.raises(InsiderMaterializationInvalidInputError, match="knowledge order"):
        _run((last, first))


@pytest.mark.parametrize("other_source", [False, True])
def test_future_receipt_is_rejected_even_with_old_event_or_excluded_source(
    other_source: bool,
) -> None:
    fact = _fact(1, source=_OTHER_SOURCE if other_source else _SOURCE)
    with pytest.raises(InsiderMaterializationInvalidInputError, match="not known"):
        _run((fact,), _request(as_of=_TIME))
    assert _run((fact,), _request(as_of=fact.observed_at)).source_receipt_count == (
        0 if other_source else 1
    )


def test_known_future_event_and_unknown_event_are_not_clamped_or_inferred() -> None:
    future = _fact(1, transaction_date=date(2030, 1, 1), filed_at=_TIME + timedelta(days=10))
    unknown = _fact(2, source_transaction_id=None, transaction_date=None, filed_at=None)
    assert _run((future, unknown)).facts == (future, unknown)


@pytest.mark.parametrize("field", ["transaction_start", "transaction_end"])
def test_unknown_transaction_date_fails_only_requested_date_filter(field: str) -> None:
    fact = _fact(transaction_date=None)
    assert _run((fact,), _request(**{field: _DAY})).facts == ()
    assert _run((fact,), _request(filing_end=_TIME)).facts == (fact,)


@pytest.mark.parametrize("field", ["filing_start", "filing_end"])
def test_unknown_filing_time_fails_only_requested_time_filter(field: str) -> None:
    fact = _fact(filed_at=None)
    assert _run((fact,), _request(**{field: _TIME})).facts == ()
    assert _run((fact,), _request(transaction_start=_DAY)).facts == (fact,)


def test_independent_half_open_date_and_filing_ranges() -> None:
    fact = _fact()
    assert _run((fact,), _request(transaction_start=_DAY)).facts == (fact,)
    assert _run((fact,), _request(transaction_end=_DAY)).facts == ()
    assert _run((fact,), _request(filing_start=fact.filed_at)).facts == (fact,)
    assert _run((fact,), _request(filing_end=fact.filed_at)).facts == ()
    assert _run(
        (fact,),
        _request(
            transaction_start=_DAY,
            transaction_end=_DAY + timedelta(days=1),
            filing_start=fact.filed_at,
            filing_end=_TIME,
        ),
    ).facts == (fact,)


@pytest.mark.parametrize("offset", [-7, 5])
def test_aware_timestamps_normalize_without_changing_calendar_dates(offset: int) -> None:
    local = _TIME.astimezone(timezone(timedelta(hours=offset)))
    fact = _fact(observed_at=local, filed_at=local)
    assert fact.observed_at == fact.filed_at == _TIME
    assert fact.observed_at.tzinfo is UTC
    assert fact.transaction_date == _DAY
    assert _request(as_of=local).as_of == _TIME


@pytest.mark.parametrize("value", [_TIME, "2025-01-09", 1736380800, True])
def test_python_transaction_dates_are_not_coerced(value: object) -> None:
    with pytest.raises(ValidationError):
        _fact(transaction_date=value)
    with pytest.raises(ValidationError):
        _request(transaction_start=value)


@pytest.mark.parametrize(
    "value", [_DAY, "2025-01-10T12:00:00Z", 1736500800, True, datetime(2025, 1, 10)]
)
def test_python_timestamps_reject_date_epoch_string_and_naive(value: object) -> None:
    with pytest.raises(ValidationError):
        _fact(observed_at=value)
    with pytest.raises(ValidationError):
        _request(filing_start=value)


@pytest.mark.parametrize("field", ["quantity", "unit_price", "reported_total_value"])
@pytest.mark.parametrize(
    "value",
    [1, 1.5, True, "1.0", Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_reported_decimals_reject_coercion_negative_and_nonfinite(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _fact(**{field: value})


def test_decimal_scale_zero_absence_and_source_assertions_are_not_recomputed() -> None:
    unknown = _fact()
    assert unknown.quantity is unknown.unit_price is unknown.reported_total_value is None
    fact = _fact(
        quantity=Decimal("0.000"),
        unit_price=Decimal("1.234567890123456789"),
        reported_total_value=Decimal("99.0000"),
    )
    selected = _run((fact,)).facts[0]
    assert selected.quantity is not None and selected.quantity.as_tuple().exponent == -3
    assert selected.reported_total_value == Decimal("99.0000")
    assert selected.model_dump_json() == fact.model_dump_json()


@pytest.mark.parametrize("version", [1, 3, 5])
def test_ids_must_be_uuid4_without_generation(version: int) -> None:
    with pytest.raises(ValidationError):
        _fact(observation_id=UUID(int=1, version=version))


def test_uuid_required_and_strings_not_coerced_in_python() -> None:
    data = _fact().model_dump(mode="python")
    del data["observation_id"]
    with pytest.raises(ValidationError):
        ObservedInsiderTransaction.model_validate(data)
    with pytest.raises(ValidationError):
        _fact(observation_id=str(UUID(int=1, version=4)))


def test_json_round_trips_nonempty_result_request_and_fact_without_losing_types() -> None:
    fact = _fact(
        quantity=Decimal("123.4500"),
        unit_price=Decimal("0"),
        source_revision_id="revision",
        reporting_role="Officer",
    )
    request = _request(transaction_start=_DAY, filing_end=_TIME)
    result = _run((fact,), request)
    assert ObservedInsiderTransaction.model_validate_json(fact.model_dump_json()) == fact
    assert InsiderMaterializationRequest.model_validate_json(request.model_dump_json()) == request
    restored = MaterializedInsiderHistory.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.model_dump_json() == result.model_dump_json()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 1.5),
        ("filed_at", 1736500800),
        ("filed_at", "1736500800"),
        ("observed_at", "1736500800000"),
        ("transaction_date", "2025-01-09T00:00:00Z"),
        ("observed_at", "2025-01-10"),
    ],
)
def test_json_rejects_ambiguous_number_and_time_coercions(field: str, value: object) -> None:
    data = json.loads(_fact().model_dump_json())
    data[field] = value
    with pytest.raises(ValidationError):
        ObservedInsiderTransaction.model_validate_json(json.dumps(data))


def test_nonincreasing_input_and_duplicate_id_at_later_receipt_fail() -> None:
    first, second = _fact(1), _fact(2)
    duplicate = first.model_copy(update={"observed_at": second.observed_at})
    for facts, message in [
        ((second, first), "knowledge order"),
        ((first, duplicate), "duplicate"),
        ((first, first), "duplicate"),
    ]:
        with pytest.raises(InsiderMaterializationInvalidInputError, match=message):
            _run(facts)


@pytest.mark.parametrize("value", [[], "facts", None, iter(())])
def test_input_requires_tuple(value: object) -> None:
    with pytest.raises(InsiderMaterializationInvalidInputError, match="tuple"):
        _run(cast(tuple[ObservedInsiderTransaction, ...], value))


def test_invalid_request_or_fact_types() -> None:
    with pytest.raises(InsiderMaterializationInvalidInputError, match="request"):
        _run((), cast(InsiderMaterializationRequest, object()))
    with pytest.raises(InsiderMaterializationInvalidInputError, match="observation"):
        _run(cast(tuple[ObservedInsiderTransaction, ...], (object(),)))


@pytest.mark.parametrize(
    "forged",
    [
        _fact().model_copy(update={"quantity": "12"}),
        _fact().model_copy(update={"observed_at": datetime(2025, 1, 10)}),
        _fact().model_copy(update={"instrument": _INSTRUMENT.model_copy(update={"symbol": ""})}),
        _fact().model_copy(update={"source": _SOURCE.model_copy(update={"name": ""})}),
        ObservedInsiderTransaction.model_construct(observation_id=UUID(int=1, version=4)),
    ],
)
def test_forged_nested_fact_state_is_rejected_at_engine_and_result(
    forged: ObservedInsiderTransaction,
) -> None:
    with pytest.raises(InsiderMaterializationInvalidInputError):
        _run((forged,))
    with pytest.raises(ValidationError):
        _history((forged,))


def test_forged_request_nested_source_and_ranges_are_revalidated() -> None:
    forged = _request().model_copy(update={"source": _SOURCE.model_copy(update={"name": ""})})
    with pytest.raises(InsiderMaterializationInvalidInputError):
        _run((), forged)
    with pytest.raises(ValidationError):
        _history((), forged)
    for changes in (
        {"transaction_start": _DAY, "transaction_end": _DAY},
        {"filing_start": _TIME, "filing_end": _TIME - timedelta(seconds=1)},
    ):
        with pytest.raises(InsiderMaterializationInvalidInputError):
            _run((), _request().model_copy(update=changes))


@pytest.mark.parametrize(
    "field",
    [
        "inspected_receipt_count",
        "source_receipt_count",
        "revision_winner_count",
        "matching_winner_count",
    ],
)
@pytest.mark.parametrize("value", [True, "1", 1.0, -1])
def test_result_counts_are_strict(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _history((_fact(),), **{field: value})


@pytest.mark.parametrize(
    "counts",
    [
        {"inspected_receipt_count": 0},
        {"source_receipt_count": 0},
        {"revision_winner_count": 0},
        {"matching_winner_count": 0},
        {"inspected_receipt_count": 2, "source_receipt_count": 2, "revision_winner_count": 2},
    ],
)
def test_result_count_relationships_are_enforced(counts: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _history((_fact(),), **counts)


def test_direct_result_cannot_claim_no_winner_for_nonempty_source() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        _history(
            (), _request(instrument=_INSTRUMENT), inspected_receipt_count=1, source_receipt_count=1
        )


def test_direct_result_enforces_source_cutoff_filters_order_and_unique_winners() -> None:
    first, second = _fact(1), _fact(2, source_transaction_id="other")
    invalid = [
        ((first,), _request(source=_OTHER_SOURCE)),
        ((first,), _request(as_of=_TIME)),
        ((first,), _request(instrument=_OTHER_INSTRUMENT)),
        ((first,), _request(transaction_end=_DAY)),
        ((first,), _request(filing_end=first.filed_at)),
        ((second, first), _request()),
        ((first, _fact(2)), _request()),
        (
            (
                first,
                first.model_copy(
                    update={"observed_at": second.observed_at, "source_transaction_id": None}
                ),
            ),
            _request(),
        ),
    ]
    for facts, request in invalid:
        with pytest.raises(ValidationError):
            _history(facts, request)


def test_result_tuple_immutability_unknown_fields_and_input_preservation() -> None:
    fact = _fact()
    original = fact.model_dump_json()
    result = _run((fact,))
    with pytest.raises(ValidationError):
        _history(cast(tuple[ObservedInsiderTransaction, ...], [fact]))
    with pytest.raises(ValidationError, match="frozen"):
        result.matching_winner_count = 0
    with pytest.raises(ValidationError, match="frozen"):
        result.facts[0].instrument.symbol = "CHANGED"
    with pytest.raises(ValidationError, match="extra"):
        _fact(unexpected="ignored")
    assert fact.model_dump_json() == original


def test_empty_and_stateless_repeated_execution() -> None:
    engine = DeterministicInsiderMaterializationEngine()
    request = _request()
    empty = engine.materialize((), request)
    assert empty.facts == ()
    assert empty.inspected_receipt_count == empty.source_receipt_count == 0
    assert empty.revision_winner_count == empty.matching_winner_count == 0
    facts = (_fact(1), _fact(2))
    first = engine.materialize(facts, request)
    assert engine.materialize((), request) == empty
    assert engine.materialize(facts, request).model_dump_json() == first.model_dump_json()


def test_later_amendment_cannot_retroactively_change_earlier_pit_result() -> None:
    original = _fact(1, quantity=Decimal("5"))
    amendment = _fact(2, quantity=Decimal("7"))
    early_request = _request(as_of=original.observed_at)
    early = _run((original,), early_request)
    late = _run((original, amendment), _request(as_of=amendment.observed_at))
    assert early.facts == (original,)
    assert late.facts == (amendment,)
    assert _run((original,), early_request) == early
    with pytest.raises(InsiderMaterializationInvalidInputError, match="not known"):
        _run((original, amendment), early_request)


def test_output_construction_failure_is_chained_but_programmer_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bad_result(**kwargs: object) -> object:
        raise ValueError("invalid output")

    monkeypatch.setattr(engine_module, "MaterializedInsiderHistory", bad_result)
    with pytest.raises(InsiderMaterializationComputationError) as captured:
        _run((_fact(),))
    assert isinstance(captured.value.__cause__, ValueError)

    def programmer_failure(**kwargs: object) -> object:
        raise RuntimeError("bug")

    monkeypatch.setattr(engine_module, "MaterializedInsiderHistory", programmer_failure)
    with pytest.raises(RuntimeError, match="bug"):
        _run((_fact(),))


def test_no_external_effect_or_downstream_authority_dependencies() -> None:
    allowed = {
        "datetime",
        "decimal",
        "typing",
        "uuid",
        "pydantic",
        "app.core.schemas",
        "app.data.observations",
        "app.insiders.models",
    }
    source = inspect.getsource(models_module) + inspect.getsource(engine_module)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
            assert node.type.id not in {"Exception", "BaseException"}
    for forbidden in (
        "datetime.now(",
        "utcnow(",
        "time.time(",
        "uuid4(",
        "sorted(",
        "open(",
        "sleep(",
        "eval(",
        "exec(",
    ):
        assert forbidden not in source
