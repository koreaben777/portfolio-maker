from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceType(StrEnum):
    LOCAL_FILE = "local_file"
    GITHUB_REPOSITORY = "github_repository"


class SourceStatus(StrEnum):
    DISCOVERED = "discovered"
    APPROVED = "approved"
    INGESTED = "ingested"
    SKIPPED_POLICY = "skipped_policy"
    EXTRACT_FAILED = "extract_failed"
    STALE_SOURCE = "stale_source"


@dataclass(frozen=True)
class Source:
    id: int | None
    type: SourceType
    uri: str
    display_name: str
    owner: str | None
    status: SourceStatus


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
    is_private: bool = False
    state_field: str | None = None


@dataclass(frozen=True)
class PublicEvidenceRecord:
    project_id: int
    project_name: str
    claim_id: int
    claim_text: str
    evidence_id: int
    evidence_stable_id: str
    evidence_locator: str
    source_id: int | None
    source_type: str | None
    source_uri: str | None
    source_display_name: str | None
    source_status: str | None
    activity_id: int | None
    activity_repo: str | None
    activity_type: str | None
    activity_url: str | None
    activity_title: str | None
    activity_state: str | None
    activity_state_field: str | None
    activity_author: str | None
    activity_created_at: str | None
    activity_is_private: bool | None
