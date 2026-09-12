from app.dashboard.capabilities import CAPABILITIES, public_capability_registry


def test_capability_registry_is_explicit_and_fail_closed() -> None:
    registry = public_capability_registry()
    assert registry["schema_version"] == 1
    assert len(CAPABILITIES) == 5
    alpaca = CAPABILITIES[0]
    assert alpaca.provider_id == "alpaca"
    assert alpaca.status == "CONNECTED"
    assert alpaca.execution == "PAPER_EXPLICIT_APPROVAL"
    for provider in CAPABILITIES[1:]:
        assert provider.execution == "NONE"
        if provider.status == "ADAPTER_REQUIRED":
            assert provider.timeframes == ()
            assert provider.data_modes == ()
