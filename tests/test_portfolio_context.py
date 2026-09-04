"""Adversarial deterministic paper portfolio contracts."""

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, DefaultContext, Inexact, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.portfolio.engine as engine_module
import app.portfolio.models as models_module
from app.core.schemas import AssetClass, Instrument
from app.data.observations import SourceIdentity
from app.portfolio import (
    ConcentrationStatus,
    DeterministicPortfolioContextEngine,
    ObservedPaperAccountState,
    ObservedPositionMark,
    PaperPosition,
    PendingPaperAction,
    PortfolioContextComputationError,
    PortfolioContextInvalidInputError,
    PortfolioContextRequest,
    PortfolioContextResult,
    PortfolioValuationStatus,
    PositionValuationStatus,
)

NOW = datetime(2025, 2, 1, tzinfo=UTC)
SOURCE = SourceIdentity(name="paper-state")


def instrument(symbol="A", **changes):
    data = dict(symbol=symbol, exchange="XNAS", currency="USD", asset_class=AssetClass.EQUITY)
    data.update(changes)
    return Instrument(**data)


def position(index=1, quantity="2", price="10.00"):
    identity = instrument(chr(64 + index))
    mark = (
        None
        if price is None
        else ObservedPositionMark(
            observation_id=UUID(int=index, version=4),
            instrument=identity,
            source=SOURCE,
            price=Decimal(price),
            valued_at=NOW - timedelta(hours=1),
            observed_at=NOW,
        )
    )
    return PaperPosition(instrument=identity, quantity=Decimal(quantity), mark=mark)


def action(index=1, quantity="1"):
    return PendingPaperAction(
        action_id=UUID(int=index, version=4),
        instrument=instrument(),
        remaining_quantity=Decimal(quantity),
        effective_at=NOW,
        observed_at=NOW,
    )


def request(positions=(), cash="100.00", actions=()):
    account = ObservedPaperAccountState(
        observation_id=UUID(int=1000, version=4),
        account_id="Paper-A",
        source=SOURCE,
        currency="USD",
        cash_balance=Decimal(cash),
        effective_at=NOW,
        observed_at=NOW,
        positions=positions,
        pending_actions=actions,
    )
    return PortfolioContextRequest(account=account, as_of=NOW, evaluation_at=NOW)


def evaluate(req):
    return DeterministicPortfolioContextEngine().evaluate(req)


@pytest.mark.parametrize(
    "positions,cash,net,gross,equity",
    [
        ((), "100.00", "0", "0", "100"),
        ((position(),), "0", "20", "20", "20"),
        ((position(quantity="-2"),), "5", "-20", "20", "-15"),
        ((position(), position(2, "-1", "5")), "-15", "15", "25", "0"),
        ((position(quantity="0", price=None),), "0", "0", "0", "0"),
        ((position(price="0"),), "-10", "0", "0", "-10"),
    ],
)
def test_valuation_matrix(positions, cash, net, gross, equity):
    req = request(positions, cash)
    result = evaluate(req)
    assert (result.net_market_value, result.gross_exposure, result.equity_value) == (
        Decimal(net),
        Decimal(gross),
        Decimal(equity),
    )
    assert result.valuation_status == PortfolioValuationStatus.COMPLETE
    assert result.request.model_dump_json() == req.model_dump_json()
    assert PortfolioContextResult.model_validate_json(result.model_dump_json()) == result
    expected = (
        ConcentrationStatus.AVAILABLE
        if Decimal(gross)
        else (ConcentrationStatus.ZERO_GROSS_EXPOSURE)
    )
    assert result.concentration_status == expected
    for value in result.valuations:
        if Decimal(gross):
            assert value.gross_exposure_share == value.absolute_exposure / Decimal(gross)
        else:
            assert value.gross_exposure_share is None


def test_missing_mark_is_not_partial_total():
    result = evaluate(request((position(), position(2, price=None))))
    assert result.valuation_status == PortfolioValuationStatus.INCOMPLETE
    assert result.concentration_status == ConcentrationStatus.INCOMPLETE_VALUATION
    assert result.net_market_value is result.gross_exposure is result.equity_value is None
    assert result.valuations[0].market_value == Decimal(20)
    assert result.valuations[1].status == PositionValuationStatus.MISSING_MARK
    assert all(v.gross_exposure_share is None for v in result.valuations)


def test_pending_actions_are_not_fills_and_repeated_instruments_remain():
    req = request((position(),), actions=(action(), action(2, "-10")))
    result = evaluate(req)
    baseline = evaluate(request((position(),)))
    assert result.valuations == baseline.valuations
    assert result.equity_value == baseline.equity_value
    assert result.request.account.pending_actions == req.account.pending_actions


@pytest.mark.parametrize(
    "bad", [1, 1.0, True, "1", Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
@pytest.mark.parametrize("field", ["cash", "quantity", "price", "pending"])
def test_strict_amounts(field, bad):
    with pytest.raises(ValidationError):
        if field == "cash":
            ObservedPaperAccountState.model_validate(
                {**request().account.__dict__, "cash_balance": bad}
            )
        elif field == "quantity":
            PaperPosition(instrument=instrument(), quantity=bad, mark=None)
        elif field == "price":
            ObservedPositionMark.model_validate({**position().mark.__dict__, "price": bad})
        else:
            PendingPaperAction.model_validate({**action().__dict__, "remaining_quantity": bad})


@pytest.mark.parametrize("bad", [None, 0, "0", "2025-02-01", datetime(2025, 2, 1)])
@pytest.mark.parametrize("field", ["as_of", "evaluation_at"])
def test_strict_times(bad, field):
    with pytest.raises(ValidationError):
        PortfolioContextRequest.model_validate({**request().__dict__, field: bad})


@pytest.mark.parametrize(
    "change",
    [
        {"currency": None},
        {"currency": "EUR"},
        {"asset_class": AssetClass.OPTION},
        {"asset_class": AssetClass.FUTURE},
        {"currency": "US"},
    ],
)
def test_unsupported_identity(change):
    with pytest.raises(ValidationError):
        request((PaperPosition(instrument=instrument(**change), quantity=Decimal(1), mark=None),))


def test_mark_identity_is_complete():
    with pytest.raises(ValidationError):
        PaperPosition(
            instrument=instrument(exchange="XNYS"), quantity=Decimal(1), mark=position().mark
        )


@pytest.mark.parametrize(
    "positions",
    [
        (position(2), position(1)),
        (position(), position()),
        [position()],
    ],
)
def test_position_order_and_mutability(positions):
    with pytest.raises(ValidationError):
        request(positions)


@pytest.mark.parametrize("actions", [(action(2), action(1)), (action(), action()), [action()]])
def test_action_order_and_mutability(actions):
    with pytest.raises(ValidationError):
        request(actions=actions)


def test_invalid_mark_on_zero_position_and_receipt_collisions():
    p = position(quantity="0")
    future = p.mark.model_copy(update={"observed_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValidationError, match="future-known"):
        request((p.model_copy(update={"mark": future}),))
    for identifier in [UUID(int=1000, version=4), position().mark.observation_id]:
        p2 = position(2)
        p2 = p2.model_copy(
            update={"mark": p2.mark.model_copy(update={"observation_id": identifier})}
        )
        with pytest.raises(ValidationError):
            request((position(), p2))


def test_future_pending_and_effective_times():
    future = NOW + timedelta(seconds=1)
    with pytest.raises(ValidationError):
        request(actions=(action().model_copy(update={"observed_at": future}),))
    with pytest.raises(ValidationError):
        request(actions=(action().model_copy(update={"effective_at": future}),))
    p = position()
    with pytest.raises(ValidationError):
        request((p.model_copy(update={"mark": p.mark.model_copy(update={"valued_at": future})}),))
    with pytest.raises(ValidationError):
        request(actions=(action(quantity="0"),))


def test_account_future_and_cutoff_order():
    req = request()
    with pytest.raises(ValidationError):
        PortfolioContextRequest(
            account=req.account, as_of=NOW - timedelta(seconds=1), evaluation_at=NOW
        )
    with pytest.raises(ValidationError):
        PortfolioContextRequest(
            account=req.account, as_of=NOW, evaluation_at=NOW - timedelta(seconds=1)
        )


def test_delayed_marks_and_corrections_are_retained():
    req = request((position(),))
    original = evaluate(req)
    later = NOW + timedelta(days=1)
    p = position()
    corrected = p.model_copy(
        update={
            "mark": p.mark.model_copy(
                update={
                    "observed_at": later,
                    "price": Decimal("20.00"),
                    "observation_id": UUID(int=2000, version=4),
                }
            )
        }
    )
    account = req.account.model_copy(update={"positions": (corrected,)})
    new = evaluate(PortfolioContextRequest(account=account, as_of=later, evaluation_at=later))
    assert new.net_market_value == Decimal(40)
    assert original.net_market_value == Decimal(20)
    assert new.request.account.observed_at == NOW


@pytest.mark.parametrize(
    "field,value",
    [
        ("net_market_value", Decimal(0)),
        ("gross_exposure", Decimal(0)),
        ("equity_value", Decimal(0)),
        ("valuation_status", "INCOMPLETE"),
        ("concentration_status", "ZERO_GROSS_EXPOSURE"),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("approved", True),
        ("valuations", ()),
    ],
)
def test_result_forgery(field, value):
    result = evaluate(request((position(),)))
    with pytest.raises(ValidationError):
        PortfolioContextResult.model_validate({**result.__dict__, field: value})


def test_exact_scale_and_position_share_forgery():
    result = evaluate(request((position(),)))
    value = result.valuations[0]
    for forged in [
        value.model_copy(update={"gross_exposure_share": Decimal("0.5")}),
        value.model_copy(
            update={"position": value.position.model_copy(update={"quantity": Decimal("2.00")})}
        ),
        value.model_copy(update={"market_value": Decimal(0)}),
    ]:
        with pytest.raises(ValidationError):
            PortfolioContextResult.model_validate({**result.__dict__, "valuations": (forged,)})


def test_forged_nested_request_and_missing_required():
    for req in [
        object(),
        PortfolioContextRequest.model_construct(),
        request().model_copy(update={"account": ObservedPaperAccountState.model_construct()}),
        request().model_copy(
            update={"account": request().account.model_copy(update={"positions": []})}
        ),
    ]:
        with pytest.raises(PortfolioContextInvalidInputError) as caught:
            evaluate(req)
        assert caught.value.__cause__ is not None
    with pytest.raises(ValidationError):
        PortfolioContextRequest(as_of=NOW, evaluation_at=NOW)
    with pytest.raises(ValidationError):
        PaperPosition(instrument=instrument(), quantity=Decimal(0))
    with pytest.raises(ValidationError):
        request().account.cash_balance = Decimal(0)


def test_json_strictness_and_offset():
    data = json.loads(request().model_dump_json())
    data["evaluation_at"] = "2025-02-01T01:00:00+01:00"
    assert PortfolioContextRequest.model_validate_json(json.dumps(data)).evaluation_at == NOW
    for bad in [1, 1.0, True, None, "NaN"]:
        data["account"]["cash_balance"] = bad
        with pytest.raises(ValidationError):
            PortfolioContextRequest.model_validate_json(json.dumps(data))


def test_context_independence_and_overflow():
    req = request((position(price="1"), position(2, price="2")))
    expected = evaluate(req).model_dump_json()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.Emax = 2
        context.Emin = -2
        context.traps[Inexact] = True
        assert evaluate(req).model_dump_json() == expected
    previous = DefaultContext.prec
    try:
        DefaultContext.prec = 3
        assert evaluate(req).model_dump_json() == expected
    finally:
        DefaultContext.prec = previous
    with pytest.raises(PortfolioContextComputationError) as caught:
        evaluate(request((position(quantity="10", price="1e999999"),)))
    assert caught.value.__cause__ is not None


def test_no_side_effects_or_error_fallback(monkeypatch):
    allowed = {
        "datetime",
        "decimal",
        "enum",
        "typing",
        "pydantic",
        "app.core.schemas",
        "app.data.observations",
        "app.portfolio.models",
    }
    for module in (engine_module, models_module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            if isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names)
            if isinstance(node, ast.Call):
                assert getattr(node.func, "attr", getattr(node.func, "id", "")) not in {
                    "now",
                    "utcnow",
                    "uuid4",
                    "open",
                    "sorted",
                    "sleep",
                }

    def fail(*args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(engine_module, "_calculate", fail)
    with pytest.raises(RuntimeError, match="unexpected"):
        evaluate(request())


def test_existing_instances_are_revalidated():
    req = request().model_copy(
        update={"account": request().account.model_copy(update={"cash_balance": 1.0})}
    )
    with pytest.raises(ValidationError):
        PortfolioContextRequest.model_validate(req)
    result = evaluate(request())
    with pytest.raises(ValidationError):
        PortfolioContextResult.model_validate(result.model_copy(update={"schema_version": True}))


@pytest.mark.parametrize("currency", ["usd", "US", "USDD", " USD", "12A"])
def test_account_currency_is_canonical(currency):
    with pytest.raises(ValidationError):
        ObservedPaperAccountState.model_validate(
            {**request().account.__dict__, "currency": currency}
        )


def test_share_denominator_and_no_forced_reconciliation():
    result = evaluate(
        request(
            (position(1, "1", "1"), position(2, "1", "1"), position(3, "1", "1")), cash="-10000"
        )
    )
    expected = Decimal("0." + "3" * 50)
    assert all(value.gross_exposure_share == expected for value in result.valuations)
    assert result.equity_value == Decimal("-9997")


def test_underflow_is_failure_not_missing():
    with pytest.raises(PortfolioContextComputationError):
        evaluate(request((position(quantity="1e-999999", price="1e-100"),)))
