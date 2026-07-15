from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class SemanticNodeKind(StrEnum):
    SOURCE = "source"
    DIRECTORY = "directory"
    FILE = "file"
    GITHUB_ACTIVITY = "github_activity"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    FAILED = "failed"


class RevisionStatus(StrEnum):
    STAGING = "staging"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


def _stable_hash(namespace: str, *parts: str) -> str:
    canonical = "\0".join((namespace, *parts)).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def stable_source_id(kind: str, provider_root_key: str) -> str:
    return _stable_hash("portfolio-maker-source-v1", kind, provider_root_key)


def stable_node_id(source_id: str, provider_item_key: str) -> str:
    return _stable_hash("portfolio-maker-node-v1", source_id, provider_item_key)


def boundary_fingerprint(boundary_type: str, node_ids: tuple[str, ...]) -> str:
    return _stable_hash("portfolio-maker-boundary-v1", boundary_type, *sorted(node_ids))


@dataclass(frozen=True)
class SemanticNode:
    node_id: str
    source_id: str
    node_kind: SemanticNodeKind
    parent_node_id: str | None
    display_name: str
    relative_hierarchy: str
    content_fingerprint: str | None
    semantic_summary: str
    semantic_roles: tuple[str, ...]
    topics: tuple[str, ...]
    evidence_ids: tuple[int, ...]
    analysis_status: AnalysisStatus
    analyzer_version: str
    updated_at: str


@dataclass(frozen=True)
class SemanticEdge:
    revision_id: str
    from_node_id: str
    relation: str
    to_node_id: str
    confidence: str | None = None


@dataclass(frozen=True)
class SemanticRevision:
    id: str
    source_id: str
    policy_hash: str
    analyzer_version: str
    status: RevisionStatus
    started_at: str | None = None
    completed_at: str | None = None
