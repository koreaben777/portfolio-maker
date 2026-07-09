from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from portfolio_maker.workspace import WorkspacePaths


class ApprovalMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceApproval:
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
    return SourceApproval(
        approved_source_uris=tuple(payload.get("approved_source_uris", ())),
        forbidden_paths=tuple(payload.get("forbidden_paths", ())),
        excluded_repositories=tuple(payload.get("excluded_repositories", ())),
        private_sources_allowed=bool(payload.get("private_sources_allowed", False)),
    )
