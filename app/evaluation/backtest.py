"""Deterministic PIT-safe evaluation of frozen descriptive setup snapshots."""

from decimal import Decimal

from app.core.schemas import CanonicalModel
from app.research.models import SingleSeriesResearchResult
from app.setups.models import SetupKey, SetupStatus


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
    stop_percent: Decimal
    target_percent: Decimal
    maximum_hold_bars: int
    round_trip_cost_percent: Decimal
    results: tuple[SetupBacktestSummary, ...]
    warning: str


def evaluate_frozen_setups(result: SingleSeriesResearchResult) -> BacktestSummary:
    """Walk forward without same-bar entry or overlapping trades per setup."""
    bars = tuple(item.bar for item in result.request.history.bars)
    stop_fraction = Decimal("0.02")
    cost_fraction = Decimal("0.001")
    horizon = 20
    summaries: list[SetupBacktestSummary] = []
    for key in tuple(SetupKey):
        outcomes: list[Decimal] = []
        wins = losses = timeouts = 0
        signal_index = 0
        while signal_index < len(bars) - 1:
            setup = next(
                item
                for item in result.setup_snapshots[signal_index].setups
                if item.key is key
            )
            if setup.status is not SetupStatus.ACTIVE:
                signal_index += 1
                continue
            entry_index = signal_index + 1
            entry = bars[entry_index].open
            if entry <= 0:
                signal_index = entry_index + 1
                continue
            risk = entry * stop_fraction
            is_long = key is SetupKey.UPSIDE_BREAKOUT_ABOVE_SMA
            stop = entry - risk if is_long else entry + risk
            target = entry + risk * Decimal("2") if is_long else entry - risk * Decimal("2")
            exit_index = min(entry_index + horizon - 1, len(bars) - 1)
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
            outcome -= cost_fraction / stop_fraction
            outcomes.append(outcome)
            signal_index = exit_index + 1
        trades = len(outcomes)
        expectancy: Decimal | None
        max_drawdown_result: Decimal | None
        win_rate: Decimal | None
        if trades:
            expectancy = sum(outcomes, Decimal("0")) / Decimal(trades)
            equity = peak = max_drawdown = Decimal("0")
            for outcome in outcomes:
                equity += outcome
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, peak - equity)
            win_rate = Decimal(wins) * Decimal("100") / Decimal(trades)
            max_drawdown_result = max_drawdown
        else:
            expectancy = max_drawdown_result = win_rate = None
        summaries.append(
            SetupBacktestSummary(
                setup=key,
                trades=trades,
                wins=wins,
                losses=losses,
                timeouts=timeouts,
                win_rate_percent=win_rate,
                expectancy_r=expectancy,
                max_drawdown_r=max_drawdown_result,
            )
        )
    return BacktestSummary(
        bars=len(bars),
        stop_percent=Decimal("2"),
        target_percent=Decimal("4"),
        maximum_hold_bars=horizon,
        round_trip_cost_percent=Decimal("0.10"),
        results=tuple(summaries),
        warning=(
            "Exploratory historical simulation, not a prediction. Small samples, gaps, "
            "liquidity, taxes, and live execution can materially change results."
        ),
    )
