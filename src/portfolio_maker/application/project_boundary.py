from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.artifact_approval import load_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.models import (
    BuildProfileRequest,
    PrepareProjectReviewRequest,
    PrepareProjectReviewResult,
)
from portfolio_maker.domain.semantic_models import boundary_fingerprint
from portfolio_maker.infrastructure.artifacts import write_json
from portfolio_maker.infrastructure.github_connector import contains_unicode_control
from portfolio_maker.infrastructure.managed_files import read_managed_bytes
from portfolio_maker.infrastructure.policy import (
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class ProjectBoundaryError(ValueError):
    pass


class ActiveSemanticRevisionMissing(ProjectBoundaryError):
    pass


@dataclass(frozen=True)
class _BoundaryNode:
    parent_node_id: str | None
    relative_hierarchy: str
    topics: tuple[str, ...]


_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9:_-]+")
_PROJECT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_BOUNDARY_TYPES = {
    "directory_root",
    "independent_child",
    "cross_directory_cluster",
    "manual",
}
_CONFIDENCE_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True)
class ProjectCandidateV2:
    id: str
    project_id: str
    title: str
    overview: str
    boundary_type: Literal[
        "directory_root", "independent_child", "cross_directory_cluster", "manual"
    ]
    boundary_node_ids: tuple[str, ...]
    boundary_fingerprint: str
    evidence_ids: tuple[int, ...]
    grouping_rationale: tuple[str, ...]
    counter_signals: tuple[str, ...]
    review_reasons: tuple[str, ...]
    confidence: Literal["low", "medium", "high"]


def build_project_review_input_v2(workspace: Path) -> dict[str, Any]:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    active, nodes = _load_active_semantic_revision(paths, repository)

    build_profile(
        BuildProfileRequest(
            workspace=paths.workspace,
            invalidate_portfolio_draft=False,
            write_artifacts=False,
        )
    )
    selection = EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="master_profile",
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )
    selected_evidence_ids = set(selection.included_evidence_ids)
    payload = {
        "version": 2,
        "artifact_kind": "master_profile",
        "delivery_scope": _delivery_scope(selection.delivery_scope),
        "policy_hash": _safe_sha256(selection.policy_hash, "policy_hash"),
        "index_revision": _safe_identifier(active["id"], "index_revision"),
        "nodes": [
            _safe_node_payload(node, selected_evidence_ids)
            for node in sorted(
                nodes,
                key=lambda item: (str(item["relative_hierarchy"]), str(item["node_id"])),
            )
        ],
        "github_evidence": _safe_github_evidence(selection.records, selected_evidence_ids),
    }
    return {
        **payload,
        "input_sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
    }


def prepare_project_review_v2(
    request: PrepareProjectReviewRequest,
) -> PrepareProjectReviewResult:
    paths = WorkspacePaths.from_root(request.workspace)
    current = build_project_review_input_v2(paths.workspace)
    write_json(paths.project_review_input_v2_path, current)
    evidence_ids = {
        evidence_id
        for node in current["nodes"]
        for evidence_id in node["evidence_ids"]
    }
    evidence_ids.update(item["evidence_id"] for item in current["github_evidence"])
    return PrepareProjectReviewResult(
        input_path=paths.project_review_input_v2_path,
        evidence_count=len(evidence_ids),
    )


def parse_candidate_payload_v2(
    payload: Any, review_input: dict[str, Any]
) -> tuple[ProjectCandidateV2, ...]:
    review_hash, nodes, available_evidence_ids = _validate_review_input_v2(review_input)
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ProjectBoundaryError("project candidates version is invalid")
    if payload.get("review_input_sha256") != review_hash:
        raise ProjectBoundaryError("project candidates do not match current review input")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ProjectBoundaryError("project candidates must be a list")

    candidates: list[ProjectCandidateV2] = []
    candidate_ids: set[str] = set()
    project_ids: set[str] = set()
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            raise ProjectBoundaryError("candidate entries must be objects")
        candidate_id = _safe_project_id(raw_candidate.get("id"), "candidate ID")
        if candidate_id in candidate_ids:
            raise ProjectBoundaryError("candidate IDs must be unique")
        candidate_ids.add(candidate_id)
        project_id = _safe_project_id(raw_candidate.get("project_id"), "project ID")
        if project_id in project_ids:
            raise ProjectBoundaryError("project IDs must be unique")
        project_ids.add(project_id)
        boundary_type = raw_candidate.get("boundary_type")
        if boundary_type not in _BOUNDARY_TYPES:
            raise ProjectBoundaryError("candidate boundary type is invalid")
        confidence = raw_candidate.get("confidence")
        if confidence not in _CONFIDENCE_LEVELS:
            raise ProjectBoundaryError("candidate confidence is invalid")
        boundary_node_ids = _candidate_boundary_node_ids(
            raw_candidate.get("boundary_node_ids"), nodes
        )
        expected_fingerprint = boundary_fingerprint(boundary_type, boundary_node_ids)
        if raw_candidate.get("boundary_fingerprint") != expected_fingerprint:
            raise ProjectBoundaryError("candidate boundary fingerprint is invalid")
        _validate_boundary_coherence(boundary_type, boundary_node_ids, nodes)
        _reject_prohibited_broad_root(boundary_node_ids, nodes)
        evidence_ids = _candidate_evidence_ids(
            raw_candidate.get("evidence_ids"), available_evidence_ids
        )
        candidates.append(
            ProjectCandidateV2(
                id=candidate_id,
                project_id=project_id,
                title=_safe_semantic_text(raw_candidate.get("title"), "candidate title"),
                overview=_safe_semantic_text(raw_candidate.get("overview"), "candidate overview"),
                boundary_type=boundary_type,
                boundary_node_ids=boundary_node_ids,
                boundary_fingerprint=expected_fingerprint,
                evidence_ids=evidence_ids,
                grouping_rationale=_candidate_text_list(
                    raw_candidate.get("grouping_rationale"), "grouping rationale", required=True
                ),
                counter_signals=_candidate_text_list(
                    raw_candidate.get("counter_signals"), "counter signals", required=False
                ),
                review_reasons=_candidate_text_list(
                    raw_candidate.get("review_reasons"), "review reasons", required=False
                ),
                confidence=confidence,
            )
        )
    return tuple(candidates)


def _validate_review_input_v2(
    review_input: dict[str, Any],
) -> tuple[str, dict[str, _BoundaryNode], set[int]]:
    if not isinstance(review_input, dict) or review_input.get("version") != 2:
        raise ProjectBoundaryError("project review input version is invalid")
    review_hash = _safe_sha256(review_input.get("input_sha256"), "review input SHA-256")
    canonical = dict(review_input)
    canonical.pop("input_sha256", None)
    if hashlib.sha256(_canonical_bytes(canonical)).hexdigest() != review_hash:
        raise ProjectBoundaryError("project review input hash is invalid")
    raw_nodes = review_input.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ProjectBoundaryError("project review input nodes are invalid")

    nodes: dict[str, _BoundaryNode] = {}
    available_evidence_ids: set[int] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise ProjectBoundaryError("project review input nodes are invalid")
        node_id = _safe_identifier(raw_node.get("node_id"), "candidate boundary node ID")
        if node_id in nodes:
            raise ProjectBoundaryError("project review input node IDs must be unique")
        parent_node_id = raw_node.get("parent_node_id")
        if parent_node_id is not None:
            parent_node_id = _safe_identifier(parent_node_id, "candidate parent node ID")
        topics = _candidate_text_list(raw_node.get("topics"), "candidate node topics", required=False)
        evidence_ids = _candidate_evidence_ids(raw_node.get("evidence_ids"), None)
        nodes[node_id] = _BoundaryNode(
            parent_node_id=parent_node_id,
            relative_hierarchy=_safe_relative_hierarchy(raw_node.get("relative_hierarchy")),
            topics=topics,
        )
        available_evidence_ids.update(evidence_ids)

    raw_github_evidence = review_input.get("github_evidence")
    if not isinstance(raw_github_evidence, list):
        raise ProjectBoundaryError("project review input GitHub evidence is invalid")
    for item in raw_github_evidence:
        if not isinstance(item, dict):
            raise ProjectBoundaryError("project review input GitHub evidence is invalid")
        evidence_id = _positive_int(item.get("evidence_id"), "GitHub evidence ID")
        available_evidence_ids.add(evidence_id)
    return review_hash, nodes, available_evidence_ids


def _safe_project_id(value: object, field: str) -> str:
    if not isinstance(value, str) or _PROJECT_ID.fullmatch(value) is None:
        raise ProjectBoundaryError(f"{field} is invalid")
    return value


def _candidate_boundary_node_ids(
    value: object, nodes: dict[str, _BoundaryNode]
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ProjectBoundaryError("candidate boundary node IDs are invalid")
    node_ids = tuple(_safe_identifier(item, "candidate boundary node ID") for item in value)
    if len(set(node_ids)) != len(node_ids):
        raise ProjectBoundaryError("candidate boundary node IDs must be unique")
    if not set(node_ids).issubset(nodes):
        raise ProjectBoundaryError("candidate boundary node is unknown")
    return node_ids


def _candidate_evidence_ids(
    value: object, available_evidence_ids: set[int] | None
) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in value)
        or len(set(value)) != len(value)
    ):
        raise ProjectBoundaryError("candidate evidence IDs are invalid")
    evidence_ids = tuple(value)
    if available_evidence_ids is not None and not set(evidence_ids).issubset(available_evidence_ids):
        raise ProjectBoundaryError("candidate evidence is unknown")
    return evidence_ids


def _candidate_text_list(value: object, field: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value) or any(
        not isinstance(item, str) for item in value
    ):
        raise ProjectBoundaryError(f"{field} is invalid")
    values = tuple(_safe_semantic_text(item, field) for item in value)
    if len(set(values)) != len(values):
        raise ProjectBoundaryError(f"{field} must be unique")
    return values


def _validate_boundary_coherence(
    boundary_type: str,
    boundary_node_ids: tuple[str, ...],
    nodes: dict[str, _BoundaryNode],
) -> None:
    if boundary_type == "directory_root":
        if len(boundary_node_ids) != 1:
            raise ProjectBoundaryError("directory root boundary must contain one node")
        _boundary_ancestry(boundary_node_ids[0], nodes, "directory root")
        return
    if boundary_type == "independent_child":
        if len(boundary_node_ids) != 1 or nodes[boundary_node_ids[0]].parent_node_id is None:
            raise ProjectBoundaryError("independent child boundary must contain one child node")
        _boundary_ancestry(boundary_node_ids[0], nodes, "independent child")
        return
    if boundary_type == "cross_directory_cluster":
        if len(boundary_node_ids) < 2:
            raise ProjectBoundaryError("cross-directory boundary must contain multiple nodes")
        shared_ancestors = set(_boundary_ancestry(boundary_node_ids[0], nodes, "cross-directory"))
        for node_id in boundary_node_ids[1:]:
            shared_ancestors.intersection_update(
                _boundary_ancestry(node_id, nodes, "cross-directory")
            )
        if not any(nodes[node_id].relative_hierarchy != "." for node_id in shared_ancestors):
            raise ProjectBoundaryError("cross-directory boundary has no shared explainable ancestor")


def _boundary_ancestry(
    node_id: str, nodes: dict[str, _BoundaryNode], boundary_name: str
) -> tuple[str, ...]:
    ancestry: list[str] = []
    current_node_id = node_id
    while True:
        if current_node_id in ancestry:
            raise ProjectBoundaryError(f"{boundary_name} boundary contains a parent cycle")
        node = nodes.get(current_node_id)
        if node is None:
            raise ProjectBoundaryError(f"{boundary_name} boundary contains a dangling parent")
        ancestry.append(current_node_id)
        if node.parent_node_id is None:
            if node.relative_hierarchy != ".":
                raise ProjectBoundaryError(f"{boundary_name} boundary contains a dangling parent")
            return tuple(ancestry)
        parent = nodes.get(node.parent_node_id)
        if parent is None or parent.relative_hierarchy != _parent_hierarchy(node.relative_hierarchy):
            raise ProjectBoundaryError(f"{boundary_name} boundary contains a dangling parent")
        current_node_id = node.parent_node_id


def _parent_hierarchy(relative_hierarchy: str) -> str:
    if "/" not in relative_hierarchy:
        return "."
    return relative_hierarchy.rsplit("/", 1)[0]


def _reject_prohibited_broad_root(
    boundary_node_ids: tuple[str, ...],
    nodes: dict[str, _BoundaryNode],
) -> None:
    for node_id in boundary_node_ids:
        node = nodes[node_id]
        if node.parent_node_id is not None:
            continue
        child_topics = [
            set(child.topics)
            for child in nodes.values()
            if child.parent_node_id == node_id and child.topics
        ]
        if any(
            first.isdisjoint(second)
            for index, first in enumerate(child_topics)
            for second in child_topics[index + 1 :]
        ):
            raise ProjectBoundaryError("candidate boundary is a prohibited broad root")


def _load_active_semantic_revision(
    paths: WorkspacePaths, repository: SQLiteRepository
) -> tuple[dict[str, str | None], list[dict[str, object]]]:
    try:
        manifest = json.loads(read_managed_bytes(paths.semantic_index_manifest_path).decode("utf-8"))
    except FileNotFoundError as error:
        raise ActiveSemanticRevisionMissing("no active semantic revision") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectBoundaryError("semantic index manifest is invalid") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("revision_id"), str):
        raise ProjectBoundaryError("semantic index manifest is invalid")

    manifest_nodes = repository.list_semantic_nodes(manifest["revision_id"])
    if not manifest_nodes:
        raise ActiveSemanticRevisionMissing("no active semantic revision")
    source_ids = {node["source_id"] for node in manifest_nodes}
    if len(source_ids) != 1 or not all(isinstance(source_id, str) for source_id in source_ids):
        raise ProjectBoundaryError("semantic index nodes are invalid")
    active = repository.get_active_semantic_revision(next(iter(source_ids)))
    if active is None:
        raise ActiveSemanticRevisionMissing("no active semantic revision")
    nodes = repository.list_semantic_nodes(active["id"])
    if not nodes:
        raise ProjectBoundaryError("active semantic revision is invalid")
    return active, nodes


def _safe_node_payload(
    node: dict[str, object], selected_evidence_ids: set[int]
) -> dict[str, object]:
    node_id = _safe_identifier(node.get("node_id"), "node_id")
    parent_node_id = node.get("parent_node_id")
    if parent_node_id is not None:
        parent_node_id = _safe_identifier(parent_node_id, "parent_node_id")
    kind = node.get("node_kind")
    if kind not in {"source", "directory", "file", "github_activity"}:
        raise ProjectBoundaryError("semantic node kind is invalid")
    evidence_ids = node.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in evidence_ids)
    ):
        raise ProjectBoundaryError("semantic node evidence IDs are invalid")
    return {
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "kind": kind,
        "display_name": _safe_display_name(node.get("display_name")),
        "relative_hierarchy": _safe_relative_hierarchy(node.get("relative_hierarchy")),
        "semantic_summary": _safe_semantic_text(node.get("semantic_summary"), "semantic_summary", allow_empty=True),
        "semantic_roles": _safe_text_list(node.get("semantic_roles"), "semantic_roles"),
        "topics": _safe_text_list(node.get("topics"), "topics"),
        "analysis_status": _analysis_status(node.get("analysis_status")),
        "evidence_ids": sorted(set(evidence_ids).intersection(selected_evidence_ids)),
    }


def _safe_github_evidence(records: tuple[Any, ...], selected_evidence_ids: set[int]) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for record in records:
        if record.evidence_id not in selected_evidence_ids or record.activity_id is None:
            continue
        origin = "private_github" if record.activity_is_private else "public_github"
        item: dict[str, object] = {
            "evidence_id": _positive_int(record.evidence_id, "github evidence ID"),
            "stable_id": _safe_identifier(record.evidence_stable_id, "github stable_id"),
            "origin": origin,
            "source_label": (
                "Private GitHub activity"
                if origin == "private_github"
                else _safe_semantic_text(record.source_display_name, "github source_label")
            ),
            "activity_type": _safe_semantic_text(record.activity_type, "github activity_type"),
            "title": _safe_semantic_text(record.activity_title, "github title"),
            "created_at": _safe_semantic_text(record.activity_created_at, "github created_at"),
        }
        evidence.append(item)
    return sorted(evidence, key=lambda item: int(item["evidence_id"]))


def _delivery_scope(value: object) -> str:
    if value not in {"restricted", "open_public"}:
        raise ProjectBoundaryError("project review delivery scope is invalid")
    return value


def _analysis_status(value: object) -> str:
    if value not in {"pending", "complete", "partial", "unsupported", "unreadable", "failed"}:
        raise ProjectBoundaryError("semantic analysis status is invalid")
    return value


def _safe_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ProjectBoundaryError(f"{field} is invalid")
    return value


def _safe_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProjectBoundaryError(f"{field} is invalid")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProjectBoundaryError(f"{field} is invalid")
    return value


def _safe_display_name(value: object) -> str:
    text = _safe_text(value, "display_name")
    if (
        "/" in text
        or "\\" in text
        or text.casefold() in {"raw", "snapshot", "snapshots", ".portfolio-maker", "portfolio.db"}
        or _contains_unsafe_locator(text)
    ):
        raise ProjectBoundaryError("display_name contains unsafe locator")
    return text


def _safe_relative_hierarchy(value: object) -> str:
    if not isinstance(value, str) or value == ".":
        if value == ".":
            return value
        raise ProjectBoundaryError("relative_hierarchy is invalid")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise ProjectBoundaryError("relative_hierarchy is invalid")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProjectBoundaryError("relative_hierarchy is invalid")
    for part in parts:
        _safe_display_name(part)
    return value


def _safe_text_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or len(set(value)) != len(value):
        raise ProjectBoundaryError(f"{field} is invalid")
    return [_safe_semantic_text(item, field) for item in value]


def _safe_semantic_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    text = _safe_text(value, field)
    if _contains_unsafe_locator(text):
        raise ProjectBoundaryError(f"{field} contains unsafe locator")
    return text


def _safe_text(value: object, field: str) -> str:
    if not isinstance(value, str) or contains_unicode_control(value):
        raise ProjectBoundaryError(f"{field} contains unsafe text")
    normalized = normalize_label(mask_public_value(value))
    if not normalized or normalized != value or contains_hidden_secret_shaped_public_value(value):
        raise ProjectBoundaryError(f"{field} contains unsafe text")
    return normalized


def _contains_unsafe_locator(value: str) -> bool:
    folded = value.casefold()
    return bool(
        "file://" in folded
        or ".portfolio-maker" in folded
        or "portfolio.db" in folded
        or re.search(r"(?i)https?://", value)
        or re.search(
            r"(?i)(?<![A-Za-z0-9_.-])[A-Za-z0-9][A-Za-z0-9._-]*@"
            r"(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+:"
            r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:\.git)?\b",
            value,
        )
        or re.search(r"(?<![A-Za-z0-9_.-])(?:/|~[/\\]|[A-Za-z]:[/\\]|\\\\)", value)
        or re.search(r"(?i)(?:^|[/\\])(?:raw|snapshots?)(?:[/\\]|$)", value)
    )


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
