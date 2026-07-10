from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from portfolio_maker.workspace import WorkspacePaths
from portfolio_maker.infrastructure.github_connector import canonical_repository_name


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
    payload = json.dumps(sample_approval_payload(), indent=2) + "\n"
    if force:
        paths.approval_path.write_text(payload, encoding="utf-8")
        return paths.approval_path
    try:
        with paths.approval_path.open("x", encoding="utf-8") as approval_file:
            approval_file.write(payload)
    except FileExistsError as error:
        raise ApprovalFormatError(
            f"Approval file already exists: {paths.approval_path}. Use --force to reset it"
        ) from error
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
    try:
        excluded_repositories = tuple(
            canonical_repository_name(repository)
            for repository in _string_list(payload, "excluded_repositories")
        )
    except ValueError as error:
        raise ApprovalFormatError(
            "excluded_repositories entries must use owner/repo form"
        ) from error

    return SourceApproval(
        approved_source_uris=_string_list(payload, "approved_source_uris"),
        forbidden_paths=forbidden_paths,
        excluded_repositories=excluded_repositories,
        private_sources_allowed=private_sources_allowed,
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
