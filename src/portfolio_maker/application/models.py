from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str
    count: int | None = None


@dataclass(frozen=True)
class DiscoverSourcesRequest:
    workspace: Path
    home: Path
    include_github: bool = True
    forbidden_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DiscoverSourcesResult:
    report_path: Path
    discovered_count: int
    skipped_count: int
    events: tuple[ProgressEvent, ...] = ()


@dataclass(frozen=True)
class ApprovalRequest:
    workspace: Path
    write_sample: bool = False


@dataclass(frozen=True)
class ApprovalResult:
    approval_path: Path
    approved_sources: int
    forbidden_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestSourcesRequest:
    workspace: Path


@dataclass(frozen=True)
class IngestSourcesResult:
    ingested_count: int
    skipped_count: int
    snapshot_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BuildProfileRequest:
    workspace: Path


@dataclass(frozen=True)
class BuildProfileResult:
    json_path: Path
    markdown_path: Path
    claim_count: int


@dataclass(frozen=True)
class DraftPortfolioRequest:
    workspace: Path


@dataclass(frozen=True)
class DraftPortfolioResult:
    markdown_path: Path
    project_count: int


@dataclass
class DiscoveryReport:
    local_candidates: list[dict[str, str]] = field(default_factory=list)
    github_candidates: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
