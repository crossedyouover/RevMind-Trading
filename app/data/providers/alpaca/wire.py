"""Private strict parsing for untrusted Alpaca JSON payloads."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt

_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<head>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d+)?(?P<zone>Z|[+-]\d{2}:\d{2})$"
)


def _wire_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError("provider numeric value must be an exact JSON number")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite():
        raise ValueError("provider numeric value must be finite")
    return result


WireDecimal = Annotated[Decimal, BeforeValidator(_wire_decimal)]


def parse_alpaca_timestamp(value: str) -> datetime:
    """Parse aware RFC3339 and deterministically truncate beyond microseconds."""
    if not isinstance(value, str):
        raise ValueError("provider timestamp must be a string")
    match = _TIMESTAMP_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("provider timestamp must be timezone-aware RFC3339")
    digits = (match.group("fraction") or "")[1:]
    fraction = f".{digits[:6].ljust(6, '0')}" if digits else ""
    zone = "+00:00" if match.group("zone") == "Z" else match.group("zone")
    return datetime.fromisoformat(f"{match.group('head')}{fraction}{zone}").astimezone(UTC)


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlpacaBarWire(_WireModel):
    timestamp: str = Field(alias="t")
    open: WireDecimal = Field(alias="o")
    high: WireDecimal = Field(alias="h")
    low: WireDecimal = Field(alias="l")
    close: WireDecimal = Field(alias="c")
    volume: WireDecimal = Field(alias="v")
    trade_count: StrictInt | None = Field(default=None, alias="n")
    volume_weighted_price: WireDecimal | None = Field(default=None, alias="vw")


class AlpacaBarsResponseWire(_WireModel):
    bars: tuple[AlpacaBarWire, ...]
    symbol: str
    next_page_token: str | None = None


class AlpacaTradeWire(_WireModel):
    timestamp: str = Field(alias="t")
    price: WireDecimal = Field(alias="p")
    size: WireDecimal | None = Field(default=None, alias="s")
    exchange: str | None = Field(default=None, alias="x")
    conditions: tuple[str, ...] | None = Field(default=None, alias="c")
    trade_id: StrictInt | str | None = Field(default=None, alias="i")
    tape: str | None = Field(default=None, alias="z")


class AlpacaSnapshotWire(BaseModel):
    """Only the independently truthful latest-trade component is consumed."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    latest_trade: AlpacaTradeWire = Field(alias="latestTrade")
