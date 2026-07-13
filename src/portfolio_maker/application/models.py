from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ArtifactKind = Literal[
    "master_profile",
    "portfolio_draft",
    "portfolio_public_manifest",
    "portfolio_html",
]
ArtifactDeliveryScope = Literal["restricted", "open_public"]


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
    excluded_directories: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DiscoverSourcesResult:
    report_path: Path
    discovered_count: int
    skipped_count: int
    events: tuple[ProgressEvent, ...] = ()


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
    invalidate_portfolio_draft: bool = True
    write_artifacts: bool = True


@dataclass(frozen=True)
class BuildProfileResult:
    json_path: Path
    markdown_path: Path
    claim_count: int
    claim_ids: tuple[int, ...] = ()
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class DraftPortfolioRequest:
    workspace: Path


@dataclass(frozen=True)
class DraftPortfolioResult:
    markdown_path: Path
    project_count: int


@dataclass(frozen=True)
class PublicPortfolioRequest:
    workspace: Path


@dataclass(frozen=True)
class PublicPortfolioResult:
    manifest_path: Path
    project_count: int
    claim_count: int
    evidence_count: int
    claim_ids: tuple[int, ...] = ()
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class RenderHtmlRequest:
    workspace: Path


@dataclass(frozen=True)
class RenderHtmlResult:
    manifest_path: Path
    html_path: Path


@dataclass(frozen=True)
class PrepareProjectReviewRequest:
    workspace: Path


@dataclass(frozen=True)
class PrepareProjectReviewResult:
    input_path: Path
    evidence_count: int


@dataclass(frozen=True)
class ComposeProjectsRequest:
    workspace: Path


@dataclass(frozen=True)
class ComposeProjectsResult:
    project_count: int
    unassigned_evidence_count: int
