"""Stable errors returned by the Sprint Loop Controller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ControllerError(Exception):
    """An expected controller failure with a stable reason code and safe message.

    ``completed_at`` is an optional monotonic timestamp for orchestration-only
    arbitration.  It is never a durable error field or user-facing diagnostic.
    """

    code: str
    message: str
    completed_at: float | None = None

    def __str__(self) -> str:
        """Return the human-readable diagnostic."""
        return self.message
