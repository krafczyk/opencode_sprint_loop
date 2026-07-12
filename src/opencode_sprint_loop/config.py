"""Versioned Sprint Loop configuration validation."""

from __future__ import annotations

import re
import os
import stat
from types import MappingProxyType
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ControllerError
from .jsonio import load_json_object_handle
from .paths import resolve_within
from .safeio import open_directory

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ZERO_CHECKS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    """The one V1 managed implementation repository."""

    name: str
    path: Path
    branch: str
    remote: str


@dataclass(frozen=True, slots=True)
class SprintConfig:
    """Fully validated schema-version-one configuration."""

    multisprint: str
    sprint: int
    repositories: tuple[RepositoryConfig, ...]
    documents: Mapping[str, Path]
    agents: Mapping[str, str]
    models: Mapping[str, str]
    pre_ci_enabled: bool
    pre_ci_max_rounds: int
    limits: Mapping[str, int]
    ci: Mapping[str, Any]


def _expect_keys(data: dict[str, Any], expected: set[str], field: str) -> None:
    """Reject unknown and missing object fields."""
    unknown = set(data) - expected
    missing = expected - set(data)
    if unknown:
        raise ControllerError("invalid_config", f"Unknown field: {field}.{sorted(unknown)[0]}")
    if missing:
        raise ControllerError("invalid_config", f"Missing field: {field}.{sorted(missing)[0]}")


def _string(value: Any, field: str) -> str:
    """Validate one non-empty single-line string."""
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ControllerError("invalid_config", f"{field} must be a non-empty single-line string")
    return value


def _positive_int(value: Any, field: str) -> int:
    """Validate one positive JSON integer without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ControllerError("invalid_config", f"{field} must be a positive integer")
    return int(value)


def _identifier(value: Any, field: str) -> str:
    """Validate a bounded identifier used in paths and agent names."""
    text = _string(value, field)
    if not IDENTIFIER.fullmatch(text):
        raise ControllerError("invalid_config", f"{field} must match {IDENTIFIER.pattern}")
    return text


def load_config(root: Path) -> SprintConfig:
    """Load immutable Sprint 1 configuration or raise ``ControllerError`` without mutation."""
    config_path = root / "sprint_config.json"
    try:
        directory = open_directory(root)
        try:
            descriptor = os.open(config_path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                details = os.fstat(descriptor)
                if not stat.S_ISREG(details.st_mode):
                    raise ControllerError("invalid_config", f"sprint_config.json must be a regular file: {config_path}")
                handle = os.fdopen(descriptor, "rb", closefd=True)
                descriptor = -1
                with handle:
                    data = load_json_object_handle(handle, config_path, code="invalid_config")
            finally:
                if descriptor != -1:
                    os.close(descriptor)
        finally:
            os.close(directory)
    except FileNotFoundError:
        raise ControllerError("invalid_config", f"sprint_config.json must be a regular file: {config_path}")
    except OSError as error:
        raise ControllerError("invalid_config", f"sprint_config.json must be a regular file: {config_path}") from error
    if "schema_version" not in data:
        raise ControllerError("invalid_config", "Missing field: sprint_config.json.schema_version")
    if not isinstance(data["schema_version"], int) or isinstance(data["schema_version"], bool) or data["schema_version"] != 1:
        raise ControllerError("unsupported_config_schema", "schema_version must equal integer 1; migrate configuration to the supported schema")
    _expect_keys(
        data,
        {
            "schema_version", "multisprint", "sprint", "repositories", "documents", "agents",
            "models", "pre_ci_audit", "limits", "ci",
        },
        "sprint_config.json",
    )
    multisprint = _identifier(data["multisprint"], "multisprint")
    sprint = _positive_int(data["sprint"], "sprint")

    repositories = data["repositories"]
    if not isinstance(repositories, list) or len(repositories) != 1 or not isinstance(repositories[0], dict):
        raise ControllerError("invalid_config", "repositories must contain exactly one object in V1")
    repository_data = repositories[0]
    _expect_keys(repository_data, {"name", "path", "branch", "remote"}, "repositories[0]")
    repository_name = _identifier(repository_data["name"], "repositories[0].name")
    repository_path_value = _string(repository_data["path"], "repositories[0].path")
    repository_path = resolve_within(root, repository_path_value, field="repositories[0].path")
    if repository_path == root:
        raise ControllerError("invalid_config", "repositories[0].path must not be the sprint root")
    repository = RepositoryConfig(
        repository_name,
        repository_path,
        _string(repository_data["branch"], "repositories[0].branch"),
        _string(repository_data["remote"], "repositories[0].remote"),
    )

    documents_data = data["documents"]
    if not isinstance(documents_data, dict):
        raise ControllerError("invalid_config", "documents must be an object")
    document_keys = {"multisprint_spec", "sprint_spec", "sprint_checklist"}
    _expect_keys(documents_data, document_keys, "documents")
    documents: dict[str, Path] = {}
    document_identities: set[tuple[int, int]] = set()
    for key in document_keys:
        path = resolve_within(root, _string(documents_data[key], f"documents.{key}"), field=f"documents.{key}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ControllerError("missing_required_file", f"documents.{key} must be a non-empty regular file: {path}")
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in document_identities:
            raise ControllerError("invalid_config", "documents must resolve to distinct files")
        document_identities.add(identity)
        documents[key] = path

    agents_data = data["agents"]
    if not isinstance(agents_data, dict):
        raise ControllerError("invalid_config", "agents must be an object")
    role_keys = {"builder", "auditor", "ci_fixer"}
    _expect_keys(agents_data, role_keys, "agents")
    agents = {role: _identifier(agents_data[role], f"agents.{role}") for role in role_keys}
    for name in agents.values():
        agent_file = root / ".opencode" / "agents" / f"{name}.md"
        try:
            local_agent = agent_file.resolve().relative_to(root)
        except ValueError:
            local_agent = None
        if agent_file.is_symlink() or not agent_file.is_file() or local_agent is None:
            raise ControllerError("invalid_agent_definition", f"Missing agent definition: {agent_file}")

    models_data = data["models"]
    if not isinstance(models_data, dict):
        raise ControllerError("invalid_config", "models must be an object")
    _expect_keys(models_data, role_keys, "models")
    models: dict[str, str] = {}
    for role in role_keys:
        value = _string(models_data[role], f"models.{role}")
        provider, separator, model = value.partition("/")
        if not separator or not provider or not model or any(character.isspace() for character in value):
            raise ControllerError("invalid_config", f"models.{role} must be provider/model without whitespace")
        models[role] = value

    audit_data = data["pre_ci_audit"]
    if not isinstance(audit_data, dict):
        raise ControllerError("invalid_config", "pre_ci_audit must be an object")
    _expect_keys(audit_data, {"enabled", "max_rounds"}, "pre_ci_audit")
    if not isinstance(audit_data["enabled"], bool):
        raise ControllerError("invalid_config", "pre_ci_audit.enabled must be a boolean")

    limits_data = data["limits"]
    if not isinstance(limits_data, dict):
        raise ControllerError("invalid_config", "limits must be an object")
    limit_keys = {"max_implementation_cycles", "max_ci_fix_attempts", "invocation_timeout_seconds", "server_unavailable_grace_seconds"}
    _expect_keys(limits_data, limit_keys, "limits")
    limits = {key: _positive_int(limits_data[key], f"limits.{key}") for key in limit_keys}

    ci_data = data["ci"]
    if not isinstance(ci_data, dict):
        raise ControllerError("invalid_config", "ci must be an object")
    ci_keys = {"provider", "poll_interval_seconds", "allow_skipped", "allow_neutral", "zero_checks"}
    _expect_keys(ci_data, ci_keys, "ci")
    if ci_data["provider"] != "github":
        raise ControllerError("invalid_config", "ci.provider must equal github in V1")
    if not isinstance(ci_data["allow_skipped"], bool) or not isinstance(ci_data["allow_neutral"], bool):
        raise ControllerError("invalid_config", "ci.allow_skipped and ci.allow_neutral must be booleans")
    zero_checks = _string(ci_data["zero_checks"], "ci.zero_checks")
    if not ZERO_CHECKS.fullmatch(zero_checks):
        raise ControllerError("invalid_config", "ci.zero_checks must be a lower-case identifier")
    ci: dict[str, Any] = {
        "provider": "github",
        "poll_interval_seconds": _positive_int(ci_data["poll_interval_seconds"], "ci.poll_interval_seconds"),
        "allow_skipped": ci_data["allow_skipped"],
        "allow_neutral": ci_data["allow_neutral"],
        "zero_checks": zero_checks,
    }
    return SprintConfig(
        multisprint,
        sprint,
        (repository,),
        MappingProxyType(documents),
        MappingProxyType(agents),
        MappingProxyType(models),
        audit_data["enabled"],
        _positive_int(audit_data["max_rounds"], "pre_ci_audit.max_rounds"),
        MappingProxyType(limits),
        MappingProxyType(ci),
    )
