"""Explicit canonical-instrument to Alpaca-symbol bindings."""

import re

from pydantic import ValidationError, field_validator, model_validator

from app.core.schemas import AssetClass, CanonicalModel, Instrument, NonBlankStr
from app.data.market import InvalidMarketDataRequestError

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,31}$")


class AlpacaInstrumentBinding(CanonicalModel):
    """Immutable explicit identity-preserving provider symbol binding."""

    instrument: Instrument
    provider_symbol: NonBlankStr

    @field_validator("provider_symbol", mode="before")
    @classmethod
    def validate_provider_symbol(cls, value: object) -> str:
        if not isinstance(value, str) or value != value.strip():
            raise ValueError("invalid Alpaca provider symbol")
        normalized = value.upper()
        if _SYMBOL_PATTERN.fullmatch(normalized) is None:
            raise ValueError("invalid Alpaca provider symbol")
        return normalized

    @model_validator(mode="after")
    def validate_supported_instrument(self) -> "AlpacaInstrumentBinding":
        if self.instrument.asset_class not in {AssetClass.EQUITY, AssetClass.ETF}:
            raise ValueError("Alpaca stock binding requires EQUITY or ETF")
        if self.instrument.exchange is None:
            raise ValueError("Alpaca stock binding requires an explicit exchange")
        if self.instrument.currency != "USD":
            raise ValueError("Alpaca stock binding requires USD currency")
        return self


def build_binding_index(
    bindings: tuple[AlpacaInstrumentBinding, ...],
) -> dict[Instrument, str]:
    """Defensively validate a unique, unambiguous immutable binding set."""
    if not isinstance(bindings, tuple):
        raise InvalidMarketDataRequestError("Alpaca bindings must be supplied as a tuple")
    index: dict[Instrument, str] = {}
    reverse: dict[str, Instrument] = {}
    try:
        for binding in bindings:
            if not isinstance(binding, AlpacaInstrumentBinding):
                raise ValueError("binding must be an AlpacaInstrumentBinding")
            trusted = AlpacaInstrumentBinding.model_validate(
                binding.model_dump(mode="python", round_trip=True, warnings="none")
            )
            if trusted.instrument in index:
                raise ValueError("duplicate canonical instrument binding")
            if trusted.provider_symbol in reverse:
                raise ValueError("provider symbol is ambiguously bound")
            index[trusted.instrument] = trusted.provider_symbol
            reverse[trusted.provider_symbol] = trusted.instrument
    except (ValidationError, AttributeError, TypeError, ValueError) as exc:
        raise InvalidMarketDataRequestError("invalid Alpaca instrument bindings") from exc
    return index
