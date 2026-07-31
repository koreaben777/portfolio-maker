from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Iterator

from portfolio_maker.application.approval import (
    ApprovalFormatError,
    ApprovalMissingError,
    load_approval,
)
from portfolio_maker.application.artifact_approval import load_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionError,
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.models import (
    BuildProfileRequest,
    PrepareProjectReviewRequest,
    PrepareProjectReviewResult,
)
from portfolio_maker.application.project_boundary import (
    ActiveSemanticRevisionMissing,
    ProjectBoundaryError,
    _analysis_status,
    _delivery_scope,
    _safe_display_name,
    _safe_identifier,
    _safe_relative_hierarchy,
    _safe_semantic_text,
    _safe_sha256,
    _safe_text_list,
)
from portfolio_maker.application.semantic_index import (
    SemanticIndexError,
    _activation_receipt_payload,
    _decode_canonical_object,
    _managed_regular_file_names,
    _parse_v2_final_manifest,
    _parse_v2_final_node,
    _policy_hash,
    _read_managed_content,
)
from portfolio_maker.domain.semantic_models import (
    SemanticNode,
    SemanticNodeDisposition,
    SemanticNodeKind,
)
from portfolio_maker.infrastructure.managed_files import (
    open_managed_directory,
    read_managed_bytes,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.sqlite_repository import (
    RepositoryError,
    SQLiteRepository,
)
from portfolio_maker.workspace import WorkspacePaths


PROJECT_REVIEW_CHUNK_BUDGET = 262_144
_CHUNK_NAME = re.compile(r"chunk-\d{4,}\.json")
_PROJECTED_KINDS = {
    SemanticNodeKind.SOURCE.value,
    SemanticNodeKind.DIRECTORY.value,
}
_CHUNK_KEYS = {
    "version",
    "protocol",
    "revision_id",
    "chunk_index",
    "nodes",
}
_MANIFEST_KEYS = {
    "version",
    "protocol",
    "artifact_kind",
    "delivery_scope",
    "active_revision_id",
    "source_policy_hash",
    "artifact_policy_hash",
    "final_manifest_sha256",
    "input_chunk_sha256s",
    "input_chunk_projected_node_counts",
    "chunk_count",
    "semantic_node_count",
    "projected_node_count",
    "condensed_node_count",
    "covered_node_count",
    "coverage_sha256",
    "selected_evidence_count",
    "manifest_sha256",
}


def prepare_project_review_v3(
    request: PrepareProjectReviewRequest,
) -> PrepareProjectReviewResult:
    paths = WorkspacePaths.from_root(request.workspace)
    try:
        paths.ensure()
        _validate_existing_projection(paths)
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        manifest_content = _read_managed_content(
            paths.semantic_final_dir / "manifest.json",
            "semantic final manifest",
        )
        manifest = _parse_v2_final_manifest(manifest_content)
        approval = load_approval(paths)
        source_policy_hash = _policy_hash(approval)
        if manifest["policy_hash"] != source_policy_hash:
            raise ProjectBoundaryError(
                "project review source policy has changed"
            )
        active = repository.get_active_semantic_revision(
            str(manifest["source_id"])
        )
        _validate_active_binding(
            paths,
            active,
            manifest,
            manifest_content,
        )
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
                current_approval=approval,
            ),
        )
        selected_evidence_ids = frozenset(selection.included_evidence_ids)
        with _ProjectReviewScratch() as scratch:
            coverage_sha256 = _load_final_nodes(
                paths,
                manifest,
                scratch,
                selected_evidence_ids,
            )
            _render_projection_chunks(
                manifest,
                scratch,
            )
            output_manifest = _project_review_manifest(
                manifest,
                manifest_content,
                selection.delivery_scope,
                selection.policy_hash,
                source_policy_hash,
                coverage_sha256,
                scratch,
            )
            _publish_projection(paths, scratch, output_manifest)
            return PrepareProjectReviewResult(
                input_path=paths.project_review_v3_manifest_path,
                evidence_count=scratch.selected_evidence_count(),
                chunk_count=scratch.output_chunk_count(),
            )
    except Exception as error:
        try:
            _invalidate_projection_manifest(paths)
        except ProjectBoundaryError as invalidation_error:
            raise invalidation_error from error
        if isinstance(error, ProjectBoundaryError):
            raise
        if isinstance(
            error,
            (
                ApprovalFormatError,
                ApprovalMissingError,
                EvidenceSelectionError,
                OSError,
                RepositoryError,
                SemanticIndexError,
                sqlite3.Error,
                UnicodeError,
            ),
        ):
            raise ProjectBoundaryError(
                "project review v3 input is invalid"
            ) from error
        raise


class _ProjectReviewScratch(AbstractContextManager["_ProjectReviewScratch"]):
    def __init__(self) -> None:
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._connection: sqlite3.Connection | None = None
        self._chunk_directory: Path | None = None

    def __enter__(self) -> "_ProjectReviewScratch":
        self._directory = tempfile.TemporaryDirectory(
            prefix="portfolio-maker-project-review-v3-"
        )
        directory = Path(self._directory.name)
        os.chmod(directory, 0o700)
        database_path = directory / "projection.db"
        descriptor = os.open(
            database_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
        self._connection = sqlite3.connect(database_path)
        self._connection.row_factory = sqlite3.Row
        _observe_project_review_scratch(
            directory,
            database_path,
        )
        self._connection.executescript(
            """
            CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY,
                parent_node_id TEXT,
                kind TEXT NOT NULL,
                relative_hierarchy TEXT NOT NULL UNIQUE,
                disposition TEXT NOT NULL,
                analysis_status TEXT NOT NULL,
                selected_evidence_json TEXT NOT NULL,
                projected_json TEXT
            );
            CREATE INDEX nodes_parent_idx ON nodes(parent_node_id);
            CREATE INDEX nodes_order_idx
                ON nodes(relative_hierarchy, node_id);
            CREATE TABLE selected_evidence (
                evidence_id INTEGER PRIMARY KEY
            );
            CREATE TABLE projected_evidence (
                node_id TEXT NOT NULL,
                evidence_id INTEGER NOT NULL,
                PRIMARY KEY (node_id, evidence_id)
            );
            CREATE TABLE output_chunks (
                chunk_index INTEGER PRIMARY KEY,
                file_name TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL,
                projected_node_count INTEGER NOT NULL,
                byte_count INTEGER NOT NULL
            );
            """
        )
        self._chunk_directory = directory / "chunks"
        self._chunk_directory.mkdir(mode=0o700)
        return self

    def __exit__(self, *args: object) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            if self._directory is not None:
                self._directory.cleanup()
                self._directory = None
                self._chunk_directory = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("project review scratch is not open")
        return self._connection

    @property
    def chunk_directory(self) -> Path:
        if self._chunk_directory is None:
            raise RuntimeError("project review scratch is not open")
        return self._chunk_directory

    def insert_node(
        self,
        node: SemanticNode,
        selected_evidence_ids: frozenset[int],
    ) -> None:
        selected = sorted(
            set(node.evidence_ids).intersection(selected_evidence_ids)
        )
        projected = (
            _projected_node_base(node, selected)
            if node.node_kind.value in _PROJECTED_KINDS
            else None
        )
        try:
            self.connection.execute(
                """
                INSERT INTO nodes (
                    node_id, parent_node_id, kind, relative_hierarchy,
                    disposition, analysis_status,
                    selected_evidence_json, projected_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.parent_node_id,
                    node.node_kind.value,
                    node.relative_hierarchy,
                    node.semantic_disposition.value,
                    node.analysis_status.value,
                    _canonical_json(selected),
                    _canonical_json(projected)
                    if projected is not None
                    else None,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ProjectBoundaryError(
                "semantic final node coverage is invalid"
            ) from error
        if selected:
            projected_owner = (
                node.node_id
                if projected is not None
                else self._nearest_projected_ancestor(
                    node.parent_node_id
                )
            )
            if projected_owner is None:
                raise ProjectBoundaryError(
                    "semantic final evidence binding is invalid"
                )
            for evidence_id in selected:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO projected_evidence(
                        node_id, evidence_id
                    )
                    VALUES (?, ?)
                    """,
                    (projected_owner, evidence_id),
                )
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO selected_evidence(evidence_id)
                    VALUES (?)
                    """,
                    (evidence_id,),
                )

    def _nearest_projected_ancestor(
        self,
        parent_node_id: str | None,
    ) -> str | None:
        current = parent_node_id
        while current is not None:
            row = self.connection.execute(
                """
                SELECT parent_node_id, projected_json
                FROM nodes
                WHERE node_id = ?
                """,
                (current,),
            ).fetchone()
            if row is None:
                return None
            if row["projected_json"] is not None:
                return current
            current = (
                str(row["parent_node_id"])
                if row["parent_node_id"] is not None
                else None
            )
        return None

    def node_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM nodes"
        ).fetchone()
        return int(row["count"])

    def projected_node_count(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM nodes
            WHERE projected_json IS NOT NULL
            """
        ).fetchone()
        return int(row["count"])

    def validate_tree(self, expected_count: int) -> None:
        roots = self.connection.execute(
            """
            SELECT node_id, kind, relative_hierarchy
            FROM nodes
            WHERE parent_node_id IS NULL
            """
        ).fetchall()
        missing_parent = self.connection.execute(
            """
            SELECT 1
            FROM nodes AS child
            LEFT JOIN nodes AS parent
              ON parent.node_id = child.parent_node_id
            WHERE child.parent_node_id IS NOT NULL
              AND parent.node_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if (
            len(roots) != 1
            or roots[0]["kind"] != SemanticNodeKind.SOURCE.value
            or roots[0]["relative_hierarchy"] != "."
            or missing_parent is not None
        ):
            raise ProjectBoundaryError(
                "semantic final node coverage is invalid"
            )
        reachable = self.connection.execute(
            """
            WITH RECURSIVE reachable(node_id) AS (
                SELECT node_id
                FROM nodes
                WHERE parent_node_id IS NULL
                UNION
                SELECT child.node_id
                FROM nodes AS child
                JOIN reachable
                  ON child.parent_node_id = reachable.node_id
            )
            SELECT COUNT(*) AS count FROM reachable
            """
        ).fetchone()
        if int(reachable["count"]) != expected_count:
            raise ProjectBoundaryError(
                "semantic final node coverage is invalid"
            )

    def projected_records(self) -> Iterator[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT node_id, projected_json
            FROM nodes
            WHERE projected_json IS NOT NULL
            ORDER BY relative_hierarchy, node_id
            """
        )
        for row in rows:
            base = json.loads(str(row["projected_json"]))
            if type(base) is not dict:
                raise ProjectBoundaryError(
                    "project review projection is invalid"
                )
            node_id = str(row["node_id"])
            child_digest = hashlib.sha256()
            child_digest.update(b"[")
            child_count = 0
            for child in self.connection.execute(
                """
                SELECT node_id
                FROM nodes
                WHERE parent_node_id = ?
                  AND projected_json IS NOT NULL
                ORDER BY relative_hierarchy, node_id
                """,
                (node_id,),
            ):
                if child_count:
                    child_digest.update(b",")
                child_digest.update(
                    _canonical_json(str(child["node_id"])).encode(
                        "utf-8"
                    )
                )
                child_count += 1
            child_digest.update(b"]")
            evidence_ids = {
                int(evidence["evidence_id"])
                for evidence in self.connection.execute(
                    """
                    SELECT evidence_id
                    FROM projected_evidence
                    WHERE node_id = ?
                    ORDER BY evidence_id
                    """,
                    (node_id,),
                )
            }
            coverage = base["coverage"]
            structural_only_count = int(
                coverage["structural_only_count"]
            ) - int(
                base["semantic_disposition"]
                == SemanticNodeDisposition.STRUCTURAL_ONLY.value
            )
            failed_count = int(coverage["failed_count"]) - int(
                base["analysis_status"] == "failed"
            )
            yield {
                **base,
                "selected_evidence_ids": sorted(evidence_ids),
                "direct_child_context": {
                    "projected_child_count": child_count,
                    "node_ids_sha256": child_digest.hexdigest(),
                },
                "descendant_coverage": {
                    "descendant_node_count": int(
                        coverage["node_count"]
                    )
                    - 1,
                    "structural_only_count": max(
                        structural_only_count,
                        0,
                    ),
                    "failed_count": max(failed_count, 0),
                    "node_ids_sha256": coverage[
                        "node_ids_sha256"
                    ],
                },
            }

    def record_output_chunk(
        self,
        *,
        chunk_index: int,
        path: Path,
        content: bytes,
        projected_node_count: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO output_chunks (
                chunk_index, file_name, sha256,
                projected_node_count, byte_count
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                chunk_index,
                path.name,
                hashlib.sha256(content).hexdigest(),
                projected_node_count,
                len(content),
            ),
        )

    def output_chunks(self) -> Iterator[sqlite3.Row]:
        return iter(
            self.connection.execute(
                """
                SELECT
                    chunk_index, file_name, sha256,
                    projected_node_count, byte_count
                FROM output_chunks
                ORDER BY chunk_index
                """
            )
        )

    def output_chunk_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM output_chunks"
        ).fetchone()
        return int(row["count"])

    def selected_evidence_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM selected_evidence"
        ).fetchone()
        return int(row["count"])


def _validate_active_binding(
    paths: WorkspacePaths,
    active: dict[str, str | None] | None,
    manifest: dict[str, object],
    manifest_content: bytes,
) -> None:
    if active is None:
        raise ActiveSemanticRevisionMissing(
            "no active protocol-v2 semantic revision"
        )
    if (
        active["id"] != manifest["revision_id"]
        or active["source_id"] != manifest["source_id"]
        or active["policy_hash"] != manifest["policy_hash"]
        or active["analyzer_version"] != manifest["analyzer_version"]
        or active["status"] != "active"
    ):
        raise ProjectBoundaryError(
            "active semantic revision binding is invalid"
        )
    target = _activation_receipt_payload(manifest, manifest_content)
    try:
        receipt_content = read_managed_bytes(
            paths.semantic_activation_receipt_path
        )
    except FileNotFoundError as error:
        raise ActiveSemanticRevisionMissing(
            "active semantic revision receipt is missing"
        ) from error
    receipt = _decode_canonical_object(
        receipt_content,
        "semantic activation receipt",
    )
    if receipt != target:
        raise ProjectBoundaryError(
            "active semantic revision receipt is invalid"
        )


def _load_final_nodes(
    paths: WorkspacePaths,
    manifest: dict[str, object],
    scratch: _ProjectReviewScratch,
    selected_evidence_ids: frozenset[int],
) -> str:
    expected_names = {
        "manifest.json",
        *{
            f"chunk-{index:04}.json"
            for index in range(1, int(manifest["chunk_count"]) + 1)
        },
    }
    if _managed_regular_file_names(paths.semantic_final_dir) != expected_names:
        raise ProjectBoundaryError("semantic final output is invalid")

    node_id_digest = hashlib.sha256()
    node_id_digest.update(b"[")
    coverage_digest = hashlib.sha256()
    coverage_digest.update(b"[")
    first_node = True
    previous_key: tuple[str, str] | None = None
    analysis_count = 0
    chunk_hashes = manifest["chunk_sha256s"]
    chunk_counts = manifest["chunk_node_counts"]
    assert isinstance(chunk_hashes, list)
    assert isinstance(chunk_counts, list)
    for index, (expected_hash, expected_count) in enumerate(
        zip(chunk_hashes, chunk_counts, strict=True),
        start=1,
    ):
        content = _read_managed_content(
            paths.semantic_final_dir / f"chunk-{index:04}.json",
            "semantic final output",
        )
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ProjectBoundaryError("semantic final output is invalid")
        chunk = _decode_canonical_object(
            content,
            "semantic final output",
        )
        raw_nodes = chunk.get("nodes")
        if (
            set(chunk) != _CHUNK_KEYS
            or chunk.get("version") != 2
            or chunk.get("protocol") != "codex-semantic-v2"
            or chunk.get("revision_id") != manifest["revision_id"]
            or chunk.get("chunk_index") != index
            or type(raw_nodes) is not list
            or not raw_nodes
            or len(raw_nodes) > 1_000
            or len(raw_nodes) != expected_count
        ):
            raise ProjectBoundaryError("semantic final output is invalid")
        for raw_node in raw_nodes:
            node = _parse_v2_final_node(raw_node, manifest)
            key = (node.relative_hierarchy, node.node_id)
            if previous_key is not None and key <= previous_key:
                raise ProjectBoundaryError(
                    "semantic final node ordering is invalid"
                )
            previous_key = key
            if not first_node:
                node_id_digest.update(b",")
                coverage_digest.update(b",")
            node_id_digest.update(
                _canonical_json(node.node_id).encode("utf-8")
            )
            coverage_digest.update(
                _canonical_json(
                    {
                        "analysis_status": node.analysis_status.value,
                        "kind": node.node_kind.value,
                        "node_id": node.node_id,
                        "node_ids_sha256": (
                            node.coverage.node_ids_sha256
                        ),
                        "parent_node_id": node.parent_node_id,
                        "semantic_disposition": (
                            node.semantic_disposition.value
                        ),
                    }
                ).encode("utf-8")
            )
            first_node = False
            analysis_count += int(
                node.semantic_disposition
                is SemanticNodeDisposition.ANALYZED
            )
            scratch.insert_node(node, selected_evidence_ids)
        _observe_project_review_stream(
            len(raw_nodes),
            scratch.node_count(),
        )
    node_id_digest.update(b"]")
    coverage_digest.update(b"]")
    if (
        scratch.node_count() != manifest["node_count"]
        or analysis_count != manifest["analysis_node_count"]
        or scratch.node_count() - analysis_count
        != manifest["structural_node_count"]
        or node_id_digest.hexdigest() != manifest["node_ids_sha256"]
    ):
        raise ProjectBoundaryError(
            "semantic final node coverage is invalid"
        )
    scratch.validate_tree(int(manifest["node_count"]))
    return coverage_digest.hexdigest()


def _projected_node_base(
    node: SemanticNode,
    selected_evidence_ids: list[int],
) -> dict[str, object]:
    parent_node_id = (
        _safe_identifier(node.parent_node_id, "parent_node_id")
        if node.parent_node_id is not None
        else None
    )
    return {
        "node_id": _safe_identifier(node.node_id, "node_id"),
        "parent_node_id": parent_node_id,
        "kind": node.node_kind.value,
        "display_name": _safe_display_name(node.display_name),
        "relative_hierarchy": _safe_relative_hierarchy(
            node.relative_hierarchy
        ),
        "semantic_summary": _safe_semantic_text(
            node.semantic_summary,
            "semantic_summary",
            allow_empty=True,
        ),
        "semantic_roles": _safe_text_list(
            list(node.semantic_roles),
            "semantic_roles",
        ),
        "topics": _safe_text_list(list(node.topics), "topics"),
        "semantic_facts": {
            "purpose_signals": _safe_text_list(
                list(node.semantic_facts.purpose_signals),
                "purpose_signals",
            ),
            "parent_context_signals": _safe_text_list(
                list(node.semantic_facts.parent_context_signals),
                "parent_context_signals",
            ),
            "independence_signals": _safe_text_list(
                list(node.semantic_facts.independence_signals),
                "independence_signals",
            ),
            "triviality_signals": _safe_text_list(
                list(node.semantic_facts.triviality_signals),
                "triviality_signals",
            ),
            "grounding_signal_ids": [
                _safe_identifier(value, "grounding_signal_id")
                for value in node.semantic_facts.grounding_signal_ids
            ],
        },
        "semantic_disposition": node.semantic_disposition.value,
        "analysis_status": _analysis_status(
            node.analysis_status.value
        ),
        "selected_evidence_ids": selected_evidence_ids,
        "coverage": {
            "node_count": node.coverage.node_count,
            "content_node_count": node.coverage.content_node_count,
            "structural_only_count": (
                node.coverage.structural_only_count
            ),
            "failed_count": node.coverage.failed_count,
            "node_ids_sha256": _safe_sha256(
                node.coverage.node_ids_sha256,
                "coverage node_ids_sha256",
            ),
        },
    }


def _render_projection_chunks(
    manifest: dict[str, object],
    scratch: _ProjectReviewScratch,
) -> None:
    current: list[dict[str, object]] = []
    chunk_count = 0
    for record in scratch.projected_records():
        candidate = [*current, record]
        candidate_payload = _review_chunk_payload(
            str(manifest["revision_id"]),
            chunk_count + 1,
            candidate,
        )
        candidate_bytes = _canonical_bytes(candidate_payload)
        if len(candidate_bytes) <= PROJECT_REVIEW_CHUNK_BUDGET:
            current = candidate
            continue
        if not current:
            raise ProjectBoundaryError(
                "project review record exceeds the byte budget"
            )
        chunk_count += 1
        _write_scratch_chunk(
            scratch,
            str(manifest["revision_id"]),
            chunk_count,
            current,
        )
        current = [record]
        if (
            len(
                _canonical_bytes(
                    _review_chunk_payload(
                        str(manifest["revision_id"]),
                        chunk_count + 1,
                        current,
                    )
                )
            )
            > PROJECT_REVIEW_CHUNK_BUDGET
        ):
            raise ProjectBoundaryError(
                "project review record exceeds the byte budget"
            )
    if current:
        chunk_count += 1
        _write_scratch_chunk(
            scratch,
            str(manifest["revision_id"]),
            chunk_count,
            current,
        )
    if chunk_count == 0:
        raise ProjectBoundaryError(
            "project review projection contains no project boundaries"
        )


def _review_chunk_payload(
    revision_id: str,
    chunk_index: int,
    nodes: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "version": 3,
        "protocol": "codex-semantic-v2",
        "revision_id": revision_id,
        "chunk_index": chunk_index,
        "nodes": nodes,
    }


def _write_scratch_chunk(
    scratch: _ProjectReviewScratch,
    revision_id: str,
    chunk_index: int,
    nodes: list[dict[str, object]],
) -> Path:
    content = _canonical_bytes(
        _review_chunk_payload(revision_id, chunk_index, nodes)
    )
    if len(content) > PROJECT_REVIEW_CHUNK_BUDGET:
        raise ProjectBoundaryError(
            "project review chunk exceeds the byte budget"
        )
    path = scratch.chunk_directory / f"chunk-{chunk_index:04}.json"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        written = 0
        while written < len(content):
            written += os.write(descriptor, content[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    scratch.record_output_chunk(
        chunk_index=chunk_index,
        path=path,
        content=content,
        projected_node_count=len(nodes),
    )
    return path


def _project_review_manifest(
    semantic_manifest: dict[str, object],
    semantic_manifest_content: bytes,
    delivery_scope: object,
    artifact_policy_hash: object,
    source_policy_hash: str,
    coverage_sha256: str,
    scratch: _ProjectReviewScratch,
) -> dict[str, object]:
    chunk_rows = list(scratch.output_chunks())
    unsigned: dict[str, object] = {
        "version": 3,
        "protocol": "codex-semantic-v2",
        "artifact_kind": "master_profile",
        "delivery_scope": _delivery_scope(delivery_scope),
        "active_revision_id": _safe_identifier(
            semantic_manifest["revision_id"],
            "active_revision_id",
        ),
        "source_policy_hash": _safe_sha256(
            source_policy_hash,
            "source_policy_hash",
        ),
        "artifact_policy_hash": _safe_sha256(
            artifact_policy_hash,
            "artifact_policy_hash",
        ),
        "final_manifest_sha256": hashlib.sha256(
            semantic_manifest_content
        ).hexdigest(),
        "input_chunk_sha256s": [
            str(row["sha256"]) for row in chunk_rows
        ],
        "input_chunk_projected_node_counts": [
            int(row["projected_node_count"]) for row in chunk_rows
        ],
        "chunk_count": len(chunk_rows),
        "semantic_node_count": scratch.node_count(),
        "projected_node_count": scratch.projected_node_count(),
        "condensed_node_count": (
            scratch.node_count() - scratch.projected_node_count()
        ),
        "covered_node_count": scratch.node_count(),
        "coverage_sha256": _safe_sha256(
            coverage_sha256,
            "coverage_sha256",
        ),
        "selected_evidence_count": scratch.selected_evidence_count(),
    }
    return {
        **unsigned,
        "manifest_sha256": hashlib.sha256(
            _canonical_bytes(unsigned)
        ).hexdigest(),
    }


def _validate_existing_projection(paths: WorkspacePaths) -> set[str]:
    try:
        directory = open_managed_directory(paths.project_review_v3_dir)
    except FileNotFoundError:
        return set()
    try:
        names = set(os.listdir(directory))
        if names - {"input", "manifest.json"} or "input" not in names:
            raise ProjectBoundaryError(
                "project review managed output is invalid"
            )
        input_metadata = os.stat(
            "input",
            dir_fd=directory,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(input_metadata.st_mode):
            raise ProjectBoundaryError(
                "project review managed output is invalid"
            )
        if "manifest.json" in names:
            manifest_metadata = os.stat(
                "manifest.json",
                dir_fd=directory,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(manifest_metadata.st_mode)
                or manifest_metadata.st_nlink != 1
            ):
                raise ProjectBoundaryError(
                    "project review managed output is invalid"
                )
    finally:
        os.close(directory)
    chunk_names = _managed_regular_file_names(
        paths.project_review_v3_input_dir
    )
    if any(_CHUNK_NAME.fullmatch(name) is None for name in chunk_names):
        raise ProjectBoundaryError(
            "project review managed output is invalid"
        )
    return chunk_names


def _invalidate_projection_manifest(paths: WorkspacePaths) -> None:
    try:
        remove_managed_file(
            paths.project_review_v3_manifest_path,
            missing_ok=True,
        )
    except FileNotFoundError:
        return
    except OSError as error:
        raise ProjectBoundaryError(
            "project review manifest invalidation failed safely"
        ) from error


def _publish_projection(
    paths: WorkspacePaths,
    scratch: _ProjectReviewScratch,
    manifest: dict[str, object],
) -> None:
    manifest_text = _canonical_json(manifest)
    existing_names = _validate_existing_projection(paths)
    if _projection_matches(
        paths,
        scratch,
        existing_names,
        manifest_text,
    ):
        return
    expected_names = {
        str(row["file_name"]) for row in scratch.output_chunks()
    }
    try:
        remove_managed_file(
            paths.project_review_v3_manifest_path,
            missing_ok=True,
        )
        for row in scratch.output_chunks():
            name = str(row["file_name"])
            content = (
                scratch.chunk_directory / name
            ).read_text(encoding="utf-8")
            write_managed_text(
                paths.project_review_v3_input_dir / name,
                content,
            )
        for name in existing_names - expected_names:
            remove_managed_file(
                paths.project_review_v3_input_dir / name
            )
        write_managed_text(
            paths.project_review_v3_manifest_path,
            manifest_text,
        )
    except OSError as error:
        raise ProjectBoundaryError(
            "project review publication failed safely"
        ) from error


def _projection_matches(
    paths: WorkspacePaths,
    scratch: _ProjectReviewScratch,
    existing_names: set[str],
    manifest_text: str,
) -> bool:
    expected_names = {
        str(row["file_name"]) for row in scratch.output_chunks()
    }
    if existing_names != expected_names:
        return False
    try:
        if (
            read_managed_bytes(paths.project_review_v3_manifest_path)
            != manifest_text.encode("utf-8")
        ):
            return False
        for row in scratch.output_chunks():
            name = str(row["file_name"])
            if read_managed_bytes(
                paths.project_review_v3_input_dir / name
            ) != (scratch.chunk_directory / name).read_bytes():
                return False
        return True
    except FileNotFoundError:
        return False


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_bytes(value: object) -> bytes:
    return _canonical_json(value).encode("utf-8")


def _observe_project_review_stream(
    current_chunk_node_count: int,
    stored_node_count: int,
) -> None:
    return None


def _observe_project_review_scratch(
    directory: Path,
    database_path: Path,
) -> None:
    return None
