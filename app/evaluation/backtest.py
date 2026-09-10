"""Deterministic PIT-safe evaluation of frozen descriptive setup snapshots."""

from decimal import Decimal
from typing import Literal

from app.core.schemas import CanonicalModel, MarketBar
from app.research.models import SingleSeriesResearchResult
from app.setups.models import SetupKey, SetupSnapshot, SetupStatus


class SetupBacktestSummary(CanonicalModel):
    setup: SetupKey
    trades: int
    wins: int
    losses: int
    timeouts: int
    win_rate_percent: Decimal | None
    expectancy_r: Decimal | None
    max_drawdown_r: Decimal | None


class BacktestSummary(CanonicalModel):
    bars: int
    split_bar_index: int
    stop_percent: Decimal
    target_percent: Decimal
    maximum_hold_bars: int
    round_trip_cost_percent: Decimal
    results: tuple[SetupBacktestSummary, ...]
    out_of_sample_results: tuple[SetupBacktestSummary, ...]
    evidence_grade: Literal["INSUFFICIENT", "WEAK", "PROMISING"]
    ranking_score: Decimal | None
    ranking_explanation: str
    warning: str


def _summarize(
    bars: tuple[MarketBar, ...],
    snapshots: tuple[SetupSnapshot, ...],
    key: SetupKey,
    *,
    minimum_entry_index: int,
) -> SetupBacktestSummary:
    outcomes: list[Decimal] = []
    wins = losses = timeouts = 0
    signal_index = max(0, minimum_entry_index - 1)
    while signal_index < len(bars) - 1:
        setup = next(item for item in snapshots[signal_index].setups if item.key is key)
        if setup.status is not SetupStatus.ACTIVE:
            signal_index += 1
            continue
        entry_index = signal_index + 1
        if entry_index < minimum_entry_index:
            signal_index += 1
            continue
        entry = bars[entry_index].open
        if entry <= 0:
            signal_index = entry_index + 1
            continue
        risk = entry * Decimal("0.02")
        is_long = key is SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA
        stop = entry - risk if is_long else entry + risk
        target = entry + risk * Decimal("2") if is_long else entry - risk * Decimal("2")
        exit_index = min(entry_index + 19, len(bars) - 1)
        outcome: Decimal | None = None
        for index in range(entry_index, exit_index + 1):
            bar = bars[index]
            stop_hit = bar.low <= stop if is_long else bar.high >= stop
            target_hit = bar.high >= target if is_long else bar.low <= target
            if stop_hit:
                outcome = Decimal("-1")
                exit_index = index
                losses += 1
                break
            if target_hit:
                outcome = Decimal("2")
                exit_index = index
                wins += 1
                break
        if outcome is None:
            final_close = bars[exit_index].close
            signed_move = final_close - entry if is_long else entry - final_close
            outcome = signed_move / risk
            timeouts += 1
        outcomes.append(outcome - Decimal("0.05"))
        signal_index = exit_index + 1
    trades = len(outcomes)
    expectancy: Decimal | None = None
    max_drawdown_result: Decimal | None = None
    win_rate: Decimal | None = None
    if trades:
        expectancy = sum(outcomes, Decimal("0")) / Decimal(trades)
        equity = peak = max_drawdown = Decimal("0")
        for outcome in outcomes:
            equity += outcome
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        win_rate = Decimal(wins) * Decimal("100") / Decimal(trades)
        max_drawdown_result = max_drawdown
    return SetupBacktestSummary(
        setup=key,
        trades=trades,
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        win_rate_percent=win_rate,
        expectancy_r=expectancy,
        max_drawdown_r=max_drawdown_result,
    )


def evaluate_frozen_setups(result: SingleSeriesResearchResult) -> BacktestSummary:
    """Walk forward, entering only after signals and scoring a held-out suffix."""
    bars = tuple(item.bar for item in result.request.history.bars)
    snapshots = result.setup_snapshots
    split = len(bars) * 7 // 10
    full = tuple(_summarize(bars, snapshots, key, minimum_entry_index=0) for key in SetupKey)
    held_out = tuple(
        _summarize(bars, snapshots, key, minimum_entry_index=split) for key in SetupKey
    )
    candidates = tuple(item for item in held_out if item.trades > 0)
    sample = max(candidates, key=lambda item: item.trades) if candidates else None
    if sample is None or sample.trades < 20:
        grade: Literal["INSUFFICIENT", "WEAK", "PROMISING"] = "INSUFFICIENT"
        score = None
        explanation = "Fewer than 20 held-out trades; do not treat the result as predictive."
    elif sample.expectancy_r is None or sample.expectancy_r <= 0:
        grade = "WEAK"
        score = sample.expectancy_r
        explanation = "The largest held-out sample does not have positive expectancy."
    else:
        grade = "PROMISING"
        drawdown = sample.max_drawdown_r or Decimal("0")
        score = sample.expectancy_r - drawdown / Decimal("20")
        explanation = (
            "Positive held-out expectancy with at least 20 trades; "
            "paper validation is still required."
        )
    return BacktestSummary(
        bars=len(bars),
        split_bar_index=split,
        stop_percent=Decimal("2"),
        target_percent=Decimal("4"),
        maximum_hold_bars=20,
        round_trip_cost_percent=Decimal("0.10"),
        results=full,
        out_of_sample_results=held_out,
        evidence_grade=grade,
        ranking_score=score,
        ranking_explanation=explanation,
        warning=(
            "Exploratory historical simulation, not a prediction. Small samples, gaps, "
            "liquidity, taxes, and live execution can materially change results."
        ),
    )
