"""Focused state-schema unit tests independent of Git fixtures."""

from __future__ import annotations

import unittest

from opencode_sprint_loop.errors import ControllerError
from opencode_sprint_loop.events import validate_event
from opencode_sprint_loop.state import is_rfc3339_utc


class StateContractTests(unittest.TestCase):
    """Verify pure durable-schema helpers without filesystem fixtures."""

    def test_rfc3339_utc_timestamp_validation(self) -> None:
        """Whole-second and fractional UTC RFC 3339 timestamps are accepted."""
        self.assertTrue(is_rfc3339_utc("2026-07-12T21:30:57Z"))
        self.assertTrue(is_rfc3339_utc("2026-07-12T21:30:57.123Z"))
        self.assertTrue(is_rfc3339_utc("2026-07-12T21:30:57+00:00"))
        self.assertFalse(is_rfc3339_utc("2026-99-99T99:99:99Z"))
        self.assertFalse(is_rfc3339_utc("2026-07-12"))

    def test_event_validation_accepts_fractional_utc_timestamp(self) -> None:
        """Events share the state timestamp contract."""
        for timestamp in ("2026-07-12T21:30:57.123Z", "2026-07-12T21:30:57+00:00"):
            with self.subTest(timestamp=timestamp):
                event = {
                    "schema_version": 1,
                    "sequence": 1,
                    "timestamp": timestamp,
                    "run_id": "00000000-0000-4000-8000-000000000000",
                    "type": "run.started",
                    "state": "initializing",
                    "payload": {},
                }
                self.assertEqual(validate_event(event), event)

    def test_controller_error_retains_machine_reason_code(self) -> None:
        """Structured expected errors preserve their public machine code."""
        error = ControllerError("corrupt_state", "fixture")
        self.assertEqual(error.code, "corrupt_state")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
