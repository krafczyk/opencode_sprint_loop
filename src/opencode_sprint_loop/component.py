"""Packaged controller identity and OpenCode compatibility contract."""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from . import __version__
from .errors import ControllerError


MAX_COMPONENT_METADATA_BYTES = 16 * 1024
MAX_RELATIONSHIP_BYTES = 768
_ASCII_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_COMPONENT_IDS = frozenset(
    {
        "mkchad",
        "container-runtime",
        "nvim-image",
        "opencode",
        "opencode-nvim",
        "opencode-project-reload",
        "compound-engineering",
        "sprint-loop-controller",
        "sprint-loop-nvim",
        "prereq-neovim",
        "prereq-git",
        "prereq-python",
        "prereq-node",
        "prereq-curl",
    }
)
_VERSION = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?P<suffix>(?:[-+][0-9A-Za-z.-]+)*)$"
)


def _metadata_error(message: str) -> ControllerError:
    """Return one bounded, credential-free packaged metadata error."""
    return ControllerError("invalid_component_metadata", message)


def _packaged_metadata_bytes() -> bytes:
    """Read only the package-owned component artifact."""
    return resources.files(__package__).joinpath("component.json").read_bytes()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys rather than silently accepting a last value."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _bounded_string(value: Any, limit: int) -> bool:
    """Return whether a UTF-8 string has no control characters and fits its field."""
    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= limit and not any(
        ord(character) < 32 or ord(character) == 127 for character in value
    )


def _validate_strings(value: Any) -> None:
    """Reject control characters and invalid Unicode anywhere in owner metadata."""
    if isinstance(value, str):
        if not _bounded_string(value, MAX_COMPONENT_METADATA_BYTES):
            raise _metadata_error("Component metadata contains an invalid string")
    elif isinstance(value, list):
        for item in value:
            _validate_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not _bounded_string(key, MAX_COMPONENT_METADATA_BYTES):
                raise _metadata_error("Component metadata contains an invalid field name")
            _validate_strings(item)


def _parse_version(value: Any) -> tuple[int, int, int, str] | None:
    """Parse one complete version while retaining its literal suffix."""
    if not _bounded_string(value, 128):
        return None
    matched = _VERSION.fullmatch(value)
    if matched is None:
        return None
    return (
        int(matched.group(1)),
        int(matched.group(2)),
        int(matched.group(3)),
        matched.group("suffix"),
    )


def _validate_contract(contract: Any) -> dict[str, Any]:
    """Validate the schema-1 contract kinds accepted in owner metadata."""
    if not isinstance(contract, dict) or not isinstance(contract.get("kind"), str):
        raise _metadata_error("Component relationship contract is invalid")
    kind = contract["kind"]
    if kind == "range":
        clauses = contract.get("clauses")
        suffix_policy = contract.get("suffix_policy")
        if (
            not isinstance(clauses, list)
            or not 1 <= len(clauses) <= 8
            or suffix_policy not in {"literal", "explicit-equivalence"}
        ):
            raise _metadata_error("Component range contract is invalid")
        seen_clauses: set[tuple[str, str | None]] = set()
        for clause in clauses:
            if not isinstance(clause, dict):
                raise _metadata_error("Component range clause is invalid")
            minimum_parsed = _parse_version(clause.get("min_inclusive"))
            maximum = clause.get("max_exclusive")
            maximum_parsed = _parse_version(maximum) if maximum is not None else None
            if (
                minimum_parsed is None
                or minimum_parsed[3]
                or (maximum is not None and (maximum_parsed is None or maximum_parsed[3]))
                or (maximum_parsed is not None and minimum_parsed[:3] >= maximum_parsed[:3])
            ):
                raise _metadata_error("Component range clause is invalid")
            key = (clause["min_inclusive"], maximum)
            if key in seen_clauses:
                raise _metadata_error("Component range clauses are duplicated")
            seen_clauses.add(key)
        if suffix_policy == "explicit-equivalence":
            equivalences = contract.get("equivalences")
            if not isinstance(equivalences, list) or len(equivalences) > 4:
                raise _metadata_error("Component suffix equivalences are invalid")
            for equivalence in equivalences:
                if (
                    not isinstance(equivalence, dict)
                    or _parse_version(equivalence.get("observed")) is None
                    or _parse_version(equivalence.get("equivalent_to")) is None
                ):
                    raise _metadata_error("Component suffix equivalence is invalid")
        return contract
    if kind in {"exact", "tested-baseline"}:
        if _parse_version(contract.get("version")) is None or contract.get("suffix_policy") not in {
            "literal",
            "explicit-equivalence",
        }:
            raise _metadata_error("Component version contract is invalid")
        return contract
    if kind == "exact-set":
        versions = contract.get("versions")
        if (
            not isinstance(versions, list)
            or not 1 <= len(versions) <= 4
            or len(set(versions)) != len(versions)
            or any(_parse_version(version) is None for version in versions)
            or contract.get("suffix_policy") not in {"literal", "explicit-equivalence"}
        ):
            raise _metadata_error("Component version-set contract is invalid")
        return contract
    if kind == "identity" and _bounded_string(contract.get("profile"), 64):
        return contract
    raise _metadata_error("Component relationship kind is unsupported")


def _validate_identity_profile(profile: Any) -> None:
    """Validate either a tree profile or one fixed-artifact SHA-256 profile."""
    if not isinstance(profile, dict) or not _bounded_string(profile.get("id"), 64):
        raise _metadata_error("Component identity profile is invalid")
    included = profile.get("included_roots")
    exclusions = profile.get("exclusions")
    if (
        not isinstance(included, list)
        or not 1 <= len(included) <= 32
        or not isinstance(exclusions, list)
        or len(exclusions) > 32
    ):
        raise _metadata_error("Component identity profile paths are invalid")
    for value in [*included, *exclusions]:
        if not _bounded_string(value, 128) or value.startswith("/") or ".." in value.split("/"):
            raise _metadata_error("Component identity profile path is invalid")
    if len(set(included)) != len(included) or len(set(exclusions)) != len(exclusions):
        raise _metadata_error("Component identity profile paths are duplicated")
    for field in (
        "max_regular_files",
        "max_total_bytes",
        "max_per_file_bytes",
        "max_elapsed_ms",
    ):
        value = profile.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise _metadata_error("Component identity profile bound is invalid")
    if not _bounded_string(profile.get("framing"), 64):
        raise _metadata_error("Component identity profile framing is invalid")
    algorithm = profile.get("algorithm")
    digest = profile.get("sha256")
    if algorithm != "sha256" and (
        not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise _metadata_error("Component identity profile algorithm is invalid")


def _validate_metadata(value: Any) -> dict[str, Any]:
    """Validate the controller's schema-1 owner artifact before use."""
    if not isinstance(value, dict):
        raise _metadata_error("Component metadata is not an object")
    _validate_strings(value)
    if value.get("schema") != 1 or isinstance(value.get("schema"), bool):
        raise _metadata_error("Component metadata schema is unsupported")
    if value.get("component_id") != "sprint-loop-controller":
        raise _metadata_error("Component metadata has an invalid component ID")
    if value.get("component_version") != __version__ or not _bounded_string(
        value.get("component_version"), 128
    ):
        raise _metadata_error("Component metadata version does not match the controller")
    relationships = value.get("relationships")
    if not isinstance(relationships, list) or len(relationships) > 4:
        raise _metadata_error("Component relationships are invalid")
    identifiers: set[str] = set()
    supports: list[dict[str, Any]] = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            raise _metadata_error("Component relationship is invalid")
        identifier = relationship.get("id")
        relationship_type = relationship.get("type")
        target = relationship.get("target_component")
        if (
            not isinstance(identifier, str)
            or _ASCII_ID.fullmatch(identifier) is None
            or identifier in identifiers
            or relationship_type not in {"ships", "requires", "supports", "tested-with"}
            or not isinstance(target, str)
            or target not in _COMPONENT_IDS
        ):
            raise _metadata_error("Component relationship is invalid")
        identifiers.add(identifier)
        _validate_contract(relationship.get("contract"))
        try:
            normalized = json.dumps(
                relationship,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as error:
            raise _metadata_error("Component relationship cannot be normalized") from error
        if len(normalized) > MAX_RELATIONSHIP_BYTES:
            raise _metadata_error("Component relationship exceeds 768 bytes")
        if relationship_type == "supports" and target == "opencode":
            supports.append(relationship)
    if len(supports) != 1:
        raise _metadata_error("Component metadata must declare one OpenCode support relationship")
    support_contract = supports[0]["contract"]
    if support_contract["kind"] != "range" or support_contract["suffix_policy"] != "literal":
        raise _metadata_error("Component OpenCode support contract is unsupported")
    if "identity_profile" in value:
        _validate_identity_profile(value["identity_profile"])
    return value


def load_component_metadata() -> dict[str, Any]:
    """Load and validate package-owned metadata without consulting runtime context."""
    try:
        raw = _packaged_metadata_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as error:
        raise _metadata_error("Packaged component metadata is unavailable") from error
    if not isinstance(raw, bytes) or len(raw) > MAX_COMPONENT_METADATA_BYTES:
        raise _metadata_error("Packaged component metadata exceeds 16 KiB")
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise _metadata_error("Packaged component metadata is malformed") from error
    return _validate_metadata(parsed)


def component_info() -> dict[str, Any]:
    """Return the stable, root-free public component identity document."""
    metadata = load_component_metadata()
    relationship = next(
        item
        for item in metadata["relationships"]
        if item["type"] == "supports" and item["target_component"] == "opencode"
    )
    document: dict[str, Any] = {
        "schema": 1,
        "component_id": metadata["component_id"],
        "controller_version": metadata["component_version"],
        "supported_opencode": relationship,
    }
    if "identity_profile" in metadata:
        document["identity_profile"] = metadata["identity_profile"]
    return document


def is_supported_opencode_version(value: Any) -> bool:
    """Evaluate the packaged OpenCode support range with literal suffix policy."""
    observed = _parse_version(value)
    if observed is None:
        return False
    metadata = load_component_metadata()
    relationship = next(
        item
        for item in metadata["relationships"]
        if item["type"] == "supports" and item["target_component"] == "opencode"
    )
    contract = relationship["contract"]
    if contract["kind"] != "range":
        return False
    if contract["suffix_policy"] == "literal" and observed[3]:
        return False
    precedence = observed[:3]
    for clause in contract["clauses"]:
        minimum = _parse_version(clause["min_inclusive"])
        maximum = _parse_version(clause.get("max_exclusive"))
        assert minimum is not None
        if precedence >= minimum[:3] and (maximum is None or precedence < maximum[:3]):
            return True
    return False
