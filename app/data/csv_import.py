"""Strict provider-neutral CSV adapter for bounded local bar imports."""

import csv
import hashlib
import io
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.core.schemas import (
    AssetClass,
    CanonicalModel,
    Instrument,
    MarketBar,
    Timeframe,
    UtcDatetime,
)
from app.data.observation_store import ObservationStore
from app.data.observations import ObservedMarketData, SourceIdentity

CSV_HEADERS = ("timestamp", "open", "high", "low", "close", "volume")
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 10_000


class CsvImportError(ValueError):
    """Raised when a local bar export cannot be trusted."""


class ImportClock(Protocol):
    def now(self) -> datetime:
        """Return the actual import receipt boundary."""
        ...


class CsvBarImportRequest(CanonicalModel):
    """Explicit identity and semantics applied to every imported CSV row."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    symbol: Annotated[str, Field(strict=True, min_length=1, max_length=40)]
    asset_class: AssetClass
    exchange: Annotated[str, Field(strict=True, min_length=1, max_length=40)] | None = None
    currency: Annotated[str, Field(strict=True, min_length=1, max_length=12)] | None = None
    timeframe: Timeframe
    source_name: Annotated[str, Field(strict=True, min_length=1, max_length=80)]

    @property
    def instrument(self) -> Instrument:
        return Instrument(
            symbol=self.symbol,
            asset_class=self.asset_class,
            exchange=self.exchange,
            currency=self.currency,
        )


class ParsedCsvBarImport(CanonicalModel):
    """Canonical immutable result of parsing one complete local export."""

    request: CsvBarImportRequest
    received_at: UtcDatetime
    content_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bars: tuple[MarketBar, ...]

    @model_validator(mode="after")
    def validate_complete_batch(self) -> "ParsedCsvBarImport":
        if not self.bars or len(self.bars) > MAX_CSV_ROWS:
            raise ValueError("bar import must contain 1 to 10000 rows")
        previous: datetime | None = None
        for bar in self.bars:
            if bar.instrument != self.request.instrument or bar.timeframe != self.request.timeframe:
                raise ValueError("bar identity differs from import request")
            if bar.timestamp >= self.received_at:
                raise ValueError("imported bars must predate the actual receipt time")
            if previous is not None and bar.timestamp <= previous:
                raise ValueError("bar timestamps must be strictly increasing")
            previous = bar.timestamp
        return self


class CsvImportReceipt(CanonicalModel):
    """Immutable evidence that one complete parsed batch was appended."""

    request: CsvBarImportRequest
    received_at: UtcDatetime
    content_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    observations: tuple[ObservedMarketData, ...]

    @property
    def count(self) -> int:
        return len(self.observations)


class CsvBarImportCoordinator:
    """Assign knowledge time and atomically persist one validated local export."""

    def __init__(
        self,
        store: ObservationStore,
        *,
        clock: ImportClock,
        observation_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._store = store
        self._clock = clock
        self._observation_id_factory = observation_id_factory

    def import_bars(self, payload: bytes, request: CsvBarImportRequest) -> CsvImportReceipt:
        received_at = self._clock.now()
        parsed = parse_csv_bars(payload, request, received_at=received_at)
        source = SourceIdentity(name=request.source_name)
        observations: list[ObservedMarketData] = []
        for index, bar in enumerate(parsed.bars, start=1):
            observation_id = self._observation_id_factory()
            if not isinstance(observation_id, UUID) or observation_id.version != 4:
                raise CsvImportError("observation ID factory must return UUID4")
            observations.append(
                ObservedMarketData(
                    observation_id=observation_id,
                    payload=bar,
                    observed_at=parsed.received_at,
                    source=source,
                    source_record_id=f"{parsed.content_digest}:{index}",
                )
            )
        complete = tuple(observations)
        self._store.append_many(complete)
        return CsvImportReceipt(
            request=request,
            received_at=parsed.received_at,
            content_digest=parsed.content_digest,
            observations=complete,
        )


def parse_csv_bars(
    payload: bytes, request: CsvBarImportRequest, *, received_at: datetime
) -> ParsedCsvBarImport:
    """Parse one exact UTF-8 CSV into canonical bars without repair or sorting."""

    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CSV_BYTES:
        raise CsvImportError("CSV payload must contain 1 to 1000000 bytes")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise CsvImportError("receipt time must include timezone information")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CsvImportError("CSV must be UTF-8") from exc
    if text.startswith("\ufeff") or "\x00" in text:
        raise CsvImportError("CSV contains an unsupported marker")
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != CSV_HEADERS:
            raise CsvImportError("CSV headers must exactly match the documented schema")
        bars: list[MarketBar] = []
        for row in reader:
            if len(bars) >= MAX_CSV_ROWS:
                raise CsvImportError("CSV exceeds the 10000 row limit")
            if None in row or any(row[name] is None or row[name] == "" for name in CSV_HEADERS):
                raise CsvImportError("CSV rows must contain every field exactly once")
            try:
                timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                values = {name: Decimal(row[name]) for name in CSV_HEADERS[1:]}
                bars.append(
                    MarketBar(
                        instrument=request.instrument,
                        timeframe=request.timeframe,
                        timestamp=timestamp,
                        **values,
                    )
                )
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise CsvImportError("CSV contains an invalid canonical bar") from exc
    except csv.Error as exc:
        raise CsvImportError("CSV structure is invalid") from exc
    try:
        return ParsedCsvBarImport(
            request=request,
            received_at=received_at.astimezone(UTC),
            content_digest=hashlib.sha256(payload).hexdigest(),
            bars=tuple(bars),
        )
    except ValueError as exc:
        raise CsvImportError(str(exc)) from exc
