"""Pure paper portfolio snapshot evaluation, without risk or execution authority."""

from decimal import DecimalException
from typing import Protocol

from app.portfolio.models import (
    PortfolioContextRequest,
    PortfolioContextResult,
    _calculate,
    _rebuild,
)


class PortfolioContextError(Exception):
    """Base portfolio context failure."""


class PortfolioContextInvalidInputError(PortfolioContextError):
    """Invalid account or valuation evidence."""


class PortfolioContextComputationError(PortfolioContextError):
    """Deterministic arithmetic could not produce a valid context."""


class PortfolioContextEngine(Protocol):
    def evaluate(self, request: PortfolioContextRequest) -> PortfolioContextResult: ...


class DeterministicPortfolioContextEngine:
    def evaluate(self, request: PortfolioContextRequest) -> PortfolioContextResult:
        try:
            if type(request) is not PortfolioContextRequest:
                raise ValueError("expected PortfolioContextRequest")
            trusted = _rebuild(request)
            if not isinstance(trusted, PortfolioContextRequest):
                raise ValueError("invalid reconstructed request")
        except (AttributeError, TypeError, ValueError) as exc:
            raise PortfolioContextInvalidInputError("invalid portfolio input") from exc
        try:
            valuations, status, concentration, net, gross, equity = _calculate(trusted)
            return PortfolioContextResult(
                request=trusted,
                valuations=valuations,
                valuation_status=status,
                concentration_status=concentration,
                net_market_value=net,
                gross_exposure=gross,
                equity_value=equity,
            )
        except DecimalException as exc:
            raise PortfolioContextComputationError("portfolio arithmetic failed") from exc
