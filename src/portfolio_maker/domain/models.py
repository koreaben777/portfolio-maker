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
