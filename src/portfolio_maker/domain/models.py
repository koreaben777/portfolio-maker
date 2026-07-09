from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceType(StrEnum):
    LOCAL_FILE = "local_file"
    LOCAL_DIRECTORY = "local_directory"
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_ACTIVITY = "github_activity"


class SourceStatus(StrEnum):
    DISCOVERED = "discovered"
    APPROVED = "approved"
    INGESTED = "ingested"
    SKIPPED_POLICY = "skipped_policy"
    SKIPPED_PERMISSION_DENIED = "skipped_permission_denied"
    EXTRACT_FAILED = "extract_failed"
    PAUSED_RATE_LIMIT = "paused_rate_limit"
    NETWORK_FAILED = "network_failed"
    AUTH_FAILED = "auth_failed"
    STALE_SOURCE = "stale_source"


class EvidenceKind(StrEnum):
    FILE_TEXT = "file_text"
    README = "readme"
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"
    ISSUE = "issue"
    REVIEW = "review"
    WORKFLOW_RUN = "workflow_run"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Source:
    id: int | None
    type: SourceType
    uri: str
    display_name: str
    owner: str | None
    status: SourceStatus


@dataclass(frozen=True)
class SourceSnapshot:
    id: int | None
    source_id: int
    snapshot_path: str
    content_hash: str
    extractor: str


@dataclass(frozen=True)
class EvidenceItem:
    id: int | None
    source_id: int
    snapshot_id: int | None
    kind: EvidenceKind
    locator: str
    quote_hash: str | None
    summary: str
    confidence: Confidence


@dataclass(frozen=True)
class GitHubActivity:
    id: int | None
    source_id: int | None
    repo: str
    activity_type: str
    url: str
    title: str
    state: str
    author: str
    created_at: str
    merged_at: str | None


@dataclass(frozen=True)
class Project:
    id: int | None
    name: str
    summary: str
    status: str
    visibility: str
    primary_source_id: int | None


@dataclass(frozen=True)
class CareerClaim:
    id: int | None
    claim_type: str
    text: str
    confidence: Confidence
    public_safe: bool


@dataclass(frozen=True)
class Artifact:
    id: int | None
    type: str
    path: str
    source_profile_version: str
