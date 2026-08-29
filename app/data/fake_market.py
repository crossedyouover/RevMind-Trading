"""Deterministic in-memory market-data provider for tests and local validation."""

from collections.abc import Iterable

from app.core.schemas import Instrument, MarketBar, MarketSnapshot, Timeframe
from app.data.market import (
    BarRequest,
    InstrumentNotFoundError,
    InvalidMarketDataRequestError,
    MarketDataProvider,
)


class FakeMarketDataProvider(MarketDataProvider):
    """Serve preloaded canonical objects without network access or fabricated data.

    Batch requests preserve input order and preserve duplicate requested instruments. If any
    requested snapshot is missing, the whole operation raises ``InstrumentNotFoundError``.
    """

    def __init__(
        self,
        snapshots: Iterable[MarketSnapshot] = (),
        bars: Iterable[MarketBar] = (),
    ) -> None:
        self._snapshots: dict[Instrument, MarketSnapshot] = {}
        grouped_bars: dict[tuple[Instrument, Timeframe], list[MarketBar]] = {}

        for snapshot in snapshots:
            if snapshot.instrument in self._snapshots:
                raise InvalidMarketDataRequestError("duplicate snapshot instrument")
            self._snapshots[snapshot.instrument] = snapshot

        for bar in bars:
            key = (bar.instrument, bar.timeframe)
            grouped_bars.setdefault(key, []).append(bar)

        self._bars: dict[tuple[Instrument, Timeframe], tuple[MarketBar, ...]] = {}
        for key, instrument_bars in grouped_bars.items():
            ordered = sorted(instrument_bars, key=lambda bar: bar.timestamp)
            timestamps = [bar.timestamp for bar in ordered]
            if len(timestamps) != len(set(timestamps)):
                raise InvalidMarketDataRequestError("duplicate bar timestamps")
            self._bars[key] = tuple(ordered)

        bar_instruments = {instrument for instrument, _timeframe in self._bars}
        self._known_instruments = frozenset(self._snapshots) | frozenset(bar_instruments)

    async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
        """Return an exact preloaded snapshot."""
        try:
            return self._snapshots[instrument]
        except KeyError as exc:
            raise InstrumentNotFoundError("snapshot instrument not found") from exc

    async def _get_bars(self, request: BarRequest) -> list[MarketBar]:
        """Filter preloaded bars to the validated half-open request interval."""
        if request.instrument not in self._known_instruments:
            raise InstrumentNotFoundError("bar instrument not found")
        key = (request.instrument, request.timeframe)
        return [
            bar
            for bar in self._bars.get(key, ())
            if request.start <= bar.timestamp < request.end
        ]

    async def get_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> list[MarketSnapshot]:
        """Return snapshots in request order, preserving duplicates."""
        return [await self.get_snapshot(instrument) for instrument in instruments]
