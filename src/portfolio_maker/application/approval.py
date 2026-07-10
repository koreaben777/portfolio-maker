from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def sample_approval_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "approved_source_uris": [],
        "forbidden_paths": [],
        "excluded_repositories": [],
        "private_sources_allowed": False,
    }


def write_sample_approval(paths: WorkspacePaths, force: bool = False) -> Path:
    paths.ensure()
    if paths.approval_path.exists() and not force:
        raise ApprovalFormatError(
            f"Approval file already exists: {paths.approval_path}. Use --force to reset it"
        )
    paths.approval_path.write_text(
        json.dumps(sample_approval_payload(), indent=2) + "\n",
        encoding="utf-8",
    )
    return paths.approval_path


def load_approval(paths: WorkspacePaths) -> SourceApproval:
    if not paths.approval_path.exists():
        raise ApprovalMissingError(f"Approval file missing: {paths.approval_path}")

    payload = json.loads(paths.approval_path.read_text(encoding="utf-8"))
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
    excluded_repositories = _string_list(payload, "excluded_repositories")
    _validate_repository_exclusions(excluded_repositories)

    return SourceApproval(
        approved_source_uris=_string_list(payload, "approved_source_uris"),
        forbidden_paths=forbidden_paths,
        excluded_repositories=excluded_repositories,
        private_sources_allowed=private_sources_allowed,
    )


def approval_forbidden_paths(_paths: WorkspacePaths, approval: SourceApproval) -> tuple[Path, ...]:
    return approval.forbidden_paths


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


REPOSITORY_EXCLUSION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _validate_repository_exclusions(excluded_repositories: tuple[str, ...]) -> None:
    if any(REPOSITORY_EXCLUSION.fullmatch(repository) is None for repository in excluded_repositories):
        raise ApprovalFormatError("excluded_repositories entries must use owner/repo form")
