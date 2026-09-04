"""Adversarial paper risk gate tests; no real accounts or providers."""

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, DefaultContext, Inexact, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.risk.engine as engine_module
import app.risk.models as models_module
from app.core.schemas import AssetClass, Instrument
from app.data.observations import SourceIdentity
from app.portfolio import (
    DeterministicPortfolioContextEngine,
    ObservedPaperAccountState,
    ObservedPositionMark,
    PaperPosition,
    PendingPaperAction,
    PortfolioContextRequest,
)
from app.risk.engine import (
    DeterministicPaperRiskEngine,
    PaperRiskComputationError,
    PaperRiskInvalidInputError,
)
from app.risk.models import (
    PaperRiskPolicy,
    PaperRiskProposal,
    PaperRiskRequest,
    PaperRiskResult,
)
from app.risk.models import (
    PaperRiskReason as R,
)
from app.risk.models import (
    PaperRiskStatus as S,
)

NOW = datetime(2025, 2, 1, tzinfo=UTC)
SOURCE = SourceIdentity(name="paper")


def identity(symbol="A", **changes):
    return Instrument(
        **(
            dict(symbol=symbol, exchange="XNAS", currency="USD", asset_class=AssetClass.EQUITY)
            | changes
        )
    )


def mark(symbol="A", price="10", index=1, **changes):
    return ObservedPositionMark(
        **(
            dict(
                observation_id=UUID(int=index, version=4),
                source=SOURCE,
                instrument=identity(symbol),
                price=Decimal(price),
                valued_at=NOW,
                observed_at=NOW,
            )
            | changes
        )
    )


def held(symbol="A", quantity="2", price="10", index=1):
    return PaperPosition(
        instrument=identity(symbol),
        quantity=Decimal(quantity),
        mark=None if price is None else mark(symbol, price, index),
    )


def policy(**changes):
    return PaperRiskPolicy(
        **(
            dict(
                policy_id="test-policy",
                policy_version="1",
                account_id="paper-A",
                currency="USD",
                max_abs_quantity_change=Decimal("100"),
                max_proposal_notional=Decimal("1000"),
                max_gross_exposure=Decimal("10000"),
                max_instrument_exposure=Decimal("10000"),
                max_gross_exposure_share=Decimal("1"),
                allow_short_positions=False,
                min_equity_value=Decimal("1"),
                min_cash_balance=Decimal("0"),
                max_account_age_us=1000000,
                max_mark_age_us=1000000,
                max_proposal_age_us=1000000,
            )
            | changes
        )
    )


def make_request(positions=(), cash="1000", quantity="1", symbol="A", actions=(), **changes):
    account = ObservedPaperAccountState(
        observation_id=UUID(int=1000, version=4),
        account_id="paper-A",
        source=SOURCE,
        currency="USD",
        effective_at=NOW,
        observed_at=NOW,
        cash_balance=Decimal(cash),
        positions=positions,
        pending_actions=actions,
    )
    context = DeterministicPortfolioContextEngine().evaluate(
        PortfolioContextRequest(account=account, as_of=NOW, evaluation_at=NOW)
    )
    matching = next((p for p in positions if p.instrument == identity(symbol)), None)
    reference = (
        matching.mark if matching is not None and matching.mark is not None else mark(symbol)
    )
    proposal = PaperRiskProposal(
        proposal_id=UUID(int=999, version=4),
        account_id="paper-A",
        instrument=identity(symbol),
        quantity_change=Decimal(quantity),
        effective_at=NOW,
        observed_at=NOW,
        reference_mark=reference,
    )
    return PaperRiskRequest(
        **(
            dict(context=context, proposal=proposal, policy=policy(), as_of=NOW, evaluation_at=NOW)
            | changes
        )
    )


def run(req):
    return DeterministicPaperRiskEngine().evaluate(req)


def with_policy(req, **changes):
    return req.model_copy(update={"policy": req.policy.model_copy(update=changes)})


def pending(quantity="1"):
    return PendingPaperAction(
        action_id=UUID(int=800, version=4),
        instrument=identity(),
        remaining_quantity=Decimal(quantity),
        effective_at=NOW,
        observed_at=NOW,
    )


@pytest.mark.parametrize(
    "positions,quantity,cash,expected_qty,expected_cash,gross",
    [
        ((), "1", "1000", "1", "990", "10"),
        ((held(),), "-2", "1000", "0", "1020", "0"),
        ((held(),), "-3", "1000", "-1", "1030", "10"),
        ((held(quantity="0", price=None),), "1", "1000", "1", "990", "10"),
        ((held(),), "-1", "-5", "1", "5", "10"),
    ],
)
def test_projection_matrix(positions, quantity, cash, expected_qty, expected_cash, gross):
    req = make_request(positions, cash, quantity)
    result = run(with_policy(req, allow_short_positions=True))
    assert result.status == S.PASS_CHECKS
    assert result.reasons == ()
    assert result.projection.positions[0].position.quantity == Decimal(expected_qty)
    assert result.projection.projected_cash == Decimal(expected_cash)
    assert result.projection.gross_exposure == Decimal(gross)
    assert PaperRiskResult.model_validate_json(result.model_dump_json()) == result
    assert result.request.context.model_dump_json() == req.context.model_dump_json()


@pytest.mark.parametrize("symbol", ["A", "C", "Z"])
def test_canonical_insertion(symbol):
    req = make_request((held("B", index=2), held("D", index=4)), symbol=symbol)
    result = run(req)
    assert result.status == S.PASS_CHECKS
    actual = [p.position.instrument.symbol for p in result.projection.positions]
    assert actual == sorted(["B", "D", symbol])
    assert len(req.context.request.account.positions) == 2


@pytest.mark.parametrize(
    "field,bound,breach,reason",
    [
        ("max_abs_quantity_change", "1", "0.999", R.QUANTITY_LIMIT),
        ("max_proposal_notional", "10", "9.999", R.PROPOSAL_NOTIONAL_LIMIT),
        ("max_gross_exposure", "10", "9.999", R.GROSS_EXPOSURE_LIMIT),
        ("max_instrument_exposure", "10", "9.999", R.INSTRUMENT_EXPOSURE_LIMIT),
        ("max_gross_exposure_share", "1", "0.999", R.CONCENTRATION_LIMIT),
        ("min_cash_balance", "990", "990.001", R.CASH_FLOOR),
        ("min_equity_value", "1000", "1000.001", R.EQUITY_BELOW_MINIMUM),
    ],
)
def test_limit_equality_and_breach(field, bound, breach, reason):
    req = make_request()
    assert run(with_policy(req, **{field: Decimal(bound)})).status == S.PASS_CHECKS
    result = run(with_policy(req, **{field: Decimal(breach)}))
    assert result.status == S.VETO
    assert result.reasons == (reason,)
    assert (result.projection is None) == (reason == R.EQUITY_BELOW_MINIMUM)


@pytest.mark.parametrize(
    "field,reason",
    [
        ("max_account_age_us", R.STALE_ACCOUNT),
        ("max_mark_age_us", R.STALE_MARK),
        ("max_proposal_age_us", R.STALE_PROPOSAL),
    ],
)
def test_freshness_microsecond_boundary(field, reason):
    req = make_request(evaluation_at=NOW + timedelta(microseconds=1))
    assert run(with_policy(req, **{field: 1})).status == S.PASS_CHECKS
    assert run(with_policy(req, **{field: 0})).reasons == (reason,)


@pytest.mark.parametrize("quantity", ["1", "-1"])
def test_pending_is_unconditional_veto(quantity):
    result = run(make_request((held(),), quantity="-1", actions=(pending(quantity),)))
    assert result.reasons == (R.PENDING_ACTIONS,)
    assert result.projection is None
    assert PaperRiskResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "price,reason", [(None, R.INCOMPLETE_VALUATION), ("0", R.NONPOSITIVE_MARK)]
)
def test_unusable_valuation(price, reason):
    result = run(make_request((held(price=price),)))
    assert result.reasons == (reason,)
    assert result.projection is None


def test_nonpositive_reference_and_exact_mark_provenance():
    req = make_request()
    req = req.model_copy(
        update={"proposal": req.proposal.model_copy(update={"reference_mark": mark(price="0")})}
    )
    assert run(req).reasons == (R.NONPOSITIVE_MARK,)
    req = make_request((held(),))
    for changes in [
        {"price": Decimal("10.0")},
        {"observation_id": UUID(int=50, version=4)},
        {"source": SourceIdentity(name="other")},
    ]:
        altered = req.proposal.reference_mark.model_copy(update=changes)
        changed = req.model_copy(
            update={"proposal": req.proposal.model_copy(update={"reference_mark": altered})}
        )
        assert run(changed).reasons == (R.REFERENCE_MARK_MISMATCH,)


def test_binding_and_multifailure_order():
    req = make_request(actions=(pending(),), evaluation_at=NOW + timedelta(seconds=2))
    result = run(with_policy(req, account_id="other", currency="EUR"))
    assert result.reasons == (
        R.ACCOUNT_MISMATCH,
        R.CURRENCY_MISMATCH,
        R.STALE_ACCOUNT,
        R.STALE_PROPOSAL,
        R.STALE_MARK,
        R.PENDING_ACTIONS,
    )
    assert result.projection is None


def test_future_context_and_proposal():
    req = make_request(as_of=NOW - timedelta(microseconds=1))
    assert run(req).reasons == (R.FUTURE_KNOWLEDGE,)
    req = make_request(
        as_of=NOW - timedelta(microseconds=1), evaluation_at=NOW - timedelta(microseconds=1)
    )
    assert run(req).reasons == (R.FUTURE_KNOWLEDGE, R.FUTURE_EVENT)
    req = make_request()
    proposal = req.proposal.model_copy(update={"observed_at": NOW + timedelta(microseconds=1)})
    assert run(req.model_copy(update={"proposal": proposal})).reasons == (R.FUTURE_KNOWLEDGE,)


def test_limits_collect_all_without_resize():
    req = make_request(quantity="-2")
    result = run(
        with_policy(
            req,
            max_abs_quantity_change=Decimal(1),
            max_proposal_notional=Decimal(1),
            min_cash_balance=Decimal(2000),
            max_gross_exposure=Decimal(1),
            max_instrument_exposure=Decimal(1),
            max_gross_exposure_share=Decimal("0.5"),
        )
    )
    assert result.reasons == (
        R.QUANTITY_LIMIT,
        R.PROPOSAL_NOTIONAL_LIMIT,
        R.CASH_FLOOR,
        R.GROSS_EXPOSURE_LIMIT,
        R.INSTRUMENT_EXPOSURE_LIMIT,
        R.CONCENTRATION_LIMIT,
        R.SHORT_POSITION_DISALLOWED,
    )
    assert result.projection.positions[0].position.quantity == Decimal("-2")


def test_whole_account_and_full_identity():
    req = make_request((held("A", "-1"), held("B", "100", index=2)), symbol="C")
    result = run(with_policy(req, max_instrument_exposure=Decimal(100)))
    assert result.reasons == (R.INSTRUMENT_EXPOSURE_LIMIT, R.SHORT_POSITION_DISALLOWED)
    req = make_request((held(),))
    other = identity(exchange="XNYS")
    proposal = req.proposal.model_copy(
        update={"instrument": other, "reference_mark": mark(instrument=other)}
    )
    assert len(run(req.model_copy(update={"proposal": proposal})).projection.positions) == 2


POSITIVE_FIELDS = [
    "max_abs_quantity_change",
    "max_proposal_notional",
    "max_gross_exposure",
    "max_instrument_exposure",
    "max_gross_exposure_share",
    "min_equity_value",
]


@pytest.mark.parametrize("field", POSITIVE_FIELDS)
@pytest.mark.parametrize("bad", [Decimal(0), Decimal("-1"), Decimal("NaN"), 1, 1.0, True, "1"])
def test_strict_positive_policy(field, bad):
    with pytest.raises(ValidationError):
        policy(**{field: bad})


@pytest.mark.parametrize("field", ["max_account_age_us", "max_mark_age_us", "max_proposal_age_us"])
@pytest.mark.parametrize("bad", [-1, 1.0, True, "1"])
def test_strict_age_policy(field, bad):
    with pytest.raises(ValidationError):
        policy(**{field: bad})


@pytest.mark.parametrize("bad", [0, 1, "true", None])
def test_short_permission_is_strict(bad):
    with pytest.raises(ValidationError):
        policy(allow_short_positions=bad)


@pytest.mark.parametrize("bad", [1, 1.0, True, "1", Decimal(0), Decimal("Infinity")])
def test_proposal_quantity_strict(bad):
    req = make_request()
    with pytest.raises(ValidationError):
        PaperRiskProposal.model_validate({**req.proposal.__dict__, "quantity_change": bad})


@pytest.mark.parametrize("bad", [None, 0, "0", "2025-02-01", datetime(2025, 2, 1)])
def test_time_strict(bad):
    with pytest.raises(ValidationError):
        PaperRiskRequest.model_validate({**make_request().__dict__, "evaluation_at": bad})


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", S.PASS_CHECKS),
        ("reasons", ()),
        ("projection", None),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("approved", True),
    ],
)
def test_result_forgery(field, value):
    result = run(with_policy(make_request(), max_proposal_notional=Decimal(1)))
    with pytest.raises(ValidationError):
        PaperRiskResult.model_validate({**result.__dict__, field: value})


def test_reason_order_duplicates_and_nested_forgery():
    result = run(
        with_policy(
            make_request(), max_proposal_notional=Decimal(1), max_abs_quantity_change=Decimal("0.5")
        )
    )
    for reasons in [
        result.reasons[::-1],
        (*result.reasons, result.reasons[0]),
        list(result.reasons),
    ]:
        with pytest.raises(ValidationError):
            PaperRiskResult.model_validate({**result.__dict__, "reasons": reasons})
    req = make_request()
    for bad in [
        object(),
        PaperRiskRequest.model_construct(),
        req.model_copy(
            update={"policy": req.policy.model_copy(update={"max_proposal_notional": Decimal(0)})}
        ),
        req.model_copy(
            update={"context": req.context.model_copy(update={"equity_value": Decimal("9999")})}
        ),
    ]:
        with pytest.raises(PaperRiskInvalidInputError) as caught:
            run(bad)
        assert caught.value.__cause__ is not None


def test_projection_forgery_and_immutability():
    result = run(make_request())
    for projection in [
        result.projection.model_copy(update={"projected_cash": Decimal(1000)}),
        result.projection.model_copy(update={"positions": ()}),
        result.projection.model_copy(update={"proposal_notional": Decimal(0)}),
    ]:
        with pytest.raises(ValidationError):
            PaperRiskResult.model_validate({**result.__dict__, "projection": projection})
    with pytest.raises(ValidationError):
        result.status = S.VETO
    with pytest.raises(ValidationError):
        PaperRiskResult.model_validate(result.model_copy(update={"schema_version": True}))


def test_json_and_decimal_context_independence():
    req = make_request((held("B", index=2), held("C", index=3)))
    expected = run(req).model_dump_json()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.Emax = 2
        context.Emin = -2
        context.traps[Inexact] = True
        assert run(req).model_dump_json() == expected
    previous = DefaultContext.prec
    try:
        DefaultContext.prec = 2
        assert run(req).model_dump_json() == expected
    finally:
        DefaultContext.prec = previous
    data = json.loads(req.model_dump_json())
    data["evaluation_at"] = "2025-02-01T01:00:00+01:00"
    assert PaperRiskRequest.model_validate_json(json.dumps(data)) == req
    data["policy"]["max_proposal_notional"] = 1000
    with pytest.raises(ValidationError):
        PaperRiskRequest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("quantity,price", [("10", "1e999999"), ("1e-999999", "1e-100")])
def test_arithmetic_failure_not_pass(quantity, price):
    req = make_request(quantity=quantity)
    req = req.model_copy(
        update={"proposal": req.proposal.model_copy(update={"reference_mark": mark(price=price)})}
    )
    with pytest.raises(PaperRiskComputationError) as caught:
        run(req)
    assert caught.value.__cause__ is not None


def test_no_side_effect_dependencies_or_error_fallback(monkeypatch):
    allowed = {
        "datetime",
        "decimal",
        "enum",
        "typing",
        "pydantic",
        "app.core.schemas",
        "app.portfolio.models",
        "app.risk.models",
    }
    for module in (models_module, engine_module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
                if node.module == "app.portfolio.models":
                    assert all(not name.name.startswith("_") for name in node.names)
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
                    "total_seconds",
                }

    def fail(*args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(engine_module, "_evaluate", fail)
    with pytest.raises(RuntimeError, match="unexpected"):
        run(make_request())


@pytest.mark.parametrize(
    "field,bad",
    [
        ("max_gross_exposure_share", Decimal("1.01")),
        ("min_cash_balance", Decimal("-1")),
        ("min_cash_balance", 0),
        ("currency", "usd"),
        ("currency", "US"),
        ("policy_id", ""),
    ],
)
def test_policy_additional_bounds(field, bad):
    with pytest.raises(ValidationError):
        policy(**{field: bad})


def test_missing_policy_and_input_constraints():
    data = policy().__dict__.copy()
    for field in data:
        missing = dict(data)
        del missing[field]
        with pytest.raises(ValidationError):
            PaperRiskPolicy.model_validate(missing)
    req = make_request()
    with pytest.raises(ValidationError):
        PaperRiskRequest.model_validate({**req.__dict__, "as_of": NOW + timedelta(seconds=1)})
    with pytest.raises(ValidationError):
        PaperRiskProposal.model_validate(
            {**req.proposal.__dict__, "effective_at": NOW + timedelta(seconds=1)}
        )
    with pytest.raises(ValidationError):
        PaperRiskProposal.model_validate(
            {**req.proposal.__dict__, "instrument": identity(exchange="XNYS")}
        )


def test_reference_receipt_cannot_be_backdated():
    req = make_request()
    reference = mark(observed_at=NOW + timedelta(microseconds=1))
    changed = req.model_copy(
        update={"proposal": req.proposal.model_copy(update={"reference_mark": reference})}
    )
    assert run(changed).reasons == (R.FUTURE_KNOWLEDGE,)
    reference = mark(
        valued_at=NOW + timedelta(microseconds=1), observed_at=NOW + timedelta(microseconds=1)
    )
    changed = req.model_copy(
        update={"proposal": req.proposal.model_copy(update={"reference_mark": reference})}
    )
    assert run(changed).reasons == (R.FUTURE_KNOWLEDGE, R.FUTURE_EVENT)


def test_correction_yields_separate_evidence():
    req = make_request()
    original = run(req)
    later = NOW + timedelta(microseconds=1)
    reference = mark(price="11", observed_at=later, index=9)
    changed = req.model_copy(
        update={
            "as_of": later,
            "evaluation_at": later,
            "proposal": req.proposal.model_copy(
                update={"reference_mark": reference, "observed_at": later}
            ),
        }
    )
    corrected = run(changed)
    assert original.projection.proposal_notional == Decimal(10)
    assert corrected.projection.proposal_notional == Decimal(11)
    assert original.request.model_dump_json() == req.model_dump_json()


def test_unrelated_stale_marks_and_reductions_still_veto():
    old = held("B", index=2)
    old = old.model_copy(
        update={"mark": old.mark.model_copy(update={"valued_at": NOW - timedelta(seconds=2)})}
    )
    req = make_request((held(), old), quantity="-2")
    result = run(req)
    assert result.reasons == (R.STALE_MARK,)
    assert result.projection is None
