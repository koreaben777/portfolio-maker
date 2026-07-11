from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from portfolio_maker.infrastructure.github_connector import canonical_repository_name
from portfolio_maker.infrastructure.managed_files import read_managed_bytes, write_managed_text
from portfolio_maker.workspace import WorkspacePaths


class ApprovalMissingError(RuntimeError):
    pass


class ApprovalFormatError(ValueError):
    pass


@dataclass(frozen=True)
class SourceApproval:
    approved_source_uris: tuple[str, ...]
    forbidden_paths: tuple[Path, ...]
    excluded_repositories: tuple[str, ...]
    private_sources_allowed: bool
    allowed_repositories: tuple[str, ...]
    excluded_file_patterns: tuple[str, ...]


def sample_approval_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "approved_source_uris": [],
        "forbidden_paths": [],
        "excluded_repositories": [],
        "private_sources_allowed": False,
        "allowed_repositories": [],
        "excluded_file_patterns": [],
    }


def write_sample_approval(paths: WorkspacePaths, force: bool = False) -> Path:
    paths.ensure()
    payload = json.dumps(sample_approval_payload(), indent=2) + "\n"
    try:
        return write_managed_text(paths.approval_path, payload, overwrite=force)
    except FileExistsError as error:
        raise ApprovalFormatError(
            f"Approval file already exists: {paths.approval_path}. Use --force to reset it"
        ) from error


def load_approval(paths: WorkspacePaths) -> SourceApproval:
    try:
        raw_payload = read_managed_bytes(paths.approval_path)
    except FileNotFoundError:
        raise ApprovalMissingError(f"Approval file missing: {paths.approval_path}")
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ApprovalFormatError(
            f"Approval file has invalid UTF-8: {paths.approval_path}. "
            "Repair or replace the damaged approval file"
        ) from error
    except json.JSONDecodeError as error:
        raise ApprovalFormatError(
            f"Approval file has invalid JSON: {paths.approval_path}. "
            "Repair or replace the damaged approval file"
        ) from error
    if not isinstance(payload, dict):
        raise ApprovalFormatError("approval payload must be an object")
    version = payload.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ApprovalFormatError("version must be 1")
    private_sources_allowed = payload.get("private_sources_allowed", False)
    if not isinstance(private_sources_allowed, bool):
        raise ApprovalFormatError("private_sources_allowed must be a bool")

    forbidden_paths = tuple(
        normalize_workspace_path(paths, value)
        for value in _string_list(payload, "forbidden_paths")
    )
    try:
        excluded_repositories = tuple(
            canonical_repository_name(repository)
            for repository in _string_list(payload, "excluded_repositories")
        )
    except ValueError as error:
        raise ApprovalFormatError(
            "excluded_repositories entries must use owner/repo form"
        ) from error
    try:
        allowed_repositories = tuple(
            canonical_repository_name(repository)
            for repository in _string_list(payload, "allowed_repositories")
        )
    except ValueError as error:
        raise ApprovalFormatError(
            "allowed_repositories entries must use owner/repo form"
        ) from error
    excluded_file_patterns = _string_list(payload, "excluded_file_patterns")
    if any(
        not pattern
        or "/" in pattern
        or "\\" in pattern
        or any(character.isspace() and character not in {" ", "\t"} for character in pattern)
        for pattern in excluded_file_patterns
    ):
        raise ApprovalFormatError("excluded_file_patterns entries must be safe filenames globs")

    return SourceApproval(
        approved_source_uris=_string_list(payload, "approved_source_uris"),
        forbidden_paths=forbidden_paths,
        excluded_repositories=excluded_repositories,
        private_sources_allowed=private_sources_allowed,
        allowed_repositories=allowed_repositories,
        excluded_file_patterns=excluded_file_patterns,
    )


def normalize_workspace_path(paths: WorkspacePaths, value: Path | str) -> Path:
    try:
        path = Path(value).expanduser()
    except RuntimeError as error:
        raise ApprovalFormatError("invalid forbidden path") from error
    if not path.is_absolute():
        path = paths.workspace / path
    return path.resolve(strict=False)


def _string_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ApprovalFormatError(f"{key} must be a list of strings")
    return tuple(value)
