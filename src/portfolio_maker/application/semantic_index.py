from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    ApplySemanticIndexResult,
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
    read_managed_bytes,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.github_connector import contains_unicode_control
from portfolio_maker.infrastructure.policy import (
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)
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
_CHUNK_FILENAME = re.compile(r"chunk-\d{4}\.json")
_SHA256 = re.compile(r"[0-9a-f]{64}")


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
    try:
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
        _publish_semantic_input(
            repository, paths, chunk_texts, _canonical_json(manifest)
        )
    except Exception:
        repository.fail_semantic_revision(revision_id)
        raise

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


def apply_semantic_index(
    request: ApplySemanticIndexRequest,
) -> ApplySemanticIndexResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    with repository._repository_critical_section():
        published = _read_published_semantic_input_unlocked(paths)
        manifest = _parse_input_manifest(published.manifest_text)
        input_nodes = _parse_input_chunks(published, manifest)
        staged_nodes = repository.list_semantic_nodes(manifest["revision_id"])
        _validate_staged_structure(input_nodes, staged_nodes, manifest)
        output_nodes = _parse_output_chunks(paths, published, manifest, input_nodes)
        replacement_nodes = _replacement_nodes(
            output_nodes, input_nodes, staged_nodes
        )
        repository.replace_and_activate_semantic_revision(
            manifest["revision_id"], replacement_nodes
        )

    statuses = [node.analysis_status for node in replacement_nodes]
    return ApplySemanticIndexResult(
        revision_id=manifest["revision_id"],
        active=True,
        complete_count=sum(status is AnalysisStatus.COMPLETE for status in statuses),
        partial_count=sum(status is AnalysisStatus.PARTIAL for status in statuses),
        failed_count=sum(status is AnalysisStatus.FAILED for status in statuses),
    )


def _parse_input_manifest(manifest_text: str | None) -> dict[str, object]:
    if manifest_text is None:
        raise SemanticIndexError("semantic index input is missing")
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as error:
        raise SemanticIndexError("semantic index input is invalid") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "version",
        "revision_id",
        "source_id",
        "policy_hash",
        "analyzer_version",
        "chunk_sha256s",
        "node_count",
    }:
        raise SemanticIndexError("semantic index input is invalid")
    if (
        manifest.get("version") != 1
        or not isinstance(manifest.get("revision_id"), str)
        or not isinstance(manifest.get("source_id"), str)
        or not isinstance(manifest.get("analyzer_version"), str)
        or not _is_sha256(manifest.get("policy_hash"))
        or not isinstance(manifest.get("node_count"), int)
        or isinstance(manifest.get("node_count"), bool)
        or manifest["node_count"] <= 0
    ):
        raise SemanticIndexError("semantic index input is invalid")
    chunk_sha256s = manifest.get("chunk_sha256s")
    if not isinstance(chunk_sha256s, list) or not chunk_sha256s or not all(
        _is_sha256(value) for value in chunk_sha256s
    ):
        raise SemanticIndexError("semantic index input is invalid")
    return manifest


def _parse_input_chunks(
    published: _PublishedSemanticInput, manifest: dict[str, object]
) -> dict[str, dict[str, object]]:
    input_nodes: dict[str, dict[str, object]] = {}
    for chunk_text in published.chunk_texts.values():
        try:
            payload = json.loads(chunk_text)
        except json.JSONDecodeError as error:
            raise SemanticIndexError("semantic index input is invalid") from error
        if not isinstance(payload, dict) or set(payload) != {
            "version",
            "revision_id",
            "nodes",
        }:
            raise SemanticIndexError("semantic index input is invalid")
        if payload.get("version") != 1 or payload.get("revision_id") != manifest["revision_id"]:
            raise SemanticIndexError("semantic index input is invalid")
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise SemanticIndexError("semantic index input is invalid")
        for node in raw_nodes:
            _validate_input_node(node)
            assert isinstance(node, dict)
            node_id = node["node_id"]
            assert isinstance(node_id, str)
            if node_id in input_nodes:
                raise SemanticIndexError("semantic index input is invalid")
            input_nodes[node_id] = node
    if len(input_nodes) != manifest["node_count"]:
        raise SemanticIndexError("semantic index input is invalid")
    return input_nodes


def _validate_input_node(node: object) -> None:
    required = {
        "node_id",
        "parent_node_id",
        "kind",
        "display_name",
        "relative_hierarchy",
        "content_fingerprint",
        "roles",
        "masked_excerpt",
        "analysis_status",
        "child_node_ids",
    }
    if not isinstance(node, dict) or set(node) != required:
        raise SemanticIndexError("semantic index input is invalid")
    if (
        not isinstance(node.get("node_id"), str)
        or not isinstance(node.get("kind"), str)
        or not isinstance(node.get("display_name"), str)
        or not isinstance(node.get("relative_hierarchy"), str)
        or not isinstance(node.get("masked_excerpt"), str)
        or node.get("parent_node_id") is not None
        and not isinstance(node.get("parent_node_id"), str)
        or node.get("content_fingerprint") is not None
        and not isinstance(node.get("content_fingerprint"), str)
        or node.get("kind") not in {kind.value for kind in SemanticNodeKind}
        or node.get("analysis_status") not in {status.value for status in AnalysisStatus}
    ):
        raise SemanticIndexError("semantic index input is invalid")
    _string_list(node.get("roles"), "semantic index input")
    _string_list(node.get("child_node_ids"), "semantic index input")


def _validate_staged_structure(
    input_nodes: dict[str, dict[str, object]],
    staged_nodes: list[dict[str, object]],
    manifest: dict[str, object],
) -> None:
    staged_by_id = {str(node["node_id"]): node for node in staged_nodes}
    if len(staged_by_id) != len(staged_nodes) or set(staged_by_id) != set(input_nodes):
        raise SemanticIndexError("semantic index input does not match the staged revision")
    for node_id, input_node in input_nodes.items():
        staged = staged_by_id[node_id]
        if (
            staged["source_id"] != manifest["source_id"]
            or staged["node_kind"] != input_node["kind"]
            or staged["parent_node_id"] != input_node["parent_node_id"]
            or staged["display_name"] != input_node["display_name"]
            or staged["relative_hierarchy"] != input_node["relative_hierarchy"]
            or staged["content_fingerprint"] != input_node["content_fingerprint"]
            or staged["semantic_roles"] != input_node["roles"]
            or staged["analysis_status"] != input_node["analysis_status"]
            or staged["analyzer_version"] != manifest["analyzer_version"]
        ):
            raise SemanticIndexError("semantic index input does not match the staged revision")
        expected_children = sorted(
            child_id
            for child_id, child in input_nodes.items()
            if child["parent_node_id"] == node_id
        )
        if input_node["child_node_ids"] != expected_children:
            raise SemanticIndexError("semantic index input has invalid child references")


def _parse_output_chunks(
    paths: WorkspacePaths,
    published: _PublishedSemanticInput,
    manifest: dict[str, object],
    input_nodes: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    expected_paths = {
        paths.semantic_index_dir / "output" / path.name
        for path in published.chunk_texts
    }
    try:
        actual_paths = set((paths.semantic_index_dir / "output").glob("chunk-*.json"))
    except OSError as error:
        raise SemanticIndexError("semantic index output is invalid") from error
    if actual_paths != expected_paths or any(
        _CHUNK_FILENAME.fullmatch(path.name) is None for path in actual_paths
    ):
        raise SemanticIndexError("semantic index output is incomplete")

    output_nodes: dict[str, dict[str, object]] = {}
    for input_path, input_text in published.chunk_texts.items():
        output_path = paths.semantic_index_dir / "output" / input_path.name
        try:
            output_text = read_managed_bytes(output_path).decode("utf-8")
            output = json.loads(output_text)
            input_payload = json.loads(input_text)
        except (FileNotFoundError, UnicodeDecodeError, OSError, json.JSONDecodeError) as error:
            raise SemanticIndexError("semantic index output is invalid") from error
        _validate_output_chunk(output, input_text, manifest)
        assert isinstance(output, dict)
        assert isinstance(input_payload, dict)
        output_chunk_nodes = output["nodes"]
        input_chunk_ids = {node["node_id"] for node in input_payload["nodes"]}
        assert isinstance(output_chunk_nodes, list)
        if len(output_chunk_nodes) != len(input_chunk_ids):
            raise SemanticIndexError("semantic index output has invalid node coverage")
        for output_node in output_chunk_nodes:
            _validate_output_node(output_node)
            assert isinstance(output_node, dict)
            node_id = output_node["node_id"]
            assert isinstance(node_id, str)
            if node_id in output_nodes or node_id not in input_chunk_ids:
                raise SemanticIndexError("semantic index output has invalid node coverage")
            input_node = input_nodes[node_id]
            if (
                output_node["analysis_status"] != input_node["analysis_status"]
                or output_node["child_node_ids"] != input_node["child_node_ids"]
            ):
                raise SemanticIndexError("semantic index output has invalid structural references")
            _validate_output_semantics(output_node, input_node)
            output_nodes[node_id] = output_node
    if set(output_nodes) != set(input_nodes):
        raise SemanticIndexError("semantic index output has invalid node coverage")
    return output_nodes


def _validate_output_chunk(
    output: object, input_text: str, manifest: dict[str, object]
) -> None:
    if not isinstance(output, dict) or set(output) != {
        "version",
        "revision_id",
        "input_sha256",
        "nodes",
        "output_sha256",
    }:
        raise SemanticIndexError("semantic index output is invalid")
    expected_output_hash = hashlib.sha256(
        _canonical_json({key: value for key, value in output.items() if key != "output_sha256"}).encode(
            "utf-8"
        )
    ).hexdigest()
    if (
        output.get("version") != 1
        or output.get("revision_id") != manifest["revision_id"]
        or output.get("input_sha256") != hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        or not _is_sha256(output.get("output_sha256"))
        or output.get("output_sha256") != expected_output_hash
        or not isinstance(output.get("nodes"), list)
    ):
        raise SemanticIndexError("semantic index output is invalid")


def _validate_output_node(node: object) -> None:
    if not isinstance(node, dict) or set(node) != {
        "node_id",
        "semantic_summary",
        "semantic_roles",
        "topics",
        "analysis_status",
        "child_node_ids",
    }:
        raise SemanticIndexError("semantic index output is invalid")
    if (
        not isinstance(node.get("node_id"), str)
        or not isinstance(node.get("semantic_summary"), str)
        or node.get("analysis_status") not in {status.value for status in AnalysisStatus}
    ):
        raise SemanticIndexError("semantic index output is invalid")
    _string_list(node.get("semantic_roles"), "semantic index output")
    _string_list(node.get("topics"), "semantic index output")
    _string_list(node.get("child_node_ids"), "semantic index output")


def _validate_output_semantics(
    output_node: dict[str, object], input_node: dict[str, object]
) -> None:
    summary = output_node["semantic_summary"]
    assert isinstance(summary, str)
    status = input_node["analysis_status"]
    if not summary and status not in {
        AnalysisStatus.UNSUPPORTED.value,
        AnalysisStatus.UNREADABLE.value,
    }:
        raise SemanticIndexError("semantic index output has an unsafe summary")
    if summary:
        _safe_output_text(summary, "semantic summary")
    for field in ("semantic_roles", "topics"):
        values = output_node[field]
        assert isinstance(values, list)
        for value in values:
            _safe_output_text(value, field)


def _replacement_nodes(
    output_nodes: dict[str, dict[str, object]],
    input_nodes: dict[str, dict[str, object]],
    staged_nodes: list[dict[str, object]],
) -> tuple[SemanticNode, ...]:
    staged_by_id = {str(node["node_id"]): node for node in staged_nodes}
    nodes: list[SemanticNode] = []
    for node_id, input_node in input_nodes.items():
        staged = staged_by_id[node_id]
        output = output_nodes[node_id]
        summary = output["semantic_summary"]
        roles = output["semantic_roles"]
        topics = output["topics"]
        assert isinstance(summary, str)
        assert isinstance(roles, list)
        assert isinstance(topics, list)
        nodes.append(
            SemanticNode(
                node_id=node_id,
                source_id=str(staged["source_id"]),
                node_kind=SemanticNodeKind(str(staged["node_kind"])),
                parent_node_id=staged["parent_node_id"] if isinstance(staged["parent_node_id"], str) else None,
                display_name=str(staged["display_name"]),
                relative_hierarchy=str(staged["relative_hierarchy"]),
                content_fingerprint=staged["content_fingerprint"] if isinstance(staged["content_fingerprint"], str) else None,
                semantic_summary=_safe_output_text(summary, "semantic summary") if summary else "",
                semantic_roles=tuple(
                    _safe_output_text(value, "semantic_roles") for value in roles
                ),
                topics=tuple(_safe_output_text(value, "topics") for value in topics),
                evidence_ids=tuple(staged["evidence_ids"]),
                analysis_status=AnalysisStatus(str(input_node["analysis_status"])),
                analyzer_version=str(staged["analyzer_version"]),
                updated_at="",
            )
        )
    return tuple(
        sorted(
            nodes,
            key=lambda node: (-_hierarchy_depth(node.relative_hierarchy), node.relative_hierarchy, node.node_id),
        )
    )


def _string_list(value: object, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        raise SemanticIndexError(f"{context} requires unique text arrays")
    return value


def _safe_output_text(value: str, field: str) -> str:
    if (
        contains_unicode_control(value)
        or contains_hidden_secret_shaped_public_value(value)
        or mask_public_value(value) != value
        or _contains_unsafe_locator(value)
    ):
        raise SemanticIndexError(f"{field} contains unsafe text")
    normalized = normalize_label(value)
    if not normalized:
        raise SemanticIndexError(f"{field} contains unsafe text")
    return normalized


def _contains_unsafe_locator(value: str) -> bool:
    folded = value.casefold()
    return bool(
        _LOCATOR_VALUE.search(value)
        or _LOCATOR_TOKEN.search(value)
        or "file://" in folded
        or ".portfolio-maker" in folded
        or "portfolio.db" in folded
        or re.search(r"(?i)https?://", value)
    )


def _hierarchy_depth(relative_hierarchy: str) -> int:
    return 0 if relative_hierarchy == "." else relative_hierarchy.count("/") + 1


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


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


@dataclass(frozen=True)
class _PublishedSemanticInput:
    manifest_text: str | None
    chunk_texts: dict[Path, str]


def _publish_semantic_input(
    repository: SQLiteRepository,
    paths: WorkspacePaths,
    chunk_texts: tuple[str, ...],
    manifest_text: str,
) -> None:
    with repository._repository_critical_section():
        ensure_managed_directory(paths.semantic_index_dir)
        ensure_managed_directory(paths.semantic_index_input_dir)
        previous = _read_published_semantic_input_unlocked(paths)
        current = {
            paths.semantic_index_input_dir / f"chunk-{index:04}.json": chunk_text
            for index, chunk_text in enumerate(chunk_texts, start=1)
        }

        try:
            for path, chunk_text in current.items():
                write_managed_text(path, chunk_text)
            for path in previous.chunk_texts.keys() - current.keys():
                remove_managed_file(path)
            write_managed_text(paths.semantic_index_manifest_path, manifest_text)
        except Exception:
            _restore_published_semantic_input(paths, previous, current)
            raise


def _read_published_semantic_input(paths: WorkspacePaths) -> _PublishedSemanticInput:
    repository = SQLiteRepository(paths.db_path)
    with repository._repository_critical_section():
        return _read_published_semantic_input_unlocked(paths)


def _read_published_semantic_input_unlocked(
    paths: WorkspacePaths,
) -> _PublishedSemanticInput:
    try:
        manifest_text = read_managed_bytes(paths.semantic_index_manifest_path).decode("utf-8")
    except FileNotFoundError:
        if tuple(paths.semantic_index_input_dir.glob("chunk-*.json")):
            raise SemanticIndexError("semantic index input has unverified managed chunks")
        return _PublishedSemanticInput(manifest_text=None, chunk_texts={})

    try:
        manifest = json.loads(manifest_text)
        chunk_sha256s = manifest["chunk_sha256s"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise SemanticIndexError("semantic index manifest is invalid") from error
    if not isinstance(chunk_sha256s, list) or not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in chunk_sha256s
    ):
        raise SemanticIndexError("semantic index manifest is invalid")

    chunk_paths = tuple(
        paths.semantic_index_input_dir / f"chunk-{index:04}.json"
        for index in range(1, len(chunk_sha256s) + 1)
    )
    existing_chunk_paths = tuple(paths.semantic_index_input_dir.glob("chunk-*.json"))
    if set(existing_chunk_paths) != set(chunk_paths) or any(
        _CHUNK_FILENAME.fullmatch(path.name) is None for path in existing_chunk_paths
    ):
        raise SemanticIndexError("semantic index input has unverified managed chunks")

    chunk_texts: dict[Path, str] = {}
    for path, digest in zip(chunk_paths, chunk_sha256s, strict=True):
        try:
            chunk_text = read_managed_bytes(path).decode("utf-8")
        except (FileNotFoundError, UnicodeDecodeError) as error:
            raise SemanticIndexError("semantic index input has unverified managed chunks") from error
        if hashlib.sha256(chunk_text.encode("utf-8")).hexdigest() != digest:
            raise SemanticIndexError("semantic index input has unverified managed chunks")
        chunk_texts[path] = chunk_text
    return _PublishedSemanticInput(manifest_text=manifest_text, chunk_texts=chunk_texts)


def _restore_published_semantic_input(
    paths: WorkspacePaths,
    previous: _PublishedSemanticInput,
    current: dict[Path, str],
) -> None:
    for path, chunk_text in previous.chunk_texts.items():
        write_managed_text(path, chunk_text)
    for path in current.keys() - previous.chunk_texts.keys():
        remove_managed_file(path, missing_ok=True)
    if previous.manifest_text is None:
        remove_managed_file(paths.semantic_index_manifest_path, missing_ok=True)
    else:
        write_managed_text(paths.semantic_index_manifest_path, previous.manifest_text)


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
