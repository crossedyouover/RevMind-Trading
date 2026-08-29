"""Provider-neutral market-data boundary and validation.

External provider payloads must remain inside future adapters and must be transformed into
canonical ``Instrument``, ``MarketSnapshot``, and ``MarketBar`` models before entering the rest of
RevMind Trading. Historical providers must also prevent observations after the evaluation clock;
market event time and future system observation time are distinct concepts.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import ValidationError, model_validator

from app.core.schemas import (
    CanonicalModel,
    Instrument,
    MarketBar,
    MarketSnapshot,
    Timeframe,
    UtcDatetime,
)


class MarketDataError(Exception):
    """Base exception for provider-neutral market-data failures."""


class InstrumentNotFoundError(MarketDataError):
    """Raised when requested canonical instrument data is unknown."""


class MarketDataUnavailableError(MarketDataError):
    """Raised when a provider cannot currently supply requested data."""


class InvalidMarketDataRequestError(MarketDataError):
    """Raised when a provider-neutral market-data request is invalid."""


class ProviderRateLimitError(MarketDataUnavailableError):
    """Raised when a provider-neutral request is rate limited."""


class BarRequest(CanonicalModel):
    """Validated canonical request for the half-open interval ``[start, end)``."""

    instrument: Instrument
    start: UtcDatetime
    end: UtcDatetime
    timeframe: Timeframe

    @model_validator(mode="after")
    def validate_range(self) -> "BarRequest":
        """Require a non-empty, forward-moving interval."""
        if self.end <= self.start:
            raise ValueError("end must be strictly greater than start")
        return self


class MarketDataProvider(ABC):
    """Asynchronous provider interface returning only canonical market-data models.

    ``get_bars`` always returns the requested timeframe with timestamps oldest to newest, rejects
    duplicate timestamps, and uses the half-open interval ``[start, end)``. A valid interval with
    no observations returns an empty list. Implementations must not silently return data for a
    different instrument or timeframe. Inconsistent provider output is an availability failure,
    not an invalid caller request.
    """

    @abstractmethod
    async def get_snapshot(self, instrument: Instrument) -> MarketSnapshot:
        """Return the canonical snapshot for an instrument or raise a neutral error."""

    async def get_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> list[MarketBar]:
        """Validate a request and return validated canonical bars."""
        try:
            request = BarRequest(
                instrument=instrument,
                start=start,
                end=end,
                timeframe=timeframe,
            )
        except ValidationError as exc:
            raise InvalidMarketDataRequestError("invalid bar request") from exc

        bars = await self._get_bars(request)
        self._validate_bar_output(request, bars)
        return bars

    @abstractmethod
    async def _get_bars(self, request: BarRequest) -> list[MarketBar]:
        """Return canonical bars for an already validated request."""

    @abstractmethod
    async def get_batch_snapshots(
        self, instruments: list[Instrument]
    ) -> list[MarketSnapshot]:
        """Return all snapshots in request order or raise without a partial result."""

    @staticmethod
    def _validate_bar_output(request: BarRequest, bars: list[MarketBar]) -> None:
        """Reject malformed provider output rather than silently repairing it."""
        previous_timestamp: datetime | None = None
        for bar in bars:
            if bar.instrument != request.instrument:
                raise MarketDataUnavailableError("provider returned an unexpected instrument")
            if bar.timeframe != request.timeframe:
                raise MarketDataUnavailableError("provider returned an unexpected timeframe")
            if not request.start <= bar.timestamp < request.end:
                raise MarketDataUnavailableError(
                    "provider returned a bar outside the request range"
                )
            if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
                if bar.timestamp == previous_timestamp:
                    raise MarketDataUnavailableError("provider returned duplicate bar timestamps")
                raise MarketDataUnavailableError(
                    "provider returned bars out of chronological order"
                )
            previous_timestamp = bar.timestamp
