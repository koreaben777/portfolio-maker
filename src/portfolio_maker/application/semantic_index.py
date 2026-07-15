from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import uuid4

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import (
    PrepareSemanticIndexRequest,
    PrepareSemanticIndexResult,
)
from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    SemanticEdge,
    SemanticNode,
    SemanticNodeKind,
)
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    write_managed_text,
)
from portfolio_maker.infrastructure.policy import mask_public_value
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.semantic_analyzers import (
    ANALYZER_VERSION,
    analyze_file_input,
)
from portfolio_maker.infrastructure.semantic_crawler import StructuralEntry, crawl_local_structure
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class SemanticIndexError(ValueError):
    pass


_LOCATOR_VALUE = re.compile(
    r"(?im)(\b[\w-]*(?:path|file|directory|dir|root|snapshot|database)[\w-]*\s*[:=]\s*)"
    r"(?:file://)?(?:/|~[/\\\\]|[A-Za-z]:[/\\\\]|\\\\\\\\)\S+"
)
_CREDENTIAL_VALUE = re.compile(
    r"(?im)(\b[\w-]*credential[\w-]*\s*[:=]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,}\]\n]+)"
)
_LOCATOR_TOKEN = re.compile(
    r"(?i)(?:file://|https?://|(?<![A-Za-z0-9_.-])(?:/|~[/\\\\]|[A-Za-z]:[/\\\\]|\\\\\\\\))[^\s'\"`<>)}\]]+"
)


@dataclass(frozen=True)
class _PreparedNode:
    node: SemanticNode
    locator: tuple[str, int | None, int | None]
    chunk_node: dict[str, object]


def prepare_semantic_index(
    request: PrepareSemanticIndexRequest,
) -> PrepareSemanticIndexResult:
    if not isinstance(request.chunk_size, int) or isinstance(request.chunk_size, bool) or request.chunk_size <= 0:
        raise SemanticIndexError("semantic index chunk size must be positive")

    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    crawl = crawl_local_structure(request.root, approval)
    if not crawl.entries:
        raise SemanticIndexError("semantic index root contains no structural entries")

    revision_id = uuid4().hex
    source_id = crawl.entries[0].source_id
    policy_hash = _policy_hash(approval)
    prepared_nodes = _prepare_nodes(crawl.entries)
    chunk_payloads = _chunk_payloads(
        revision_id,
        tuple(node.chunk_node for node in prepared_nodes),
        request.chunk_size,
    )
    chunk_texts = tuple(_canonical_json(payload) for payload in chunk_payloads)
    chunk_sha256s = tuple(
        hashlib.sha256(text.encode("utf-8")).hexdigest() for text in chunk_texts
    )
    manifest = {
        "version": 1,
        "revision_id": revision_id,
        "source_id": source_id,
        "policy_hash": policy_hash,
        "analyzer_version": ANALYZER_VERSION,
        "chunk_sha256s": list(chunk_sha256s),
        "node_count": len(prepared_nodes),
    }

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.create_semantic_revision(
        revision_id, source_id, policy_hash, ANALYZER_VERSION
    )
    repository.replace_semantic_revision_graph(
        revision_id,
        tuple(item.node for item in prepared_nodes),
        {item.node.node_id: item.locator for item in prepared_nodes},
        tuple(
            SemanticEdge(revision_id, item.node.parent_node_id, "contains", item.node.node_id)
            for item in prepared_nodes
            if item.node.parent_node_id is not None
        ),
    )

    ensure_managed_directory(paths.semantic_index_dir)
    ensure_managed_directory(paths.semantic_index_input_dir)
    for index, chunk_text in enumerate(chunk_texts, start=1):
        write_managed_text(
            paths.semantic_index_input_dir / f"chunk-{index:04}.json", chunk_text
        )
    write_managed_text(paths.semantic_index_manifest_path, _canonical_json(manifest))

    return PrepareSemanticIndexResult(
        manifest_path=paths.semantic_index_manifest_path,
        revision_id=revision_id,
        node_count=len(prepared_nodes),
        chunk_count=len(chunk_texts),
        partial_count=sum(
            item.node.analysis_status is AnalysisStatus.PARTIAL
            for item in prepared_nodes
        ),
    )


def _prepare_nodes(entries: tuple[StructuralEntry, ...]) -> tuple[_PreparedNode, ...]:
    structural_entries = entries
    child_node_ids: dict[str, list[str]] = {}
    for entry in structural_entries:
        parent_node_id = entry.parent_node_id
        if parent_node_id is not None:
            child_node_ids.setdefault(parent_node_id, []).append(entry.node_id)

    prepared: list[_PreparedNode] = []
    for entry in structural_entries:
        if entry.kind is SemanticNodeKind.FILE:
            analysis = analyze_file_input(entry)
            content_fingerprint = analysis.content_fingerprint
            semantic_roles = analysis.semantic_roles
            masked_excerpt = _redact_locators(analysis.masked_excerpt)
            status = analysis.status
        else:
            content_fingerprint = entry.content_fingerprint
            semantic_roles = ()
            masked_excerpt = ""
            status = entry.status
        node = SemanticNode(
            node_id=entry.node_id,
            source_id=entry.source_id,
            node_kind=entry.kind,
            parent_node_id=entry.parent_node_id,
            display_name=normalize_label(mask_public_value(entry.display_name)),
            relative_hierarchy=entry.relative_hierarchy,
            content_fingerprint=content_fingerprint,
            semantic_summary="",
            semantic_roles=semantic_roles,
            topics=(),
            evidence_ids=(),
            analysis_status=status,
            analyzer_version=ANALYZER_VERSION,
            updated_at="",
        )
        prepared.append(
            _PreparedNode(
                node=node,
                locator=(str(entry.absolute_path), entry.device, entry.inode),
                chunk_node={
                    "node_id": node.node_id,
                    "parent_node_id": node.parent_node_id,
                    "kind": node.node_kind.value,
                    "display_name": node.display_name,
                    "relative_hierarchy": node.relative_hierarchy,
                    "content_fingerprint": node.content_fingerprint,
                    "roles": list(node.semantic_roles),
                    "masked_excerpt": masked_excerpt,
                    "analysis_status": node.analysis_status.value,
                    "child_node_ids": sorted(child_node_ids.get(node.node_id, [])),
                },
            )
        )
    return tuple(prepared)


def _chunk_payloads(
    revision_id: str, nodes: tuple[dict[str, object], ...], chunk_size: int
) -> tuple[dict[str, object], ...]:
    return tuple(
        {"version": 1, "revision_id": revision_id, "nodes": list(nodes[index:index + chunk_size])}
        for index in range(0, len(nodes), chunk_size)
    )


def _policy_hash(approval: object) -> str:
    policy = {
        "approved_source_uris": sorted(approval.approved_source_uris),
        "excluded_directories": sorted(str(path) for path in approval.excluded_directories),
        "excluded_repositories": sorted(approval.excluded_repositories),
        "private_sources_allowed": approval.private_sources_allowed,
        "allowed_repositories": sorted(approval.allowed_repositories),
        "excluded_file_patterns": sorted(approval.excluded_file_patterns),
        "approved_github_activity_urls": sorted(approval.approved_github_activity_urls),
        "approved_private_github_activity_urls": sorted(
            approval.approved_private_github_activity_urls
        ),
    }
    return hashlib.sha256(_canonical_json(policy).encode("utf-8")).hexdigest()


def _redact_locators(value: str) -> str:
    value = _LOCATOR_VALUE.sub(r"\1[REDACTED]", value)
    value = _CREDENTIAL_VALUE.sub(r"\1[REDACTED]", value)
    return _LOCATOR_TOKEN.sub("[REDACTED]", value)


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
