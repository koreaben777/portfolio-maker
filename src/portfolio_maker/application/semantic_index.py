from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
from portfolio_maker.application.semantic_work import (
    STRUCTURAL_CHUNK_SIZE,
    _OutputBinding,
    _ReceiptBinding,
    build_semantic_work_plan,
    semantic_work_plan_payload,
    structural_chunk_name,
)
from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    SemanticCoverage,
    SemanticEdge,
    SemanticFacts,
    SemanticImportReceipt,
    SemanticNode,
    SemanticNodeDisposition,
    SemanticNodeKind,
    stable_source_id,
)
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    open_managed_directory,
    read_managed_bytes,
    read_managed_relative_file,
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
    FileAnalysisInput,
    analyze_file_input,
)
from portfolio_maker.infrastructure.semantic_capsules import build_feature_capsules
from portfolio_maker.infrastructure.semantic_crawler import (
    MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    MAX_DIRECTORY_TIMEOUT_SECONDS,
    StructuralCrawlRootError,
    StructuralCrawlRootTimeoutError,
    StructuralCrawlTimeoutBudgetError,
    StructuralCrawlWorkerError,
    StructuralEntry,
    crawl_local_structure,
)
from portfolio_maker.infrastructure.sqlite_repository import (
    RepositoryError,
    SQLiteRepository,
)
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
_LOCATOR_TOKEN = re.compile(
    r"(?i)(?:file://|https?://|(?<![A-Za-z0-9_.-])(?:/|~[/\\\\]|[A-Za-z]:[/\\\\]|\\\\\\\\))[^\s'\"`<>)}\]]+"
)
_RELATIVE_FILE_LOCATOR = re.compile(
    r"(?i)(?<![\w.-])(?:\.{1,2}[/\\\\][^\s'\"`<>)}\]]+|(?:[\w-]+[/\\\\])+[\w-]+\.[a-z][a-z0-9_-]*)"
)
_CHUNK_FILENAME = re.compile(r"chunk-\d{4,}\.json")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9:._-]+")
_FINAL_MANIFEST_KEYS = {
    "version",
    "protocol",
    "revision_id",
    "source_id",
    "policy_hash",
    "analyzer_version",
    "work_plan_sha256",
    "input_manifest_sha256",
    "directory_timeout_seconds",
    "timed_out_directory_count",
    "unreadable_directory_count",
    "node_assignment_sha256",
    "receipt_file_sha256s",
    "chunk_sha256s",
    "chunk_node_counts",
    "chunk_count",
    "node_count",
    "analysis_node_count",
    "structural_node_count",
    "node_ids_sha256",
    "manifest_sha256",
}
_FINAL_NODE_KEYS = {
    "result_kind",
    "node_id",
    "source_id",
    "parent_node_id",
    "kind",
    "display_name",
    "relative_hierarchy",
    "content_fingerprint",
    "semantic_summary",
    "semantic_roles",
    "topics",
    "semantic_facts",
    "semantic_disposition",
    "analysis_status",
    "child_node_ids",
    "skip_reason",
    "evidence_ids",
    "coverage",
}
_SEMANTIC_FACT_KEYS = {
    "purpose_signals",
    "parent_context_signals",
    "independence_signals",
    "triviality_signals",
    "grounding_signal_ids",
}
_COVERAGE_KEYS = {
    "node_count",
    "content_node_count",
    "structural_only_count",
    "failed_count",
    "node_ids_sha256",
}
_ACTIVATION_RECEIPT_KEYS = {
    "version",
    "protocol",
    "revision_id",
    "source_id",
    "policy_hash",
    "final_manifest_sha256",
    "chunk_count",
    "node_count",
    "active",
}
_V2_INPUT_MANIFEST_KEYS = {
    "version",
    "protocol",
    "revision_id",
    "source_id",
    "policy_hash",
    "analyzer_version",
    "capsule_chunk_sha256s",
    "work_plan_sha256",
    "node_count",
    "work_unit_count",
    "managed_input_bytes",
    "analysis_target_count",
    "structural_only_count",
    "directory_timeout_seconds",
    "timed_out_directory_count",
    "unreadable_directory_count",
    "manifest_sha256",
}
_TIMEOUT_REVIEW_KEYS = {
    "version",
    "status",
    "policy_hash",
    "root_source_id",
    "directory_timeout_seconds",
    "directory_timeout_budget_seconds",
    "timed_out_directory_count",
    "unreadable_directory_count",
    "consumed_timeout_wait_seconds",
    "timed_out_relative_hierarchies",
    "reviewed_failure_sha256",
    "review_sha256",
}
_MAX_TIMEOUT_REVIEW_BYTES = 262_144
_WORK_PLAN_KEYS = {
    "version",
    "revision_id",
    "policy_hash",
    "protocol",
    "node_count",
    "analysis_target_count",
    "structural_owner_count",
    "work_unit_count",
    "max_work_unit_bytes",
    "reduce_fan_in",
    "node_assignment_sha256",
    "units",
}
@dataclass(frozen=True)
class _PreparedNode:
    node: SemanticNode
    locator: tuple[str, int | None, int | None]
    chunk_node: dict[str, object]


class _ScratchCapsuleMapping(Mapping[str, dict[str, object]]):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __getitem__(self, node_id: str) -> dict[str, object]:
        row = self._connection.execute(
            """
            SELECT capsule_json
            FROM capsules
            WHERE node_id = ? AND capsule_json IS NOT NULL
            """,
            (node_id,),
        ).fetchone()
        if row is None:
            raise KeyError(node_id)
        value = json.loads(row["capsule_json"])
        if type(value) is not dict:
            raise KeyError(node_id)
        return value

    def __iter__(self) -> Iterator[str]:
        rows = self._connection.execute(
            """
            SELECT node_id
            FROM capsules
            WHERE capsule_json IS NOT NULL
            ORDER BY node_id
            """
        )
        for row in rows:
            yield str(row["node_id"])

    def __len__(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM capsules WHERE capsule_json IS NOT NULL"
        ).fetchone()
        return int(row["count"])


class _ScratchReceiptMapping(MutableMapping[str, _ReceiptBinding]):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __getitem__(self, unit_id: str) -> _ReceiptBinding:
        row = self._connection.execute(
            """
            SELECT input_sha256, output_sha256, output_payload_json,
                   dependency_output_sha256s_json, receipt_content
            FROM receipts
            WHERE unit_id = ?
            """,
            (unit_id,),
        ).fetchone()
        if row is None:
            raise KeyError(unit_id)
        payload = json.loads(row["output_payload_json"])
        dependency_hashes = json.loads(
            row["dependency_output_sha256s_json"]
        )
        if type(payload) is not dict or type(dependency_hashes) is not list:
            raise KeyError(unit_id)
        return _ReceiptBinding(
            input_sha256=str(row["input_sha256"]),
            output=_OutputBinding(
                output_sha256=str(row["output_sha256"]),
                payload=payload,
                content=b"",
                validated=None,
            ),
            dependency_output_sha256s=tuple(
                str(value) for value in dependency_hashes
            ),
            content=bytes(row["receipt_content"]),
        )

    def __setitem__(self, unit_id: str, value: _ReceiptBinding) -> None:
        if not isinstance(value, _ReceiptBinding):
            raise TypeError("invalid semantic receipt scratch value")
        self._connection.execute(
            """
            INSERT INTO receipts (
                unit_id, input_sha256, output_sha256, output_payload_json,
                dependency_output_sha256s_json, receipt_content
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                unit_id,
                value.input_sha256,
                value.output.output_sha256,
                _canonical_json(value.output.payload),
                _canonical_json(list(value.dependency_output_sha256s)),
                value.content,
            ),
        )

    def __delitem__(self, unit_id: str) -> None:
        cursor = self._connection.execute(
            "DELETE FROM receipts WHERE unit_id = ?",
            (unit_id,),
        )
        if cursor.rowcount != 1:
            raise KeyError(unit_id)

    def __iter__(self) -> Iterator[str]:
        rows = self._connection.execute(
            "SELECT unit_id FROM receipts ORDER BY unit_id"
        )
        for row in rows:
            yield str(row["unit_id"])

    def __len__(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM receipts"
        ).fetchone()
        return int(row["count"])


class _SemanticBindingScratch:
    def __init__(self) -> None:
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> _SemanticBindingScratch:
        self._directory = tempfile.TemporaryDirectory(
            prefix="portfolio-maker-semantic-binding-"
        )
        try:
            directory = Path(self._directory.name)
            os.chmod(directory, 0o700)
            database_path = directory / "binding.db"
            descriptor = os.open(
                database_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(descriptor)
            self._connection = sqlite3.connect(database_path)
            self._connection.row_factory = sqlite3.Row
            _observe_v2_binding_scratch(database_path)
            self._connection.executescript(
                """
                CREATE TABLE capsules (
                    node_id TEXT PRIMARY KEY,
                    relative_hierarchy TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    capsule_json TEXT,
                    owner_kind TEXT,
                    owner_id TEXT
                );
                CREATE TABLE plan_owners (
                    node_id TEXT PRIMARY KEY,
                    owner_unit_id TEXT NOT NULL
                );
                CREATE TABLE receipts (
                    unit_id TEXT PRIMARY KEY,
                    input_sha256 TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    output_payload_json TEXT NOT NULL,
                    dependency_output_sha256s_json TEXT NOT NULL,
                    receipt_content BLOB NOT NULL
                );
                """
            )
            return self
        except Exception:
            self.__exit__()
            raise

    def __exit__(self, *args: object) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            if self._directory is not None:
                self._directory.cleanup()
                self._directory = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("semantic binding scratch is not open")
        return self._connection

    @property
    def capsules(self) -> Mapping[str, dict[str, object]]:
        return _ScratchCapsuleMapping(self.connection)

    @property
    def receipts(self) -> MutableMapping[str, _ReceiptBinding]:
        return _ScratchReceiptMapping(self.connection)

    def insert_plan_owner(self, node_id: str, owner_unit_id: str) -> None:
        try:
            self.connection.execute(
                """
                INSERT INTO plan_owners (node_id, owner_unit_id)
                VALUES (?, ?)
                """,
                (node_id, owner_unit_id),
            )
        except sqlite3.IntegrityError as error:
            raise SemanticIndexError(
                "semantic work plan is invalid"
            ) from error

    def plan_owner(self, node_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT owner_unit_id
            FROM plan_owners
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        return None if row is None else str(row["owner_unit_id"])

    def plan_owner_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM plan_owners"
        ).fetchone()
        return int(row["count"])

    def insert_capsule(
        self,
        capsule: dict[str, object],
        *,
        owner_id: str | None,
    ) -> None:
        disposition = str(capsule.get("semantic_disposition", ""))
        is_analysis = disposition == "analysis_required"
        if is_analysis != (owner_id is not None):
            raise SemanticIndexError("semantic work ownership is invalid")
        try:
            self.connection.execute(
                """
                INSERT INTO capsules (
                    node_id, relative_hierarchy, disposition, capsule_json,
                    owner_kind, owner_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(capsule["node_id"]),
                    str(capsule.get("relative_hierarchy", "")),
                    disposition,
                    _canonical_json(capsule) if is_analysis else None,
                    "semantic_work_unit" if is_analysis else None,
                    owner_id,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise SemanticIndexError(
                "semantic work capsule input is invalid"
            ) from error

    def assign_structural_chunks(self) -> None:
        rows = self.connection.execute(
            """
            SELECT node_id, disposition
            FROM capsules
            ORDER BY relative_hierarchy, node_id
            """
        )
        for index, row in enumerate(rows):
            if row["disposition"] == "analysis_required":
                continue
            self.connection.execute(
                """
                UPDATE capsules
                SET owner_kind = 'private_structural_chunk', owner_id = ?
                WHERE node_id = ?
                """,
                (structural_chunk_name(index), row["node_id"]),
            )

    def assignment_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(b"[")
        first = True
        rows = self.connection.execute(
            """
            SELECT node_id, owner_kind, owner_id
            FROM capsules
            ORDER BY node_id
            """
        )
        for row in rows:
            if row["owner_kind"] is None or row["owner_id"] is None:
                raise SemanticIndexError("semantic work ownership is invalid")
            if not first:
                digest.update(b",")
            digest.update(
                _canonical_json(
                    (
                        str(row["node_id"]),
                        str(row["owner_kind"]),
                        str(row["owner_id"]),
                    )
                ).encode("utf-8")
            )
            first = False
        digest.update(b"]")
        return digest.hexdigest()

    def counts(self) -> tuple[int, int]:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN disposition = 'analysis_required'
                            THEN 1 ELSE 0 END) AS analysis
            FROM capsules
            """
        ).fetchone()
        return int(row["total"]), int(row["analysis"] or 0)


def _observe_v2_capsule_chunk(
    capsule_count: int,
    chunk_byte_count: int,
    managed_input_bytes: int,
) -> None:
    del capsule_count, chunk_byte_count, managed_input_bytes


def _observe_v2_binding_scratch(database_path: Path) -> None:
    del database_path


def _validated_directory_timeout(value: object) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or value <= 0
        or value > MAX_DIRECTORY_TIMEOUT_SECONDS
    ):
        raise SemanticIndexError("semantic index directory timeout is invalid")
    return float(value)


def _validated_directory_timeout_budget(value: object) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or value < MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS
        or value > MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS
    ):
        raise SemanticIndexError("semantic index directory timeout budget is invalid")
    return float(value)


def prepare_semantic_index(
    request: PrepareSemanticIndexRequest,
) -> PrepareSemanticIndexResult:
    if request.protocol == "codex-semantic-v2":
        return _prepare_v2_semantic_index(request)
    if request.protocol != "codex-semantic-v3":
        raise SemanticIndexError("semantic index protocol is invalid")
    from portfolio_maker.application.semantic_prepare_v3 import (
        SemanticPrepareV3Error,
        prepare_semantic_index_v3,
    )

    try:
        return prepare_semantic_index_v3(request)
    except SemanticPrepareV3Error as error:
        raise SemanticIndexError("semantic index preparation failed safely") from error


def _prepare_v2_semantic_index(
    request: PrepareSemanticIndexRequest,
) -> PrepareSemanticIndexResult:
    directory_timeout_seconds = _validated_directory_timeout(
        request.directory_timeout_seconds
    )
    directory_timeout_budget_seconds = _validated_directory_timeout_budget(
        request.directory_timeout_budget_seconds
    )
    if not isinstance(request.chunk_size, int) or isinstance(request.chunk_size, bool) or request.chunk_size <= 0:
        raise SemanticIndexError("semantic index chunk size must be positive")

    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    _validate_approved_root(request.root, approval)
    policy_hash = _policy_hash(approval)
    try:
        crawl = crawl_local_structure(
            request.root,
            approval,
            directory_timeout_seconds=directory_timeout_seconds,
            directory_timeout_budget_seconds=directory_timeout_budget_seconds,
        )
    except StructuralCrawlTimeoutBudgetError as error:
        try:
            _assert_timeout_review_root_is_current(paths, error.source_id)
            _write_timeout_review(
                paths,
                status="approval_required",
                policy_hash=policy_hash,
                root_source_id=error.source_id,
                directory_timeout_seconds=directory_timeout_seconds,
                directory_timeout_budget_seconds=directory_timeout_budget_seconds,
                timed_out_directory_count=error.timed_out_directory_count,
                unreadable_directory_count=error.unreadable_directory_count,
                consumed_timeout_wait_seconds=error.consumed_timeout_wait_seconds,
                timed_out_relative_hierarchies=(
                    error.timed_out_relative_hierarchies
                ),
                reviewed_failure_sha256=None,
            )
        except (OSError, SemanticIndexError) as review_error:
            raise SemanticIndexError(
                "semantic index crawl timeout review failed safely"
            ) from review_error
        raise SemanticIndexError(
            "semantic index crawl timeout budget exhausted"
        ) from error
    except StructuralCrawlRootTimeoutError as error:
        raise SemanticIndexError("semantic index root crawl timed out") from error
    except StructuralCrawlWorkerError as error:
        raise SemanticIndexError("semantic index crawl failed safely") from error
    except StructuralCrawlRootError as error:
        raise SemanticIndexError("semantic index root is invalid") from error
    if not crawl.entries:
        raise SemanticIndexError("semantic index root contains no structural entries")

    source_id = crawl.entries[0].source_id
    try:
        timeout_review_update = _prepare_timeout_review_resolution(
            paths,
            policy_hash=policy_hash,
            root_source_id=source_id,
            directory_timeout_seconds=directory_timeout_seconds,
            directory_timeout_budget_seconds=directory_timeout_budget_seconds,
        )
    except (OSError, SemanticIndexError) as error:
        raise SemanticIndexError(
            "semantic index crawl timeout review failed safely"
        ) from error

    revision_id = uuid4().hex
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    approved_evidence_by_source_uri = _approved_local_evidence_by_source_uri(
        repository, approval
    )
    analyses, evidence_ids_by_node = _analyze_capsule_inputs(
        crawl.entries,
        approved_evidence_by_source_uri,
    )
    capsules = build_feature_capsules(
        crawl.entries,
        analyses,
        revision_id=revision_id,
        evidence_ids_by_node=evidence_ids_by_node,
    )
    plan = build_semantic_work_plan(
        capsules,
        revision_id=revision_id,
        policy_hash=policy_hash,
    )
    chunk_payloads = _capsule_chunk_payloads(
        revision_id,
        capsules,
        request.chunk_size,
    )
    chunk_texts = tuple(_canonical_json(payload) for payload in chunk_payloads)
    chunk_sha256s = tuple(
        hashlib.sha256(text.encode("utf-8")).hexdigest() for text in chunk_texts
    )
    plan_text = _canonical_json(semantic_work_plan_payload(plan))
    structural_texts, locator_texts = _private_revision_chunks(
        revision_id,
        crawl.entries,
        capsules,
    )
    managed_input_bytes = len(plan_text.encode("utf-8")) + sum(
        len(text.encode("utf-8")) for text in chunk_texts
    )
    unsigned_manifest: dict[str, object] = {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": revision_id,
        "source_id": source_id,
        "policy_hash": policy_hash,
        "analyzer_version": ANALYZER_VERSION,
        "capsule_chunk_sha256s": list(chunk_sha256s),
        "work_plan_sha256": hashlib.sha256(plan_text.encode("utf-8")).hexdigest(),
        "node_count": len(capsules),
        "work_unit_count": len(plan.units),
        "managed_input_bytes": managed_input_bytes,
        "analysis_target_count": plan.analysis_target_count,
        "structural_only_count": plan.structural_owner_count,
        "directory_timeout_seconds": directory_timeout_seconds,
        "timed_out_directory_count": crawl.timed_out_directory_count,
        "unreadable_directory_count": crawl.unreadable_directory_count,
    }
    manifest = {
        **unsigned_manifest,
        "manifest_sha256": hashlib.sha256(
            _canonical_json(unsigned_manifest).encode("utf-8")
        ).hexdigest(),
    }

    repository.create_semantic_revision(
        revision_id, source_id, policy_hash, ANALYZER_VERSION
    )
    try:
        _publish_semantic_input(
            repository,
            paths,
            chunk_texts,
            _canonical_json(manifest),
            work_plan_text=plan_text,
            revision_id=revision_id,
            structural_texts=structural_texts,
            locator_texts=locator_texts,
            timeout_review_update=timeout_review_update,
        )
    except Exception:
        repository.fail_semantic_revision(revision_id)
        raise

    return PrepareSemanticIndexResult(
        manifest_path=paths.semantic_index_manifest_path,
        revision_id=revision_id,
        node_count=len(capsules),
        chunk_count=len(chunk_texts),
        partial_count=sum(
            capsule["analysis_status"] == AnalysisStatus.PARTIAL.value
            for capsule in capsules
        ),
        work_unit_count=len(plan.units),
        managed_input_bytes=managed_input_bytes,
        analysis_target_count=plan.analysis_target_count,
        structural_only_count=plan.structural_owner_count,
        directory_timeout_seconds=directory_timeout_seconds,
        directory_timeout_budget_seconds=directory_timeout_budget_seconds,
        timed_out_directory_count=crawl.timed_out_directory_count,
        unreadable_directory_count=crawl.unreadable_directory_count,
    )


def apply_semantic_index(
    request: ApplySemanticIndexRequest,
) -> ApplySemanticIndexResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    try:
        final_manifest_content = read_managed_bytes(
            paths.semantic_final_dir / "manifest.json"
        )
    except FileNotFoundError:
        final_manifest_content = None
    except OSError as error:
        raise SemanticIndexError("semantic final output is invalid") from error
    if final_manifest_content is not None:
        try:
            return _apply_v2_semantic_index(
                paths,
                repository,
            )
        except RepositoryError as error:
            raise SemanticIndexError(
                "semantic index repository state is invalid"
            ) from error

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


def _apply_v2_semantic_index(
    paths: WorkspacePaths,
    repository: SQLiteRepository,
) -> ApplySemanticIndexResult:
    with repository._repository_critical_section():
        final_manifest_content = _read_managed_content(
            paths.semantic_final_dir / "manifest.json",
            "semantic final manifest",
        )
        manifest = _parse_v2_final_manifest(final_manifest_content)
        approval = load_approval(paths)
        if manifest["policy_hash"] != _policy_hash(approval):
            raise SemanticIndexError(
                "semantic index approval policy has changed"
            )
        _validate_v2_input_and_work_bindings(paths, manifest)
        expected_final_names = {
            "manifest.json",
            *{
                f"chunk-{index:04}.json"
                for index in range(1, int(manifest["chunk_count"]) + 1)
            },
        }
        if (
            _managed_regular_file_names(paths.semantic_final_dir)
            != expected_final_names
        ):
            raise SemanticIndexError("semantic final output is invalid")
        locator_dir = paths.semantic_revision_locator_dir(
            str(manifest["revision_id"])
        )
        expected_locator_names = expected_final_names - {"manifest.json"}
        if _managed_regular_file_names(locator_dir) != expected_locator_names:
            raise SemanticIndexError("semantic private locator state is invalid")

        target_receipt = _activation_receipt_payload(
            manifest,
            final_manifest_content,
        )
        current_active = repository.get_active_semantic_revision(
            str(manifest["source_id"])
        )
        _validate_existing_activation_receipt(
            paths,
            current_active,
            target_receipt,
        )
        target_already_active = (
            current_active is not None
            and current_active["id"] == manifest["revision_id"]
        )

        approved_roots = _approved_root_paths(approval)
        root_locator: Path | None = None
        previous_key: tuple[str, str] | None = None
        global_node_digest = hashlib.sha256()
        global_node_digest.update(b"[")
        first_node = True
        node_count = 0
        analysis_count = 0
        complete_count = 0
        partial_count = 0
        failed_count = 0
        expected_receipts: list[SemanticImportReceipt] = []

        chunk_sha256s = manifest["chunk_sha256s"]
        chunk_node_counts = manifest["chunk_node_counts"]
        assert isinstance(chunk_sha256s, list)
        assert isinstance(chunk_node_counts, list)
        for index, (expected_hash, expected_count) in enumerate(
            zip(chunk_sha256s, chunk_node_counts, strict=True),
            start=1,
        ):
            chunk_path = (
                paths.semantic_final_dir / f"chunk-{index:04}.json"
            )
            locator_path = locator_dir / f"chunk-{index:04}.json"
            chunk_content = _read_managed_content(
                chunk_path,
                "semantic final output",
            )
            locator_content = _read_managed_content(
                locator_path,
                "semantic private locator state",
            )
            if hashlib.sha256(chunk_content).hexdigest() != expected_hash:
                raise SemanticIndexError("semantic final output is invalid")
            chunk = _decode_canonical_object(
                chunk_content,
                "semantic final output",
            )
            locator_chunk = _decode_canonical_object(
                locator_content,
                "semantic private locator state",
            )
            raw_nodes = chunk.get("nodes")
            raw_locators = locator_chunk.get("locators")
            if (
                set(chunk)
                != {
                    "version",
                    "protocol",
                    "revision_id",
                    "chunk_index",
                    "nodes",
                }
                or chunk.get("version") != 2
                or chunk.get("protocol") != "codex-semantic-v2"
                or chunk.get("revision_id") != manifest["revision_id"]
                or chunk.get("chunk_index") != index
                or type(raw_nodes) is not list
                or not raw_nodes
                or len(raw_nodes) > 1_000
                or len(raw_nodes) != expected_count
                or set(locator_chunk)
                != {"version", "revision_id", "locators"}
                or locator_chunk.get("version") != 2
                or locator_chunk.get("revision_id")
                != manifest["revision_id"]
                or type(raw_locators) is not list
                or len(raw_locators) != len(raw_nodes)
            ):
                raise SemanticIndexError("semantic final output is invalid")

            nodes: list[SemanticNode] = []
            locators: dict[str, tuple[str, int | None, int | None]] = {}
            edges: list[SemanticEdge] = []
            chunk_node_ids: list[str] = []
            for raw_node, raw_locator in zip(
                raw_nodes,
                raw_locators,
                strict=True,
            ):
                node = _parse_v2_final_node(raw_node, manifest)
                key = (node.relative_hierarchy, node.node_id)
                if previous_key is not None and key <= previous_key:
                    raise SemanticIndexError(
                        "semantic final node ordering is invalid"
                    )
                previous_key = key
                if not first_node:
                    global_node_digest.update(b",")
                global_node_digest.update(
                    _canonical_json(node.node_id).encode("utf-8")
                )
                first_node = False
                node_count += 1
                analysis_count += int(
                    node.semantic_disposition
                    is SemanticNodeDisposition.ANALYZED
                )
                complete_count += int(
                    node.analysis_status is AnalysisStatus.COMPLETE
                )
                partial_count += int(
                    node.analysis_status is AnalysisStatus.PARTIAL
                )
                failed_count += int(
                    node.analysis_status is AnalysisStatus.FAILED
                )

                locator = _parse_v2_locator(raw_locator, node.node_id)
                locator_path_value = Path(locator[0])
                if node.parent_node_id is None:
                    if (
                        root_locator is not None
                        or node.relative_hierarchy != "."
                        or locator_path_value not in approved_roots
                    ):
                        raise SemanticIndexError(
                            "semantic private locator state is invalid"
                        )
                    root_locator = locator_path_value
                else:
                    if root_locator is None:
                        raise SemanticIndexError(
                            "semantic private locator state is invalid"
                        )
                    relative = PurePosixPath(node.relative_hierarchy)
                    expected_locator = root_locator.joinpath(*relative.parts)
                    if locator_path_value != expected_locator:
                        raise SemanticIndexError(
                            "semantic private locator state is invalid"
                        )
                    edges.append(
                        SemanticEdge(
                            revision_id=str(manifest["revision_id"]),
                            from_node_id=node.parent_node_id,
                            relation="contains",
                            to_node_id=node.node_id,
                        )
                    )
                nodes.append(node)
                locators[node.node_id] = locator
                chunk_node_ids.append(node.node_id)

            chunk_node_ids_sha256 = hashlib.sha256(
                _canonical_json(chunk_node_ids).encode("utf-8")
            ).hexdigest()
            receipt = SemanticImportReceipt(
                revision_id=str(manifest["revision_id"]),
                chunk_index=index,
                chunk_sha256=str(expected_hash),
                locator_chunk_sha256=hashlib.sha256(
                    locator_content
                ).hexdigest(),
                node_ids_sha256=chunk_node_ids_sha256,
                node_count=len(nodes),
            )
            expected_receipts.append(receipt)
            if not target_already_active:
                repository.import_semantic_revision_chunk(
                    revision_id=receipt.revision_id,
                    chunk_index=receipt.chunk_index,
                    chunk_sha256=receipt.chunk_sha256,
                    locator_chunk_sha256=receipt.locator_chunk_sha256,
                    node_ids_sha256=receipt.node_ids_sha256,
                    nodes=tuple(nodes),
                    locators=locators,
                    edges=tuple(edges),
                )

        global_node_digest.update(b"]")
        if (
            root_locator is None
            or node_count != manifest["node_count"]
            or analysis_count != manifest["analysis_node_count"]
            or node_count - analysis_count
            != manifest["structural_node_count"]
            or global_node_digest.hexdigest()
            != manifest["node_ids_sha256"]
        ):
            raise SemanticIndexError("semantic final node coverage is invalid")

        repository.activate_imported_semantic_revision(
            revision_id=str(manifest["revision_id"]),
            expected_receipts=tuple(expected_receipts),
            expected_node_count=node_count,
            expected_edge_count=node_count - 1,
            source_id=str(manifest["source_id"]),
            policy_hash=str(manifest["policy_hash"]),
            analyzer_version=str(manifest["analyzer_version"]),
        )
        try:
            _publish_activation_receipt(paths, target_receipt)
        except OSError as error:
            raise SemanticIndexError(
                "semantic index activation receipt is unavailable"
            ) from error

    return ApplySemanticIndexResult(
        revision_id=str(manifest["revision_id"]),
        active=True,
        complete_count=complete_count,
        partial_count=partial_count,
        failed_count=failed_count,
    )


def _parse_v2_final_manifest(content: bytes) -> dict[str, object]:
    manifest = _decode_canonical_object(content, "semantic final manifest")
    unsigned = dict(manifest)
    manifest_sha256 = unsigned.pop("manifest_sha256", None)
    chunk_sha256s = manifest.get("chunk_sha256s")
    chunk_node_counts = manifest.get("chunk_node_counts")
    receipt_hashes = manifest.get("receipt_file_sha256s")
    if (
        set(manifest) != _FINAL_MANIFEST_KEYS
        or manifest.get("version") != 2
        or manifest.get("protocol") != "codex-semantic-v2"
        or not _is_safe_identifier(manifest.get("revision_id"))
        or not _is_safe_identifier(manifest.get("source_id"))
        or not _is_sha256(manifest.get("policy_hash"))
        or manifest.get("analyzer_version") != "semantic-input-v2"
        or not _is_sha256(manifest.get("work_plan_sha256"))
        or not _is_sha256(manifest.get("input_manifest_sha256"))
        or not _is_sha256(manifest.get("node_assignment_sha256"))
        or not _is_sha256(manifest.get("node_ids_sha256"))
        or not _is_sha256(manifest_sha256)
        or hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest()
        != manifest_sha256
        or type(chunk_sha256s) is not list
        or not chunk_sha256s
        or not all(_is_sha256(value) for value in chunk_sha256s)
        or type(chunk_node_counts) is not list
        or len(chunk_node_counts) != len(chunk_sha256s)
        or not all(
            type(value) is int and 1 <= value <= 1_000
            for value in chunk_node_counts
        )
        or type(receipt_hashes) is not list
        or not all(_is_sha256(value) for value in receipt_hashes)
        or not _is_positive_int(manifest.get("chunk_count"))
        or manifest["chunk_count"] != len(chunk_sha256s)
        or not _is_positive_int(manifest.get("node_count"))
        or manifest["node_count"] != sum(chunk_node_counts)
        or not _is_nonnegative_int(manifest.get("analysis_node_count"))
        or not _is_nonnegative_int(manifest.get("structural_node_count"))
        or not _is_valid_directory_timeout(
            manifest.get("directory_timeout_seconds")
        )
        or not _is_nonnegative_int(
            manifest.get("timed_out_directory_count")
        )
        or not _is_nonnegative_int(
            manifest.get("unreadable_directory_count")
        )
        or manifest["timed_out_directory_count"]
        > manifest["unreadable_directory_count"]
        or manifest["unreadable_directory_count"]
        > manifest["node_count"]
        or manifest["analysis_node_count"]
        + manifest["structural_node_count"]
        != manifest["node_count"]
    ):
        raise SemanticIndexError("semantic final manifest is invalid")
    return manifest


def _validate_v2_input_and_work_bindings(
    paths: WorkspacePaths,
    manifest: dict[str, object],
) -> None:
    from portfolio_maker.application.semantic_work import (
        SemanticWorkError,
        _VerifiedState,
        _evaluate_lifecycle,
        _validate_capsule_contract,
        _validate_plan_structure,
        _validate_unit_capsule_binding,
    )

    input_manifest_content = _read_managed_content(
        paths.semantic_index_manifest_path,
        "semantic work manifest",
    )
    input_manifest = _decode_canonical_object(
        input_manifest_content,
        "semantic work manifest",
    )
    input_manifest_sha256 = _validated_v2_input_manifest_sha256(
        input_manifest,
    )
    if (
        input_manifest.get("revision_id") != manifest["revision_id"]
        or input_manifest.get("source_id") != manifest["source_id"]
        or input_manifest.get("policy_hash") != manifest["policy_hash"]
        or input_manifest.get("analyzer_version")
        != manifest["analyzer_version"]
        or input_manifest.get("work_plan_sha256")
        != manifest["work_plan_sha256"]
        or input_manifest.get("node_count") != manifest["node_count"]
        or input_manifest.get("analysis_target_count")
        != manifest["analysis_node_count"]
        or input_manifest.get("structural_only_count")
        != manifest["structural_node_count"]
        or input_manifest_sha256 != manifest["input_manifest_sha256"]
        or input_manifest.get("directory_timeout_seconds")
        != manifest["directory_timeout_seconds"]
        or input_manifest.get("timed_out_directory_count")
        != manifest["timed_out_directory_count"]
        or input_manifest.get("unreadable_directory_count")
        != manifest["unreadable_directory_count"]
    ):
        raise SemanticIndexError("semantic work manifest is invalid")
    capsule_hashes = input_manifest.get("capsule_chunk_sha256s")
    if (
        type(capsule_hashes) is not list
        or not capsule_hashes
        or not all(_is_sha256(value) for value in capsule_hashes)
    ):
        raise SemanticIndexError("semantic work manifest is invalid")
    expected_capsule_names = {
        f"chunk-{index:04}.json"
        for index in range(1, len(capsule_hashes) + 1)
    }
    if (
        _managed_regular_file_names(paths.semantic_index_input_dir)
        != expected_capsule_names
    ):
        raise SemanticIndexError("semantic work capsule input is invalid")

    plan_content = _read_managed_content(
        paths.semantic_work_plan_path,
        "semantic work plan",
    )
    plan = _decode_canonical_object(plan_content, "semantic work plan")
    units = plan.get("units")
    if (
        hashlib.sha256(plan_content).hexdigest()
        != manifest["work_plan_sha256"]
        or set(plan) != _WORK_PLAN_KEYS
        or plan.get("version") != 2
        or plan.get("protocol") != "codex-semantic-v2"
        or plan.get("revision_id") != manifest["revision_id"]
        or plan.get("policy_hash") != manifest["policy_hash"]
        or plan.get("node_count") != manifest["node_count"]
        or plan.get("node_assignment_sha256")
        != manifest["node_assignment_sha256"]
        or type(units) is not list
        or plan.get("work_unit_count") != len(units)
    ):
        raise SemanticIndexError("semantic work plan is invalid")
    managed_input_bytes = len(plan_content)
    try:
        with _SemanticBindingScratch() as scratch:
            try:
                verified_units = _validate_plan_structure(
                    plan,
                    input_manifest,
                    owner_sink=scratch.insert_plan_owner,
                )
            except SemanticWorkError as error:
                raise SemanticIndexError(str(error)) from error
            unit_by_id = {unit.unit_id: unit for unit in verified_units}
            for index, expected_hash in enumerate(capsule_hashes, start=1):
                content = _read_managed_content(
                    paths.semantic_index_input_dir / f"chunk-{index:04}.json",
                    "semantic work capsule input",
                )
                managed_input_bytes += len(content)
                if hashlib.sha256(content).hexdigest() != expected_hash:
                    raise SemanticIndexError(
                        "semantic work capsule input is invalid"
                    )
                chunk = _decode_canonical_object(
                    content,
                    "semantic work capsule input",
                )
                raw_capsules = chunk.get("capsules")
                if (
                    set(chunk) != {"version", "revision_id", "capsules"}
                    or chunk.get("version") != 2
                    or chunk.get("revision_id") != manifest["revision_id"]
                    or type(raw_capsules) is not list
                ):
                    raise SemanticIndexError(
                        "semantic work capsule input is invalid"
                    )
                _observe_v2_capsule_chunk(
                    len(raw_capsules),
                    len(content),
                    managed_input_bytes,
                )
                for raw_capsule in raw_capsules:
                    try:
                        capsule = _validate_capsule_contract(raw_capsule)
                    except SemanticWorkError as error:
                        raise SemanticIndexError(str(error)) from error
                    node_id = str(capsule["node_id"])
                    owner_id = scratch.plan_owner(node_id)
                    if owner_id is not None:
                        try:
                            _validate_unit_capsule_binding(
                                unit_by_id[owner_id],
                                capsule,
                                input_manifest,
                            )
                        except SemanticWorkError as error:
                            raise SemanticIndexError(str(error)) from error
                    scratch.insert_capsule(capsule, owner_id=owner_id)

            scratch.assign_structural_chunks()
            node_count, analysis_count = scratch.counts()
            owner_count = scratch.plan_owner_count()
            if (
                node_count != input_manifest["node_count"]
                or analysis_count != input_manifest["analysis_target_count"]
                or node_count - analysis_count
                != input_manifest["structural_only_count"]
                or analysis_count != owner_count
                or len(verified_units) != input_manifest["work_unit_count"]
                or managed_input_bytes
                != input_manifest["managed_input_bytes"]
                or scratch.assignment_sha256()
                != plan["node_assignment_sha256"]
            ):
                raise SemanticIndexError(
                    "semantic work manifest counts are invalid"
                )

            verified_state = _VerifiedState(
                paths=paths,
                revision_id=str(input_manifest["revision_id"]),
                source_id=str(input_manifest["source_id"]),
                policy_hash=str(input_manifest["policy_hash"]),
                analyzer_version=str(input_manifest["analyzer_version"]),
                work_plan_sha256=str(input_manifest["work_plan_sha256"]),
                input_manifest_sha256=str(input_manifest_sha256),
                directory_timeout_seconds=float(
                    input_manifest["directory_timeout_seconds"]
                ),
                timed_out_directory_count=int(
                    input_manifest["timed_out_directory_count"]
                ),
                unreadable_directory_count=int(
                    input_manifest["unreadable_directory_count"]
                ),
                node_assignment_sha256=str(plan["node_assignment_sha256"]),
                node_count=int(input_manifest["node_count"]),
                analysis_node_count=int(
                    input_manifest["analysis_target_count"]
                ),
                structural_node_count=int(
                    input_manifest["structural_only_count"]
                ),
                max_work_unit_bytes=int(plan["max_work_unit_bytes"]),
                capsules=scratch.capsules,
                units=verified_units,
                unit_by_id=unit_by_id,
            )
            try:
                lifecycle = _evaluate_lifecycle(
                    verified_state,
                    scratch.receipts,
                )
            except SemanticWorkError as error:
                raise SemanticIndexError(str(error)) from error
            if (
                lifecycle.invalid_count
                or lifecycle.ready_unit_ids
                or len(lifecycle.valid_receipts) != len(verified_units)
            ):
                raise SemanticIndexError("semantic work receipts are invalid")

            unit_ids = [unit.unit_id for unit in verified_units]
            expected_receipt_names = {
                f"{unit_id}.json" for unit_id in unit_ids
            }
            if (
                _managed_regular_file_names(paths.semantic_work_receipts_dir)
                != expected_receipt_names
            ):
                raise SemanticIndexError("semantic work receipts are invalid")
            receipt_hashes = [
                hashlib.sha256(
                    _read_managed_content(
                        paths.semantic_work_receipts_dir
                        / f"{unit_id}.json",
                        "semantic work receipt",
                    )
                ).hexdigest()
                for unit_id in unit_ids
            ]
            if receipt_hashes != manifest["receipt_file_sha256s"]:
                raise SemanticIndexError("semantic work receipts are invalid")
    except SemanticIndexError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise SemanticIndexError(
            "semantic work binding validation is unavailable"
        ) from error


def _parse_v2_final_node(
    value: object,
    manifest: dict[str, object],
) -> SemanticNode:
    if type(value) is not dict or set(value) != _FINAL_NODE_KEYS:
        raise SemanticIndexError("semantic final node is invalid")
    facts_value = value.get("semantic_facts")
    coverage_value = value.get("coverage")
    roles = value.get("semantic_roles")
    topics = value.get("topics")
    child_ids = value.get("child_node_ids")
    evidence_ids = value.get("evidence_ids")
    if (
        value.get("result_kind") != "node"
        or not _is_safe_identifier(value.get("node_id"))
        or value.get("source_id") != manifest["source_id"]
        or value.get("parent_node_id") is not None
        and not _is_safe_identifier(value.get("parent_node_id"))
        or value.get("kind")
        not in {kind.value for kind in SemanticNodeKind}
        or not _is_safe_presentation_text(value.get("display_name"))
        or not _is_safe_relative_hierarchy(value.get("relative_hierarchy"))
        or value.get("content_fingerprint") is not None
        and (
            not isinstance(value.get("content_fingerprint"), str)
            or _FINGERPRINT.fullmatch(
                str(value.get("content_fingerprint"))
            )
            is None
        )
        or not isinstance(value.get("semantic_summary"), str)
        or value.get("semantic_disposition")
        not in {
            disposition.value for disposition in SemanticNodeDisposition
        }
        or value.get("analysis_status")
        not in {status.value for status in AnalysisStatus}
        or value.get("skip_reason") is not None
        and not _is_safe_identifier(value.get("skip_reason"))
        or not _is_unique_text_list(roles, safe=True)
        or not _is_unique_text_list(topics, safe=True)
        or not _is_unique_text_list(child_ids, identifier=True)
        or child_ids != sorted(child_ids)
        or type(evidence_ids) is not list
        or any(type(item) is not int or item < 1 for item in evidence_ids)
        or evidence_ids != sorted(set(evidence_ids))
        or type(facts_value) is not dict
        or set(facts_value) != _SEMANTIC_FACT_KEYS
        or not all(
            _is_unique_text_list(
                facts_value[key],
                identifier=(key == "grounding_signal_ids"),
                safe=(key != "grounding_signal_ids"),
            )
            for key in _SEMANTIC_FACT_KEYS
        )
        or type(coverage_value) is not dict
        or set(coverage_value) != _COVERAGE_KEYS
        or not all(
            _is_nonnegative_int(coverage_value.get(key))
            for key in (
                "node_count",
                "content_node_count",
                "structural_only_count",
                "failed_count",
            )
        )
        or coverage_value["node_count"] < 1
        or not _is_sha256(coverage_value.get("node_ids_sha256"))
    ):
        raise SemanticIndexError("semantic final node is invalid")

    disposition = SemanticNodeDisposition(
        str(value["semantic_disposition"])
    )
    summary = str(value["semantic_summary"])
    if disposition is SemanticNodeDisposition.ANALYZED:
        if not summary or not facts_value["grounding_signal_ids"]:
            raise SemanticIndexError("semantic final node is invalid")
        _require_exact_safe_text(summary)
    elif (
        summary
        or roles
        or topics
        or any(facts_value[key] for key in _SEMANTIC_FACT_KEYS)
    ):
        raise SemanticIndexError("semantic final node is invalid")

    return SemanticNode(
        node_id=str(value["node_id"]),
        source_id=str(value["source_id"]),
        node_kind=SemanticNodeKind(str(value["kind"])),
        parent_node_id=(
            str(value["parent_node_id"])
            if value["parent_node_id"] is not None
            else None
        ),
        display_name=str(value["display_name"]),
        relative_hierarchy=str(value["relative_hierarchy"]),
        content_fingerprint=(
            str(value["content_fingerprint"])
            if value["content_fingerprint"] is not None
            else None
        ),
        semantic_summary=summary,
        semantic_roles=tuple(roles),
        topics=tuple(topics),
        evidence_ids=tuple(evidence_ids),
        analysis_status=AnalysisStatus(str(value["analysis_status"])),
        analyzer_version=str(manifest["analyzer_version"]),
        updated_at="",
        semantic_disposition=disposition,
        semantic_facts=SemanticFacts(
            purpose_signals=tuple(facts_value["purpose_signals"]),
            parent_context_signals=tuple(
                facts_value["parent_context_signals"]
            ),
            independence_signals=tuple(
                facts_value["independence_signals"]
            ),
            triviality_signals=tuple(
                facts_value["triviality_signals"]
            ),
            grounding_signal_ids=tuple(
                facts_value["grounding_signal_ids"]
            ),
        ),
        analysis_protocol="codex-semantic-v2",
        skip_reason=(
            str(value["skip_reason"])
            if value["skip_reason"] is not None
            else None
        ),
        child_node_ids=tuple(child_ids),
        coverage=SemanticCoverage(
            node_count=int(coverage_value["node_count"]),
            content_node_count=int(
                coverage_value["content_node_count"]
            ),
            structural_only_count=int(
                coverage_value["structural_only_count"]
            ),
            failed_count=int(coverage_value["failed_count"]),
            node_ids_sha256=str(coverage_value["node_ids_sha256"]),
        ),
    )


def _parse_v2_locator(
    value: object,
    expected_node_id: str,
) -> tuple[str, int | None, int | None]:
    if (
        type(value) is not dict
        or set(value) != {"node_id", "locator", "device", "inode"}
        or value.get("node_id") != expected_node_id
        or not isinstance(value.get("locator"), str)
        or not value["locator"]
        or not Path(value["locator"]).is_absolute()
        or value.get("device") is not None
        and not _is_nonnegative_int(value.get("device"))
        or value.get("inode") is not None
        and not _is_nonnegative_int(value.get("inode"))
    ):
        raise SemanticIndexError("semantic private locator state is invalid")
    return (
        str(value["locator"]),
        value["device"],
        value["inode"],
    )


def _activation_receipt_payload(
    manifest: dict[str, object],
    manifest_content: bytes,
) -> dict[str, object]:
    return {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": manifest["revision_id"],
        "source_id": manifest["source_id"],
        "policy_hash": manifest["policy_hash"],
        "final_manifest_sha256": hashlib.sha256(
            manifest_content
        ).hexdigest(),
        "chunk_count": manifest["chunk_count"],
        "node_count": manifest["node_count"],
        "active": True,
    }


def _validate_existing_activation_receipt(
    paths: WorkspacePaths,
    current_active: dict[str, str | None] | None,
    target: dict[str, object],
) -> None:
    try:
        content = read_managed_bytes(paths.semantic_activation_receipt_path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise SemanticIndexError(
            "semantic index activation receipt is invalid"
        ) from error
    receipt = _decode_canonical_object(
        content,
        "semantic index activation receipt",
    )
    if (
        set(receipt) != _ACTIVATION_RECEIPT_KEYS
        or receipt.get("version") != 2
        or receipt.get("protocol") != "codex-semantic-v2"
        or receipt.get("active") is not True
        or not _is_safe_identifier(receipt.get("revision_id"))
        or not _is_safe_identifier(receipt.get("source_id"))
        or not _is_sha256(receipt.get("policy_hash"))
        or not _is_sha256(receipt.get("final_manifest_sha256"))
        or not _is_positive_int(receipt.get("chunk_count"))
        or not _is_positive_int(receipt.get("node_count"))
        or current_active is None
        or receipt.get("revision_id") != current_active["id"]
        or receipt.get("source_id") != current_active["source_id"]
        or receipt.get("policy_hash") != current_active["policy_hash"]
        or (
            current_active["id"] == target["revision_id"]
            and receipt != target
        )
    ):
        raise SemanticIndexError(
            "semantic index activation receipt is invalid"
        )


def _publish_activation_receipt(
    paths: WorkspacePaths,
    payload: dict[str, object],
) -> None:
    content = _canonical_json(payload)
    try:
        existing = read_managed_bytes(
            paths.semantic_activation_receipt_path
        )
    except FileNotFoundError:
        existing = None
    if existing == content.encode("utf-8"):
        return
    write_managed_text(paths.semantic_activation_receipt_path, content)


def _approved_root_paths(approval: SourceApproval) -> set[Path]:
    roots: set[Path] = set()
    for uri in approval.approved_source_uris:
        parsed = urlparse(uri)
        if (
            parsed.scheme != "file"
            or parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            continue
        path = Path(unquote(parsed.path))
        if path.is_absolute() and path.as_uri() == uri:
            roots.add(path)
    if not roots:
        raise SemanticIndexError("semantic index root is not approved")
    return roots


def _managed_regular_file_names(directory: Path) -> set[str]:
    try:
        descriptor = open_managed_directory(directory)
    except OSError as error:
        raise SemanticIndexError("semantic managed directory is invalid") from error
    try:
        names = set(os.listdir(descriptor))
        for name in names:
            metadata = os.stat(
                name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SemanticIndexError(
                    "semantic managed directory is invalid"
                )
        return names
    except OSError as error:
        raise SemanticIndexError("semantic managed directory is invalid") from error
    finally:
        os.close(descriptor)


def _read_managed_content(path: Path, label: str) -> bytes:
    try:
        return read_managed_bytes(path)
    except (OSError, UnicodeError) as error:
        raise SemanticIndexError(f"{label} is invalid") from error


def _decode_canonical_object(
    content: bytes,
    label: str,
) -> dict[str, object]:
    def reject_duplicates(pairs):
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise SemanticIndexError(f"{label} is invalid") from error
    if (
        type(value) is not dict
        or content != _canonical_json(value).encode("utf-8")
    ):
        raise SemanticIndexError(f"{label} is invalid")
    return value


def _is_safe_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and _SAFE_IDENTIFIER.fullmatch(value) is not None
    )


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_valid_directory_timeout(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and 0 < value <= MAX_DIRECTORY_TIMEOUT_SECONDS
    )


def _validated_v2_input_manifest_sha256(
    manifest: dict[str, object],
) -> str:
    unsigned = dict(manifest)
    manifest_sha256 = unsigned.pop("manifest_sha256", None)
    capsule_hashes = manifest.get("capsule_chunk_sha256s")
    if (
        set(manifest) != _V2_INPUT_MANIFEST_KEYS
        or manifest.get("version") != 2
        or manifest.get("protocol") != "codex-semantic-v2"
        or not _is_safe_identifier(manifest.get("revision_id"))
        or not _is_safe_identifier(manifest.get("source_id"))
        or not _is_sha256(manifest.get("policy_hash"))
        or not _is_safe_identifier(manifest.get("analyzer_version"))
        or type(capsule_hashes) is not list
        or not capsule_hashes
        or not all(_is_sha256(value) for value in capsule_hashes)
        or not _is_sha256(manifest.get("work_plan_sha256"))
        or not _is_positive_int(manifest.get("node_count"))
        or not _is_positive_int(manifest.get("work_unit_count"))
        or not _is_positive_int(manifest.get("managed_input_bytes"))
        or not _is_nonnegative_int(manifest.get("analysis_target_count"))
        or not _is_nonnegative_int(manifest.get("structural_only_count"))
        or manifest["analysis_target_count"]
        + manifest["structural_only_count"]
        != manifest["node_count"]
        or not _is_valid_directory_timeout(
            manifest.get("directory_timeout_seconds")
        )
        or not _is_nonnegative_int(
            manifest.get("timed_out_directory_count")
        )
        or not _is_nonnegative_int(
            manifest.get("unreadable_directory_count")
        )
        or manifest["timed_out_directory_count"]
        > manifest["unreadable_directory_count"]
        or manifest["unreadable_directory_count"] > manifest["node_count"]
        or not _is_sha256(manifest_sha256)
        or hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
        != manifest_sha256
    ):
        raise SemanticIndexError("semantic index manifest is invalid")
    return str(manifest_sha256)


def _is_safe_presentation_text(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return _safe_output_text(value, "semantic text") == value
    except SemanticIndexError:
        return False


def _is_safe_relative_hierarchy(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or contains_unicode_control(value)
        or contains_hidden_secret_shaped_public_value(value)
        or mask_public_value(value) != value
        or "://" in value
        or value.startswith(("/", "~/"))
        or ".portfolio-maker" in value.casefold()
        or "portfolio.db" in value.casefold()
    ):
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and str(path) == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _is_unique_text_list(
    value: object,
    *,
    identifier: bool = False,
    safe: bool = False,
) -> bool:
    if (
        type(value) is not list
        or len(value) != len(set(value))
        or not all(isinstance(item, str) and item for item in value)
    ):
        return False
    if identifier and not all(_is_safe_identifier(item) for item in value):
        return False
    if safe and not all(_is_safe_presentation_text(item) for item in value):
        return False
    return True


def _require_exact_safe_text(value: str) -> None:
    if _safe_output_text(value, "semantic text") != value:
        raise SemanticIndexError("semantic final node is invalid")


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
        is_analyzable_file = (
            entry.kind is SemanticNodeKind.FILE
            and entry.status is not AnalysisStatus.UNSUPPORTED
        )
        if is_analyzable_file:
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
        if is_analyzable_file:
            try:
                evidence_ids = approved_evidence_by_source_uri.get(
                    _canonical_file_uri(entry.absolute_path), ()
                )
            except (FileNotFoundError, OSError, RuntimeError):
                pass
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


def _analyze_capsule_inputs(
    entries: tuple[StructuralEntry, ...],
    approved_evidence_by_source_uri: dict[str, tuple[int, ...]],
) -> tuple[dict[str, FileAnalysisInput], dict[str, tuple[int, ...]]]:
    analyses: dict[str, FileAnalysisInput] = {}
    evidence_ids_by_node: dict[str, tuple[int, ...]] = {}
    for entry in entries:
        if (
            entry.kind is not SemanticNodeKind.FILE
            or entry.status is AnalysisStatus.UNSUPPORTED
        ):
            continue
        analysis = analyze_file_input(entry)
        analyses[entry.node_id] = analysis
        try:
            evidence_ids = approved_evidence_by_source_uri.get(
                _canonical_file_uri(entry.absolute_path),
                (),
            )
        except (FileNotFoundError, OSError, RuntimeError):
            evidence_ids = ()
        evidence_ids_by_node[entry.node_id] = evidence_ids
    return analyses, evidence_ids_by_node


def _private_revision_chunks(
    revision_id: str,
    entries: tuple[StructuralEntry, ...],
    capsules: tuple[dict[str, object], ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    entry_by_id = {entry.node_id: entry for entry in entries}
    structural_records: list[dict[str, object]] = []
    locator_records: list[dict[str, object]] = []
    for capsule in capsules:
        node_id = str(capsule["node_id"])
        entry = entry_by_id[node_id]
        structural_records.append(
            {
                "node_id": node_id,
                "source_id": entry.source_id,
                "parent_node_id": capsule["parent_node_id"],
                "kind": capsule["kind"],
                "display_name": capsule["display_name"],
                "relative_hierarchy": capsule["relative_hierarchy"],
                "content_fingerprint": capsule["content_fingerprint"],
                "analysis_status": capsule["analysis_status"],
                "semantic_disposition": capsule["semantic_disposition"],
                "roles": capsule["roles"],
                "child_node_ids": capsule["child_node_ids"],
                "skip_reason": capsule["skip_reason"],
                "evidence_ids": capsule["evidence_ids"],
                "coverage": capsule["coverage"],
            }
        )
        locator_records.append(
            {
                "node_id": node_id,
                "locator": str(entry.absolute_path),
                "device": entry.device,
                "inode": entry.inode,
            }
        )
    return (
        _private_chunk_texts(revision_id, "nodes", structural_records),
        _private_chunk_texts(revision_id, "locators", locator_records),
    )


def _private_chunk_texts(
    revision_id: str,
    field: str,
    records: list[dict[str, object]],
) -> tuple[str, ...]:
    return tuple(
        _canonical_json(
            {
                "version": 2,
                "revision_id": revision_id,
                field: records[index : index + STRUCTURAL_CHUNK_SIZE],
            }
        )
        for index in range(0, len(records), STRUCTURAL_CHUNK_SIZE)
    )


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


def _capsule_chunk_payloads(
    revision_id: str,
    capsules: tuple[dict[str, object], ...],
    chunk_size: int,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "version": 2,
            "revision_id": revision_id,
            "capsules": list(capsules[index : index + chunk_size]),
        }
        for index in range(0, len(capsules), chunk_size)
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


def read_semantic_crawl_timeout_review(
    workspace: Path,
    root: Path,
) -> dict[str, object] | None:
    paths = WorkspacePaths.from_root(workspace)
    try:
        approval = load_approval(paths)
    except (ApprovalMissingError, ApprovalFormatError) as error:
        raise SemanticIndexError("semantic index approval is unavailable") from error
    _validate_approved_root(root, approval)
    review = _read_timeout_review(paths)
    if review is None:
        return None
    if (
        review["policy_hash"] != _policy_hash(approval)
        or review["root_source_id"] != _root_source_id(root)
    ):
        raise SemanticIndexError("semantic crawl timeout review is stale")
    return review


def _root_source_id(root: Path) -> str:
    try:
        metadata = root.lstat()
    except OSError as error:
        raise SemanticIndexError("semantic index root is invalid") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise SemanticIndexError("semantic index root is invalid")
    if type(metadata.st_dev) is int and type(metadata.st_ino) is int and metadata.st_ino > 0:
        provider_key = f"{metadata.st_dev}:{metadata.st_ino}"
    else:
        provider_key = "."
    return stable_source_id("local", provider_key)


def _write_timeout_review(
    paths: WorkspacePaths,
    *,
    status: str,
    policy_hash: str,
    root_source_id: str,
    directory_timeout_seconds: float,
    directory_timeout_budget_seconds: float,
    timed_out_directory_count: int,
    unreadable_directory_count: int,
    consumed_timeout_wait_seconds: float,
    timed_out_relative_hierarchies: tuple[str, ...],
    reviewed_failure_sha256: str | None,
) -> str:
    prior_snapshot = _read_timeout_review_snapshot(paths)
    previous_text = None if prior_snapshot is None else prior_snapshot[1]
    content, review_sha256 = _timeout_review_text(
        status=status,
        policy_hash=policy_hash,
        root_source_id=root_source_id,
        directory_timeout_seconds=directory_timeout_seconds,
        directory_timeout_budget_seconds=directory_timeout_budget_seconds,
        timed_out_directory_count=timed_out_directory_count,
        unreadable_directory_count=unreadable_directory_count,
        consumed_timeout_wait_seconds=consumed_timeout_wait_seconds,
        timed_out_relative_hierarchies=timed_out_relative_hierarchies,
        reviewed_failure_sha256=reviewed_failure_sha256,
    )
    try:
        write_managed_text(paths.semantic_crawl_timeout_review_path, content)
        persisted = _read_timeout_review(paths)
        if persisted is None or persisted["review_sha256"] != review_sha256:
            raise SemanticIndexError("semantic crawl timeout review is invalid")
    except Exception:
        try:
            _restore_timeout_review_snapshot(paths, previous_text)
        except Exception as recovery_error:
            raise SemanticIndexError(
                "semantic crawl timeout review recovery failed"
            ) from recovery_error
        raise
    return review_sha256


def _timeout_review_text(
    *,
    status: str,
    policy_hash: str,
    root_source_id: str,
    directory_timeout_seconds: float,
    directory_timeout_budget_seconds: float,
    timed_out_directory_count: int,
    unreadable_directory_count: int,
    consumed_timeout_wait_seconds: float,
    timed_out_relative_hierarchies: tuple[str, ...],
    reviewed_failure_sha256: str | None,
) -> tuple[str, str]:
    unsigned: dict[str, object] = {
        "version": 1,
        "status": status,
        "policy_hash": policy_hash,
        "root_source_id": root_source_id,
        "directory_timeout_seconds": directory_timeout_seconds,
        "directory_timeout_budget_seconds": directory_timeout_budget_seconds,
        "timed_out_directory_count": timed_out_directory_count,
        "unreadable_directory_count": unreadable_directory_count,
        "consumed_timeout_wait_seconds": consumed_timeout_wait_seconds,
        "timed_out_relative_hierarchies": list(
            sorted(set(timed_out_relative_hierarchies))
        ),
        "reviewed_failure_sha256": reviewed_failure_sha256,
    }
    review_sha256 = hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()
    review = {**unsigned, "review_sha256": review_sha256}
    content = _canonical_json(review)
    if len(content.encode("utf-8")) > _MAX_TIMEOUT_REVIEW_BYTES:
        raise SemanticIndexError("semantic crawl timeout review is invalid")
    _validate_timeout_review(review)
    return content, review_sha256


def _read_timeout_review(paths: WorkspacePaths) -> dict[str, object] | None:
    snapshot = _read_timeout_review_snapshot(paths)
    return None if snapshot is None else snapshot[0]


def _read_timeout_review_snapshot(
    paths: WorkspacePaths,
) -> tuple[dict[str, object], str] | None:
    try:
        snapshot = read_managed_relative_file(
            paths.root,
            Path("reviews") / paths.semantic_crawl_timeout_review_path.name,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SemanticIndexError("semantic crawl timeout review is invalid") from error
    if snapshot.size > _MAX_TIMEOUT_REVIEW_BYTES:
        raise SemanticIndexError("semantic crawl timeout review is invalid")
    review = _decode_canonical_object(
        snapshot.content,
        "semantic crawl timeout review",
    )
    _validate_timeout_review(review)
    return review, snapshot.content.decode("utf-8")


@dataclass(frozen=True)
class _TimeoutReviewUpdate:
    previous_text: str
    resolved_text: str


def _prepare_timeout_review_resolution(
    paths: WorkspacePaths,
    *,
    policy_hash: str,
    root_source_id: str,
    directory_timeout_seconds: float,
    directory_timeout_budget_seconds: float,
) -> _TimeoutReviewUpdate | None:
    prior_snapshot = _read_timeout_review_snapshot(paths)
    if prior_snapshot is None:
        return None
    prior, previous_text = prior_snapshot
    _assert_timeout_review_root_is_current(paths, root_source_id, prior=prior)
    reviewed_failure_sha256: str | None = None
    if prior["status"] == "approval_required":
        reviewed_failure_sha256 = str(prior["review_sha256"])
    elif prior["reviewed_failure_sha256"] is not None:
        reviewed_failure_sha256 = str(prior["reviewed_failure_sha256"])
    resolved_text, _review_sha256 = _timeout_review_text(
        status="resolved",
        policy_hash=policy_hash,
        root_source_id=root_source_id,
        directory_timeout_seconds=directory_timeout_seconds,
        directory_timeout_budget_seconds=directory_timeout_budget_seconds,
        timed_out_directory_count=0,
        unreadable_directory_count=0,
        consumed_timeout_wait_seconds=0.0,
        timed_out_relative_hierarchies=(),
        reviewed_failure_sha256=reviewed_failure_sha256,
    )
    return _TimeoutReviewUpdate(
        previous_text=previous_text,
        resolved_text=resolved_text,
    )


def _assert_timeout_review_root_is_current(
    paths: WorkspacePaths,
    root_source_id: str,
    *,
    prior: dict[str, object] | None = None,
) -> None:
    current = _read_timeout_review(paths) if prior is None else prior
    if current is not None and current["root_source_id"] != root_source_id:
        raise SemanticIndexError("semantic crawl timeout review is stale")


def _validate_timeout_review(review: dict[str, object]) -> None:
    unsigned = dict(review)
    review_sha256 = unsigned.pop("review_sha256", None)
    timed_out_relative_hierarchies = review.get(
        "timed_out_relative_hierarchies"
    )
    reviewed_failure_sha256 = review.get("reviewed_failure_sha256")
    status = review.get("status")
    if (
        set(review) != _TIMEOUT_REVIEW_KEYS
        or type(review.get("version")) is not int
        or review.get("version") != 1
        or status not in {"approval_required", "resolved"}
        or not _is_sha256(review.get("policy_hash"))
        or not _is_source_id(review.get("root_source_id"))
        or not _is_valid_directory_timeout(
            review.get("directory_timeout_seconds")
        )
        or not _is_valid_directory_timeout_budget(
            review.get("directory_timeout_budget_seconds")
        )
        or not _is_nonnegative_int(review.get("timed_out_directory_count"))
        or not _is_nonnegative_int(review.get("unreadable_directory_count"))
        or review["timed_out_directory_count"]
        > review["unreadable_directory_count"]
        or type(review.get("consumed_timeout_wait_seconds"))
        not in {int, float}
        or not math.isfinite(review["consumed_timeout_wait_seconds"])
        or review["consumed_timeout_wait_seconds"] < 0
        or review["consumed_timeout_wait_seconds"]
        > review["directory_timeout_budget_seconds"]
        or type(timed_out_relative_hierarchies) is not list
        or timed_out_relative_hierarchies
        != sorted(set(timed_out_relative_hierarchies))
        or not all(
            _is_valid_timeout_review_relative_hierarchy(value)
            for value in timed_out_relative_hierarchies
        )
        or reviewed_failure_sha256 is not None
        and not _is_sha256(reviewed_failure_sha256)
        or not _is_sha256(review_sha256)
        or hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
        != review_sha256
        or status == "approval_required"
        and (
            not timed_out_relative_hierarchies
            or review["timed_out_directory_count"]
            != len(timed_out_relative_hierarchies)
            or review["consumed_timeout_wait_seconds"]
            != review["directory_timeout_budget_seconds"]
            or reviewed_failure_sha256 is not None
        )
        or status == "resolved"
        and (
            timed_out_relative_hierarchies
            or review["timed_out_directory_count"] != 0
            or review["unreadable_directory_count"] != 0
            or review["consumed_timeout_wait_seconds"] != 0.0
        )
    ):
        raise SemanticIndexError("semantic crawl timeout review is invalid")


def _is_valid_directory_timeout_budget(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS <= value
        <= MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS
    )


def _is_valid_timeout_review_relative_hierarchy(value: object) -> bool:
    if not isinstance(value, str) or not value or contains_unicode_control(value):
        return False
    if value == ".":
        return True
    return (
        "\\" not in value
        and not value.startswith("/")
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _is_source_id(value: object) -> bool:
    return isinstance(value, str) and _FINGERPRINT.fullmatch(value) is not None


def _redact_locators(value: str) -> str:
    value = _LOCATOR_VALUE.sub(r"\1[REDACTED]", value)
    value = _CREDENTIAL_VALUE.sub(r"\1[REDACTED]", value)
    return _LOCATOR_TOKEN.sub("[REDACTED]", value)


@dataclass(frozen=True)
class _PublishedSemanticInput:
    manifest_text: str | None
    chunk_texts: dict[Path, str]
    work_plan_text: str | None


def _publish_semantic_input(
    repository: SQLiteRepository,
    paths: WorkspacePaths,
    chunk_texts: tuple[str, ...],
    manifest_text: str,
    *,
    work_plan_text: str,
    revision_id: str,
    structural_texts: tuple[str, ...],
    locator_texts: tuple[str, ...],
    timeout_review_update: _TimeoutReviewUpdate | None,
) -> None:
    with repository._repository_critical_section():
        ensure_managed_directory(paths.semantic_index_dir)
        ensure_managed_directory(paths.semantic_index_input_dir)
        structural_dir = paths.semantic_revision_structural_dir(revision_id)
        locator_dir = paths.semantic_revision_locator_dir(revision_id)
        ensure_managed_directory(structural_dir)
        ensure_managed_directory(locator_dir)
        previous = _read_published_semantic_input_unlocked(paths)
        if timeout_review_update is not None:
            current_review = _read_timeout_review_snapshot(paths)
            if (
                current_review is None
                or current_review[1] != timeout_review_update.previous_text
            ):
                raise SemanticIndexError("semantic crawl timeout review is stale")
        current = {
            paths.semantic_index_input_dir / f"chunk-{index:04}.json": chunk_text
            for index, chunk_text in enumerate(chunk_texts, start=1)
        }

        try:
            for index, text in enumerate(structural_texts, start=1):
                write_managed_text(
                    structural_dir / f"chunk-{index:04}.json",
                    text,
                    overwrite=False,
                )
            for index, text in enumerate(locator_texts, start=1):
                write_managed_text(
                    locator_dir / f"chunk-{index:04}.json",
                    text,
                    overwrite=False,
                )
            for path, chunk_text in current.items():
                write_managed_text(path, chunk_text)
            for path in previous.chunk_texts.keys() - current.keys():
                remove_managed_file(path)
            write_managed_text(paths.semantic_work_plan_path, work_plan_text)
            if timeout_review_update is not None:
                write_managed_text(
                    paths.semantic_crawl_timeout_review_path,
                    timeout_review_update.resolved_text,
                )
                resolved_review = _read_timeout_review_snapshot(paths)
                if (
                    resolved_review is None
                    or resolved_review[1]
                    != timeout_review_update.resolved_text
                ):
                    raise SemanticIndexError(
                        "semantic crawl timeout review is invalid"
                    )
            write_managed_text(paths.semantic_index_manifest_path, manifest_text)
        except Exception:
            restore_error: Exception | None = None
            try:
                _restore_published_semantic_input(paths, previous, current)
            except Exception as error:
                restore_error = error
            try:
                _restore_timeout_review(paths, timeout_review_update)
            except Exception as error:
                restore_error = restore_error or error
            if restore_error is not None:
                raise SemanticIndexError(
                    "semantic index publication failed safely"
                ) from restore_error
            raise


def _read_published_semantic_input(paths: WorkspacePaths) -> _PublishedSemanticInput:
    repository = SQLiteRepository(paths.db_path)
    with repository._repository_critical_section():
        return _read_published_semantic_input_unlocked(paths)


def _read_published_semantic_input_unlocked(
    paths: WorkspacePaths,
) -> _PublishedSemanticInput:
    try:
        work_plan_text = read_managed_bytes(paths.semantic_work_plan_path).decode(
            "utf-8"
        )
    except FileNotFoundError:
        work_plan_text = None
    try:
        manifest_text = read_managed_bytes(paths.semantic_index_manifest_path).decode("utf-8")
    except FileNotFoundError:
        if (
            tuple(paths.semantic_index_input_dir.glob("chunk-*.json"))
            or work_plan_text is not None
        ):
            raise SemanticIndexError("semantic index input has unverified managed chunks")
        return _PublishedSemanticInput(
            manifest_text=None,
            chunk_texts={},
            work_plan_text=None,
        )

    try:
        manifest = json.loads(manifest_text)
        if type(manifest) is not dict:
            raise TypeError
        if manifest.get("version") == 2:
            _validated_v2_input_manifest_sha256(manifest)
        chunk_sha256s = (
            manifest["capsule_chunk_sha256s"]
            if manifest.get("version") == 2
            else manifest["chunk_sha256s"]
        )
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
    if manifest.get("version") == 2:
        work_plan_sha256 = manifest.get("work_plan_sha256")
        if (
            work_plan_text is None
            or not _is_sha256(work_plan_sha256)
            or hashlib.sha256(work_plan_text.encode("utf-8")).hexdigest()
            != work_plan_sha256
        ):
            raise SemanticIndexError("semantic index work plan is invalid")
    return _PublishedSemanticInput(
        manifest_text=manifest_text,
        chunk_texts=chunk_texts,
        work_plan_text=work_plan_text,
    )


def _restore_published_semantic_input(
    paths: WorkspacePaths,
    previous: _PublishedSemanticInput,
    current: dict[Path, str],
) -> None:
    for path, chunk_text in previous.chunk_texts.items():
        write_managed_text(path, chunk_text)
    for path in current.keys() - previous.chunk_texts.keys():
        remove_managed_file(path, missing_ok=True)
    if previous.work_plan_text is None:
        remove_managed_file(paths.semantic_work_plan_path, missing_ok=True)
    else:
        write_managed_text(paths.semantic_work_plan_path, previous.work_plan_text)
    if previous.manifest_text is None:
        remove_managed_file(paths.semantic_index_manifest_path, missing_ok=True)
    else:
        write_managed_text(paths.semantic_index_manifest_path, previous.manifest_text)


def _restore_timeout_review(
    paths: WorkspacePaths,
    update: _TimeoutReviewUpdate | None,
) -> None:
    if update is None:
        return
    _restore_timeout_review_snapshot(paths, update.previous_text)


def _restore_timeout_review_snapshot(
    paths: WorkspacePaths,
    previous_text: str | None,
) -> None:
    if previous_text is None:
        remove_managed_file(paths.semantic_crawl_timeout_review_path, missing_ok=True)
        if _read_timeout_review_snapshot(paths) is not None:
            raise SemanticIndexError("semantic crawl timeout review is invalid")
        return
    write_managed_text(paths.semantic_crawl_timeout_review_path, previous_text)
    restored = _read_timeout_review_snapshot(paths)
    if restored is None or restored[1] != previous_text:
        raise SemanticIndexError("semantic crawl timeout review is invalid")


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
