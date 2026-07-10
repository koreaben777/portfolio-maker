from __future__ import annotations

import json
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
    version: int
    approved_source_uris: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
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


def write_sample_approval(paths: WorkspacePaths) -> Path:
    paths.ensure()
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

    return SourceApproval(
        version=version,
        approved_source_uris=_string_list(payload, "approved_source_uris"),
        forbidden_paths=_string_list(payload, "forbidden_paths"),
        excluded_repositories=_string_list(payload, "excluded_repositories"),
        private_sources_allowed=private_sources_allowed,
    )


def approval_forbidden_paths(paths: WorkspacePaths, approval: SourceApproval) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for value in approval.forbidden_paths:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = paths.workspace / path
        normalized.append(path.resolve(strict=False))
    return tuple(normalized)


def _string_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ApprovalFormatError(f"{key} must be a list of strings")
    return tuple(value)
