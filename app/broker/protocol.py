"""Provider-neutral asynchronous paper-broker protocol."""

from typing import Protocol

from app.broker.models import PaperAccount, PaperOrderReceipt, PaperOrderRequest, PaperOrderStatus


class PaperBroker(Protocol):
    async def account(self) -> PaperAccount: ...

    async def place_bracket_order(self, request: PaperOrderRequest) -> PaperOrderReceipt: ...

    async def order_status(self, provider_order_id: str) -> PaperOrderStatus: ...

    async def cancel_order(self, provider_order_id: str) -> None: ...

    async def aclose(self) -> None: ...
