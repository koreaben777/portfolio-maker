from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import unicodedata

from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
)
from portfolio_maker.infrastructure.managed_files import read_managed_bytes, write_managed_text
from portfolio_maker.workspace import WorkspacePaths


class ApprovalMissingError(RuntimeError):
    pass


class ApprovalFormatError(ValueError):
    pass


@dataclass(frozen=True)
class SourceApproval:
    approved_source_uris: tuple[str, ...]
    legacy_forbidden_paths: tuple[Path, ...]
    excluded_directories: tuple[Path, ...]
    excluded_repositories: tuple[str, ...]
    private_sources_allowed: bool
    allowed_repositories: tuple[str, ...]
    excluded_file_patterns: tuple[str, ...]
    approved_github_activity_urls: tuple[str, ...]
    approved_private_github_activity_urls: tuple[str, ...]

    @property
    def forbidden_paths(self) -> tuple[Path, ...]:
        return self.excluded_directories


def sample_approval_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "approved_source_uris": [],
        "forbidden_paths": [],
        "excluded_directories": [],
        "excluded_repositories": [],
        "private_sources_allowed": False,
        "allowed_repositories": [],
        "excluded_file_patterns": [],
        "approved_github_activity_urls": [],
        "approved_private_github_activity_urls": [],
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

    legacy_forbidden_paths = tuple(
        normalize_workspace_path(paths, value)
        for value in _string_list(payload, "forbidden_paths")
    )
    excluded_directories = tuple(
        normalize_workspace_path(paths, value)
        for value in _string_list(payload, "excluded_directories")
    )
    merged_excluded_directories = tuple(
        path
        for index, path in enumerate(legacy_forbidden_paths + excluded_directories)
        if path not in (legacy_forbidden_paths + excluded_directories)[:index]
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
        or any(unicodedata.category(character).startswith("C") for character in pattern)
        for pattern in excluded_file_patterns
    ):
        raise ApprovalFormatError("excluded_file_patterns entries must be safe filenames globs")
    approved_github_activity_urls = _string_list(payload, "approved_github_activity_urls")
    canonical_approved_urls = tuple(
        canonical_public_github_activity_url(url)
        for url in approved_github_activity_urls
    )
    if any(url is None for url in canonical_approved_urls):
        raise ApprovalFormatError(
            "approved_github_activity_urls entries must be public GitHub activity URLs"
        )
    approved_private_activity_urls = _canonical_activity_urls(
        payload,
        "approved_private_github_activity_urls",
        "approved_private_github_activity_urls entries must be GitHub activity URLs",
    )

    return SourceApproval(
        approved_source_uris=_string_list(payload, "approved_source_uris"),
        legacy_forbidden_paths=legacy_forbidden_paths,
        excluded_directories=merged_excluded_directories,
        excluded_repositories=excluded_repositories,
        private_sources_allowed=private_sources_allowed,
        allowed_repositories=allowed_repositories,
        excluded_file_patterns=excluded_file_patterns,
        approved_github_activity_urls=tuple(
            url for url in canonical_approved_urls if url is not None
        ),
        approved_private_github_activity_urls=approved_private_activity_urls,
    )


def persist_excluded_directories(
    paths: WorkspacePaths, directories: tuple[Path | str, ...]
) -> None:
    paths.ensure()
    if not paths.approval_path.exists():
        write_sample_approval(paths)
    approval = load_approval(paths)
    requested = tuple(normalize_workspace_path(paths, directory) for directory in directories)
    merged = tuple(
        path
        for index, path in enumerate(approval.excluded_directories + requested)
        if path not in (approval.excluded_directories + requested)[:index]
    )
    try:
        payload = json.loads(read_managed_bytes(paths.approval_path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalFormatError("Approval file cannot be updated") from error
    if not isinstance(payload, dict):
        raise ApprovalFormatError("approval payload must be an object")
    payload["excluded_directories"] = [str(path) for path in merged]
    write_managed_text(
        paths.approval_path,
        json.dumps(payload, indent=2) + "\n",
        overwrite=True,
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


def _canonical_activity_urls(
    payload: dict[str, Any], key: str, error_message: str
) -> tuple[str, ...]:
    values = _string_list(payload, key)
    canonical_values = tuple(canonical_public_github_activity_url(value) for value in values)
    if any(value is None for value in canonical_values):
        raise ApprovalFormatError(error_message)
    return tuple(value for value in canonical_values if value is not None)
