"""Provider capability declarations exposed to the local dashboard.

This registry is descriptive only.  A capability is never evidence that data was received and it
never grants execution authority.
"""

from typing import Literal

from pydantic import ConfigDict

from app.core.schemas import AssetClass, CanonicalModel, Timeframe


class ProviderCapability(CanonicalModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str
    display_name: str
    status: Literal["CONNECTED", "ADAPTER_REQUIRED", "REFERENCE_ONLY"]
    asset_classes: tuple[AssetClass, ...]
    timeframes: tuple[Timeframe, ...]
    data_modes: tuple[Literal["LIVE_DELAYED", "HISTORICAL", "REFERENCE"], ...]
    execution: Literal["PAPER_EXPLICIT_APPROVAL", "NONE"]
    explanation: str


CAPABILITIES = (
    ProviderCapability(
        provider_id="alpaca",
        display_name="Alpaca",
        status="CONNECTED",
        asset_classes=(AssetClass.EQUITY, AssetClass.ETF),
        timeframes=(
            Timeframe.ONE_MINUTE,
            Timeframe.FIVE_MINUTES,
            Timeframe.FIFTEEN_MINUTES,
            Timeframe.ONE_HOUR,
            Timeframe.ONE_DAY,
        ),
        data_modes=("LIVE_DELAYED", "HISTORICAL"),
        execution="PAPER_EXPLICIT_APPROVAL",
        explanation="US equity and ETF research; paper orders require a separate approval.",
    ),
    ProviderCapability(
        provider_id="fx",
        display_name="FX provider",
        status="ADAPTER_REQUIRED",
        asset_classes=(AssetClass.FX,),
        timeframes=(),
        data_modes=(),
        execution="NONE",
        explanation="No FX adapter is configured; no prices or analysis are claimed.",
    ),
    ProviderCapability(
        provider_id="futures",
        display_name="Futures provider",
        status="ADAPTER_REQUIRED",
        asset_classes=(AssetClass.FUTURE, AssetClass.OPTION, AssetClass.INDEX),
        timeframes=(),
        data_modes=(),
        execution="NONE",
        explanation="No futures/options adapter is configured; no prices or analysis are claimed.",
    ),
    ProviderCapability(
        provider_id="crypto",
        display_name="Crypto provider",
        status="ADAPTER_REQUIRED",
        asset_classes=(AssetClass.CRYPTO,),
        timeframes=(),
        data_modes=(),
        execution="NONE",
        explanation="No crypto adapter is configured; no prices or analysis are claimed.",
    ),
    ProviderCapability(
        provider_id="alternative",
        display_name="Alternative/private markets",
        status="REFERENCE_ONLY",
        asset_classes=(AssetClass.OTHER,),
        timeframes=(),
        data_modes=("REFERENCE",),
        execution="NONE",
        explanation="Catalog context only until a reliable point-in-time pricing source exists.",
    ),
)


def public_capability_registry() -> dict[str, object]:
    return {
        "schema_version": 1,
        "providers": [item.model_dump(mode="json") for item in CAPABILITIES],
    }
