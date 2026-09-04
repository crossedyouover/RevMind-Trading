"""Adversarial trend-regime tests: exact comparisons, PIT provenance, and stage contracts."""

import ast
import inspect
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.regime.engine as engine_module
import app.regime.models as models_module
from app.core.schemas import AssetClass, Instrument, MarketBar, Timeframe
from app.data.observations import SourceIdentity
from app.materialization import BarSeriesRequest, MaterializedBar, MaterializedBarHistory
from app.regime import (
    DeterministicTrendRegimeEngine,
    RegimeEvidenceStatus,
    TrendRegime,
    TrendRegimeComputationError,
    TrendRegimeConfig,
    TrendRegimeInvalidInputError,
    TrendRegimeRequest,
    TrendRegimeResult,
    TrendRegimeSnapshot,
)
from app.technical import (
    DeterministicTechnicalAnalysisEngine,
    TechnicalAnalysisConfig,
    TechnicalAnalysisInvalidInputError,
    TechnicalFeature,
    TechnicalFeatureKey,
    TechnicalFeatureStatus,
    TechnicalSnapshot,
)

_EVENT = datetime(2025, 1, 1, tzinfo=UTC)
_KNOWLEDGE = datetime(2025, 1, 10, tzinfo=UTC)
_INSTRUMENT = Instrument(
    symbol="TEST", asset_class=AssetClass.EQUITY, exchange="XNAS", currency="USD"
)
_SOURCE = SourceIdentity(name="research-source")


def _request(
    closes: tuple[str, ...] = ("1", "2", "3"),
    *,
    sma: int = 2,
    returns: int = 1,
) -> TrendRegimeRequest:
    bars = tuple(
        MaterializedBar(
            bar=MarketBar(
                instrument=_INSTRUMENT,
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=_EVENT + timedelta(minutes=index),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal("100"),
            ),
            observation_id=UUID(int=index + 1, version=4),
            observed_at=_KNOWLEDGE,
            source=_SOURCE,
            source_record_id=f"bar-{index}",
        )
        for index, close in enumerate(closes)
    )
    return TrendRegimeRequest(
        history=MaterializedBarHistory(
            request=BarSeriesRequest(
                instrument=_INSTRUMENT,
                source=_SOURCE,
                timeframe=Timeframe.ONE_MINUTE,
                as_of=_KNOWLEDGE,
            ),
            bars=bars,
            inspected_observation_count=len(bars),
            eligible_bar_candidate_count=len(bars),
        ),
        config=TrendRegimeConfig(sma_period=sma, return_period=returns),
        evaluation_at=_KNOWLEDGE,
    )


def _snapshot(
    *,
    sma_value: Decimal = Decimal("10"),
    return_value: Decimal = Decimal("0"),
    sma_status: TechnicalFeatureStatus = TechnicalFeatureStatus.AVAILABLE,
    return_status: TechnicalFeatureStatus = TechnicalFeatureStatus.AVAILABLE,
    status: RegimeEvidenceStatus = RegimeEvidenceStatus.AVAILABLE,
    regime: TrendRegime | None = TrendRegime.FLAT,
) -> TrendRegimeSnapshot:
    return TrendRegimeSnapshot(
        observation=_request(("10",)).history.bars[0],
        sma=TechnicalFeature(
            key=TechnicalFeatureKey.SMA_CLOSE,
            period=2,
            status=sma_status,
            value=sma_value if sma_status is TechnicalFeatureStatus.AVAILABLE else None,
        ),
        arithmetic_return=TechnicalFeature(
            key=TechnicalFeatureKey.ARITHMETIC_RETURN,
            period=1,
            status=return_status,
            value=return_value if return_status is TechnicalFeatureStatus.AVAILABLE else None,
        ),
        status=status,
        regime=regime,
    )


@pytest.mark.parametrize(
    ("sma", "returns", "expected"),
    [
        ("9", "1", TrendRegime.UPWARD),
        ("11", "-1", TrendRegime.DOWNWARD),
        ("10", "0", TrendRegime.FLAT),
        ("9", "-1", TrendRegime.MIXED),
        ("9", "0", TrendRegime.MIXED),
        ("11", "1", TrendRegime.MIXED),
        ("11", "0", TrendRegime.MIXED),
        ("10", "1", TrendRegime.MIXED),
        ("10", "-1", TrendRegime.MIXED),
    ],
)
def test_all_nine_available_sign_combinations(
    sma: str, returns: str, expected: TrendRegime
) -> None:
    snapshot = _snapshot(sma_value=Decimal(sma), return_value=Decimal(returns), regime=expected)
    assert snapshot.status is RegimeEvidenceStatus.AVAILABLE
    assert snapshot.regime is expected


@pytest.mark.parametrize("sma_status", list(TechnicalFeatureStatus))
@pytest.mark.parametrize("return_status", list(TechnicalFeatureStatus))
def test_full_availability_matrix(
    sma_status: TechnicalFeatureStatus,
    return_status: TechnicalFeatureStatus,
) -> None:
    if TechnicalFeatureStatus.WARMING_UP in (sma_status, return_status):
        expected = RegimeEvidenceStatus.WARMING_UP
    elif TechnicalFeatureStatus.UNDEFINED in (sma_status, return_status):
        expected = RegimeEvidenceStatus.UNDEFINED
    else:
        expected = RegimeEvidenceStatus.AVAILABLE
    snapshot = _snapshot(
        sma_status=sma_status,
        return_status=return_status,
        status=expected,
        regime=TrendRegime.FLAT if expected is RegimeEvidenceStatus.AVAILABLE else None,
    )
    assert snapshot.status is expected


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        (("1", "2", "3"), TrendRegime.UPWARD),
        (("3", "2", "1"), TrendRegime.DOWNWARD),
        (("2", "2", "2"), TrendRegime.FLAT),
    ],
)
def test_reference_engine_composes_real_frozen_technical_values(
    closes: tuple[str, ...],
    expected: TrendRegime,
) -> None:
    request = _request(closes)
    result = DeterministicTrendRegimeEngine().analyze(request)
    assert result.request == request
    assert len(result.snapshots) == len(closes)
    assert result.snapshots[0].status is RegimeEvidenceStatus.WARMING_UP
    assert result.snapshots[-1].regime is expected
    assert tuple(item.observation for item in result.snapshots) == request.history.bars
    assert result.snapshots[-1].sma.value == (Decimal(closes[-1]) + Decimal(closes[-2])) / 2


def test_zero_return_denominator_is_undefined_and_warmup_takes_precedence() -> None:
    engine = DeterministicTrendRegimeEngine()
    undefined = engine.analyze(_request(("0", "1")))
    assert undefined.snapshots[-1].status is RegimeEvidenceStatus.UNDEFINED
    assert undefined.snapshots[-1].regime is None
    warming = engine.analyze(_request(("0", "1"), sma=3))
    assert warming.snapshots[-1].status is RegimeEvidenceStatus.WARMING_UP
    assert warming.snapshots[-1].regime is None


def test_mixed_real_series_and_explicit_different_periods() -> None:
    result = DeterministicTrendRegimeEngine().analyze(_request(("2", "1", "2"), sma=3, returns=2))
    assert result.snapshots[-1].regime is TrendRegime.MIXED
    assert result.snapshots[-1].sma.period == 3
    assert result.snapshots[-1].arithmetic_return.period == 2


@pytest.mark.parametrize("field", ["sma_period", "return_period"])
@pytest.mark.parametrize("value", [True, "2", 2.0, 0, -1, 100001])
def test_configuration_rejects_coercion_and_out_of_range(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TrendRegimeConfig.model_validate({"sma_period": 2, "return_period": 1, field: value})


def test_configuration_has_no_implicit_defaults() -> None:
    with pytest.raises(ValidationError):
        TrendRegimeConfig.model_validate({})


def test_empty_history_is_explicit() -> None:
    result = DeterministicTrendRegimeEngine().analyze(_request(()))
    assert result.snapshots == ()
    assert result.request.history.bars == ()


def test_future_knowledge_and_event_cutoffs_fail_closed_including_empty_history() -> None:
    for request in (_request(), _request(())):
        bad = request.model_copy(update={"evaluation_at": _KNOWLEDGE - timedelta(microseconds=1)})
        with pytest.raises(TrendRegimeInvalidInputError, match="PIT-safe"):
            DeterministicTrendRegimeEngine().analyze(bad)
    request = _request(("1",))
    original = request.history.bars[0]
    future_bar = original.bar.model_copy(
        update={"timestamp": _KNOWLEDGE + timedelta(microseconds=1)}
    )
    future_observation = original.model_copy(update={"bar": future_bar})
    bad = request.model_copy(
        update={"history": request.history.model_copy(update={"bars": (future_observation,)})}
    )
    with pytest.raises(TrendRegimeInvalidInputError):
        DeterministicTrendRegimeEngine().analyze(bad)


def test_exact_event_boundary_and_evaluation_offset_are_accepted() -> None:
    request = _request(("1",))
    original = request.history.bars[0]
    at_cutoff = original.model_copy(
        update={"bar": original.bar.model_copy(update={"timestamp": _KNOWLEDGE})}
    )
    request = TrendRegimeRequest(
        history=request.history.model_copy(update={"bars": (at_cutoff,)}),
        config=request.config,
        evaluation_at=_KNOWLEDGE.astimezone(timezone(timedelta(hours=-5))),
    )
    result = DeterministicTrendRegimeEngine().analyze(request)
    assert result.request.evaluation_at == _KNOWLEDGE
    assert result.snapshots[0].observation.bar.timestamp == _KNOWLEDGE


def test_prefix_stability_and_caller_decimal_context_do_not_change_results() -> None:
    engine = DeterministicTrendRegimeEngine()
    prefix = engine.analyze(_request(("1", "2", "3")))
    extended = engine.analyze(_request(("1", "2", "3", "1000", "0")))
    assert prefix.snapshots == extended.snapshots[:3]
    with localcontext() as context:
        context.prec = 3
        assert engine.analyze(_request(("1", "2", "3"))) == prefix


def test_late_corrected_asof_view_does_not_relabel_prior_knowledge_or_mutate_result() -> None:
    engine = DeterministicTrendRegimeEngine()
    original = _request()
    first = engine.analyze(original)
    amended = _request(("1", "2", "0"))
    later_time = _KNOWLEDGE + timedelta(hours=1)
    observations = tuple(
        item.model_copy(
            update={
                "observed_at": later_time,
                "observation_id": UUID(int=index + 100, version=4),
            }
        )
        for index, item in enumerate(amended.history.bars)
    )
    later_history = MaterializedBarHistory(
        request=amended.history.request.model_copy(update={"as_of": later_time}),
        bars=observations,
        inspected_observation_count=3,
        eligible_bar_candidate_count=3,
    )
    later = engine.analyze(
        TrendRegimeRequest(
            history=later_history,
            config=amended.config,
            evaluation_at=later_time,
        )
    )
    assert first.snapshots[-1].regime is TrendRegime.UPWARD
    assert later.snapshots[-1].regime is TrendRegime.DOWNWARD
    assert later.request.history.request.as_of == later_time
    assert first.request.history.request.as_of == _KNOWLEDGE
    assert engine.analyze(original) == first


def test_forged_nested_request_and_duplicate_receipt_identity_rejected() -> None:
    request = _request()
    duplicate = request.history.bars[1].model_copy(
        update={
            "observation_id": request.history.bars[0].observation_id,
        }
    )
    bad_histories = [
        request.history.model_copy(update={"bars": [*request.history.bars]}),
        request.history.model_copy(
            update={"bars": (request.history.bars[0], duplicate, request.history.bars[2])}
        ),
        request.history.model_copy(update={"bars": (object(),)}),
    ]
    for history in bad_histories:
        with pytest.raises(TrendRegimeInvalidInputError):
            DeterministicTrendRegimeEngine().analyze(
                request.model_copy(update={"history": history})
            )
    with pytest.raises(TrendRegimeInvalidInputError):
        DeterministicTrendRegimeEngine().analyze(
            request.model_copy(
                update={
                    "config": request.config.model_copy(update={"sma_period": True}),
                }
            )
        )
    with pytest.raises(TrendRegimeInvalidInputError):
        DeterministicTrendRegimeEngine().analyze(cast(TrendRegimeRequest, object()))


class _TechnicalStub:
    def __init__(self, mode: str = "correct") -> None:
        self.mode = mode
        self.calls: list[TechnicalAnalysisConfig] = []

    def analyze(
        self,
        bars: Sequence[MarketBar],
        config: TechnicalAnalysisConfig,
    ) -> tuple[TechnicalSnapshot, ...]:
        self.calls.append(config)
        if self.mode == "failure":
            raise TechnicalAnalysisInvalidInputError("injected stage error")
        if self.mode == "bug":
            raise RuntimeError("programmer error")
        snapshots = DeterministicTechnicalAnalysisEngine().analyze(bars, config)
        if self.mode == "correct":
            return snapshots
        if self.mode == "short":
            return snapshots[:-1]
        if self.mode == "list":
            return cast(tuple[TechnicalSnapshot, ...], list(snapshots))
        if self.mode == "objects":
            return cast(tuple[TechnicalSnapshot, ...], tuple(object() for _ in snapshots))
        first = snapshots[0]
        if self.mode == "timestamp":
            first = first.model_copy(update={"timestamp": _EVENT + timedelta(seconds=1)})
        elif self.mode == "instrument":
            first = first.model_copy(
                update={"instrument": _INSTRUMENT.model_copy(update={"exchange": "OTHER"})}
            )
        elif self.mode == "timeframe":
            first = first.model_copy(update={"timeframe": Timeframe.ONE_DAY})
        elif self.mode == "missing":
            first = first.model_copy(update={"features": first.features[:1]})
        elif self.mode == "reordered":
            first = first.model_copy(update={"features": tuple(reversed(first.features))})
        elif self.mode == "period":
            first = first.model_copy(
                update={
                    "features": (
                        first.features[0].model_copy(update={"period": config.sma_periods[0] + 1}),
                        first.features[1],
                    )
                }
            )
        elif self.mode == "extra":
            first = first.model_copy(
                update={
                    "features": (
                        first.features[0],
                        first.features[0].model_copy(update={"period": 99}),
                        first.features[1],
                    )
                }
            )
        return (first, *snapshots[1:])


def test_technical_stage_runs_once_with_exact_explicit_features_including_empty() -> None:
    for request in (_request(), _request(())):
        stage = _TechnicalStub()
        DeterministicTrendRegimeEngine(stage).analyze(request)
        assert len(stage.calls) == 1
        config = stage.calls[0]
        assert config.sma_periods == (2,) and config.return_periods == (1,)
        assert config.ema_periods == config.rsi_periods == config.atr_periods == ()
        assert config.rolling_high_periods == config.rolling_low_periods == ()
        assert config.volume_mean_periods == config.volume_stddev_periods == ()
        assert config.volume_zscore_periods == ()


@pytest.mark.parametrize(
    "mode",
    [
        "failure",
        "short",
        "list",
        "objects",
        "timestamp",
        "instrument",
        "timeframe",
        "missing",
        "reordered",
        "period",
        "extra",
    ],
)
def test_malformed_technical_stage_fails_without_retry_or_fallback(mode: str) -> None:
    stage = _TechnicalStub(mode)
    with pytest.raises(TrendRegimeComputationError) as captured:
        DeterministicTrendRegimeEngine(stage).analyze(_request())
    assert captured.value.__cause__ is not None
    assert len(stage.calls) == 1


def test_unexpected_programmer_error_is_not_hidden() -> None:
    with pytest.raises(RuntimeError, match="programmer"):
        DeterministicTrendRegimeEngine(_TechnicalStub("bug")).analyze(_request())


def test_direct_snapshot_cannot_forge_status_regime_or_operand_keys() -> None:
    with pytest.raises(ValidationError, match="contradicts"):
        _snapshot(regime=TrendRegime.UPWARD)
    with pytest.raises(ValidationError, match="contradicts"):
        _snapshot(status=RegimeEvidenceStatus.UNDEFINED, regime=None)
    snapshot = _snapshot()
    for changed in [
        {"sma": snapshot.sma.model_copy(update={"key": TechnicalFeatureKey.EMA_CLOSE})},
        {
            "arithmetic_return": snapshot.arithmetic_return.model_copy(
                update={"key": TechnicalFeatureKey.RSI_CLOSE_WILDER}
            )
        },
    ]:
        with pytest.raises(ValidationError):
            TrendRegimeSnapshot.model_validate({**snapshot.model_dump(mode="python"), **changed})


def test_result_enforces_counts_periods_full_provenance_and_immutable_tuple() -> None:
    result = DeterministicTrendRegimeEngine().analyze(_request())
    for snapshots in [
        result.snapshots[:-1],
        tuple(reversed(result.snapshots)),
        (
            result.snapshots[0].model_copy(
                update={
                    "observation": result.snapshots[0].observation.model_copy(
                        update={"source_record_id": "forged"}
                    )
                }
            ),
            *result.snapshots[1:],
        ),
        (
            result.snapshots[0].model_copy(
                update={"sma": result.snapshots[0].sma.model_copy(update={"period": 77})}
            ),
            *result.snapshots[1:],
        ),
    ]:
        with pytest.raises(ValidationError):
            TrendRegimeResult(request=result.request, snapshots=snapshots)
    with pytest.raises(ValidationError, match="tuple"):
        TrendRegimeResult(
            request=result.request,
            snapshots=cast(tuple[TrendRegimeSnapshot, ...], list(result.snapshots)),
        )


def test_json_round_trip_nested_provenance_and_config_are_lossless() -> None:
    result = DeterministicTrendRegimeEngine().analyze(_request())
    restored = TrendRegimeResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.model_dump_json() == result.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        result.snapshots[0].observation.bar.close = Decimal("99")
    with pytest.raises(ValidationError, match="extra"):
        TrendRegimeConfig.model_validate({"sma_period": 2, "return_period": 1, "score": 1})


@pytest.mark.parametrize("value", [1736467200, "1736467200", "2025-01-10", None])
def test_serialized_evaluation_rejects_epoch_naive_and_absent_values(value: object) -> None:
    data = json.loads(_request().model_dump_json())
    data["evaluation_at"] = value
    with pytest.raises(ValidationError):
        TrendRegimeRequest.model_validate_json(json.dumps(data))


def test_incomplete_low_level_models_fail_as_validation_errors() -> None:
    request = _request()
    with pytest.raises(ValidationError):
        TrendRegimeRequest(
            history=MaterializedBarHistory.model_construct(bars=()),
            config=request.config,
            evaluation_at=_KNOWLEDGE,
        )
    with pytest.raises(ValidationError):
        TrendRegimeResult(request=TrendRegimeRequest.model_construct(), snapshots=())


def test_direct_result_preserves_decimal_scale_in_observation_provenance() -> None:
    result = DeterministicTrendRegimeEngine().analyze(_request(("1.000",)))
    original = result.snapshots[0]
    changed_bar = original.observation.bar.model_copy(update={"close": Decimal("1.0")})
    changed = original.model_copy(
        update={"observation": original.observation.model_copy(update={"bar": changed_bar})}
    )
    with pytest.raises(ValidationError, match="provenance"):
        TrendRegimeResult(request=result.request, snapshots=(changed,))


def test_production_has_no_external_side_effects_or_risk_control_dependencies() -> None:
    source = inspect.getsource(engine_module) + inspect.getsource(models_module)
    allowed = {
        "datetime",
        "enum",
        "typing",
        "pydantic",
        "app.core.schemas",
        "app.materialization.models",
        "app.technical.models",
        "app.technical.engine",
        "app.regime.models",
    }
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
    for forbidden in (
        "datetime.now(",
        "utcnow(",
        "uuid4(",
        "open(",
        "sleep(",
        "sorted(",
        "except Exception",
        "risk_decision",
        "confidence",
    ):
        assert forbidden not in source
