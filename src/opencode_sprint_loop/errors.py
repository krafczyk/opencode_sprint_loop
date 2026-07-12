"""Stable errors returned by the Sprint Loop Controller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ControllerError(Exception):
    """An expected controller failure with a stable reason code and safe message."""

    code: str
    message: str

    def __str__(self) -> str:
        """Return the human-readable diagnostic."""
        return self.message
