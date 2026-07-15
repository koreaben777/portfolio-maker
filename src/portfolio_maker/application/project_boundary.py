from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

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


_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9:_-]+")


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
        or re.search(r"(?<![A-Za-z0-9_.-])(?:/|~[/\\]|[A-Za-z]:[/\\]|\\\\)", value)
        or re.search(r"(?i)(?:^|[/\\])(?:raw|snapshots?)(?:[/\\]|$)", value)
    )


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
