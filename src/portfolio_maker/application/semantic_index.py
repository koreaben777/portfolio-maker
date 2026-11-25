from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlparse
from uuid import uuid4

from portfolio_maker.application.approval import (
    ApprovalFormatError,
    ApprovalMissingError,
    SourceApproval,
    load_approval,
)
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
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    read_managed_bytes,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.github_connector import contains_unicode_control
from portfolio_maker.infrastructure.policy import (
    FilePolicy,
    SourcePathPolicyError,
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.semantic_analyzers import (
    ANALYZER_VERSION,
    analyze_file_input,
)
from portfolio_maker.infrastructure.semantic_crawler import (
    StructuralCrawlRootError,
    StructuralEntry,
    crawl_local_structure,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import load_valid_local_snapshot
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
_SEMANTIC_TERM = re.compile(r"[^\W\d_][\w+#-]{2,}", re.UNICODE)
_SEMANTIC_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "into",
        "that",
        "the",
        "this",
        "with",
    }
)
_LOCATOR_TOKEN = re.compile(
    r"(?i)(?:file://|https?://|(?<![A-Za-z0-9_.-])(?:/|~[/\\\\]|[A-Za-z]:[/\\\\]|\\\\\\\\))[^\s'\"`<>)}\]]+"
)
_RELATIVE_FILE_LOCATOR = re.compile(
    r"(?i)(?<![\w.-])(?:\.{1,2}[/\\\\][^\s'\"`<>)}\]]+|(?:[\w-]+[/\\\\])+[\w-]+\.[a-z][a-z0-9_-]*)"
)
_CHUNK_FILENAME = re.compile(r"chunk-\d{4,}\.json")
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
    _validate_approved_root(request.root, approval)
    try:
        crawl = crawl_local_structure(request.root, approval)
    except StructuralCrawlRootError as error:
        raise SemanticIndexError("semantic index root is invalid") from error
    if not crawl.entries:
        raise SemanticIndexError("semantic index root contains no structural entries")

    revision_id = uuid4().hex
    source_id = crawl.entries[0].source_id
    policy_hash = _policy_hash(approval)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    approved_evidence_by_source_uri = _approved_local_evidence_by_source_uri(
        repository, approval
    )
    prepared_nodes = _prepare_nodes(crawl.entries, approved_evidence_by_source_uri)
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
        if manifest["policy_hash"] != _current_policy_hash(paths):
            raise SemanticIndexError("semantic index approval policy has changed")
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


def generate_semantic_outputs(workspace: Path) -> int:
    """Generate content-derived semantic output from managed input chunks only."""
    paths = WorkspacePaths.from_root(workspace)
    manifest = _load_streaming_input_manifest(paths)
    with TemporaryDirectory(prefix="portfolio-maker-semantic-") as temp_dir:
        connection = sqlite3.connect(Path(temp_dir) / "state.sqlite3")
        try:
            _initialize_semantic_output_state(connection)
            chunk_paths = _verified_input_chunk_paths(paths, manifest)
            for input_path, input_text in chunk_paths:
                _stage_semantic_output_input(connection, input_text, manifest)
            processed_count = _process_semantic_output_state(connection)
            output_dir = paths.semantic_index_dir / "output"
            ensure_managed_directory(output_dir)
            for input_path, input_text in _verified_input_chunk_paths(paths, manifest):
                input_payload = json.loads(input_text)
                raw_nodes = input_payload["nodes"]
                assert isinstance(raw_nodes, list)
                output_nodes = [
                    _read_generated_output_node(connection, node["node_id"])
                    for node in raw_nodes
                ]
                payload: dict[str, object] = {
                    "version": 1,
                    "revision_id": manifest["revision_id"],
                    "input_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
                    "nodes": output_nodes,
                }
                payload["output_sha256"] = hashlib.sha256(
                    _canonical_json(payload).encode("utf-8")
                ).hexdigest()
                write_managed_text(output_dir / input_path.name, _canonical_json(payload))
        finally:
            connection.close()
    return processed_count


def _load_streaming_input_manifest(paths: WorkspacePaths) -> dict[str, object]:
    try:
        manifest_text = read_managed_bytes(paths.semantic_index_manifest_path).decode("utf-8")
    except (FileNotFoundError, UnicodeDecodeError) as error:
        raise SemanticIndexError("semantic index input is missing") from error
    return _parse_input_manifest(manifest_text)


def _verified_input_chunk_paths(
    paths: WorkspacePaths, manifest: dict[str, object]
) -> tuple[tuple[Path, str], ...]:
    chunk_sha256s = manifest["chunk_sha256s"]
    assert isinstance(chunk_sha256s, list)
    expected_paths = tuple(
        paths.semantic_index_input_dir / f"chunk-{index:04}.json"
        for index in range(1, len(chunk_sha256s) + 1)
    )
    try:
        actual_paths = tuple(paths.semantic_index_input_dir.glob("chunk-*.json"))
    except OSError as error:
        raise SemanticIndexError("semantic index input has unverified managed chunks") from error
    if set(actual_paths) != set(expected_paths) or any(
        _CHUNK_FILENAME.fullmatch(path.name) is None for path in actual_paths
    ):
        raise SemanticIndexError("semantic index input has unverified managed chunks")
    verified: list[tuple[Path, str]] = []
    for path, digest in zip(expected_paths, chunk_sha256s, strict=True):
        try:
            text = read_managed_bytes(path).decode("utf-8")
        except (FileNotFoundError, UnicodeDecodeError, OSError) as error:
            raise SemanticIndexError(
                "semantic index input has unverified managed chunks"
            ) from error
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            raise SemanticIndexError("semantic index input has unverified managed chunks")
        verified.append((path, text))
    return tuple(verified)


def _initialize_semantic_output_state(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE nodes (
            node_id TEXT PRIMARY KEY,
            parent_node_id TEXT,
            kind TEXT NOT NULL,
            relative_hierarchy TEXT NOT NULL,
            masked_excerpt TEXT NOT NULL,
            input_roles_json TEXT NOT NULL,
            analysis_status TEXT NOT NULL,
            child_node_ids_json TEXT NOT NULL,
            pending_children INTEGER NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,
            semantic_summary TEXT,
            semantic_roles_json TEXT,
            topics_json TEXT
        );
        CREATE INDEX nodes_parent_idx ON nodes(parent_node_id);
        CREATE TABLE ready_nodes (
            node_id TEXT PRIMARY KEY
        );
        """
    )


def _stage_semantic_output_input(
    connection: sqlite3.Connection,
    input_text: str,
    manifest: dict[str, object],
) -> None:
    try:
        payload = json.loads(input_text)
    except json.JSONDecodeError as error:
        raise SemanticIndexError("semantic index input is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {"version", "revision_id", "nodes"}:
        raise SemanticIndexError("semantic index input is invalid")
    if payload.get("version") != 1 or payload.get("revision_id") != manifest["revision_id"]:
        raise SemanticIndexError("semantic index input is invalid")
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise SemanticIndexError("semantic index input is invalid")
    rows = []
    for node in raw_nodes:
        _validate_input_node(node)
        assert isinstance(node, dict)
        child_node_ids = node["child_node_ids"]
        assert isinstance(child_node_ids, list)
        rows.append(
            (
                node["node_id"],
                node["parent_node_id"],
                node["kind"],
                node["relative_hierarchy"],
                node["masked_excerpt"],
                json.dumps(node["roles"], ensure_ascii=False),
                node["analysis_status"],
                json.dumps(child_node_ids, ensure_ascii=False),
                len(child_node_ids),
            )
        )
    try:
        connection.executemany(
            """
            INSERT INTO nodes
                (node_id, parent_node_id, kind, relative_hierarchy, masked_excerpt,
                 input_roles_json, analysis_status, child_node_ids_json, pending_children)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        raise SemanticIndexError("semantic index input has duplicate node IDs") from error


def _process_semantic_output_state(connection: sqlite3.Connection) -> int:
    connection.execute(
        "INSERT INTO ready_nodes SELECT node_id FROM nodes WHERE pending_children = 0"
    )
    connection.commit()
    processed_count = 0
    while True:
        ready = connection.execute(
            "SELECT node_id FROM ready_nodes ORDER BY node_id LIMIT 256"
        ).fetchall()
        if not ready:
            break
        for (node_id,) in ready:
            connection.execute("DELETE FROM ready_nodes WHERE node_id = ?", (node_id,))
            row = connection.execute(
                """
                SELECT node_id, parent_node_id, kind, relative_hierarchy, masked_excerpt,
                       input_roles_json, analysis_status, child_node_ids_json, processed
                FROM nodes WHERE node_id = ?
                """,
                (node_id,),
            ).fetchone()
            if row is None or row[8] != 0:
                raise SemanticIndexError("semantic output node was processed more than once")
            input_node = {
                "node_id": row[0],
                "parent_node_id": row[1],
                "kind": row[2],
                "relative_hierarchy": row[3],
                "masked_excerpt": row[4],
                "roles": json.loads(row[5]),
                "analysis_status": row[6],
                "child_node_ids": json.loads(row[7]),
            }
            summary, roles, topics = _generate_node_semantics(connection, input_node)
            connection.execute(
                """
                UPDATE nodes
                SET processed = 1, semantic_summary = ?, semantic_roles_json = ?, topics_json = ?
                WHERE node_id = ? AND processed = 0
                """,
                (
                    summary,
                    json.dumps(roles, ensure_ascii=False),
                    json.dumps(topics, ensure_ascii=False),
                    node_id,
                ),
            )
            processed_count += 1
            parent_node_id = row[1]
            if parent_node_id is not None:
                updated = connection.execute(
                    """
                    UPDATE nodes
                    SET pending_children = pending_children - 1
                    WHERE node_id = ? AND pending_children > 0
                    """,
                    (parent_node_id,),
                )
                if updated.rowcount != 1:
                    raise SemanticIndexError("semantic output parent graph is invalid")
                pending = connection.execute(
                    "SELECT pending_children FROM nodes WHERE node_id = ?",
                    (parent_node_id,),
                ).fetchone()
                if pending is None:
                    raise SemanticIndexError("semantic output parent graph is invalid")
                if pending[0] == 0:
                    connection.execute(
                        "INSERT OR IGNORE INTO ready_nodes (node_id) VALUES (?)",
                        (parent_node_id,),
                    )
        connection.commit()
    total = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    if processed_count != total:
        raise SemanticIndexError("semantic output graph could not be processed bottom-up")
    return processed_count


def _generate_node_semantics(
    connection: sqlite3.Connection,
    input_node: dict[str, object],
) -> tuple[str, list[str], list[str]]:
    status = str(input_node["analysis_status"])
    input_roles = [str(role) for role in input_node["roles"]]
    if status in {AnalysisStatus.UNSUPPORTED.value, AnalysisStatus.UNREADABLE.value}:
        return "", input_roles, []
    if input_node["kind"] == SemanticNodeKind.FILE.value:
        return _generate_content_semantics(input_node)
    child_rows = connection.execute(
        """
        SELECT semantic_roles_json, topics_json
        FROM nodes
        WHERE parent_node_id = ? AND processed = 1
        """,
        (input_node["node_id"],),
    ).fetchall()
    topics = sorted(
        {
            str(topic)
            for row in child_rows
            for topic in json.loads(row[1] or "[]")
        }
    )
    roles = sorted(
        set(input_roles)
        | {
            str(role)
            for row in child_rows
            for role in json.loads(row[0] or "[]")
        }
    )
    summary = (
        f"Combines child signals for {', '.join(topics)}."
        if topics
        else "No analyzable child signal was found."
    )
    return summary, roles, topics


def _generate_content_semantics(
    input_node: dict[str, object],
) -> tuple[str, list[str], list[str]]:
    terms = sorted(_semantic_terms(str(input_node["masked_excerpt"])))
    summary = (
        f"Discusses {', '.join(terms)}."
        if terms
        else "Managed content has no extractable topic."
    )
    roles = sorted(
        {str(role) for role in input_node["roles"]}
        | {_ROLE_BY_TERM[term] for term in terms if term in _ROLE_BY_TERM}
    )
    return summary, roles, terms


def _read_generated_output_node(
    connection: sqlite3.Connection, node_id: str
) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT node_id, semantic_summary, semantic_roles_json, topics_json,
               analysis_status, child_node_ids_json, processed
        FROM nodes WHERE node_id = ?
        """,
        (node_id,),
    ).fetchone()
    if row is None or row[6] != 1:
        raise SemanticIndexError("semantic output node is missing")
    return {
        "node_id": row[0],
        "semantic_summary": row[1] or "",
        "semantic_roles": json.loads(row[2] or "[]"),
        "topics": json.loads(row[3] or "[]"),
        "analysis_status": row[4],
        "child_node_ids": json.loads(row[5]),
    }


_ROLE_BY_TERM = {
    "analysis": "analysis",
    "api": "api",
    "backend": "backend",
    "database": "database",
    "deploy": "deployment",
    "deployment": "deployment",
    "frontend": "frontend",
    "guide": "documentation",
    "model": "modeling",
    "portfolio": "portfolio",
    "python": "python",
    "rag": "retrieval",
    "setup": "setup",
    "test": "testing",
    "testing": "testing",
    "typescript": "typescript",
}


def _generate_output_nodes(
    input_nodes: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    generated: dict[str, dict[str, object]] = {}
    ordered_nodes = sorted(
        input_nodes.items(),
        key=lambda item: (
            -_hierarchy_depth(str(item[1]["relative_hierarchy"])),
            str(item[1]["relative_hierarchy"]),
            item[0],
        ),
    )
    for node_id, input_node in ordered_nodes:
        status = str(input_node["analysis_status"])
        if status in {AnalysisStatus.UNSUPPORTED.value, AnalysisStatus.UNREADABLE.value}:
            summary = ""
            roles = list(input_node["roles"])
            topics: list[str] = []
        elif input_node["kind"] == SemanticNodeKind.FILE.value:
            terms = sorted(_semantic_terms(str(input_node["masked_excerpt"])))
            summary = (
                f"Discusses {', '.join(terms)}."
                if terms
                else "Managed content has no extractable topic."
            )
            topics = terms
            roles = sorted(
                set(str(role) for role in input_node["roles"])
                | {_ROLE_BY_TERM[term] for term in terms if term in _ROLE_BY_TERM}
            )
        else:
            child_nodes = [generated[child_id] for child_id in input_node["child_node_ids"]]
            topics = sorted(
                {
                    str(topic)
                    for child in child_nodes
                    for topic in child["topics"]
                }
            )
            roles = sorted(
                {
                    str(role)
                    for child in child_nodes
                    for role in child["semantic_roles"]
                }
                | {str(role) for role in input_node["roles"]}
            )
            summary = (
                f"Combines child signals for {', '.join(topics)}."
                if topics
                else "No analyzable child signal was found."
            )

        generated[node_id] = {
            "node_id": node_id,
            "semantic_summary": _safe_output_text(summary, "semantic summary") if summary else "",
            "semantic_roles": [
                _safe_output_text(role, "semantic_roles") for role in roles
            ],
            "topics": [_safe_output_text(topic, "topics") for topic in topics],
            "analysis_status": status,
            "child_node_ids": list(input_node["child_node_ids"]),
        }
    return generated


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
    children_by_parent: dict[str, list[str]] = {}
    for child_id, child in input_nodes.items():
        parent_node_id = child["parent_node_id"]
        if isinstance(parent_node_id, str):
            children_by_parent.setdefault(parent_node_id, []).append(child_id)
    for child_ids in children_by_parent.values():
        child_ids.sort()
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
        expected_children = children_by_parent.get(node_id, [])
        if input_node["child_node_ids"] != expected_children:
            raise SemanticIndexError("semantic index input has invalid child references")


def _parse_output_chunks(
    paths: WorkspacePaths,
    published: _PublishedSemanticInput,
    manifest: dict[str, object],
    input_nodes: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    output_dir = paths.semantic_index_dir / "output"
    try:
        if not output_dir.exists():
            raise SemanticIndexError("semantic index output is missing")
    except OSError as error:
        raise SemanticIndexError("semantic index output is invalid") from error
    expected_paths = {
        output_dir / path.name
        for path in published.chunk_texts
    }
    try:
        actual_paths = set(output_dir.glob("chunk-*.json"))
    except OSError as error:
        raise SemanticIndexError("semantic index output is invalid") from error
    if actual_paths != expected_paths or any(
        _CHUNK_FILENAME.fullmatch(path.name) is None for path in actual_paths
    ):
        raise SemanticIndexError("semantic index output is incomplete")

    output_nodes: dict[str, dict[str, object]] = {}
    for input_path, input_text in published.chunk_texts.items():
        output_path = output_dir / input_path.name
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
    _validate_semantic_quality(output_nodes, input_nodes)
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


def _validate_semantic_quality(
    output_nodes: dict[str, dict[str, object]],
    input_nodes: dict[str, dict[str, object]],
) -> None:
    content_node_ids = [
        node_id
        for node_id, node in input_nodes.items()
        if (
            node["kind"] == SemanticNodeKind.FILE.value
            and node["analysis_status"]
            not in {AnalysisStatus.UNSUPPORTED.value, AnalysisStatus.UNREADABLE.value}
            and isinstance(node["masked_excerpt"], str)
            and _semantic_terms(node["masked_excerpt"])
        )
    ]
    if len(content_node_ids) > 1:
        summaries = {
            output_nodes[node_id]["semantic_summary"] for node_id in content_node_ids
        }
        topics = {
            tuple(output_nodes[node_id]["topics"]) for node_id in content_node_ids
        }
        if len(summaries) == 1 and len(topics) == 1:
            raise SemanticIndexError(
                "semantic quality is indistinguishable across content-bearing nodes"
            )

    for node_id in content_node_ids:
        input_terms = _semantic_terms(str(input_nodes[node_id]["masked_excerpt"]))
        output = output_nodes[node_id]
        output_terms = _semantic_terms(
            " ".join(
                [
                    str(output["semantic_summary"]),
                    *[str(topic) for topic in output["topics"]],
                ]
            )
        )
        if not input_terms.intersection(output_terms):
            raise SemanticIndexError(
                "semantic quality does not reflect managed content"
            )

    for node_id, input_node in input_nodes.items():
        if input_node["kind"] != SemanticNodeKind.DIRECTORY.value:
            continue
        child_terms = set()
        for child_id in input_node["child_node_ids"]:
            child = output_nodes.get(child_id)
            if child is None:
                continue
            child_terms.update(
                _semantic_terms(
                    " ".join(
                        [
                            str(child["semantic_summary"]),
                            *[str(topic) for topic in child["topics"]],
                        ]
                    )
                )
            )
        if not child_terms:
            continue
        directory = output_nodes[node_id]
        directory_terms = _semantic_terms(
            " ".join(
                [
                    str(directory["semantic_summary"]),
                    *[str(topic) for topic in directory["topics"]],
                ]
            )
        )
        if not child_terms.intersection(directory_terms):
            raise SemanticIndexError(
                "semantic quality does not propagate child meaning to directory"
            )


def _semantic_terms(value: str) -> set[str]:
    return {
        term.casefold()
        for term in _SEMANTIC_TERM.findall(value)
        if term.casefold() not in _SEMANTIC_STOPWORDS
    }


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
        or _RELATIVE_FILE_LOCATOR.search(value)
        or "file://" in folded
        or ".portfolio-maker" in folded
        or "portfolio.db" in folded
        or re.search(r"(?i)https?://", value)
    )


def _hierarchy_depth(relative_hierarchy: str) -> int:
    return 0 if relative_hierarchy == "." else relative_hierarchy.count("/") + 1


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _prepare_nodes(
    entries: tuple[StructuralEntry, ...],
    approved_evidence_by_source_uri: dict[str, tuple[int, ...]],
) -> tuple[_PreparedNode, ...]:
    structural_entries = entries
    child_node_ids: dict[str, list[str]] = {}
    for entry in structural_entries:
        parent_node_id = entry.parent_node_id
        if parent_node_id is not None:
            child_node_ids.setdefault(parent_node_id, []).append(entry.node_id)

    prepared: list[_PreparedNode] = []
    for entry in structural_entries:
        if (
            entry.kind is SemanticNodeKind.FILE
            and entry.status is not AnalysisStatus.UNSUPPORTED
        ):
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
        evidence_ids: tuple[int, ...] = ()
        if (
            entry.kind is SemanticNodeKind.FILE
            and entry.status is not AnalysisStatus.UNSUPPORTED
        ):
            try:
                evidence_ids = approved_evidence_by_source_uri.get(
                    _canonical_file_uri(entry.absolute_path), ()
                )
            except (OSError, RuntimeError):
                evidence_ids = ()
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
            evidence_ids=evidence_ids,
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


def _approved_local_evidence_by_source_uri(
    repository: SQLiteRepository,
    approval: SourceApproval,
) -> dict[str, tuple[int, ...]]:
    approved_source_uris = set(approval.approved_source_uris)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    snapshots = repository.latest_snapshot_metadata_by_source_id()
    current_sources: dict[int, tuple[str, str]] = {}
    for source in repository.list_sources(status=SourceStatus.INGESTED):
        if (
            source.id is None
            or source.type is not SourceType.LOCAL_FILE
            or source.uri not in approved_source_uris
        ):
            continue
        try:
            source_path, extracted = extract_approved_text(source.uri, policy)
        except FileNotFoundError:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        except SourcePathPolicyError:
            repository.update_source_status(source.id, SourceStatus.SKIPPED_POLICY)
            continue
        except OSError:
            repository.update_source_status(source.id, SourceStatus.EXTRACT_FAILED)
            continue

        snapshot = snapshots.get(source.id)
        if (
            snapshot is None
            or snapshot[2] != extracted.content_hash
            or snapshot[3] != extracted.extractor
            or load_valid_local_snapshot(
                snapshot[1],
                source.id,
                source.uri,
                source_path.name,
                extracted,
            )
            is None
        ):
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        current_sources[source.id] = (source.uri, snapshot[2])

    evidence_by_source_uri: dict[str, set[int]] = {}
    for record in repository.list_evidence_selection_records():
        current_source = current_sources.get(record.source_id or -1)
        if current_source is None:
            continue
        source_uri, content_hash = current_source
        if (
            record.source_type != SourceType.LOCAL_FILE.value
            or record.source_uri != source_uri
            or record.activity_id is not None
            or record.evidence_locator != source_uri
            or record.evidence_stable_id
            != f"source-snapshot:{record.source_id}:{content_hash}"
        ):
            continue
        canonical_source_uri = _canonical_file_uri_from_uri(source_uri)
        if canonical_source_uri is not None:
            evidence_by_source_uri.setdefault(canonical_source_uri, set()).add(
                record.evidence_id
            )
    return {
        source_uri: tuple(sorted(evidence_ids))
        for source_uri, evidence_ids in evidence_by_source_uri.items()
    }


def _canonical_file_uri(path: Path) -> str:
    return path.resolve(strict=True).as_uri()


def _canonical_file_uri_from_uri(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc:
        return None
    try:
        canonical_uri = _canonical_file_uri(Path(unquote(parsed.path)))
    except (OSError, RuntimeError):
        return None
    return canonical_uri if canonical_uri == uri else None


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


def _validate_approved_root(root: Path, approval: SourceApproval) -> None:
    try:
        canonical_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SemanticIndexError("semantic index root is invalid") from error
    if not canonical_root.is_dir():
        raise SemanticIndexError("semantic index root is invalid")

    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    if (
        policy.classify_path(canonical_root) != "candidate"
        or canonical_root.as_uri() not in approval.approved_source_uris
    ):
        raise SemanticIndexError("semantic index root is not approved")


def _current_policy_hash(paths: WorkspacePaths) -> str:
    try:
        approval = load_approval(paths)
    except (ApprovalMissingError, ApprovalFormatError) as error:
        raise SemanticIndexError("semantic index approval is unavailable") from error
    return _policy_hash(approval)


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
