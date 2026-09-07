"""Provider-neutral asynchronous paper-broker protocol."""

from typing import Protocol

from app.broker.models import PaperAccount, PaperOrderReceipt, PaperOrderRequest


class PaperBroker(Protocol):
    async def account(self) -> PaperAccount: ...

    async def place_bracket_order(self, request: PaperOrderRequest) -> PaperOrderReceipt: ...

    async def aclose(self) -> None: ...
