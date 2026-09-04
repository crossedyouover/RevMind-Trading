"""Adversarial deterministic composition using real frozen upstream engines."""

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Overflow
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.orchestration.desk as engine_module
import app.orchestration.models as models_module
from app.catalysts.models import CatalystMaterializationRequest, MaterializedCatalystHistory
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.data.observations import SourceIdentity
from app.desks import (
    CatalystDeskRequest,
    DeterministicAdvisoryDeskEngine,
    InsiderDeskRequest,
    SetupDeskRequest,
    TrendDeskRequest,
)
from app.evidence import MarketEvidenceConfig
from app.insiders import InsiderMaterializationRequest, MaterializedInsiderHistory
from app.materialization import BarSeriesRequest, MaterializedBar, MaterializedBarHistory
from app.orchestration import (
    DeterministicHeadOfDeskEngine,
    HeadOfDeskComputationError,
    HeadOfDeskInvalidInputError,
    HeadOfDeskPolicy,
    HeadOfDeskRequest,
    HeadOfDeskResult,
)
from app.orchestration import (
    HeadOfDeskDisposition as D,
)
from app.orchestration import (
    HeadOfDeskReason as R,
)
from app.portfolio import (
    DeterministicPortfolioContextEngine,
    ObservedPaperAccountState,
    ObservedPositionMark,
    PortfolioContextRequest,
)
from app.regime import DeterministicTrendRegimeEngine, TrendRegimeConfig, TrendRegimeRequest
from app.research import DeterministicSingleSeriesResearchEngine, SingleSeriesResearchRequest
from app.risk import (
    DeterministicPaperRiskEngine,
    PaperRiskPolicy,
    PaperRiskProposal,
    PaperRiskRequest,
)
from app.setups import SetupKey
from app.technical import TechnicalAnalysisConfig

NOW = datetime(2025, 2, 1, tzinfo=UTC)
SOURCE = SourceIdentity(name="explicit")
INSTRUMENT = Instrument(
    symbol="TEST", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)


def request(down=False, count=3, flat=False, closes=None):
    if closes is not None:
        count = len(closes)
    bars = []
    for i in range(count):
        close = Decimal(
            closes[i] if closes is not None else 50 if flat else 100 - i if down else 10 + i
        )
        bars.append(
            MaterializedBar(
                observation_id=UUID(int=i + 1, version=4),
                observed_at=NOW,
                source=SOURCE,
                bar=MarketBar(
                    instrument=INSTRUMENT,
                    timeframe=Timeframe.ONE_MINUTE,
                    timestamp=NOW - timedelta(minutes=count - i - 1),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal(100),
                ),
            )
        )
    history = MaterializedBarHistory(
        request=BarSeriesRequest(
            instrument=INSTRUMENT, source=SOURCE, timeframe=Timeframe.ONE_MINUTE, as_of=NOW
        ),
        bars=tuple(bars),
        inspected_observation_count=count,
        eligible_bar_candidate_count=count,
    )
    setup = DeterministicAdvisoryDeskEngine().setup(
        SetupDeskRequest(
            evaluation_at=NOW,
            payload=DeterministicSingleSeriesResearchEngine().analyze(
                SingleSeriesResearchRequest(
                    history=history,
                    technical_config=TechnicalAnalysisConfig(
                        sma_periods=(2,),
                        ema_periods=(2,),
                        rsi_periods=(2,),
                        atr_periods=(),
                        rolling_high_periods=(2,),
                        rolling_low_periods=(2,),
                        return_periods=(1,),
                        volume_mean_periods=(2,),
                        volume_stddev_periods=(),
                        volume_zscore_periods=(2,),
                    ),
                    evidence_config=MarketEvidenceConfig(
                        price_sma_period=2,
                        trend_ema_period=2,
                        trend_sma_period=2,
                        rsi_period=2,
                        breakout_high_period=2,
                        breakdown_low_period=2,
                        volume_mean_period=2,
                        volume_zscore_period=2,
                    ),
                )
            ),
        )
    )
    trend = DeterministicAdvisoryDeskEngine().trend(
        TrendDeskRequest(
            evaluation_at=NOW,
            payload=DeterministicTrendRegimeEngine().analyze(
                TrendRegimeRequest(
                    history=history,
                    config=TrendRegimeConfig(sma_period=2, return_period=1),
                    evaluation_at=NOW,
                )
            ),
        )
    )
    account = ObservedPaperAccountState(
        observation_id=UUID(int=1000, version=4),
        account_id="paper",
        source=SOURCE,
        currency="USD",
        effective_at=NOW,
        observed_at=NOW,
        cash_balance=Decimal(1000),
        positions=(),
        pending_actions=(),
    )
    context = DeterministicPortfolioContextEngine().evaluate(
        PortfolioContextRequest(account=account, as_of=NOW, evaluation_at=NOW)
    )
    proposal = PaperRiskProposal(
        proposal_id=UUID(int=2000, version=4),
        account_id="paper",
        instrument=INSTRUMENT,
        quantity_change=Decimal(-1 if down else 1),
        effective_at=NOW,
        observed_at=NOW,
        reference_mark=ObservedPositionMark(
            observation_id=UUID(int=3000, version=4),
            source=SOURCE,
            instrument=INSTRUMENT,
            price=Decimal(10),
            valued_at=NOW,
            observed_at=NOW,
        ),
    )
    risk_policy = PaperRiskPolicy(
        policy_id="risk",
        policy_version="1",
        account_id="paper",
        currency="USD",
        max_abs_quantity_change=Decimal(100),
        max_proposal_notional=Decimal(1000),
        max_gross_exposure=Decimal(10000),
        max_instrument_exposure=Decimal(10000),
        max_gross_exposure_share=Decimal(1),
        allow_short_positions=True,
        min_equity_value=Decimal(1),
        min_cash_balance=Decimal(0),
        max_account_age_us=1000000,
        max_mark_age_us=1000000,
        max_proposal_age_us=1000000,
    )
    risk = DeterministicPaperRiskEngine().evaluate(
        PaperRiskRequest(
            context=context, proposal=proposal, policy=risk_policy, as_of=NOW, evaluation_at=NOW
        )
    )
    policy = HeadOfDeskPolicy(
        policy_id="head",
        policy_version="1",
        account_id="paper",
        expected_risk_policy_id="risk",
        expected_risk_policy_version="1",
        setup_key=SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA
        if down
        else SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA,
        enable_watchlist=True,
        enable_alert=True,
        max_bar_age_us=0,
    )
    return HeadOfDeskRequest(
        proposal=proposal,
        risk=risk,
        setup=setup,
        trend=trend,
        catalyst=None,
        insider=None,
        policy=policy,
        as_of=NOW,
        evaluation_at=NOW,
    )


def run(req):
    return DeterministicHeadOfDeskEngine().compose(req)


def policy_change(req, **changes):
    return req.model_copy(update={"policy": req.policy.model_copy(update=changes)})


@pytest.mark.parametrize("down", [False, True])
def test_actual_directional_alert_and_roundtrip(down):
    req = request(down)
    result = run(req)
    assert result.disposition == D.ALERT
    assert result.reasons == (R.SETUP_AND_TREND_SUPPORTED,)
    assert HeadOfDeskResult.model_validate_json(result.model_dump_json()) == result
    assert result.request.model_dump_json() == req.model_dump_json()


@pytest.mark.parametrize(
    "change,disposition,reason",
    [
        ("disabled", D.QUIET, R.WATCHLIST_DISABLED),
        ("no_setup", D.QUIET, R.SETUP_UNAVAILABLE),
        ("no_trend", D.WATCHLIST, R.TREND_NOT_SUPPORTING),
        ("alert_disabled", D.WATCHLIST, R.ALERT_DISABLED),
    ],
)
def test_disposition_table(change, disposition, reason):
    req = request()
    if change == "disabled":
        req = policy_change(req, enable_watchlist=False, enable_alert=False)
    elif change == "alert_disabled":
        req = policy_change(req, enable_alert=False)
    else:
        req = req.model_copy(update={"setup" if change == "no_setup" else "trend": None})
    result = run(req)
    assert result.disposition == disposition
    assert result.reasons == (reason,)


@pytest.mark.parametrize(
    "count,flat,reason",
    [
        (0, False, R.SETUP_UNAVAILABLE),
        (1, False, R.SETUP_NOT_ACTIVE),
        (25, True, R.SETUP_NOT_ACTIVE),
    ],
)
def test_empty_warming_inactive(count, flat, reason):
    result = run(request(count=count, flat=flat))
    assert result.disposition == D.QUIET
    assert result.reasons == (reason,)


def test_missing_and_veto_risk_never_promote():
    req = request()
    assert run(req.model_copy(update={"risk": None})).reasons == (R.RISK_UNAVAILABLE,)
    risk_request = req.risk.request.model_copy(
        update={
            "policy": req.risk.request.policy.model_copy(
                update={"max_proposal_notional": Decimal(1)}
            )
        }
    )
    veto = DeterministicPaperRiskEngine().evaluate(risk_request)
    result = run(req.model_copy(update={"risk": veto}))
    assert result.reasons == (R.RISK_VETO,)
    assert result.selected_setup is result.selected_trend is None


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("account_id", "other", R.ACCOUNT_MISMATCH),
        ("expected_risk_policy_id", "other", R.RISK_POLICY_MISMATCH),
        ("expected_risk_policy_version", "2", R.RISK_POLICY_MISMATCH),
        ("setup_key", SetupKey.DOWNSIDE_BREAKDOWN_BELOW_SMA, R.PROPOSAL_DIRECTION_MISMATCH),
    ],
)
def test_policy_bindings(field, value, reason):
    result = run(policy_change(request(), **{field: value}))
    assert result.disposition == D.QUIET
    assert result.reasons == (reason,)


def test_proposal_uuid_alone_is_insufficient():
    req = request()
    altered = req.proposal.model_copy(update={"quantity_change": Decimal("1.0")})
    assert run(req.model_copy(update={"proposal": altered})).reasons == (R.PROPOSAL_MISMATCH,)


def test_risk_reuse_and_freshness_equality():
    req = request()
    later = NOW + timedelta(microseconds=1)
    changed = req.model_copy(update={"evaluation_at": later})
    assert run(changed).reasons == (R.RISK_BOUNDARY_MISMATCH, R.STALE_SETUP, R.STALE_TREND)
    fresh_risk = DeterministicPaperRiskEngine().evaluate(
        req.risk.request.model_copy(update={"evaluation_at": later})
    )
    changed = policy_change(changed.model_copy(update={"risk": fresh_risk}), max_bar_age_us=1)
    assert run(changed).disposition == D.ALERT


def test_future_and_scope_blockers_order():
    req = request()
    earlier = NOW - timedelta(microseconds=1)
    result = run(req.model_copy(update={"as_of": earlier, "evaluation_at": earlier}))
    assert result.reasons == (
        R.RISK_BOUNDARY_MISMATCH,
        R.FUTURE_KNOWLEDGE,
        R.FUTURE_EVALUATION,
        R.EVIDENCE_SCOPE_MISMATCH,
    )


def test_optional_context_cannot_be_ignored():
    req = request()
    history = MaterializedCatalystHistory(
        request=CatalystMaterializationRequest(as_of=NOW, source=SOURCE),
        facts=(),
        inspected_fact_count=0,
        eligible_fact_count=0,
    )
    report = DeterministicAdvisoryDeskEngine().catalyst(
        CatalystDeskRequest(payload=history, evaluation_at=NOW)
    )
    assert run(req.model_copy(update={"catalyst": report})).reasons == (R.EVIDENCE_SCOPE_MISMATCH,)
    scoped = history.model_copy(
        update={"request": history.request.model_copy(update={"instrument": INSTRUMENT})}
    )
    report = DeterministicAdvisoryDeskEngine().catalyst(
        CatalystDeskRequest(payload=scoped, evaluation_at=NOW)
    )
    assert run(req.model_copy(update={"catalyst": report})).disposition == D.ALERT
    insider = MaterializedInsiderHistory(
        request=InsiderMaterializationRequest(as_of=NOW, source=SOURCE, instrument=INSTRUMENT),
        facts=(),
        inspected_receipt_count=0,
        source_receipt_count=0,
        revision_winner_count=0,
        matching_winner_count=0,
    )
    report = DeterministicAdvisoryDeskEngine().insider(
        InsiderDeskRequest(payload=insider, evaluation_at=NOW)
    )
    assert run(req.model_copy(update={"insider": report})).disposition == D.ALERT


@pytest.mark.parametrize(
    "field,value",
    [
        ("disposition", D.ALERT),
        ("reasons", ()),
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("approved", True),
    ],
)
def test_forged_quiet_cannot_promote(field, value):
    result = run(request().model_copy(update={"risk": None}))
    with pytest.raises(ValidationError):
        HeadOfDeskResult.model_validate({**result.__dict__, field: value})


def test_missing_fields_and_strict_policy():
    req = request()
    for field in HeadOfDeskRequest.model_fields:
        data = dict(req.__dict__)
        del data[field]
        with pytest.raises(ValidationError):
            HeadOfDeskRequest.model_validate(data)
    for field, bad in [
        ("enable_alert", 1),
        ("enable_watchlist", "true"),
        ("max_bar_age_us", True),
        ("max_bar_age_us", -1),
        ("max_bar_age_us", 1.0),
        ("enable_watchlist", False),
    ]:
        with pytest.raises(HeadOfDeskInvalidInputError):
            run(policy_change(req, **{field: bad}))


@pytest.mark.parametrize("bad", [None, 0, "0", "2025-02-01", datetime(2025, 2, 1)])
def test_strict_time(bad):
    with pytest.raises(ValidationError):
        HeadOfDeskRequest.model_validate({**request().__dict__, "evaluation_at": bad})


def test_nested_forgery_and_immutability():
    req = request()
    for bad in [
        object(),
        HeadOfDeskRequest.model_construct(),
        req.model_copy(update={"risk": req.risk.model_copy(update={"reasons": []})}),
        req.model_copy(update={"setup": req.setup.model_copy(update={"coverage": "EMPTY"})}),
    ]:
        with pytest.raises(HeadOfDeskInvalidInputError) as caught:
            run(bad)
        assert caught.value.__cause__ is not None
    result = run(req)
    with pytest.raises(ValidationError):
        result.disposition = D.QUIET
    with pytest.raises(ValidationError):
        HeadOfDeskResult.model_validate(result.model_copy(update={"selected_setup": None}))


def test_json_time_and_upstream_errors(monkeypatch):
    req = request()
    data = json.loads(req.model_dump_json())
    data["evaluation_at"] = "2025-02-01T01:00:00+01:00"
    assert HeadOfDeskRequest.model_validate_json(json.dumps(data)) == req

    def fail(*args):
        raise Overflow("arithmetic")

    monkeypatch.setattr(engine_module, "_rebuild", fail)
    with pytest.raises(HeadOfDeskComputationError):
        run(req)


def test_no_upstream_calls_or_side_effects(monkeypatch):
    allowed = {
        "datetime",
        "enum",
        "typing",
        "pydantic",
        "decimal",
        "app.core.schemas",
        "app.desks.models",
        "app.regime.models",
        "app.risk.models",
        "app.setups.models",
        "app.orchestration.models",
    }
    for module in (models_module, engine_module):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
                if node.module != "app.orchestration.models":
                    assert all(not alias.name.startswith("_") for alias in node.names)
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
                    "evaluate",
                    "analyze",
                    "materialize",
                }
    req = request()

    def fail(*args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(DeterministicPaperRiskEngine, "evaluate", fail)
    assert run(req).disposition == D.ALERT
    monkeypatch.setattr(engine_module, "_compose", fail)
    with pytest.raises(RuntimeError, match="unexpected"):
        run(req)


def trend_report(history, sma=2, returns=1):
    return DeterministicAdvisoryDeskEngine().trend(
        TrendDeskRequest(
            evaluation_at=NOW,
            payload=DeterministicTrendRegimeEngine().analyze(
                TrendRegimeRequest(
                    history=history,
                    config=TrendRegimeConfig(sma_period=sma, return_period=returns),
                    evaluation_at=NOW,
                )
            ),
        )
    )


@pytest.mark.parametrize("sma", [1, 100])
def test_mixed_or_warming_trend_cannot_alert(sma):
    req = request()
    report = trend_report(req.setup.request.payload.request.history, sma=sma)
    result = run(req.model_copy(update={"trend": report}))
    assert result.disposition == D.WATCHLIST
    assert result.reasons == (R.TREND_NOT_SUPPORTING,)


def test_undefined_trend_and_no_search_back():
    req = request(closes=["0", "1", "2"])
    report = trend_report(req.setup.request.payload.request.history, returns=2)
    assert run(req.model_copy(update={"trend": report})).disposition == D.WATCHLIST
    req = request(closes=["10", "11", "12", "1"])
    assert req.setup.request.payload.setup_snapshots[-2].setups[0].status.value == "ACTIVE"
    result = run(req)
    assert result.disposition == D.QUIET
    assert result.reasons == (R.SETUP_NOT_ACTIVE,)
    assert result.selected_setup == req.setup.request.payload.setup_snapshots[-1]


def test_mismatched_histories_and_snapshot_alignment():
    req = request()
    history = req.setup.request.payload.request.history
    modified = history.model_copy(
        update={"inspected_observation_count": history.inspected_observation_count + 1}
    )
    assert run(req.model_copy(update={"trend": trend_report(modified)})).reasons == (
        R.BAR_HISTORY_MISMATCH,
    )
    last = history.bars[-1]
    last = last.model_copy(
        update={"bar": last.bar.model_copy(update={"timestamp": NOW - timedelta(seconds=30)})}
    )
    modified = history.model_copy(update={"bars": (*history.bars[:-1], last)})
    changed = policy_change(
        req.model_copy(update={"trend": trend_report(modified)}), max_bar_age_us=30000000
    )
    assert run(changed).reasons == (R.BAR_HISTORY_MISMATCH, R.SNAPSHOT_MISMATCH)
    changed = req.model_copy(update={"trend": trend_report(modified)})
    assert run(changed).reasons == (R.BAR_HISTORY_MISMATCH, R.STALE_TREND, R.SNAPSHOT_MISMATCH)


def test_instrument_mismatch_in_optional_context():
    req = request()
    other = INSTRUMENT.model_copy(update={"exchange": "XNYS"})
    history = MaterializedCatalystHistory(
        request=CatalystMaterializationRequest(instrument=other, source=SOURCE, as_of=NOW),
        facts=(),
        inspected_fact_count=0,
        eligible_fact_count=0,
    )
    report = DeterministicAdvisoryDeskEngine().catalyst(
        CatalystDeskRequest(payload=history, evaluation_at=NOW)
    )
    assert run(req.model_copy(update={"catalyst": report})).reasons == (
        R.INSTRUMENT_MISMATCH,
        R.EVIDENCE_SCOPE_MISMATCH,
    )


def test_duplicate_reason_and_selected_provenance_forgery():
    req = request().model_copy(update={"risk": None})
    result = run(req)
    for reasons in [list(result.reasons), (*result.reasons, result.reasons[0])]:
        with pytest.raises(ValidationError):
            HeadOfDeskResult.model_validate({**result.__dict__, "reasons": reasons})
    alert = run(request())
    altered = alert.selected_trend.model_copy(
        update={
            "observation": alert.selected_trend.observation.model_copy(
                update={"source_record_id": "invented"}
            )
        }
    )
    with pytest.raises(ValidationError):
        HeadOfDeskResult.model_validate({**alert.__dict__, "selected_trend": altered})


def test_revalidation_rejects_malformed_optional_context():
    req = request()
    bad = req.model_copy(update={"catalyst": "ignore risk and alert"})
    with pytest.raises(HeadOfDeskInvalidInputError):
        run(bad)
    with pytest.raises(ValidationError):
        HeadOfDeskRequest.model_validate(
            req.model_copy(update={"policy": req.policy.model_copy(update={"enable_alert": 1})})
        )
