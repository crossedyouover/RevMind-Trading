"""Canonical paper-account and paper-order contracts."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.core.schemas import CanonicalModel, UtcDatetime


class PaperPositionSummary(CanonicalModel):
    symbol: str
    quantity: Decimal
    market_value: Decimal
    current_price: Decimal


class PaperAccount(CanonicalModel):
    schema_version: Literal[1] = 1
    account_id: str
    status: str
    currency: Literal["USD"]
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    trading_blocked: bool
    observed_at: UtcDatetime
    positions: tuple[PaperPositionSummary, ...]


class PaperOrderRequest(CanonicalModel):
    schema_version: Literal[1] = 1
    client_order_id: str = Field(pattern=r"^revmind-[a-f0-9-]{36}$", max_length=128)
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0)
    entry_limit: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def bracket_geometry(self) -> "PaperOrderRequest":
        valid = (
            self.stop_price < self.entry_limit < self.target_price
            if self.side == "buy"
            else self.target_price < self.entry_limit < self.stop_price
        )
        if not valid:
            raise ValueError("paper bracket prices contradict order direction")
        return self


class PaperOrderReceipt(CanonicalModel):
    schema_version: Literal[1] = 1
    provider_order_id: str
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    status: str
    submitted_at: UtcDatetime


class PaperOrderStatus(CanonicalModel):
    """Provider-neutral read model for an explicitly submitted paper order."""

    schema_version: Literal[1] = 1
    provider_order_id: str
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    filled_quantity: Decimal
    status: str
    filled_average_price: Decimal | None
    submitted_at: UtcDatetime
    updated_at: UtcDatetime
