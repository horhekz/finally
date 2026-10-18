"""API error type: every 4xx the services raise. One handler maps it to {"error": ...}."""

from __future__ import annotations


class ApiError(Exception):
    """An error that maps directly to an HTTP response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class TradeRejected(ApiError):  # noqa: N818 - name fixed by the team contract
    """A trade could not be executed (400 validation, 409 no price)."""
