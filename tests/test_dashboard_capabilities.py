from app.core.schemas import AssetClass, Timeframe
from app.dashboard.capabilities import CAPABILITIES, public_capability_registry


def test_capability_registry_is_explicit_and_fail_closed() -> None:
    registry = public_capability_registry()
    assert registry["schema_version"] == 1
    assert len(CAPABILITIES) == 6
    alpaca = CAPABILITIES[0]
    assert alpaca.provider_id == "alpaca"
    assert alpaca.status == "CONNECTED"
    assert alpaca.execution == "PAPER_EXPLICIT_APPROVAL"
    local_csv = CAPABILITIES[1]
    assert local_csv.provider_id == "local_csv"
    assert local_csv.status == "CONNECTED"
    assert set(local_csv.asset_classes) == set(AssetClass)
    assert set(local_csv.timeframes) == set(Timeframe)
    for provider in CAPABILITIES[1:]:
        assert provider.execution == "NONE"
        if provider.status == "ADAPTER_REQUIRED":
            assert provider.timeframes == ()
            assert provider.data_modes == ()
