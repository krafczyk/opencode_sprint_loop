"""Offline contract tests for packaged controller component metadata."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from copy import deepcopy
from unittest.mock import patch

from opencode_sprint_loop import __version__
from opencode_sprint_loop.cli import main
from opencode_sprint_loop.component import is_supported_opencode_version, load_component_metadata


class ComponentInfoTests(unittest.TestCase):
    """Ensure component-info needs neither a sprint root nor runtime context."""

    def invoke(self) -> tuple[int, str, str]:
        """Run the public command while capturing its separated output streams."""
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["component-info", "--json"])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_component_info_is_pure_and_matches_packaged_contract(self) -> None:
        """The root-free JSON command emits one declared owner contract without files."""
        with TemporaryDirectory(dir="/tmp/opencode-mkchad") as temporary:
            root = Path(temporary)
            home = root / "empty-home"
            xdg = root / "empty-xdg"
            before = sorted(root.rglob("*"))
            with patch.dict(
                os.environ, {"HOME": str(home), "XDG_STATE_HOME": str(xdg)}, clear=True
            ):
                code, stdout, stderr = self.invoke()
            self.assertEqual(code, 0, stderr)
            self.assertEqual(stderr, "")
            self.assertTrue(stdout.endswith("\n"))
            document = json.loads(stdout)
            self.assertEqual(
                set(document),
                {"schema", "component_id", "controller_version", "supported_opencode"},
            )
            self.assertEqual(document["schema"], 1)
            self.assertEqual(document["component_id"], "sprint-loop-controller")
            self.assertEqual(document["controller_version"], __version__)
            relationship = document["supported_opencode"]
            self.assertEqual(relationship["type"], "supports")
            self.assertEqual(relationship["target_component"], "opencode")
            self.assertEqual(relationship["contract"]["kind"], "range")
            self.assertEqual(relationship["contract"]["suffix_policy"], "literal")
            self.assertEqual(sorted(root.rglob("*")), before)

    def test_malformed_packaged_metadata_fails_without_partial_json(self) -> None:
        """Unreadable owner data fails closed with bounded diagnostics and no stdout."""
        with patch(
            "opencode_sprint_loop.component._packaged_metadata_bytes",
            return_value=b'{"schema": 1, "schema": 1}',
        ):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertLessEqual(len(stderr.encode("utf-8")), 1024)
        self.assertIn("invalid_component_metadata", stderr)

    def test_every_packaged_metadata_failure_is_closed_and_bounded(self) -> None:
        """Missing, oversized, malformed, and unsupported data never emits a document."""
        cases = {
            "missing": FileNotFoundError(),
            "oversized": b" " * (16 * 1024 + 1),
            "malformed": b"{",
            "unsupported_schema": b'{"schema":2}',
            "missing_relationship": b'{"schema":1,"component_id":"sprint-loop-controller",'
            b'"component_version":"0.1.0"}',
            "duplicate": b'{"schema":1,"schema":1}',
        }
        for label, fixture in cases.items():
            with self.subTest(label=label):
                if isinstance(fixture, BaseException):
                    replacement = patch(
                        "opencode_sprint_loop.component._packaged_metadata_bytes",
                        side_effect=fixture,
                    )
                else:
                    replacement = patch(
                        "opencode_sprint_loop.component._packaged_metadata_bytes",
                        return_value=fixture,
                    )
                with replacement:
                    code, stdout, stderr = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertLessEqual(len(stderr.encode("utf-8")), 1024)
                self.assertIn("invalid_component_metadata", stderr)

    def test_support_range_and_literal_suffix_policy_round_trip(self) -> None:
        """The owner range preserves release support without stripping suffixes."""
        metadata = load_component_metadata()
        relationship = metadata["relationships"][0]
        self.assertEqual(
            relationship["contract"],
            {
                "kind": "range",
                "clauses": [{"min_inclusive": "1.17.0", "max_exclusive": "1.19.0"}],
                "suffix_policy": "literal",
            },
        )
        for version in ("1.17.0", "1.17.99", "1.18.0", "1.18.999"):
            with self.subTest(version=version):
                self.assertTrue(is_supported_opencode_version(version))
        for version in ("1.16.99", "1.19.0", "1.18.01", "1.18.1-beta.1", "1.18.1-mkchad.7"):
            with self.subTest(version=version):
                self.assertFalse(is_supported_opencode_version(version))

    def test_identity_profile_is_optional_and_relationships_are_bounded(self) -> None:
        """Declared profiles project, while oversized relationship records fail closed."""
        metadata = load_component_metadata()
        with_profile = deepcopy(metadata)
        with_profile["identity_profile"] = {
            "id": "controller-v1",
            "algorithm": "sha256",
            "included_roots": ["opencode_sprint_loop"],
            "exclusions": [],
            "max_regular_files": 64,
            "max_total_bytes": 1048576,
            "max_per_file_bytes": 262144,
            "max_elapsed_ms": 1000,
            "framing": "path-u32be-content-u64be-v1",
        }
        with patch(
            "opencode_sprint_loop.component._packaged_metadata_bytes",
            return_value=json.dumps(with_profile).encode("utf-8"),
        ):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["identity_profile"], with_profile["identity_profile"])

        oversized = deepcopy(metadata)
        oversized["relationships"][0]["padding"] = "x" * 768
        with patch(
            "opencode_sprint_loop.component._packaged_metadata_bytes",
            return_value=json.dumps(oversized).encode("utf-8"),
        ):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("invalid_component_metadata", stderr)

    def test_package_version_and_metadata_are_mechanically_in_parity(self) -> None:
        """The build version source, metadata, and compatibility docs cannot drift."""
        root = Path(__file__).parents[2]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["dynamic"], ["version"])
        self.assertEqual(
            project["tool"]["setuptools"]["dynamic"]["version"],
            {"attr": "opencode_sprint_loop.__version__"},
        )
        metadata = load_component_metadata()
        self.assertEqual(metadata["component_version"], __version__)
        support = metadata["relationships"][0]["contract"]
        self.assertEqual(
            support["clauses"], [{"min_inclusive": "1.17.0", "max_exclusive": "1.19.0"}]
        )
        for document in (root / "README.md", root / "docs" / "v1_final_software_specification.md"):
            with self.subTest(document=document):
                text = document.read_text(encoding="utf-8")
                self.assertIn("component.json", text)
                self.assertIn("1.17.x and 1.18.x release", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
