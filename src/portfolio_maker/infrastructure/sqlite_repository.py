from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
from itertools import zip_longest
import json
import os
import sqlite3
import threading
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Literal

from portfolio_maker.domain.history_models import (
    CleanupExecutionState,
    HistoryDatabaseFamilyIdentity,
    HistoryDatabaseSidecarIdentity,
    HistoryExportArchiveBinding,
    HistoryPlan,
    HistoryPolicy,
    HistoryUnit,
    utc_now,
)
from portfolio_maker.domain.models import (
    GitHubActivity,
    PublicEvidenceRecord,
    Source,
    SourceStatus,
    SourceType,
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
)
from portfolio_maker.domain.semantic_frontier import (
    FrontierAction,
    FrontierPhase,
    SemanticAuditFreeze,
    SemanticBudget,
    SemanticCheckpoint,
    SemanticCompletion,
    SemanticExecutionProposal,
    SemanticLeaseState,
    SemanticPlannedUnit,
    SemanticPhaseBinding,
    SemanticProposalEnvelope,
    SemanticReceiptCommit,
    SemanticRevisionBinding,
    SemanticRunState,
    SemanticRuntimeDeferredRow,
    SemanticStatusSnapshot,
    SemanticUnitState,
    semantic_canonical_json_bytes,
    semantic_execution_proposal_from_payload,
    semantic_proposal_sha256,
)
from portfolio_maker.domain.vision_models import (
    AppliedVisionAnalysisResult,
    PreparedVisionRequest,
    VisionAnalysisResult,
    VisionRequestStatus,
    VisionReviewStatus,
)
from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
    is_valid_github_activity_state,
)
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    open_managed_directory,
)
from portfolio_maker.infrastructure.vision_store import (
    PersistedVisionAnalysisBinding,
    VisionStore,
)
from portfolio_maker.workspace import WorkspacePaths


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('local_file', 'github_repository')),
    uri TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    owner TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'discovered', 'approved', 'ingested', 'skipped_policy', 'extract_failed', 'stale_source'
    )),
    origin_type TEXT NOT NULL DEFAULT 'local',
    origin_visibility TEXT NOT NULL DEFAULT 'unknown',
    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    snapshot_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS github_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id),
    repo TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    merged_at TEXT,
    is_private INTEGER NOT NULL DEFAULT 0 CHECK(is_private IN (0, 1)),
    state_field TEXT CHECK(state_field IS NULL OR state_field IN ('conclusion', 'status')),
    origin_type TEXT NOT NULL DEFAULT 'public_github',
    origin_visibility TEXT NOT NULL DEFAULT 'unknown',
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    UNIQUE(repo, activity_type, url)
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id),
    snapshot_id INTEGER REFERENCES source_snapshots(id),
    github_activity_id INTEGER REFERENCES github_activities(id),
    locator TEXT NOT NULL,
    stable_id TEXT NOT NULL UNIQUE,
    content_hash TEXT,
    public_safe INTEGER NOT NULL DEFAULT 0 CHECK(public_safe IN (0, 1)),
    origin_type TEXT NOT NULL DEFAULT 'local',
    origin_visibility TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK(source_id IS NOT NULL OR github_activity_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    public_safe INTEGER NOT NULL DEFAULT 0 CHECK(public_safe IN (0, 1)),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    overview TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'approved'),
    approval_sha256 TEXT NOT NULL,
    review_input_sha256 TEXT NOT NULL,
    decision_status TEXT NOT NULL DEFAULT 'manually_approved',
    decision_origin TEXT NOT NULL DEFAULT 'manual',
    confidence TEXT,
    boundary_fingerprint TEXT,
    candidate_input_sha256 TEXT,
    index_revision TEXT,
    decision_updated_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_project_evidence (
    project_id TEXT NOT NULL REFERENCES portfolio_projects(id) ON DELETE CASCADE,
    evidence_id INTEGER NOT NULL REFERENCES evidence_items(id),
    support_level TEXT NOT NULL CHECK(support_level IN ('direct', 'contextual')),
    PRIMARY KEY (project_id, evidence_id),
    UNIQUE (evidence_id)
);

CREATE TABLE IF NOT EXISTS career_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    text TEXT NOT NULL,
    public_safe INTEGER NOT NULL DEFAULT 0 CHECK(public_safe IN (0, 1)),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id INTEGER NOT NULL REFERENCES career_claims(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence_items(id),
    support_level TEXT NOT NULL CHECK(support_level IN ('direct', 'contextual')),
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    input_manifest TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS semantic_index_revisions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('staging','active','superseded','failed')),
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS semantic_nodes (
    revision_id TEXT NOT NULL REFERENCES semantic_index_revisions(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    node_kind TEXT NOT NULL,
    parent_node_id TEXT,
    display_name TEXT NOT NULL,
    relative_hierarchy TEXT NOT NULL,
    content_fingerprint TEXT,
    semantic_summary TEXT NOT NULL DEFAULT '',
    semantic_roles_json TEXT NOT NULL DEFAULT '[]',
    topics_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    analysis_status TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    semantic_disposition TEXT NOT NULL DEFAULT 'structural_only',
    semantic_facts_json TEXT NOT NULL DEFAULT '{}',
    analysis_protocol TEXT NOT NULL DEFAULT 'legacy-v1',
    skip_reason TEXT,
    child_node_ids_json TEXT NOT NULL DEFAULT '[]',
    coverage_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (revision_id, node_id)
);

CREATE TABLE IF NOT EXISTS semantic_node_locators (
    revision_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    locator TEXT NOT NULL,
    device INTEGER,
    inode INTEGER,
    PRIMARY KEY (revision_id, node_id),
    FOREIGN KEY (revision_id, node_id)
      REFERENCES semantic_nodes(revision_id, node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_edges (
    revision_id TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    confidence TEXT,
    PRIMARY KEY (revision_id, from_node_id, relation, to_node_id)
);

CREATE TABLE IF NOT EXISTS semantic_import_receipts (
    revision_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_sha256 TEXT NOT NULL,
    locator_chunk_sha256 TEXT NOT NULL,
    node_ids_sha256 TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (revision_id, chunk_index),
    FOREIGN KEY (revision_id)
      REFERENCES semantic_index_revisions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vision_analysis_requests (
    id TEXT PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_content_hash TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'completed', 'unsupported', 'unavailable', 'failed', 'cancelled',
        'stale_source'
    )),
    confirmation_at TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    reason_code TEXT
);

CREATE TABLE IF NOT EXISTS vision_analyses (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES vision_analysis_requests(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_content_hash TEXT NOT NULL,
    result_hash TEXT,
    relative_path TEXT,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT,
    review_status TEXT NOT NULL CHECK(review_status IN ('pending', 'included', 'excluded')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS history_cleanup_plans (
    id TEXT PRIMARY KEY,
    policy_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    canonical_policy_json TEXT NOT NULL,
    canonical_units_json TEXT NOT NULL,
    manifest_relative_path TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    export_receipt_hash TEXT,
    export_archive_binding_json TEXT,
    execution_id TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS history_cleanup_journal_entries (
    execution_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    stage TEXT NOT NULL,
    unit_kind TEXT NOT NULL,
    row_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (execution_id, sequence)
);

"""


SEMANTIC_V3_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_v3_revision_bindings (revision_id TEXT PRIMARY KEY REFERENCES semantic_index_revisions(id), manifest_sha256 TEXT NOT NULL, source_policy_sha256 TEXT NOT NULL, source_approval_sha256 TEXT, catalog_file_sha256 TEXT NOT NULL, catalog_logical_root TEXT NOT NULL, lifecycle_schema_version TEXT NOT NULL, lifecycle_genesis_root TEXT NOT NULL, current_checkpoint_root TEXT NOT NULL, protocol TEXT NOT NULL CHECK(protocol = 'codex-semantic-v3'));
CREATE TABLE IF NOT EXISTS semantic_v3_proposals (run_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES semantic_v3_revision_bindings(revision_id), approval_kind TEXT NOT NULL CHECK(approval_kind IN ('frontier','project_deepening')), project_id TEXT, proposal_canonical_json BLOB NOT NULL, proposal_sha256 TEXT NOT NULL UNIQUE, plan_canonical_json BLOB NOT NULL, plan_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, source_policy_sha256 TEXT NOT NULL, source_approval_sha256 TEXT, catalog_root TEXT NOT NULL, selector_version TEXT NOT NULL, private_context_scope TEXT NOT NULL, authority_root TEXT NOT NULL, parent_checkpoint_root TEXT, project_boundary_sha256 TEXT, parent_proposal_sha256 TEXT, evidence_policy_sha256 TEXT, total_budget_json TEXT NOT NULL, phase_bindings_json TEXT NOT NULL, budget_binding_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('proposed','approved','stale')), created_at TEXT NOT NULL, UNIQUE(run_id, proposal_sha256));
CREATE TABLE IF NOT EXISTS semantic_v3_runs (run_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES semantic_v3_revision_bindings(revision_id), approval_kind TEXT NOT NULL CHECK(approval_kind IN ('frontier','project_deepening')), project_id TEXT, state TEXT NOT NULL CHECK(state IN ('prepared','awaiting_approval','running','paused_user','paused_budget','blocked','converged','finalizing','finalized','failed')), phase TEXT NOT NULL CHECK(phase IN ('orientation','expansion','audit','deepening')), proposal_sha256 TEXT NOT NULL, used_units INTEGER NOT NULL DEFAULT 0, used_input_bytes INTEGER NOT NULL DEFAULT 0, used_active_seconds REAL NOT NULL DEFAULT 0, result_pack_length INTEGER NOT NULL DEFAULT 0, result_pack_root TEXT NOT NULL, FOREIGN KEY(run_id, proposal_sha256) REFERENCES semantic_v3_proposals(run_id, proposal_sha256));
CREATE UNIQUE INDEX IF NOT EXISTS semantic_v3_one_running_run ON semantic_v3_runs(state) WHERE state = 'running';
CREATE TABLE IF NOT EXISTS semantic_v3_execution_lock (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id), unit_id TEXT NOT NULL, lease_nonce TEXT NOT NULL, lease_state TEXT NOT NULL CHECK(lease_state IN ('materializing','issued')));
CREATE TABLE IF NOT EXISTS semantic_v3_units (run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id), unit_id TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('orientation','expansion','audit','deepening')), state TEXT NOT NULL CHECK(state IN ('ready','leased','validated','deferred','error')), priority INTEGER NOT NULL, fairness_lane TEXT NOT NULL, target_ref_json TEXT NOT NULL, target_ref_sha256 TEXT NOT NULL, dependency_root TEXT NOT NULL, estimated_input_bytes INTEGER NOT NULL CHECK(estimated_input_bytes >= 0), terminal_reason TEXT, PRIMARY KEY(run_id, unit_id));
CREATE INDEX IF NOT EXISTS semantic_v3_units_ready ON semantic_v3_units(run_id, state, phase, priority, fairness_lane, unit_id);
CREATE TABLE IF NOT EXISTS semantic_v3_dependencies (run_id TEXT NOT NULL, unit_id TEXT NOT NULL, dependency_unit_id TEXT NOT NULL, dependency_digest TEXT NOT NULL, PRIMARY KEY(run_id, unit_id, dependency_unit_id), FOREIGN KEY(run_id, unit_id) REFERENCES semantic_v3_units(run_id, unit_id));
CREATE INDEX IF NOT EXISTS semantic_v3_dependencies_reverse ON semantic_v3_dependencies(run_id, dependency_unit_id, unit_id);
CREATE TABLE IF NOT EXISTS semantic_v3_leases (run_id TEXT NOT NULL, unit_id TEXT NOT NULL, lease_nonce TEXT NOT NULL UNIQUE, state TEXT NOT NULL CHECK(state IN ('materializing','issued','expired')), input_sha256 TEXT, input_bytes INTEGER, lease_started_active_seconds REAL NOT NULL, lease_deadline_active_seconds REAL NOT NULL, PRIMARY KEY(run_id, unit_id), FOREIGN KEY(run_id, unit_id) REFERENCES semantic_v3_units(run_id, unit_id));
CREATE TABLE IF NOT EXISTS semantic_v3_receipts (run_id TEXT NOT NULL, unit_id TEXT NOT NULL, sequence INTEGER NOT NULL, lease_nonce TEXT NOT NULL, input_sha256 TEXT NOT NULL, input_bytes INTEGER NOT NULL DEFAULT 0, output_sha256 TEXT NOT NULL, result_offset INTEGER NOT NULL, result_length INTEGER NOT NULL, result_sha256 TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('accept_boundary','expand_children','stop_non_project','defer_on_demand')), validator_version TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, active_seconds REAL NOT NULL DEFAULT 0, commit_canonical_json BLOB, commit_sha256 TEXT, PRIMARY KEY(run_id, unit_id), UNIQUE(run_id, sequence));
CREATE TABLE IF NOT EXISTS semantic_v3_aggregates (run_id TEXT PRIMARY KEY REFERENCES semantic_v3_runs(run_id), ready_count INTEGER NOT NULL, leased_count INTEGER NOT NULL, validated_count INTEGER NOT NULL, deferred_count INTEGER NOT NULL, error_count INTEGER NOT NULL, used_units INTEGER NOT NULL, used_input_bytes INTEGER NOT NULL, used_active_seconds REAL NOT NULL, phase_usage_json TEXT NOT NULL, completion_json TEXT NOT NULL, aggregate_root TEXT NOT NULL, last_verified_at TEXT);
CREATE TABLE IF NOT EXISTS semantic_v3_checkpoints (run_id TEXT NOT NULL, sequence INTEGER NOT NULL, receipt_sequence INTEGER NOT NULL, result_pack_length INTEGER NOT NULL, result_pack_root TEXT NOT NULL, queue_root TEXT NOT NULL, aggregate_root TEXT NOT NULL, resume_cursor_json TEXT NOT NULL, audit_ordered_set_sha256 TEXT, audit_population_sha256 TEXT, selector_version TEXT NOT NULL, previous_checkpoint_root TEXT NOT NULL, checkpoint_root TEXT NOT NULL, PRIMARY KEY(run_id, sequence), UNIQUE(run_id, checkpoint_root));
CREATE INDEX IF NOT EXISTS semantic_v3_checkpoints_lookup ON semantic_v3_checkpoints(run_id, sequence, checkpoint_root);
CREATE TABLE IF NOT EXISTS semantic_v3_approvals (run_id TEXT PRIMARY KEY REFERENCES semantic_v3_runs(run_id), approval_kind TEXT NOT NULL CHECK(approval_kind IN ('frontier','project_deepening')), proposal_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, source_policy_sha256 TEXT NOT NULL, source_approval_sha256 TEXT, catalog_root TEXT NOT NULL, selector_version TEXT NOT NULL, private_context_scope TEXT NOT NULL, parent_checkpoint_root TEXT, project_boundary_sha256 TEXT, parent_proposal_sha256 TEXT, evidence_policy_sha256 TEXT, total_budget_json TEXT NOT NULL, phase_bindings_json TEXT NOT NULL, budget_binding_sha256 TEXT NOT NULL, project_id TEXT, user_confirmed INTEGER NOT NULL CHECK(user_confirmed = 1), approved_at TEXT NOT NULL, FOREIGN KEY(run_id, proposal_sha256) REFERENCES semantic_v3_proposals(run_id, proposal_sha256));
CREATE TABLE IF NOT EXISTS semantic_v3_deferred (run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id), set_kind TEXT NOT NULL CHECK(set_kind IN ('initial_catalog','runtime_transition')), deferred_count INTEGER NOT NULL, deferred_root TEXT NOT NULL, deferred_cursor_root TEXT NOT NULL, current_cursor_json TEXT NOT NULL, PRIMARY KEY(run_id, set_kind));
CREATE TABLE IF NOT EXISTS semantic_v3_runtime_deferred (run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id), node_id TEXT NOT NULL, reason TEXT NOT NULL, node_digest TEXT NOT NULL, cursor_json TEXT NOT NULL, cursor_order INTEGER NOT NULL, source_id TEXT NOT NULL, coverage_stratum TEXT NOT NULL, relevance_rank INTEGER NOT NULL, selector_version TEXT NOT NULL, selector_feature_digest TEXT NOT NULL, source_transition_unit_id TEXT NOT NULL, created_sequence INTEGER NOT NULL, PRIMARY KEY(run_id, node_id), UNIQUE(run_id, cursor_order), FOREIGN KEY(run_id, source_transition_unit_id) REFERENCES semantic_v3_units(run_id, unit_id));
CREATE INDEX IF NOT EXISTS semantic_v3_runtime_deferred_sequence ON semantic_v3_runtime_deferred(run_id, created_sequence, cursor_order, node_id);
CREATE INDEX IF NOT EXISTS semantic_v3_runtime_deferred_audit_order ON semantic_v3_runtime_deferred(run_id, relevance_rank, coverage_stratum, source_id, cursor_order, node_id);
CREATE TABLE IF NOT EXISTS semantic_v3_expansion_cursors (run_id TEXT NOT NULL, lane TEXT NOT NULL, last_priority INTEGER NOT NULL, last_node_id TEXT NOT NULL, population_digest TEXT NOT NULL, PRIMARY KEY(run_id, lane));
CREATE TABLE IF NOT EXISTS semantic_v3_audit_members (run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id), ordinal INTEGER NOT NULL, stratum TEXT NOT NULL CHECK(stratum IN ('relevance','coverage','deterministic_random')), node_id TEXT NOT NULL, node_digest TEXT NOT NULL, audit_unit_id TEXT NOT NULL, PRIMARY KEY(run_id, ordinal), UNIQUE(run_id, node_id));
CREATE INDEX IF NOT EXISTS semantic_v3_audit_unit_members ON semantic_v3_audit_members(run_id, audit_unit_id, ordinal);
"""


class RepositoryError(RuntimeError):
    pass


class CommitOutcomeAmbiguousError(RepositoryError):
    pass


class DatabaseFamilyChangedError(RepositoryError):
    pass


_DATABASE_SIDECARS = ("-journal", "-wal", "-shm")
_REPOSITORY_OPERATION_LOCK = threading.RLock()
_REPOSITORY_OPERATION_STATE = threading.local()
_ACTIVE_PORTFOLIO_PROJECT_DECISION_STATUSES = (
    "manually_approved",
    "auto_included_high",
    "auto_included_medium",
)
_PORTFOLIO_PROJECT_DECISION_STATUSES = frozenset(
    {
        "manually_approved",
        "auto_included_high",
        "auto_included_medium",
        "excluded",
        "inactive",
        "review_required",
    }
)
_PORTFOLIO_PROJECT_DECISION_ORIGINS = frozenset({"manual", "automatic"})
_PORTFOLIO_PROJECT_CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
_HISTORY_UNIT_KINDS = frozenset(
    {"source_snapshot", "vision_analysis", "vision_request"}
)
_HISTORY_UNIT_ROOTS = {
    "source_snapshot": ("raw", "snapshots", "local"),
    "vision_analysis": ("derived", "vision"),
    "vision_request": ("reviews", "vision-requests"),
}
_HISTORY_POLICY_FIELDS = frozenset(
    {
        "minimum_age_days",
        "keep_latest_text_snapshots_per_source",
        "retain_included_vision_analyses",
        "retain_pending_review_vision_analyses",
        "retain_incomplete_vision_requests",
    }
)
_HISTORY_UNIT_FIELDS = frozenset(
    {
        "kind",
        "row_id",
        "relative_path",
        "content_hash",
        "size",
        "device",
        "inode",
        "reason",
    }
)


@dataclass(frozen=True)
class _DatabaseIdentity:
    directory_device: int
    directory_inode: int
    device: int
    inode: int
    sidecars: tuple[tuple[str, int | None, int | None], ...]


class SQLiteRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def exclusive_workspace_operation(self) -> Iterator[None]:
        with self._repository_critical_section():
            yield

    @contextmanager
    def pinned_history_cleanup_operation(
        self,
        expected_family: HistoryDatabaseFamilyIdentity | None = None,
    ) -> Iterator[HistoryDatabaseFamilyIdentity]:
        expected_identity = (
            None
            if expected_family is None
            else self._database_identity_from_history(expected_family)
        )
        with self._existing_database_operation(
            expected_identity=expected_identity,
            require_complete=True,
        ) as (directory_descriptor, identity):
            key = self._operation_key()
            pins = getattr(_REPOSITORY_OPERATION_STATE, "database_pins", {})
            if key in pins:
                raise RepositoryError(
                    "history database family is already pinned"
                )
            pin_descriptors = getattr(
                _REPOSITORY_OPERATION_STATE,
                "database_pin_descriptors",
                {},
            )
            pins[key] = identity
            pin_descriptors[key] = directory_descriptor
            _REPOSITORY_OPERATION_STATE.database_pins = pins
            _REPOSITORY_OPERATION_STATE.database_pin_descriptors = (
                pin_descriptors
            )
            try:
                self._validate_database_family(
                    directory_descriptor,
                    identity,
                    "before history cleanup",
                )
                yield self._history_database_family(identity)
                current_identity = (
                    self._pinned_database_identity() or identity
                )
                self._validate_database_family(
                    directory_descriptor,
                    current_identity,
                    "after history cleanup",
                )
            finally:
                del pin_descriptors[key]
                del pins[key]

    @contextmanager
    def exact_history_plan_binding(
        self,
        plan: HistoryPlan,
        receipt_hash: str,
    ) -> Iterator[None]:
        self._validate_history_hex(receipt_hash, 64)
        self._canonical_history_plan_metadata(plan.policy, plan.units)
        key = self._operation_key()
        bindings = getattr(
            _REPOSITORY_OPERATION_STATE,
            "history_plan_bindings",
            {},
        )
        if key in bindings:
            raise RepositoryError(
                "history cleanup plan binding is already active"
            )
        bindings[key] = (plan, receipt_hash)
        _REPOSITORY_OPERATION_STATE.history_plan_bindings = bindings
        try:
            yield
        finally:
            del bindings[key]

    def assert_pinned_history_database_family(self) -> None:
        identity = self._pinned_database_identity()
        if identity is None:
            raise RepositoryError("history database family is not pinned")
        directory_descriptor = open_managed_directory(self.db_path.parent)
        try:
            self._validate_database_family(
                directory_descriptor,
                identity,
                "during history cleanup",
            )
        finally:
            os.close(directory_descriptor)

    def release_pinned_history_database_directory_lock(self) -> None:
        identity = self._pinned_database_identity()
        descriptors = getattr(
            _REPOSITORY_OPERATION_STATE,
            "database_pin_descriptors",
            {},
        )
        directory_descriptor = descriptors.get(self._operation_key())
        if identity is None or not isinstance(directory_descriptor, int):
            raise RepositoryError("history database family is not pinned")
        self._validate_database_family(
            directory_descriptor,
            identity,
            "before VACUUM",
        )
        self._unlock_database_directory(directory_descriptor)

    def refresh_pinned_history_database_family(
        self,
    ) -> HistoryDatabaseFamilyIdentity:
        identity = self._pinned_database_identity()
        descriptors = getattr(
            _REPOSITORY_OPERATION_STATE,
            "database_pin_descriptors",
            {},
        )
        directory_descriptor = descriptors.get(self._operation_key())
        if identity is None or not isinstance(directory_descriptor, int):
            raise RepositoryError("history database family is not pinned")
        self._validate_database_main_identity(
            directory_descriptor,
            identity,
            "after VACUUM",
        )
        self._ensure_database_sidecars(directory_descriptor)
        refreshed = self._capture_database_identity(
            directory_descriptor,
            require_complete=True,
        )
        if (
            refreshed.directory_device,
            refreshed.directory_inode,
            refreshed.device,
            refreshed.inode,
        ) != (
            identity.directory_device,
            identity.directory_inode,
            identity.device,
            identity.inode,
        ):
            raise self._changed_database_error("after VACUUM")
        pins = getattr(_REPOSITORY_OPERATION_STATE, "database_pins", {})
        pins[self._operation_key()] = refreshed
        return self._history_database_family(refreshed)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn: sqlite3.Connection | None = None
        commit_started = False
        try:
            with self._database_operation() as (directory_descriptor, identity):
                try:
                    conn = self._connect_validated(directory_descriptor, identity, read_only=False)
                    self._configure_write_journal(conn)
                    self._validate_database_family(directory_descriptor, identity, "after journal setup")
                    conn.execute("BEGIN IMMEDIATE")
                    self._validate_database_family(directory_descriptor, identity, "after write transaction")
                    yield conn
                    self._validate_database_family(directory_descriptor, identity, "before commit")
                    commit_started = True
                    conn.commit()
                    self._validate_database_family(directory_descriptor, identity, "after commit")
                except BaseException:
                    if not commit_started:
                        self._rollback(conn)
                    raise
                finally:
                    if conn is not None:
                        conn.close()
        except RepositoryError as error:
            self._rollback(conn)
            if commit_started and not isinstance(
                error,
                CommitOutcomeAmbiguousError,
            ):
                raise CommitOutcomeAmbiguousError(str(error)) from error
            raise
        except sqlite3.Error as error:
            self._rollback(conn)
            repository_error = self._repository_error(error)
            if commit_started:
                raise CommitOutcomeAmbiguousError(
                    str(repository_error)
                ) from error
            raise repository_error from error
        except OSError as error:
            self._rollback(conn)
            repository_error = self._unsafe_path_error(self.db_path.name)
            if commit_started:
                raise CommitOutcomeAmbiguousError(
                    str(repository_error)
                ) from error
            raise repository_error from error
        except Exception as error:
            self._rollback(conn)
            if commit_started:
                raise CommitOutcomeAmbiguousError(
                    "Repository transaction outcome is committed or ambiguous; "
                    "preserve or back up the workspace state and rerun"
                ) from error
            raise

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        with self._database_operation() as (directory_descriptor, identity):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect_validated(directory_descriptor, identity, read_only=True)
                yield conn
            except RepositoryError:
                raise
            except sqlite3.Error as error:
                raise self._repository_error(error) from error
            except OSError as error:
                raise self._unsafe_path_error(self.db_path.name) from error
            finally:
                if conn is not None:
                    conn.close()

    @contextmanager
    def _existing_read_connection(self) -> Iterator[sqlite3.Connection]:
        with self._existing_database_operation() as (
            directory_descriptor,
            identity,
        ):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect_validated(
                    directory_descriptor,
                    identity,
                    read_only=True,
                )
                yield conn
                self._validate_database_family(
                    directory_descriptor,
                    identity,
                    "after read",
                )
            except RepositoryError:
                raise
            except sqlite3.Error as error:
                raise self._repository_error(error) from error
            except OSError as error:
                raise self._unsafe_path_error(self.db_path.name) from error
            finally:
                if conn is not None:
                    conn.close()

    @contextmanager
    def _database_operation(self) -> Iterator[tuple[int, _DatabaseIdentity]]:
        with self._repository_critical_section() as outermost:
            directory_descriptor, current_identity = self._open_database_family(
                ensure_directory=outermost
            )
            pinned_identity = self._pinned_database_identity()
            identity = pinned_identity or current_identity
            directory_locked = False
            try:
                if pinned_identity is not None:
                    self._validate_database_family(
                        directory_descriptor,
                        identity,
                        "during pinned database operation",
                    )
                if outermost:
                    self._lock_database_directory(directory_descriptor)
                    directory_locked = True
                yield directory_descriptor, identity
            finally:
                try:
                    if directory_locked:
                        self._unlock_database_directory(directory_descriptor)
                finally:
                    os.close(directory_descriptor)

    @contextmanager
    def _existing_database_operation(
        self,
        *,
        expected_identity: _DatabaseIdentity | None = None,
        require_complete: bool = False,
    ) -> Iterator[tuple[int, _DatabaseIdentity]]:
        with self._repository_critical_section() as outermost:
            directory_descriptor: int | None = None
            directory_locked = False
            try:
                directory_descriptor = open_managed_directory(
                    self.db_path.parent
                )
                self._verify_current_database_directory(directory_descriptor)
                current_identity = self._capture_database_identity(
                    directory_descriptor,
                    require_complete=require_complete,
                )
                pinned_identity = self._pinned_database_identity()
                identity = (
                    expected_identity
                    or pinned_identity
                    or current_identity
                )
                self._validate_database_family(
                    directory_descriptor,
                    identity,
                    "before connect",
                )
                if outermost:
                    self._lock_database_directory(directory_descriptor)
                    directory_locked = True
                yield directory_descriptor, identity
            except RepositoryError:
                raise
            except OSError as error:
                raise self._unsafe_path_error(self.db_path.name) from error
            finally:
                if directory_descriptor is not None:
                    try:
                        if directory_locked:
                            self._unlock_database_directory(
                                directory_descriptor
                            )
                    finally:
                        os.close(directory_descriptor)

    @contextmanager
    def _repository_critical_section(self) -> Iterator[bool]:
        key = self._operation_key()
        with _REPOSITORY_OPERATION_LOCK:
            depths = getattr(_REPOSITORY_OPERATION_STATE, "depths", {})
            depth = depths.get(key, 0)
            depths[key] = depth + 1
            _REPOSITORY_OPERATION_STATE.depths = depths
            lock_descriptor: int | None = None
            lock_acquired = False
            try:
                if depth == 0:
                    lock_descriptor = self._open_repository_lock()
                    try:
                        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                    except OSError as error:
                        raise RepositoryError("Portfolio repository is busy; try again shortly") from error
                    lock_acquired = True
                yield depth == 0
            finally:
                try:
                    if lock_descriptor is not None:
                        if lock_acquired:
                            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                        os.close(lock_descriptor)
                finally:
                    if depth:
                        depths[key] = depth
                    else:
                        del depths[key]

    def _operation_key(self) -> str:
        return str(self.db_path.parent.absolute())

    def _pinned_database_identity(self) -> _DatabaseIdentity | None:
        pins = getattr(_REPOSITORY_OPERATION_STATE, "database_pins", {})
        identity = pins.get(self._operation_key())
        return identity if isinstance(identity, _DatabaseIdentity) else None

    def _active_history_plan_binding(
        self,
    ) -> tuple[HistoryPlan, str] | None:
        bindings = getattr(
            _REPOSITORY_OPERATION_STATE,
            "history_plan_bindings",
            {},
        )
        binding = bindings.get(self._operation_key())
        if (
            isinstance(binding, tuple)
            and len(binding) == 2
            and isinstance(binding[0], HistoryPlan)
            and isinstance(binding[1], str)
        ):
            return binding
        return None

    def _open_repository_lock(self) -> int:
        workspace = self.db_path.parent.parent
        try:
            workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor = os.open(
                workspace,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
        except OSError as error:
            raise RepositoryError("Portfolio repository is busy; try again shortly") from error
        try:
            stat_result = os.fstat(descriptor)
            if not S_ISDIR(stat_result.st_mode):
                raise RepositoryError("Unsafe repository lock directory; preserve the workspace and rerun")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _open_database_family(self, *, ensure_directory: bool) -> tuple[int, _DatabaseIdentity]:
        try:
            if ensure_directory:
                ensure_managed_directory(self.db_path.parent)
            return self._prepare_database_family()
        except RepositoryError:
            raise
        except OSError as error:
            raise self._unsafe_path_error(self.db_path.name) from error

    def _prepare_database_family(self) -> tuple[int, _DatabaseIdentity]:
        directory_descriptor = open_managed_directory(self.db_path.parent)
        try:
            self._verify_current_database_directory(directory_descriptor)
            self._validate_or_create_main_database(directory_descriptor)
            self._ensure_database_sidecars(directory_descriptor)
            identity = self._capture_database_identity(
                directory_descriptor,
                require_complete=True,
            )
            self._validate_database_family(directory_descriptor, identity, "before connect")
            return directory_descriptor, identity
        except Exception:
            os.close(directory_descriptor)
            raise

    def _connect_validated(
        self,
        directory_descriptor: int,
        identity: _DatabaseIdentity,
        *,
        read_only: bool,
    ) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = None
        try:
            self._verify_current_database_directory(directory_descriptor)
            mode = "ro" if read_only else "rw"
            database_uri = f"{self.db_path.absolute().as_uri()}?mode={mode}"
            conn = sqlite3.connect(database_uri, uri=True)
            if not read_only:
                conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            self._validate_database_family(directory_descriptor, identity, "after connect")
            return conn
        except RepositoryError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.Error as error:
            if conn is not None:
                conn.close()
            raise self._repository_error(error) from error

    def _ensure_database_sidecars(self, directory_descriptor: int) -> None:
        for suffix in _DATABASE_SIDECARS:
            name = f"{self.db_path.name}{suffix}"
            if self._validated_family_entry(directory_descriptor, name) is not None:
                continue
            try:
                descriptor = os.open(
                    name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                self._validated_family_entry(directory_descriptor, name)
                continue
            try:
                os.fchmod(descriptor, 0o600)
                created = os.fstat(descriptor)
                if not S_ISREG(created.st_mode) or created.st_nlink != 1:
                    raise self._unsafe_path_error(name)
            finally:
                os.close(descriptor)

    def _lock_database_directory(self, directory_descriptor: int) -> None:
        os.fchmod(directory_descriptor, 0o500)

    def _unlock_database_directory(self, directory_descriptor: int) -> None:
        os.fchmod(directory_descriptor, 0o700)

    def _configure_write_journal(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        if row is None or not isinstance(row[0], str):
            raise self._controlled_error()
        if row[0].casefold() != "wal":
            conn.execute("PRAGMA journal_mode = PERSIST")

    def _validate_or_create_main_database(self, directory_descriptor: int) -> None:
        name = self.db_path.name
        stat_result = self._validated_family_entry(directory_descriptor, name)
        if stat_result is None:
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            try:
                os.fchmod(descriptor, 0o600)
                stat_result = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        else:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (stat_result.st_dev, stat_result.st_ino):
                    raise self._changed_database_error("before connect")
                os.fchmod(descriptor, 0o600)
                stat_result = opened
            finally:
                os.close(descriptor)
        if not S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1:
            raise self._unsafe_path_error(name)

    def _validate_database_family(
        self,
        directory_descriptor: int,
        identity: _DatabaseIdentity,
        stage: str,
    ) -> None:
        self._validate_database_main_identity(
            directory_descriptor,
            identity,
            stage,
        )
        for suffix, expected_device, expected_inode in identity.sidecars:
            sidecar = self._validated_family_entry(
                directory_descriptor,
                f"{self.db_path.name}{suffix}",
            )
            if sidecar is None:
                if expected_device is not None or expected_inode is not None:
                    raise self._changed_database_error(stage)
            elif (
                expected_device is None
                or expected_inode is None
                or (sidecar.st_dev, sidecar.st_ino)
                != (expected_device, expected_inode)
            ):
                raise self._changed_database_error(stage)

    def _validate_database_main_identity(
        self,
        directory_descriptor: int,
        identity: _DatabaseIdentity,
        stage: str,
    ) -> None:
        self._verify_current_database_directory(directory_descriptor)
        directory = os.fstat(directory_descriptor)
        if (
            directory.st_dev,
            directory.st_ino,
        ) != (
            identity.directory_device,
            identity.directory_inode,
        ):
            raise self._changed_database_error(stage)
        main = self._validated_family_entry(
            directory_descriptor,
            self.db_path.name,
        )
        if (
            main is None
            or (main.st_dev, main.st_ino)
            != (identity.device, identity.inode)
        ):
            raise self._changed_database_error(stage)

    def _capture_database_identity(
        self,
        directory_descriptor: int,
        *,
        require_complete: bool,
    ) -> _DatabaseIdentity:
        directory = os.fstat(directory_descriptor)
        if not S_ISDIR(directory.st_mode):
            raise self._changed_database_error("before connect")
        main = self._validated_family_entry(
            directory_descriptor,
            self.db_path.name,
        )
        if main is None:
            raise self._changed_database_error("before connect")
        sidecars: list[tuple[str, int | None, int | None]] = []
        for suffix in _DATABASE_SIDECARS:
            sidecar = self._validated_family_entry(
                directory_descriptor,
                f"{self.db_path.name}{suffix}",
            )
            if sidecar is None:
                if require_complete:
                    raise self._changed_database_error("before connect")
                sidecars.append((suffix, None, None))
            else:
                sidecars.append(
                    (suffix, sidecar.st_dev, sidecar.st_ino)
                )
        return _DatabaseIdentity(
            directory_device=directory.st_dev,
            directory_inode=directory.st_ino,
            device=main.st_dev,
            inode=main.st_ino,
            sidecars=tuple(sidecars),
        )

    def _history_database_family(
        self,
        identity: _DatabaseIdentity,
    ) -> HistoryDatabaseFamilyIdentity:
        sidecars: list[HistoryDatabaseSidecarIdentity] = []
        for suffix, device, inode in identity.sidecars:
            if device is None or inode is None:
                raise RepositoryError(
                    "history database family is incomplete"
                )
            sidecars.append(
                HistoryDatabaseSidecarIdentity(suffix, device, inode)
            )
        return HistoryDatabaseFamilyIdentity(
            directory_device=identity.directory_device,
            directory_inode=identity.directory_inode,
            main_device=identity.device,
            main_inode=identity.inode,
            sidecars=tuple(sidecars),
        )

    def _database_identity_from_history(
        self,
        identity: HistoryDatabaseFamilyIdentity,
    ) -> _DatabaseIdentity:
        if (
            not isinstance(identity, HistoryDatabaseFamilyIdentity)
            or type(identity.directory_device) is not int
            or type(identity.directory_inode) is not int
            or type(identity.main_device) is not int
            or type(identity.main_inode) is not int
            or not isinstance(identity.sidecars, tuple)
            or tuple(sidecar.suffix for sidecar in identity.sidecars)
            != _DATABASE_SIDECARS
            or any(
                not isinstance(sidecar, HistoryDatabaseSidecarIdentity)
                or type(sidecar.device) is not int
                or type(sidecar.inode) is not int
                for sidecar in identity.sidecars
            )
        ):
            raise RepositoryError(
                "history database family identity is invalid"
            )
        return _DatabaseIdentity(
            directory_device=identity.directory_device,
            directory_inode=identity.directory_inode,
            device=identity.main_device,
            inode=identity.main_inode,
            sidecars=tuple(
                (sidecar.suffix, sidecar.device, sidecar.inode)
                for sidecar in identity.sidecars
            ),
        )

    def _validated_family_entry(self, directory_descriptor: int, name: str) -> os.stat_result | None:
        try:
            stat_result = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1:
            raise self._unsafe_path_error(name)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if not S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise self._unsafe_path_error(name)
        if (opened.st_dev, opened.st_ino) != (stat_result.st_dev, stat_result.st_ino):
            raise self._changed_database_error("during validation")
        return opened

    def _verify_current_database_directory(self, directory_descriptor: int) -> None:
        current = os.stat(self.db_path.parent, follow_symlinks=False)
        descriptor = os.fstat(directory_descriptor)
        if (
            not S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (descriptor.st_dev, descriptor.st_ino)
        ):
            raise self._changed_database_error("before connect")

    def _rollback(self, conn: sqlite3.Connection | None) -> None:
        if conn is None:
            return
        try:
            conn.rollback()
        except sqlite3.Error:
            pass

    def _unsafe_path_error(self, name: str) -> RepositoryError:
        return RepositoryError(
            f"Unsafe managed database path: {name}. Preserve or back up the workspace state, "
            "remove the unsafe managed path, and rerun the command"
        )

    def _changed_database_error(self, stage: str) -> RepositoryError:
        return DatabaseFamilyChangedError(
            f"Managed database changed {stage}; preserve or back up the workspace state and rerun"
        )

    def _controlled_error(self) -> RepositoryError:
        return RepositoryError(
            f"Portfolio database is invalid or unavailable: {self.db_path}. "
            "Preserve or back up the workspace state, then repair or replace the damaged database"
        )

    def _repository_error(self, error: sqlite3.Error) -> RepositoryError:
        code = getattr(error, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return RepositoryError("Portfolio database is busy; try again shortly")
        return self._controlled_error()

    def _semantic_error(self) -> RepositoryError:
        return RepositoryError(
            "stored data is invalid; preserve or back up the workspace state, "
            "then repair or replace the damaged database"
        )

    def initialize(self) -> None:
        with self._connection() as conn:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            for statement in SEMANTIC_V3_SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            self._ensure_github_activity_visibility_column(conn)
            self._ensure_github_activity_state_field_column(conn)
            self._ensure_github_activity_current_column(conn)
            self._ensure_legacy_normalized_columns(conn)
            self._ensure_origin_columns(conn)
            self._ensure_portfolio_project_decision_columns(conn)
            self._ensure_history_cleanup_plan_columns(conn)
            self._ensure_semantic_import_schema(conn)
            self._ensure_semantic_v3_receipt_columns(conn)
            self._ensure_semantic_v3_authority_columns(conn)

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {
            SQLiteRepository._required_text(row, "name")
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @classmethod
    def _ensure_semantic_import_schema(cls, conn: sqlite3.Connection) -> None:
        columns = cls._table_columns(conn, "semantic_nodes")
        additions = (
            (
                "semantic_disposition",
                "TEXT NOT NULL DEFAULT 'structural_only'",
            ),
            ("semantic_facts_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("analysis_protocol", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
            ("skip_reason", "TEXT"),
            ("child_node_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("coverage_json", "TEXT NOT NULL DEFAULT '{}'"),
        )
        for column, declaration in additions:
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE semantic_nodes ADD COLUMN {column} {declaration}"
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_import_receipts (
                revision_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_sha256 TEXT NOT NULL,
                locator_chunk_sha256 TEXT NOT NULL,
                node_ids_sha256 TEXT NOT NULL,
                node_count INTEGER NOT NULL,
                imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (revision_id, chunk_index),
                FOREIGN KEY (revision_id)
                  REFERENCES semantic_index_revisions(id) ON DELETE CASCADE
            )
            """
        )

    @classmethod
    def _ensure_semantic_v3_receipt_columns(cls, conn: sqlite3.Connection) -> None:
        columns = cls._table_columns(conn, "semantic_v3_receipts")
        for name, definition in (
            ("input_bytes", "INTEGER NOT NULL DEFAULT 0"),
            ("commit_canonical_json", "BLOB"),
            ("commit_sha256", "TEXT"),
        ):
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE semantic_v3_receipts ADD COLUMN {name} {definition}"
                )

    @classmethod
    def _ensure_semantic_v3_authority_columns(cls, conn: sqlite3.Connection) -> None:
        additions = (
            ("source_approval_sha256", "TEXT"),
            ("parent_checkpoint_root", "TEXT"),
            ("project_boundary_sha256", "TEXT"),
            ("parent_proposal_sha256", "TEXT"),
            ("evidence_policy_sha256", "TEXT"),
        )
        for table in ("semantic_v3_proposals", "semantic_v3_approvals"):
            columns = cls._table_columns(conn, table)
            for name, definition in additions:
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        proposal_columns = cls._table_columns(conn, "semantic_v3_proposals")
        if "project_id" not in proposal_columns:
            conn.execute("ALTER TABLE semantic_v3_proposals ADD COLUMN project_id TEXT")
        binding_columns = cls._table_columns(conn, "semantic_v3_revision_bindings")
        if "source_approval_sha256" not in binding_columns:
            conn.execute("ALTER TABLE semantic_v3_revision_bindings ADD COLUMN source_approval_sha256 TEXT")

    @classmethod
    def _ensure_legacy_normalized_columns(cls, conn: sqlite3.Connection) -> None:
        projects = cls._table_columns(conn, "projects")
        if "public_safe" not in projects:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN public_safe INTEGER NOT NULL DEFAULT 0 "
                "CHECK(public_safe IN (0, 1))"
            )

        claims = cls._table_columns(conn, "career_claims")
        if "project_id" not in claims:
            conn.execute(
                "ALTER TABLE career_claims ADD COLUMN project_id INTEGER "
                "REFERENCES projects(id)"
            )

        evidence = cls._table_columns(conn, "evidence_items")
        if "github_activity_id" not in evidence:
            conn.execute(
                "ALTER TABLE evidence_items ADD COLUMN github_activity_id INTEGER "
                "REFERENCES github_activities(id)"
            )
        if "stable_id" not in evidence:
            conn.execute("ALTER TABLE evidence_items ADD COLUMN stable_id TEXT")
        if "content_hash" not in evidence:
            conn.execute("ALTER TABLE evidence_items ADD COLUMN content_hash TEXT")
        if "public_safe" not in evidence:
            conn.execute(
                "ALTER TABLE evidence_items ADD COLUMN public_safe INTEGER NOT NULL DEFAULT 0 "
                "CHECK(public_safe IN (0, 1))"
            )
        conn.execute(
            "UPDATE evidence_items SET stable_id = 'legacy-evidence:' || id "
            "WHERE stable_id IS NULL OR trim(stable_id) = ''"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS evidence_items_stable_id_unique "
            "ON evidence_items(stable_id)"
        )

        artifacts = cls._table_columns(conn, "artifacts")
        if "kind" not in artifacts:
            conn.execute("ALTER TABLE artifacts ADD COLUMN kind TEXT")
        if "version" not in artifacts:
            conn.execute("ALTER TABLE artifacts ADD COLUMN version INTEGER")
        if "input_manifest" not in artifacts:
            conn.execute("ALTER TABLE artifacts ADD COLUMN input_manifest TEXT")
        if "type" in artifacts:
            conn.execute(
                "UPDATE artifacts SET kind = COALESCE(kind, type) WHERE kind IS NULL"
            )
        conn.execute("UPDATE artifacts SET kind = 'legacy' WHERE kind IS NULL")
        conn.execute("UPDATE artifacts SET version = 1 WHERE version IS NULL")
        conn.execute(
            "UPDATE artifacts SET input_manifest = '{}' WHERE input_manifest IS NULL"
        )

    @staticmethod
    def _ensure_github_activity_visibility_column(conn: sqlite3.Connection) -> None:
        columns = {
            SQLiteRepository._required_text(row, "name")
            for row in conn.execute("PRAGMA table_info(github_activities)").fetchall()
        }
        if "is_private" not in columns:
            # Existing rows predate explicit visibility. Keep them out of public artifacts.
            conn.execute(
                "ALTER TABLE github_activities "
                "ADD COLUMN is_private INTEGER NOT NULL DEFAULT 1 CHECK(is_private IN (0, 1))"
            )

    @staticmethod
    def _ensure_github_activity_state_field_column(conn: sqlite3.Connection) -> None:
        columns = {
            SQLiteRepository._required_text(row, "name")
            for row in conn.execute("PRAGMA table_info(github_activities)").fetchall()
        }
        if "state_field" not in columns:
            conn.execute(
                "ALTER TABLE github_activities "
                "ADD COLUMN state_field TEXT "
                "CHECK(state_field IS NULL OR state_field IN ('conclusion', 'status'))"
            )

    @staticmethod
    def _ensure_github_activity_current_column(conn: sqlite3.Connection) -> None:
        columns = SQLiteRepository._table_columns(conn, "github_activities")
        if "is_current" not in columns:
            conn.execute(
                "ALTER TABLE github_activities "
                "ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1 "
                "CHECK(is_current IN (0, 1))"
            )

    @staticmethod
    def _ensure_origin_columns(conn: sqlite3.Connection) -> None:
        table_defaults = (
            ("sources", "origin_type", "local"),
            ("sources", "origin_visibility", "unknown"),
            ("github_activities", "origin_type", "public_github"),
            ("github_activities", "origin_visibility", "unknown"),
            ("evidence_items", "origin_type", "local"),
            ("evidence_items", "origin_visibility", "unknown"),
        )
        for table, column, default in table_defaults:
            if column not in SQLiteRepository._table_columns(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'"
                )
        conn.execute(
            """
            UPDATE sources
            SET origin_type = CASE
                WHEN type = 'github_repository' THEN 'public_github'
                ELSE 'local'
            END,
            origin_visibility = CASE
                WHEN type = 'local_file' THEN 'private'
                WHEN EXISTS (
                    SELECT 1 FROM github_activities
                    WHERE github_activities.source_id = sources.id
                      AND github_activities.is_private = 1
                ) THEN 'private'
                ELSE 'public'
            END
            WHERE origin_visibility = 'unknown'
            """
        )
        conn.execute(
            """
            UPDATE github_activities
            SET origin_type = CASE WHEN is_private = 1 THEN 'private_github' ELSE 'public_github' END,
                origin_visibility = CASE WHEN is_private = 1 THEN 'private' ELSE 'public' END
            WHERE origin_visibility = 'unknown'
            """
        )
        conn.execute(
            """
            UPDATE evidence_items
            SET origin_type = CASE
                    WHEN github_activity_id IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM github_activities
                            WHERE github_activities.id = evidence_items.github_activity_id
                              AND github_activities.is_private = 1
                        ) THEN 'private_github'
                    WHEN github_activity_id IS NOT NULL THEN 'public_github'
                    ELSE 'local'
                END,
                origin_visibility = CASE
                    WHEN github_activity_id IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM github_activities
                            WHERE github_activities.id = evidence_items.github_activity_id
                              AND github_activities.is_private = 1
                        ) THEN 'private'
                    WHEN github_activity_id IS NOT NULL THEN 'public'
                    ELSE 'private'
                END
            WHERE origin_visibility = 'unknown'
            """
        )

    @classmethod
    def _ensure_portfolio_project_decision_columns(cls, conn: sqlite3.Connection) -> None:
        columns = cls._table_columns(conn, "portfolio_projects")
        definitions = (
            ("decision_status", "TEXT NOT NULL DEFAULT 'manually_approved'"),
            ("decision_origin", "TEXT NOT NULL DEFAULT 'manual'"),
            ("confidence", "TEXT"),
            ("boundary_fingerprint", "TEXT"),
            ("candidate_input_sha256", "TEXT"),
            ("index_revision", "TEXT"),
            ("decision_updated_at", "TEXT"),
        )
        for column, definition in definitions:
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE portfolio_projects ADD COLUMN {column} {definition}"
                )
        conn.execute(
            """
            UPDATE portfolio_projects
            SET decision_status = CASE
                    WHEN decision_status IS NULL OR trim(decision_status) = ''
                        THEN 'manually_approved'
                    ELSE decision_status
                END,
                decision_origin = CASE
                    WHEN decision_origin IS NULL OR trim(decision_origin) = ''
                        THEN 'manual'
                    ELSE decision_origin
                END,
                decision_updated_at = COALESCE(decision_updated_at, updated_at, created_at, CURRENT_TIMESTAMP)
            WHERE decision_status IS NULL
               OR trim(decision_status) = ''
               OR decision_origin IS NULL
               OR trim(decision_origin) = ''
               OR decision_updated_at IS NULL
            """
        )

    @classmethod
    def _ensure_history_cleanup_plan_columns(cls, conn: sqlite3.Connection) -> None:
        columns = cls._table_columns(conn, "history_cleanup_plans")
        if "export_archive_binding_json" not in columns:
            conn.execute(
                "ALTER TABLE history_cleanup_plans "
                "ADD COLUMN export_archive_binding_json TEXT"
            )
        if "canonical_policy_json" not in columns:
            conn.execute(
                "ALTER TABLE history_cleanup_plans "
                "ADD COLUMN canonical_policy_json TEXT"
            )
        if "canonical_units_json" not in columns:
            conn.execute(
                "ALTER TABLE history_cleanup_plans "
                "ADD COLUMN canonical_units_json TEXT"
            )
        if "manifest_relative_path" not in columns:
            conn.execute(
                "ALTER TABLE history_cleanup_plans "
                "ADD COLUMN manifest_relative_path TEXT"
            )

    def table_names(self) -> set[str]:
        with self._read_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return {row["name"] for row in rows}

    # v3 lifecycle methods deliberately keep catalog-scale records out of this
    # database: a unit stores only canonical references and bounded receipts.
    @staticmethod
    def _v3_digest(value: str, field: str) -> None:
        if not isinstance(value, str) or len(value) != 64:
            raise RepositoryError(f"semantic {field} is invalid")
        try:
            valid = bytes.fromhex(value).hex() == value
        except ValueError:
            valid = False
        if not valid:
            raise RepositoryError(f"semantic {field} is invalid")

    @staticmethod
    def _v3_canonical_json(value: str, field: str) -> object:
        if not isinstance(value, str):
            raise RepositoryError(f"semantic {field} is invalid")
        try:
            parsed = json.loads(value, object_pairs_hook=SQLiteRepository._v3_no_duplicates)
            canonical = semantic_canonical_json_bytes(parsed).decode("utf-8")
        except (TypeError, ValueError, UnicodeDecodeError) as error:
            raise RepositoryError(f"semantic {field} is invalid") from error
        if canonical != value:
            raise RepositoryError(f"semantic {field} is not canonical")
        return parsed

    @staticmethod
    def _v3_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    @classmethod
    def _v3_root(cls, domain: bytes, rows: Iterator[object]) -> str:
        root = bytes(32)
        for row in rows:
            root = sha256(domain + root + semantic_canonical_json_bytes(row)).digest()
        return root.hex()

    @classmethod
    def _v3_runtime_roots(
        cls, rows: Iterator[SemanticRuntimeDeferredRow]
    ) -> tuple[int, str, str, str]:
        root = bytes(32)
        cursor_root = bytes(32)
        count = 0
        current_cursor = "{}"
        for row in rows:
            root = sha256(
                b"portfolio-maker-semantic-v3-runtime-deferred\0" + root
                + semantic_canonical_json_bytes({
                    "audit": row.audit,
                    "source_transition_unit_id": row.source_transition_unit_id,
                    "created_sequence": row.created_sequence,
                })
            ).digest()
            cursor_root = sha256(
                b"portfolio-maker-semantic-v3-runtime-cursor\0" + cursor_root
                + row.audit.cursor_json.encode("utf-8")
            ).digest()
            count += 1
            current_cursor = row.audit.cursor_json
        return count, root.hex(), cursor_root.hex(), current_cursor

    @staticmethod
    def _v3_budget_binding(proposal: SemanticExecutionProposal) -> tuple[str, str, str]:
        budget = semantic_canonical_json_bytes(proposal.budget).decode("utf-8")
        phases = semantic_canonical_json_bytes(proposal.phase_bindings).decode("utf-8")
        return budget, phases, sha256((budget + "\0" + phases).encode("utf-8")).hexdigest()

    @staticmethod
    def _v3_receipt_binding(receipt: SemanticReceiptCommit) -> tuple[bytes, str]:
        body = semantic_canonical_json_bytes({"receipt": receipt})
        return body, sha256(body).hexdigest()

    def _v3_validate_canonical_proposal_row(
        self, row: sqlite3.Row,
    ) -> SemanticExecutionProposal:
        """Bind every durable proposal projection back to its canonical authority."""
        try:
            envelope, _, _ = self._semantic_v3_proposal_from_row(row)
        except (KeyError, ValueError, TypeError) as error:
            raise RepositoryError("semantic proposal authority is invalid") from error
        proposal = envelope.proposal
        budget, phases, budget_binding = self._v3_budget_binding(proposal)
        expected = {
            "run_id": proposal.run_id,
            "revision_id": proposal.revision_id,
            "approval_kind": proposal.approval_kind,
            "project_id": proposal.project_id,
            "proposal_sha256": semantic_proposal_sha256(proposal),
            "plan_sha256": proposal.plan_sha256,
            "manifest_sha256": proposal.manifest_sha256,
            "source_policy_sha256": proposal.policy_sha256,
            "source_approval_sha256": proposal.source_approval_sha256,
            "catalog_root": proposal.catalog_root,
            "selector_version": proposal.selector_version,
            "private_context_scope": proposal.private_context_scope,
            "authority_root": proposal.authority_root,
            "parent_checkpoint_root": proposal.parent_checkpoint_root,
            "project_boundary_sha256": proposal.project_boundary_sha256,
            "parent_proposal_sha256": proposal.parent_proposal_sha256,
            "evidence_policy_sha256": proposal.evidence_policy_sha256,
            "total_budget_json": budget,
            "phase_bindings_json": phases,
            "budget_binding_sha256": budget_binding,
        }
        def projection(name: str) -> object:
            key = f"proposal_{name}"
            return row[key] if key in row.keys() else row[name]

        if any(projection(name) != value for name, value in expected.items()):
            raise RepositoryError("semantic proposal authority is invalid")
        return proposal

    @staticmethod
    def _v3_validate_run_projection(
        run: sqlite3.Row, proposal: SemanticExecutionProposal, error_message: str,
    ) -> None:
        expected = {
            "run_id": proposal.run_id,
            "revision_id": proposal.revision_id,
            "approval_kind": proposal.approval_kind,
            "project_id": proposal.project_id,
            "proposal_sha256": semantic_proposal_sha256(proposal),
        }
        if any(run[field] != value for field, value in expected.items()):
            raise RepositoryError(error_message)

    @staticmethod
    def _v3_validate_revision_authority(
        conn: sqlite3.Connection,
        proposal: SemanticExecutionProposal,
        error_message: str,
    ) -> sqlite3.Row:
        binding = conn.execute(
            """SELECT manifest_sha256, source_policy_sha256, source_approval_sha256,
                      catalog_logical_root, current_checkpoint_root
               FROM semantic_v3_revision_bindings WHERE revision_id = ?""",
            (proposal.revision_id,),
        ).fetchone()
        latest_checkpoint = conn.execute(
            """SELECT checkpoint.checkpoint_root
               FROM semantic_v3_checkpoints AS checkpoint
               JOIN semantic_v3_runs AS run ON run.run_id = checkpoint.run_id
               WHERE checkpoint.run_id = ? AND run.revision_id = ?
                 AND run.proposal_sha256 = ?
               ORDER BY checkpoint.sequence DESC LIMIT 1""",
            (proposal.run_id, proposal.revision_id, semantic_proposal_sha256(proposal)),
        ).fetchone()
        expected_checkpoint_root = (
            proposal.parent_checkpoint_root
            if latest_checkpoint is None
            else latest_checkpoint["checkpoint_root"]
        )
        if binding is None or (
            binding["manifest_sha256"] != proposal.manifest_sha256
            or binding["source_policy_sha256"] != proposal.policy_sha256
            or binding["source_approval_sha256"] != proposal.source_approval_sha256
            or binding["catalog_logical_root"] != proposal.catalog_root
            or binding["current_checkpoint_root"] != expected_checkpoint_root
        ):
            raise RepositoryError(error_message)
        return binding

    def _v3_authority(
        self, conn: sqlite3.Connection, run_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row, SemanticBudget, dict[str, SemanticBudget]]:
        row = conn.execute(
            """SELECT run.*, run.approval_kind AS run_approval_kind,
                      proposal.run_id AS proposal_run_id,
                      proposal.revision_id AS proposal_revision_id,
                      proposal.approval_kind AS proposal_approval_kind,
                      proposal.project_id AS proposal_project_id,
                      proposal.proposal_canonical_json, proposal.plan_canonical_json,
                      proposal.plan_sha256 AS proposal_plan_sha256,
                      proposal.proposal_sha256 AS proposal_proposal_sha256,
                      proposal.manifest_sha256 AS proposal_manifest_sha256,
                      proposal.source_policy_sha256 AS proposal_source_policy_sha256,
                      proposal.source_approval_sha256 AS proposal_source_approval_sha256,
                      proposal.catalog_root AS proposal_catalog_root,
                      proposal.selector_version AS proposal_selector_version,
                      proposal.private_context_scope AS proposal_private_context_scope,
                      proposal.authority_root AS proposal_authority_root,
                      proposal.parent_checkpoint_root AS proposal_parent_checkpoint_root,
                      proposal.project_boundary_sha256 AS proposal_project_boundary_sha256,
                      proposal.parent_proposal_sha256 AS proposal_parent_proposal_sha256,
                      proposal.evidence_policy_sha256 AS proposal_evidence_policy_sha256,
                      proposal.total_budget_json AS proposal_total_budget_json,
                      proposal.phase_bindings_json AS proposal_phase_bindings_json,
                      proposal.budget_binding_sha256 AS proposal_budget_binding_sha256,
                      approval.approval_kind AS approval_kind, approval.proposal_sha256 AS approval_proposal_sha256,
                      approval.manifest_sha256 AS approval_manifest_sha256,
                      approval.source_policy_sha256 AS approval_source_policy_sha256,
                      approval.source_approval_sha256 AS approval_source_approval_sha256,
                      approval.catalog_root AS approval_catalog_root,
                      approval.selector_version AS approval_selector_version,
                      approval.private_context_scope AS approval_private_context_scope,
                      approval.parent_checkpoint_root AS approval_parent_checkpoint_root,
                      approval.project_boundary_sha256 AS approval_project_boundary_sha256,
                      approval.parent_proposal_sha256 AS approval_parent_proposal_sha256,
                      approval.evidence_policy_sha256 AS approval_evidence_policy_sha256,
                      approval.total_budget_json AS approval_total_budget_json,
                      approval.phase_bindings_json AS approval_phase_bindings_json,
                      approval.budget_binding_sha256 AS approval_budget_binding_sha256,
                      approval.project_id AS approval_project_id,
                      binding.manifest_sha256 AS binding_manifest_sha256,
                      binding.source_policy_sha256 AS binding_source_policy_sha256,
                      binding.source_approval_sha256 AS binding_source_approval_sha256,
                      binding.catalog_logical_root AS binding_catalog_root
               FROM semantic_v3_runs AS run
               JOIN semantic_v3_proposals AS proposal ON proposal.run_id = run.run_id
               JOIN semantic_v3_approvals AS approval ON approval.run_id = run.run_id
               JOIN semantic_v3_revision_bindings AS binding ON binding.revision_id = run.revision_id
               WHERE run.run_id = ?""", (run_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("semantic receipt authority is invalid")
        proposal = self._v3_validate_canonical_proposal_row(row)
        self._v3_validate_run_projection(
            row, proposal, "semantic receipt authority is invalid",
        )
        self._v3_validate_revision_authority(
            conn, proposal, "semantic receipt authority is invalid",
        )
        exact_pairs = (
            ("proposal_sha256", "approval_proposal_sha256"),
            ("proposal_project_id", "approval_project_id"),
            ("run_approval_kind", "proposal_approval_kind"),
            ("run_approval_kind", "approval_kind"),
            ("proposal_manifest_sha256", "approval_manifest_sha256"),
            ("proposal_source_policy_sha256", "approval_source_policy_sha256"),
            ("proposal_source_approval_sha256", "approval_source_approval_sha256"),
            ("proposal_catalog_root", "approval_catalog_root"),
            ("proposal_selector_version", "approval_selector_version"),
            ("proposal_private_context_scope", "approval_private_context_scope"),
            ("proposal_parent_checkpoint_root", "approval_parent_checkpoint_root"),
            ("proposal_project_boundary_sha256", "approval_project_boundary_sha256"),
            ("proposal_parent_proposal_sha256", "approval_parent_proposal_sha256"),
            ("proposal_evidence_policy_sha256", "approval_evidence_policy_sha256"),
            ("proposal_total_budget_json", "approval_total_budget_json"),
            ("proposal_phase_bindings_json", "approval_phase_bindings_json"),
            ("proposal_budget_binding_sha256", "approval_budget_binding_sha256"),
        )
        if (
            any(row[left] != row[right] for left, right in exact_pairs)
            or proposal.project_id != row["project_id"]
            or proposal.project_id != row["approval_project_id"]
            or row["proposal_manifest_sha256"] != row["binding_manifest_sha256"]
            or row["proposal_source_policy_sha256"] != row["binding_source_policy_sha256"]
            or row["proposal_source_approval_sha256"] != row["binding_source_approval_sha256"]
            or row["proposal_catalog_root"] != row["binding_catalog_root"]
        ):
            raise RepositoryError("semantic receipt authority is invalid")
        try:
            phase_budgets = {
                binding.phase.value: binding.hard_budget
                for binding in proposal.phase_bindings
            }
        except (TypeError, ValueError) as error:
            raise RepositoryError("semantic budget authority is invalid") from error
        if not phase_budgets:
            raise RepositoryError("semantic budget authority is invalid")
        return row, row, proposal.budget, phase_budgets

    @staticmethod
    def _v3_pause_budget(conn: sqlite3.Connection, run_id: str) -> None:
        conn.execute(
            """UPDATE semantic_v3_runs SET state = 'paused_budget'
               WHERE run_id = ? AND state IN ('prepared', 'running')""", (run_id,)
        )

    @staticmethod
    def _v3_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def bind_semantic_v3_revision(self, binding: SemanticRevisionBinding) -> None:
        for value, field in (
            (binding.manifest_sha256, "manifest digest"),
            (binding.source_policy_sha256, "source policy digest"),
            (binding.source_approval_sha256, "source approval digest"),
            (binding.catalog_file_sha256, "catalog file digest"),
            (binding.catalog_logical_root, "catalog root"),
            (binding.lifecycle_genesis_root, "genesis root"),
            (binding.current_checkpoint_root, "checkpoint root"),
        ):
            self._v3_digest(value, field)
        if not binding.revision_id or not binding.lifecycle_schema_version:
            raise RepositoryError("semantic revision binding is invalid")
        with self._connection() as conn:
            revision = conn.execute(
                "SELECT id FROM semantic_index_revisions WHERE id = ?", (binding.revision_id,)
            ).fetchone()
            if revision is None:
                raise RepositoryError("semantic revision binding is invalid")
            conn.execute(
                """
                INSERT INTO semantic_v3_revision_bindings(
                    revision_id, manifest_sha256, source_policy_sha256, source_approval_sha256,
                    catalog_file_sha256,
                    catalog_logical_root, lifecycle_schema_version, lifecycle_genesis_root,
                    current_checkpoint_root, protocol
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'codex-semantic-v3')
                """,
                (
                    binding.revision_id, binding.manifest_sha256, binding.source_policy_sha256,
                    binding.source_approval_sha256, binding.catalog_file_sha256,
                    binding.catalog_logical_root,
                    binding.lifecycle_schema_version, binding.lifecycle_genesis_root,
                    binding.current_checkpoint_root,
                ),
            )

    def create_semantic_v3_proposal(
        self, envelope: SemanticProposalEnvelope, canonical_bytes: bytes, plan_bytes: bytes
    ) -> None:
        proposal = envelope.proposal
        expected = semantic_canonical_json_bytes(proposal)
        if canonical_bytes != expected:
            raise RepositoryError("semantic proposal bytes are not canonical")
        try:
            plan_value = json.loads(plan_bytes, object_pairs_hook=self._v3_no_duplicates)
            canonical_plan = semantic_canonical_json_bytes(plan_value)
        except (TypeError, ValueError) as error:
            raise RepositoryError("semantic plan bytes are not canonical") from error
        if plan_bytes != canonical_plan:
            raise RepositoryError("semantic plan bytes are not canonical")
        plan_sha = sha256(plan_bytes).hexdigest()
        if proposal.plan_sha256 != plan_sha:
            raise RepositoryError("semantic proposal plan binding is invalid")
        for value, field in (
            (envelope.proposal_sha256, "proposal digest"),
            (proposal.source_approval_sha256, "source approval digest"),
            (proposal.manifest_sha256, "manifest digest"),
            (proposal.policy_sha256, "source policy digest"),
            (proposal.parent_checkpoint_root, "parent checkpoint root"),
        ):
            self._v3_digest(value, field)
        for value, field in (
            (proposal.project_boundary_sha256, "project boundary digest"),
            (proposal.parent_proposal_sha256, "parent proposal digest"),
            (proposal.evidence_policy_sha256, "evidence policy digest"),
        ):
            if value is not None:
                self._v3_digest(value, field)
        budget, phases, budget_binding = self._v3_budget_binding(proposal)
        with self._connection() as conn:
            self._v3_validate_revision_authority(
                conn, proposal, "semantic proposal revision authority is invalid",
            )
            conn.execute(
                """
                INSERT INTO semantic_v3_proposals(
                    run_id, revision_id, approval_kind, project_id, proposal_canonical_json, proposal_sha256,
                    plan_canonical_json, plan_sha256, manifest_sha256, source_policy_sha256,
                    source_approval_sha256, catalog_root, selector_version, private_context_scope,
                    authority_root, parent_checkpoint_root, project_boundary_sha256,
                    parent_proposal_sha256, evidence_policy_sha256,
                    total_budget_json, phase_bindings_json, budget_binding_sha256, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    proposal.run_id, proposal.revision_id, proposal.approval_kind, proposal.project_id,
                    canonical_bytes,
                    envelope.proposal_sha256, plan_bytes, plan_sha, proposal.manifest_sha256,
                    proposal.policy_sha256, proposal.source_approval_sha256, proposal.catalog_root,
                    proposal.selector_version, proposal.private_context_scope, proposal.authority_root,
                    proposal.parent_checkpoint_root, proposal.project_boundary_sha256,
                    proposal.parent_proposal_sha256, proposal.evidence_policy_sha256, budget,
                    phases, budget_binding, self._v3_now(),
                ),
            )

    def _semantic_v3_proposal_from_row(
        self, row: sqlite3.Row
    ) -> tuple[SemanticProposalEnvelope, bytes, bytes]:
        body = row["proposal_canonical_json"]
        plan = row["plan_canonical_json"]
        if not isinstance(body, bytes) or not isinstance(plan, bytes):
            raise RepositoryError("semantic proposal storage is invalid")
        try:
            payload = json.loads(body, object_pairs_hook=self._v3_no_duplicates)
            proposal = semantic_execution_proposal_from_payload(payload)
        except (TypeError, ValueError) as error:
            raise RepositoryError("semantic proposal storage is invalid") from error
        if semantic_canonical_json_bytes(proposal) != body:
            raise RepositoryError("semantic proposal storage is invalid")
        envelope = SemanticProposalEnvelope(proposal, row["proposal_sha256"])
        if sha256(plan).hexdigest() != proposal.plan_sha256:
            raise RepositoryError("semantic proposal storage is invalid")
        return envelope, body, plan

    def get_semantic_v3_proposal_by_digest(
        self, revision_id: str, proposal_sha256: str
    ) -> tuple[SemanticProposalEnvelope, bytes, bytes]:
        with self._read_connection() as conn:
            row = conn.execute(
                """SELECT * FROM semantic_v3_proposals
                   WHERE revision_id = ? AND proposal_sha256 = ?""",
                (revision_id, proposal_sha256),
            ).fetchone()
        if row is None:
            raise RepositoryError("semantic proposal was not found")
        return self._semantic_v3_proposal_from_row(row)

    def get_semantic_v3_proposal_for_run(
        self, run_id: str
    ) -> tuple[SemanticProposalEnvelope, bytes, bytes]:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_v3_proposals WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RepositoryError("semantic proposal was not found")
        return self._semantic_v3_proposal_from_row(row)

    def create_semantic_v3_run(self, envelope: SemanticProposalEnvelope) -> None:
        requested_proposal = envelope.proposal
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_v3_proposals WHERE run_id = ?",
                (requested_proposal.run_id,),
            ).fetchone()
            if row is None:
                raise RepositoryError("semantic run proposal authority is invalid")
            try:
                proposal = self._v3_validate_canonical_proposal_row(row)
            except RepositoryError as error:
                raise RepositoryError("semantic run proposal authority is invalid") from error
            if (
                proposal != requested_proposal
                or row["proposal_sha256"] != envelope.proposal_sha256
                or row["state"] != "proposed"
            ):
                raise RepositoryError("semantic run proposal authority is invalid")
            self._v3_validate_revision_authority(
                conn, proposal, "semantic run proposal authority is invalid",
            )
            phase = proposal.phase_bindings[0].phase.value
            conn.execute(
                """INSERT INTO semantic_v3_runs(
                    run_id, revision_id, approval_kind, project_id, state, phase, proposal_sha256,
                    result_pack_root
                ) VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?)""",
                (proposal.run_id, proposal.revision_id, proposal.approval_kind, proposal.project_id,
                 phase, envelope.proposal_sha256, "0" * 64),
            )
            conn.execute(
                """INSERT INTO semantic_v3_aggregates(
                    run_id, ready_count, leased_count, validated_count, deferred_count, error_count,
                    used_units, used_input_bytes, used_active_seconds, phase_usage_json,
                    completion_json, aggregate_root
                ) VALUES (?, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?, ?)""",
                (proposal.run_id, "{}", "{}", "0" * 64),
            )
            conn.execute(
                """INSERT INTO semantic_v3_deferred(
                    run_id, set_kind, deferred_count, deferred_root, deferred_cursor_root, current_cursor_json
                ) VALUES (?, 'initial_catalog', ?, ?, ?, '{}')""",
                (proposal.run_id, 0, "0" * 64, "0" * 64),
            )

    def _v3_queue_authority(
        self, conn: sqlite3.Connection, run_id: str,
    ) -> tuple[sqlite3.Row, SemanticExecutionProposal, dict[str, SemanticBudget]]:
        run = conn.execute(
            "SELECT * FROM semantic_v3_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        proposal_row = conn.execute(
            "SELECT * FROM semantic_v3_proposals WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None or proposal_row is None:
            raise RepositoryError("semantic proposal authority is invalid")
        proposal = self._v3_validate_canonical_proposal_row(proposal_row)
        self._v3_validate_run_projection(
            run, proposal, "semantic proposal authority is invalid",
        )
        self._v3_validate_revision_authority(
            conn, proposal, "semantic proposal authority is invalid",
        )
        approval = conn.execute(
            "SELECT * FROM semantic_v3_approvals WHERE run_id = ?", (run_id,)
        ).fetchone()
        if approval is not None:
            budget, phases, budget_binding = self._v3_budget_binding(proposal)
            expected = {
                "approval_kind": proposal.approval_kind,
                "proposal_sha256": semantic_proposal_sha256(proposal),
                "manifest_sha256": proposal.manifest_sha256,
                "source_policy_sha256": proposal.policy_sha256,
                "source_approval_sha256": proposal.source_approval_sha256,
                "catalog_root": proposal.catalog_root,
                "selector_version": proposal.selector_version,
                "private_context_scope": proposal.private_context_scope,
                "parent_checkpoint_root": proposal.parent_checkpoint_root,
                "project_boundary_sha256": proposal.project_boundary_sha256,
                "parent_proposal_sha256": proposal.parent_proposal_sha256,
                "evidence_policy_sha256": proposal.evidence_policy_sha256,
                "total_budget_json": budget,
                "phase_bindings_json": phases,
                "budget_binding_sha256": budget_binding,
                "project_id": proposal.project_id,
            }
            if any(approval[field] != value for field, value in expected.items()):
                raise RepositoryError("semantic proposal authority is invalid")
        return run, proposal, {
            binding.phase.value: binding.hard_budget for binding in proposal.phase_bindings
        }

    def add_semantic_v3_units(
        self, run_id: str, units: tuple[SemanticPlannedUnit, ...]
    ) -> None:
        if len(units) > 512 or len({unit.unit_id for unit in units}) != len(units):
            raise RepositoryError("semantic unit batch is invalid")
        budget_exhausted = False
        with self._connection() as conn:
            run, proposal, phase_budgets = self._v3_queue_authority(conn, run_id)
            if run is None or run["state"] != SemanticRunState.PREPARED.value:
                raise RepositoryError("semantic run is not prepared")
            total = proposal.budget
            for unit in units:
                phase_budget = phase_budgets.get(unit.phase.value)
                if (
                    len(unit.target_node_ids) > 64
                    or len(unit.dependency_unit_ids) > 64
                    or unit.estimated_input_bytes < 0
                    or phase_budget is None
                ):
                    raise RepositoryError("semantic unit is invalid")
                if unit.estimated_input_bytes > min(
                    total.max_unit_input_bytes, phase_budget.max_unit_input_bytes,
                ):
                    raise RepositoryError("semantic unit exceeds its per-unit input budget")
            existing = {
                row["phase"]: (row["units"], row["bytes"])
                for row in conn.execute(
                    """SELECT phase, COUNT(*) AS units, COALESCE(SUM(estimated_input_bytes), 0) AS bytes
                       FROM semantic_v3_units WHERE run_id = ? GROUP BY phase""", (run_id,)
                )
            }
            planned_units = sum(value[0] for value in existing.values()) + len(units)
            planned_bytes = sum(value[1] for value in existing.values()) + sum(
                unit.estimated_input_bytes for unit in units
            )
            if planned_units > total.max_units or planned_bytes > total.max_input_bytes:
                self._v3_pause_budget(conn, run_id)
                budget_exhausted = True
            for phase in {unit.phase.value for unit in units}:
                phase_budget = phase_budgets.get(phase)
                phase_units = existing.get(phase, (0, 0))[0] + sum(unit.phase.value == phase for unit in units)
                phase_bytes = existing.get(phase, (0, 0))[1] + sum(unit.estimated_input_bytes for unit in units if unit.phase.value == phase)
                if phase_budget is None or phase_units > phase_budget.max_units or phase_bytes > phase_budget.max_input_bytes:
                    self._v3_pause_budget(conn, run_id)
                    budget_exhausted = True
            units_to_insert = () if budget_exhausted else units
            for unit in units_to_insert:
                target = semantic_canonical_json_bytes(unit.target_node_ids).decode("utf-8")
                target_sha = sha256(target.encode("utf-8")).hexdigest()
                dependency_root = self._v3_root(
                    b"portfolio-maker-semantic-v3-dependency\0", iter(unit.dependency_unit_ids)
                )
                conn.execute(
                    """INSERT INTO semantic_v3_units(
                        run_id, unit_id, phase, state, priority, fairness_lane, target_ref_json,
                        target_ref_sha256, dependency_root, estimated_input_bytes
                    ) VALUES (?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?)""",
                    (run_id, unit.unit_id, unit.phase.value, unit.priority, unit.fairness_lane,
                     target, target_sha, dependency_root, unit.estimated_input_bytes),
                )
                for dependency in unit.dependency_unit_ids:
                    conn.execute(
                        """INSERT INTO semantic_v3_dependencies(run_id, unit_id, dependency_unit_id, dependency_digest)
                           VALUES (?, ?, ?, ?)""",
                        (run_id, unit.unit_id, dependency, sha256(dependency.encode("utf-8")).hexdigest()),
                    )
            if not budget_exhausted:
                self._semantic_v3_refresh_aggregate(conn, run_id)
        if budget_exhausted:
            raise RepositoryError("semantic budget is exhausted")

    def approve_semantic_v3_run(self, run_id: str, proposal_sha256: str) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM semantic_v3_proposals WHERE run_id = ?", (run_id,)
            ).fetchone()
            run = conn.execute("SELECT * FROM semantic_v3_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None or run is None or row["proposal_sha256"] != proposal_sha256:
                raise RepositoryError("semantic approval authority is invalid")
            try:
                proposal = self._v3_validate_canonical_proposal_row(row)
            except RepositoryError as error:
                raise RepositoryError("semantic approval authority is invalid") from error
            self._v3_validate_run_projection(
                run, proposal, "semantic approval authority is invalid",
            )
            self._v3_validate_revision_authority(
                conn, proposal, "semantic approval authority is invalid",
            )
            if proposal.run_id != run_id or row["state"] != "proposed":
                raise RepositoryError("semantic approval authority is invalid")
            if run["state"] != SemanticRunState.PREPARED.value:
                raise RepositoryError("semantic run is not prepared")
            budget, phases, budget_binding = self._v3_budget_binding(proposal)
            conn.execute(
                """INSERT INTO semantic_v3_approvals(
                    run_id, approval_kind, proposal_sha256, manifest_sha256, source_policy_sha256,
                    source_approval_sha256, catalog_root, selector_version, private_context_scope,
                    parent_checkpoint_root, project_boundary_sha256, parent_proposal_sha256,
                    evidence_policy_sha256, total_budget_json, phase_bindings_json,
                    budget_binding_sha256, project_id, user_confirmed, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, proposal.approval_kind, proposal_sha256,
                    proposal.manifest_sha256, proposal.policy_sha256,
                    proposal.source_approval_sha256, proposal.catalog_root,
                    proposal.selector_version, proposal.private_context_scope,
                    proposal.parent_checkpoint_root, proposal.project_boundary_sha256,
                    proposal.parent_proposal_sha256, proposal.evidence_policy_sha256,
                    budget, phases, budget_binding, proposal.project_id, 1, self._v3_now(),
                ),
            )
            result = conn.execute(
                """UPDATE semantic_v3_proposals SET state = 'approved'
                   WHERE run_id = ? AND state = 'proposed'""",
                (run_id,),
            )
            if result.rowcount != 1:
                raise RepositoryError("semantic approval authority is invalid")

    def run_state(self, run_id: str) -> SemanticRunState:
        with self._read_connection() as conn:
            row = conn.execute("SELECT state FROM semantic_v3_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RepositoryError("semantic run was not found")
        try:
            return SemanticRunState(row["state"])
        except ValueError as error:
            raise RepositoryError("semantic run state is invalid") from error

    def claim_semantic_v3_running_slot(self, run_id: str) -> None:
        with self._connection() as conn:
            self._v3_authority(conn, run_id)
            occupied = conn.execute(
                "SELECT run_id FROM semantic_v3_runs WHERE state = 'running'"
            ).fetchone()
            if occupied is not None and occupied["run_id"] != run_id:
                raise RepositoryError("semantic execution is busy")
            result = conn.execute(
                """UPDATE semantic_v3_runs SET state = 'running'
                   WHERE run_id = ? AND state IN ('prepared', 'paused_user')""", (run_id,)
            )
            if result.rowcount != 1:
                raise RepositoryError("semantic run is not ready")

    def acquire_semantic_v3_execution(self, run_id: str, unit_id: str, lease_nonce: str) -> None:
        if not lease_nonce:
            raise RepositoryError("semantic lease nonce is invalid")
        with self._connection() as conn:
            self._v3_authority(conn, run_id)
            lock = conn.execute("SELECT * FROM semantic_v3_execution_lock WHERE singleton = 1").fetchone()
            if lock is not None:
                raise RepositoryError("semantic execution is busy")
            unit = conn.execute(
                """SELECT unit.state, run.state AS run_state, run.used_active_seconds
                   FROM semantic_v3_units AS unit JOIN semantic_v3_runs AS run ON run.run_id = unit.run_id
                   WHERE unit.run_id = ? AND unit.unit_id = ?""", (run_id, unit_id),
            ).fetchone()
            if unit is None or unit["run_state"] != SemanticRunState.RUNNING.value or unit["state"] != SemanticUnitState.READY.value:
                raise RepositoryError("semantic unit is not ready")
            conn.execute("UPDATE semantic_v3_units SET state = 'leased' WHERE run_id = ? AND unit_id = ?", (run_id, unit_id))
            conn.execute(
                """INSERT INTO semantic_v3_leases(run_id, unit_id, lease_nonce, state, lease_started_active_seconds, lease_deadline_active_seconds)
                   VALUES (?, ?, ?, 'materializing', ?, ?)""",
                (run_id, unit_id, lease_nonce, unit["used_active_seconds"], unit["used_active_seconds"] + 300),
            )
            conn.execute(
                """INSERT INTO semantic_v3_execution_lock(singleton, run_id, unit_id, lease_nonce, lease_state)
                   VALUES (1, ?, ?, ?, 'materializing')""", (run_id, unit_id, lease_nonce),
            )
            self._semantic_v3_refresh_aggregate(conn, run_id)

    def issue_semantic_v3_input(self, run_id: str, lease_nonce: str, input_sha256: str, byte_count: int) -> None:
        self._v3_digest(input_sha256, "input digest")
        if type(byte_count) is not int or byte_count < 0:
            raise RepositoryError("semantic input exceeds its budget")
        with self._connection() as conn:
            lock = conn.execute("SELECT * FROM semantic_v3_execution_lock WHERE singleton = 1").fetchone()
            lease = conn.execute("SELECT * FROM semantic_v3_leases WHERE run_id = ? AND lease_nonce = ?", (run_id, lease_nonce)).fetchone()
            if lock is None or lease is None or lock["run_id"] != run_id or lock["lease_nonce"] != lease_nonce or lock["lease_state"] != SemanticLeaseState.MATERIALIZING.value or lease["state"] != SemanticLeaseState.MATERIALIZING.value:
                raise RepositoryError("semantic execution lease is invalid")
            authority, _, total_budget, phase_budgets = self._v3_authority(conn, run_id)
            unit = conn.execute(
                "SELECT phase FROM semantic_v3_units WHERE run_id = ? AND unit_id = ?",
                (run_id, lease["unit_id"]),
            ).fetchone()
            if unit is None or unit["phase"] not in phase_budgets:
                raise RepositoryError("semantic budget authority is invalid")
            run = conn.execute(
                "SELECT used_input_bytes FROM semantic_v3_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            phase_used = conn.execute(
                """SELECT COALESCE(SUM(receipt.input_bytes), 0) AS input_bytes
                   FROM semantic_v3_receipts AS receipt JOIN semantic_v3_units AS unit
                   ON unit.run_id = receipt.run_id AND unit.unit_id = receipt.unit_id
                   WHERE receipt.run_id = ? AND unit.phase = ?""", (run_id, unit["phase"]),
            ).fetchone()
            phase_budget = phase_budgets[unit["phase"]]
            if (
                byte_count > total_budget.max_unit_input_bytes
                or run is None
                or run["used_input_bytes"] + byte_count > total_budget.max_input_bytes
                or phase_used["input_bytes"] + byte_count > phase_budget.max_input_bytes
            ):
                self._v3_pause_budget(conn, run_id)
                return
            conn.execute("UPDATE semantic_v3_leases SET state = 'issued', input_sha256 = ?, input_bytes = ? WHERE run_id = ? AND lease_nonce = ?", (input_sha256, byte_count, run_id, lease_nonce))
            conn.execute("UPDATE semantic_v3_execution_lock SET lease_state = 'issued' WHERE singleton = 1")

    def execution_lock(self) -> tuple[str, str, str, str] | None:
        with self._read_connection() as conn:
            row = conn.execute("SELECT run_id, unit_id, lease_nonce, lease_state FROM semantic_v3_execution_lock WHERE singleton = 1").fetchone()
        return None if row is None else (row["run_id"], row["unit_id"], row["lease_nonce"], row["lease_state"])

    def receipt_count(self, run_id: str) -> int:
        with self._read_connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM semantic_v3_receipts WHERE run_id = ?", (run_id,)).fetchone()[0])

    @classmethod
    def _semantic_v3_runtime_rows(
        cls, conn: sqlite3.Connection, run_id: str
    ) -> tuple[SemanticRuntimeDeferredRow, ...]:
        rows = conn.execute(
            """SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                      coverage_stratum, relevance_rank, selector_version, selector_feature_digest,
                      source_transition_unit_id, created_sequence
               FROM semantic_v3_runtime_deferred WHERE run_id = ?
               ORDER BY cursor_order, node_id""", (run_id,)
        ).fetchall()
        from portfolio_maker.domain.semantic_frontier import SemanticDeferredAuditRow
        return tuple(
            SemanticRuntimeDeferredRow(
                SemanticDeferredAuditRow(*tuple(row)[:10]), row["source_transition_unit_id"], row["created_sequence"]
            )
            for row in rows
        )

    @classmethod
    def _semantic_v3_refresh_aggregate(cls, conn: sqlite3.Connection, run_id: str) -> str:
        counts = {state: 0 for state in SemanticUnitState}
        for row in conn.execute(
            "SELECT state, COUNT(*) AS count FROM semantic_v3_units WHERE run_id = ? GROUP BY state", (run_id,)
        ):
            try:
                counts[SemanticUnitState(row["state"])] = row["count"]
            except ValueError as error:
                raise RepositoryError("semantic unit state is invalid") from error
        receipt = conn.execute(
            """SELECT COUNT(*) AS used_units, COALESCE(SUM(lease.input_bytes), 0) AS input_bytes,
                      COALESCE(SUM(receipt.active_seconds), 0) AS active_seconds
               FROM semantic_v3_receipts AS receipt
               LEFT JOIN semantic_v3_leases AS lease ON lease.run_id = receipt.run_id AND lease.unit_id = receipt.unit_id
               WHERE receipt.run_id = ?""", (run_id,)
        ).fetchone()
        # Leases are deleted at receipt commit, so input usage is retained in the run row.
        run = conn.execute("SELECT used_input_bytes FROM semantic_v3_runs WHERE run_id = ?", (run_id,)).fetchone()
        if receipt is None or run is None:
            raise RepositoryError("semantic aggregate is invalid")
        phase_rows = conn.execute(
            """SELECT phase, COUNT(*) AS count FROM semantic_v3_units
               WHERE run_id = ? AND state IN ('validated', 'deferred') GROUP BY phase""", (run_id,)
        ).fetchall()
        phase_usage = {row["phase"]: row["count"] for row in phase_rows}
        completion = {
            "structural_complete": False,
            "local_baseline_complete": False,
            "planned_receipts_complete": counts[SemanticUnitState.READY] == 0 and counts[SemanticUnitState.LEASED] == 0,
            "frontier_converged": False,
            "paused_budget": False,
            "project_discovery_ready": False,
            "deepening_complete": False,
        }
        payload = {
            "counts": {state.value: counts[state] for state in SemanticUnitState},
            "used_units": receipt["used_units"], "used_input_bytes": run["used_input_bytes"],
            "used_active_seconds": receipt["active_seconds"], "phase_usage": phase_usage,
            "completion": completion,
        }
        root = sha256(semantic_canonical_json_bytes(payload)).hexdigest()
        conn.execute(
            """UPDATE semantic_v3_aggregates SET ready_count = ?, leased_count = ?, validated_count = ?,
                      deferred_count = ?, error_count = ?, used_units = ?, used_input_bytes = ?,
                      used_active_seconds = ?, phase_usage_json = ?, completion_json = ?, aggregate_root = ?
               WHERE run_id = ?""",
            (counts[SemanticUnitState.READY], counts[SemanticUnitState.LEASED],
             counts[SemanticUnitState.VALIDATED], counts[SemanticUnitState.DEFERRED],
             counts[SemanticUnitState.ERROR], receipt["used_units"], run["used_input_bytes"],
             receipt["active_seconds"], semantic_canonical_json_bytes(phase_usage).decode("utf-8"),
             semantic_canonical_json_bytes(completion).decode("utf-8"), root, run_id),
        )
        conn.execute(
            """UPDATE semantic_v3_runs SET used_units = ?, used_active_seconds = ? WHERE run_id = ?""",
            (receipt["used_units"], receipt["active_seconds"], run_id),
        )
        return root

    @staticmethod
    def _semantic_v3_receipt_matches(row: sqlite3.Row, receipt: SemanticReceiptCommit) -> bool:
        body, digest = SQLiteRepository._v3_receipt_binding(receipt)
        return (
            isinstance(row["commit_canonical_json"], bytes)
            and row["commit_canonical_json"] == body
            and row["commit_sha256"] == digest
        )

    def commit_semantic_v3_receipt(self, receipt: SemanticReceiptCommit) -> None:
        commit_body, commit_digest = self._v3_receipt_binding(receipt)
        for value, field in (
            (receipt.input_sha256, "input digest"), (receipt.output_sha256, "output digest"),
            (receipt.result.sha256, "result digest"), (receipt.receipt_sha256, "receipt digest"),
        ):
            self._v3_digest(value, field)
        if receipt.result.offset < 0 or receipt.result.length < 0 or receipt.active_seconds < 0:
            raise RepositoryError("semantic receipt is invalid")
        if receipt.result_pack_length < receipt.result.offset + receipt.result.length or len(receipt.result_pack_root) != 64:
            raise RepositoryError("semantic result pack binding is invalid")
        self._v3_digest(receipt.result_pack_root, "result pack root")
        if len(receipt.runtime_deferred) > 64:
            raise RepositoryError("semantic runtime deferred batch exceeds its bound")
        with self._connection() as conn:
            authority, _, total_budget, phase_budgets = self._v3_authority(
                conn, receipt.run_id
            )
            replay = conn.execute(
                "SELECT * FROM semantic_v3_receipts WHERE run_id = ? AND unit_id = ?", (receipt.run_id, receipt.unit_id)
            ).fetchone()
            if replay is not None:
                if self._semantic_v3_receipt_matches(replay, receipt):
                    return
                raise RepositoryError("semantic receipt replay conflicts")
            lock = conn.execute("SELECT * FROM semantic_v3_execution_lock WHERE singleton = 1").fetchone()
            lease = conn.execute(
                "SELECT * FROM semantic_v3_leases WHERE run_id = ? AND unit_id = ?", (receipt.run_id, receipt.unit_id)
            ).fetchone()
            run = conn.execute("SELECT * FROM semantic_v3_runs WHERE run_id = ?", (receipt.run_id,)).fetchone()
            proposal = conn.execute("SELECT * FROM semantic_v3_proposals WHERE run_id = ?", (receipt.run_id,)).fetchone()
            approval = conn.execute("SELECT * FROM semantic_v3_approvals WHERE run_id = ?", (receipt.run_id,)).fetchone()
            if (
                lock is None or lease is None or run is None or proposal is None or approval is None
                or lock["run_id"] != receipt.run_id or lock["unit_id"] != receipt.unit_id
                or lock["lease_nonce"] != receipt.lease_nonce or lock["lease_state"] != SemanticLeaseState.ISSUED.value
                or lease["lease_nonce"] != receipt.lease_nonce or lease["state"] != SemanticLeaseState.ISSUED.value
                or lease["input_sha256"] != receipt.input_sha256 or run["state"] != SemanticRunState.RUNNING.value
            ):
                raise RepositoryError("semantic receipt authority is invalid")
            unit = conn.execute(
                "SELECT phase FROM semantic_v3_units WHERE run_id = ? AND unit_id = ?",
                (receipt.run_id, receipt.unit_id),
            ).fetchone()
            if unit is None:
                raise RepositoryError("semantic receipt authority is invalid")
            phase_budget = phase_budgets.get(unit["phase"])
            if phase_budget is None:
                raise RepositoryError("semantic budget authority is invalid")
            phase_usage = conn.execute(
                """SELECT COUNT(*) AS units, COALESCE(SUM(receipt.input_bytes), 0) AS input_bytes,
                          COALESCE(SUM(receipt.active_seconds), 0) AS active_seconds
                   FROM semantic_v3_receipts AS receipt
                   JOIN semantic_v3_units AS unit ON unit.run_id = receipt.run_id AND unit.unit_id = receipt.unit_id
                   WHERE receipt.run_id = ? AND unit.phase = ?""", (receipt.run_id, unit["phase"]),
            ).fetchone()
            if (
                lease["input_bytes"] > total_budget.max_unit_input_bytes
                or receipt.result.length > total_budget.max_unit_output_bytes
                or receipt.active_seconds > total_budget.max_unit_wall_seconds
                or run["used_units"] + 1 > total_budget.max_units
                or run["used_input_bytes"] + lease["input_bytes"] > total_budget.max_input_bytes
                or run["used_active_seconds"] + receipt.active_seconds > total_budget.max_wall_seconds
                or phase_usage["units"] + 1 > phase_budget.max_units
                or phase_usage["input_bytes"] + lease["input_bytes"] > phase_budget.max_input_bytes
                or phase_usage["active_seconds"] + receipt.active_seconds > phase_budget.max_wall_seconds
            ):
                self._v3_pause_budget(conn, receipt.run_id)
                return
            sequence = int(conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM semantic_v3_receipts WHERE run_id = ?", (receipt.run_id,)
            ).fetchone()[0])
            seen_nodes: set[str] = set()
            seen_orders: set[int] = set()
            for runtime in receipt.runtime_deferred:
                audit = runtime.audit
                if runtime.source_transition_unit_id != receipt.unit_id or runtime.created_sequence not in (0, sequence):
                    raise RepositoryError("semantic runtime deferred lineage is invalid")
                if audit.node_id in seen_nodes or audit.cursor_order in seen_orders or audit.selector_version != proposal["selector_version"]:
                    raise RepositoryError("semantic runtime deferred selector is invalid")
                seen_nodes.add(audit.node_id); seen_orders.add(audit.cursor_order)
                self._v3_canonical_json(audit.cursor_json, "runtime deferred cursor")
                for value, field in ((audit.node_digest, "runtime node digest"), (audit.selector_feature_digest, "runtime feature digest")):
                    self._v3_digest(value, field)
                if not audit.source_id or not audit.coverage_stratum or audit.relevance_rank < 0 or audit.cursor_order < 0:
                    raise RepositoryError("semantic runtime deferred selector is invalid")
            conn.execute(
                """INSERT INTO semantic_v3_receipts(run_id, unit_id, sequence, lease_nonce, input_sha256, input_bytes,
                    output_sha256, result_offset, result_length, result_sha256, action, validator_version,
                    receipt_sha256, active_seconds, commit_canonical_json, commit_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt.run_id, receipt.unit_id, sequence, receipt.lease_nonce, receipt.input_sha256, lease["input_bytes"],
                 receipt.output_sha256, receipt.result.offset, receipt.result.length, receipt.result.sha256,
                 receipt.action.value, receipt.validator_version, receipt.receipt_sha256, receipt.active_seconds,
                 commit_body, commit_digest),
            )
            terminal = SemanticUnitState.DEFERRED.value if receipt.action is FrontierAction.DEFER_ON_DEMAND else SemanticUnitState.VALIDATED.value
            conn.execute("UPDATE semantic_v3_units SET state = ?, terminal_reason = ? WHERE run_id = ? AND unit_id = ?", (terminal, receipt.action.value, receipt.run_id, receipt.unit_id))
            conn.execute("UPDATE semantic_v3_runs SET result_pack_length = ?, result_pack_root = ?, used_input_bytes = used_input_bytes + ? WHERE run_id = ?", (receipt.result_pack_length, receipt.result_pack_root, lease["input_bytes"], receipt.run_id))
            for runtime in receipt.runtime_deferred:
                audit = runtime.audit
                conn.execute(
                    """INSERT INTO semantic_v3_runtime_deferred(run_id, node_id, reason, node_digest, cursor_json,
                        cursor_order, source_id, coverage_stratum, relevance_rank, selector_version,
                        selector_feature_digest, source_transition_unit_id, created_sequence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (receipt.run_id, audit.node_id, audit.reason, audit.node_digest, audit.cursor_json,
                     audit.cursor_order, audit.source_id, audit.coverage_stratum, audit.relevance_rank,
                     audit.selector_version, audit.selector_feature_digest, receipt.unit_id, sequence),
                )
            runtime_rows = conn.execute(
                """SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                          coverage_stratum, relevance_rank, selector_version, selector_feature_digest,
                          source_transition_unit_id, created_sequence
                   FROM semantic_v3_runtime_deferred WHERE run_id = ?
                   ORDER BY cursor_order, node_id""", (receipt.run_id,)
            )
            from portfolio_maker.domain.semantic_frontier import SemanticDeferredAuditRow
            count, root, cursor_root, current_cursor = self._v3_runtime_roots(
                (
                    SemanticRuntimeDeferredRow(
                        SemanticDeferredAuditRow(*tuple(row)[:10]),
                        row["source_transition_unit_id"], row["created_sequence"],
                    )
                    for row in runtime_rows
                )
            )
            conn.execute(
                """INSERT INTO semantic_v3_deferred(run_id, set_kind, deferred_count, deferred_root, deferred_cursor_root, current_cursor_json)
                   VALUES (?, 'runtime_transition', ?, ?, ?, ?)
                   ON CONFLICT(run_id, set_kind) DO UPDATE SET deferred_count = excluded.deferred_count,
                       deferred_root = excluded.deferred_root, deferred_cursor_root = excluded.deferred_cursor_root,
                       current_cursor_json = excluded.current_cursor_json""",
                (receipt.run_id, count, root, cursor_root, current_cursor),
            )
            aggregate_root = self._semantic_v3_refresh_aggregate(conn, receipt.run_id)
            conn.execute("DELETE FROM semantic_v3_execution_lock WHERE singleton = 1")
            conn.execute("DELETE FROM semantic_v3_leases WHERE run_id = ? AND unit_id = ?", (receipt.run_id, receipt.unit_id))
            # A bounded checkpoint is published exactly at the configured default interval.
            if sequence % 16 == 0:
                last_checkpoint = conn.execute(
                    """SELECT sequence FROM semantic_v3_checkpoints
                       WHERE run_id = ? ORDER BY sequence DESC LIMIT 1""",
                    (receipt.run_id,),
                ).fetchone()
                checkpoint_sequence = (
                    1
                    if last_checkpoint is None
                    else int(last_checkpoint["sequence"]) + 1
                )
                previous = conn.execute(
                    """SELECT current_checkpoint_root FROM semantic_v3_revision_bindings
                       WHERE revision_id = ?""", (run["revision_id"],)
                ).fetchone()
                if previous is None:
                    raise RepositoryError("semantic checkpoint authority is invalid")
                checkpoint = SemanticCheckpoint(
                    receipt.run_id, checkpoint_sequence, sequence, receipt.result_pack_length,
                    receipt.result_pack_root, "0" * 64, aggregate_root, "{}", proposal["selector_version"],
                    previous["current_checkpoint_root"], "0" * 64,
                )
                checkpoint = SemanticCheckpoint(
                    **{**checkpoint.__dict__, "checkpoint_root": self._semantic_v3_checkpoint_root(checkpoint)}
                )
                self._semantic_v3_insert_checkpoint(conn, checkpoint)

    def iter_semantic_v3_runtime_deferred(self, run_id: str, fetch_size: int = 256) -> Iterator[SemanticRuntimeDeferredRow]:
        if type(fetch_size) is not int or not 0 < fetch_size <= 256:
            raise RepositoryError("semantic runtime deferred fetch size is invalid")
        with self._read_connection() as conn:
            cursor_order = -1
            node_id = ""
            while True:
                rows = conn.execute(
                    """SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                              coverage_stratum, relevance_rank, selector_version, selector_feature_digest,
                              source_transition_unit_id, created_sequence
                       FROM semantic_v3_runtime_deferred WHERE run_id = ?
                         AND (cursor_order, node_id) > (?, ?)
                       ORDER BY cursor_order, node_id LIMIT ?""", (run_id, cursor_order, node_id, fetch_size),
                ).fetchall()
                if not rows:
                    return
                for row in rows:
                    from portfolio_maker.domain.semantic_frontier import SemanticDeferredAuditRow
                    yield SemanticRuntimeDeferredRow(SemanticDeferredAuditRow(*tuple(row)[:10]), row["source_transition_unit_id"], row["created_sequence"])
                cursor_order, node_id = rows[-1]["cursor_order"], rows[-1]["node_id"]

    def iter_semantic_v3_runtime_deferred_audit(self, run_id: str, fetch_size: int = 256) -> Iterator[object]:
        for row in self.iter_semantic_v3_runtime_deferred(run_id, fetch_size):
            yield row.audit

    def semantic_v3_status(self, run_id: str | None) -> SemanticStatusSnapshot:
        with self._read_connection() as conn:
            if run_id is None:
                row = conn.execute(
                    "SELECT * FROM semantic_v3_runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM semantic_v3_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
            if row is None:
                raise RepositoryError("semantic run was not found")
            aggregate = conn.execute(
                "SELECT completion_json FROM semantic_v3_aggregates WHERE run_id = ?", (row["run_id"],)
            ).fetchone()
        if aggregate is None:
            raise RepositoryError("semantic aggregate is invalid")
        try:
            completion = SemanticCompletion(**json.loads(aggregate["completion_json"]))
            return SemanticStatusSnapshot(row["revision_id"], row["run_id"], SemanticRunState(row["state"]), completion)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RepositoryError("semantic status is invalid") from error

    def _semantic_v3_insert_checkpoint(
        self, conn: sqlite3.Connection, checkpoint: SemanticCheckpoint
    ) -> None:
        for value, field in (
            (checkpoint.result_pack_root, "result pack root"), (checkpoint.queue_root, "queue root"),
            (checkpoint.aggregate_root, "aggregate root"), (checkpoint.previous_checkpoint_root, "previous checkpoint root"),
            (checkpoint.checkpoint_root, "checkpoint root"),
        ):
            self._v3_digest(value, field)
        self._v3_canonical_json(checkpoint.resume_cursor_json, "checkpoint cursor")
        if checkpoint.sequence < 1 or checkpoint.receipt_sequence < 0 or checkpoint.result_pack_length < 0:
            raise RepositoryError("semantic checkpoint is invalid")
        expected_root = self._semantic_v3_checkpoint_root(checkpoint)
        if checkpoint.checkpoint_root != expected_root:
            raise RepositoryError("semantic checkpoint authority is invalid")
        proposal_row = conn.execute(
            "SELECT * FROM semantic_v3_proposals WHERE run_id = ?", (checkpoint.run_id,)
        ).fetchone()
        run = conn.execute(
            "SELECT * FROM semantic_v3_runs WHERE run_id = ?", (checkpoint.run_id,)
        ).fetchone()
        if proposal_row is None or run is None:
            raise RepositoryError("semantic checkpoint authority is invalid")
        try:
            proposal = self._v3_validate_canonical_proposal_row(proposal_row)
            self._v3_validate_run_projection(
                run, proposal, "semantic checkpoint authority is invalid",
            )
            self._v3_validate_revision_authority(
                conn, proposal, "semantic checkpoint authority is invalid",
            )
        except RepositoryError as error:
            raise RepositoryError("semantic checkpoint authority is invalid") from error
        aggregate = conn.execute("SELECT aggregate_root FROM semantic_v3_aggregates WHERE run_id = ?", (checkpoint.run_id,)).fetchone()
        last = conn.execute("SELECT checkpoint_root FROM semantic_v3_checkpoints WHERE run_id = ? ORDER BY sequence DESC LIMIT 1", (checkpoint.run_id,)).fetchone()
        last_sequence = 0 if last is None else int(
            conn.execute("SELECT MAX(sequence) FROM semantic_v3_checkpoints WHERE run_id = ?", (checkpoint.run_id,)).fetchone()[0]
        )
        last_receipt_sequence = int(conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM semantic_v3_receipts WHERE run_id = ?", (checkpoint.run_id,)
        ).fetchone()[0])
        expected_previous = (
            last["checkpoint_root"] if last is not None
            else proposal.parent_checkpoint_root
        )
        if aggregate is None or checkpoint.sequence != last_sequence + 1 or checkpoint.receipt_sequence != last_receipt_sequence or proposal.selector_version != checkpoint.selector_version or run["result_pack_length"] != checkpoint.result_pack_length or run["result_pack_root"] != checkpoint.result_pack_root or aggregate["aggregate_root"] != checkpoint.aggregate_root or checkpoint.previous_checkpoint_root != expected_previous:
            raise RepositoryError("semantic checkpoint authority is invalid")
        conn.execute(
            """INSERT INTO semantic_v3_checkpoints(
                run_id, sequence, receipt_sequence, result_pack_length, result_pack_root, queue_root,
                aggregate_root, resume_cursor_json, audit_ordered_set_sha256, audit_population_sha256,
                selector_version, previous_checkpoint_root, checkpoint_root
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (checkpoint.run_id, checkpoint.sequence, checkpoint.receipt_sequence,
             checkpoint.result_pack_length, checkpoint.result_pack_root, checkpoint.queue_root,
             checkpoint.aggregate_root, checkpoint.resume_cursor_json,
             checkpoint.audit_ordered_set_sha256, checkpoint.audit_population_sha256,
             checkpoint.selector_version, checkpoint.previous_checkpoint_root, checkpoint.checkpoint_root),
        )
        conn.execute("UPDATE semantic_v3_revision_bindings SET current_checkpoint_root = ? WHERE revision_id = (SELECT revision_id FROM semantic_v3_runs WHERE run_id = ?)", (checkpoint.checkpoint_root, checkpoint.run_id))

    @staticmethod
    def _semantic_v3_checkpoint_root(checkpoint: SemanticCheckpoint) -> str:
        payload = {
            "run_id": checkpoint.run_id,
            "sequence": checkpoint.sequence,
            "receipt_sequence": checkpoint.receipt_sequence,
            "result_pack_length": checkpoint.result_pack_length,
            "result_pack_root": checkpoint.result_pack_root,
            "queue_root": checkpoint.queue_root,
            "aggregate_root": checkpoint.aggregate_root,
            "resume_cursor_json": checkpoint.resume_cursor_json,
            "audit_ordered_set_sha256": checkpoint.audit_ordered_set_sha256,
            "audit_population_sha256": checkpoint.audit_population_sha256,
            "selector_version": checkpoint.selector_version,
            "previous_checkpoint_root": checkpoint.previous_checkpoint_root,
        }
        return sha256(
            b"portfolio-maker-semantic-v3-checkpoint\0" + semantic_canonical_json_bytes(payload)
        ).hexdigest()

    def checkpoint_semantic_v3_run(self, checkpoint: SemanticCheckpoint) -> None:
        with self._connection() as conn:
            self._semantic_v3_insert_checkpoint(conn, checkpoint)

    def record_semantic_v3_initial_deferred(
        self, run_id: str, rows: Iterator[object]
    ) -> None:
        """Persist only the streaming summary of the sealed catalog deferred set."""
        count, root, cursor_root, current = self._semantic_v3_initial_summary(rows)
        with self._connection() as conn:
            conn.execute(
                """UPDATE semantic_v3_deferred SET deferred_count = ?, deferred_root = ?,
                   deferred_cursor_root = ?, current_cursor_json = ?
                   WHERE run_id = ? AND set_kind = 'initial_catalog'""",
                (count, root, cursor_root, current, run_id),
            )

    def _semantic_v3_initial_summary(
        self, rows: Iterator[object]
    ) -> tuple[int, str, str, str]:
        root = bytes(32)
        cursor_root = bytes(32)
        count = 0
        current = "{}"
        for audit in rows:
            if not hasattr(audit, "cursor_json"):
                raise RepositoryError("semantic initial deferred row is invalid")
            self._v3_canonical_json(audit.cursor_json, "initial deferred cursor")
            root = sha256(
                b"portfolio-maker-semantic-v3-initial-deferred\0" + root
                + semantic_canonical_json_bytes(audit)
            ).digest()
            cursor_root = sha256(
                b"portfolio-maker-semantic-v3-initial-cursor\0" + cursor_root
                + audit.cursor_json.encode("utf-8")
            ).digest()
            count += 1
            current = audit.cursor_json
        return count, root.hex(), cursor_root.hex(), current

    def semantic_v3_audit_population(
        self, run_id: str, initial_rows: Iterator[object]
    ) -> tuple[int, str]:
        root = bytes(32)
        count = 0
        last_initial: tuple[int, str] | None = None
        for row in initial_rows:
            cursor = (row.cursor_order, row.node_id)
            if last_initial is not None and cursor <= last_initial:
                raise RepositoryError("semantic audit population order is invalid")
            last_initial = cursor
            root = sha256(
                b"portfolio-maker-semantic-v3-audit-population\0" + root
                + semantic_canonical_json_bytes({"set_kind": "initial_catalog", "row": row})
            ).digest()
            count += 1
        last_runtime: tuple[int, str] | None = None
        for runtime in self.iter_semantic_v3_runtime_deferred(run_id, 256):
            cursor = (runtime.audit.cursor_order, runtime.audit.node_id)
            if last_runtime is not None and cursor <= last_runtime:
                raise RepositoryError("semantic audit population order is invalid")
            last_runtime = cursor
            root = sha256(
                b"portfolio-maker-semantic-v3-audit-population\0" + root
                + semantic_canonical_json_bytes({"set_kind": "runtime_transition", "row": runtime.audit})
            ).digest()
            count += 1
        return count, root.hex()

    def _semantic_v3_runtime_rows_from_read(self, run_id: str) -> tuple[SemanticRuntimeDeferredRow, ...]:
        return tuple(self.iter_semantic_v3_runtime_deferred(run_id, 256))

    def commit_semantic_v3_audit_freeze(self, freeze: SemanticAuditFreeze) -> None:
        ordered_members = tuple(sorted(freeze.members, key=lambda member: member.ordinal))
        if len({member.ordinal for member in ordered_members}) != len(ordered_members) or len({member.node_id for member in ordered_members}) != len(ordered_members):
            raise RepositoryError("semantic audit membership is invalid")
        ordered_root = self._v3_root(
            b"portfolio-maker-semantic-v3-audit-members\0", iter(ordered_members)
        )
        with self._connection() as conn:
            existing = conn.execute("SELECT 1 FROM semantic_v3_audit_members WHERE run_id = ?", (freeze.run_id,)).fetchone()
            if existing is not None:
                raise RepositoryError("semantic audit is already frozen")
            initial_count, initial_root, initial_cursor_root, initial_cursor = self._semantic_v3_initial_summary(
                iter(freeze.initial_deferred)
            )
            stored_initial = conn.execute(
                """SELECT deferred_count, deferred_root, deferred_cursor_root, current_cursor_json
                   FROM semantic_v3_deferred WHERE run_id = ? AND set_kind = 'initial_catalog'""",
                (freeze.run_id,),
            ).fetchone()
            if stored_initial is None or (
                stored_initial["deferred_count"], stored_initial["deferred_root"],
                stored_initial["deferred_cursor_root"], stored_initial["current_cursor_json"],
            ) != (initial_count, initial_root, initial_cursor_root, initial_cursor):
                raise RepositoryError("semantic audit population authority is invalid")
            from portfolio_maker.domain.semantic_frontier import SemanticDeferredAuditRow
            persisted_runtime = conn.execute(
                """SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                          coverage_stratum, relevance_rank, selector_version, selector_feature_digest,
                          source_transition_unit_id, created_sequence
                   FROM semantic_v3_runtime_deferred WHERE run_id = ? ORDER BY cursor_order, node_id""",
                (freeze.run_id,),
            )
            sentinel = object()
            for supplied, row in zip_longest(freeze.runtime_deferred, persisted_runtime, fillvalue=sentinel):
                persisted = sentinel if row is sentinel else SemanticRuntimeDeferredRow(
                    SemanticDeferredAuditRow(*tuple(row)[:10]),
                    row["source_transition_unit_id"], row["created_sequence"],
                )
                if supplied != persisted:
                    raise RepositoryError("semantic audit population authority is invalid")
            runtime_rows = conn.execute(
                """SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                          coverage_stratum, relevance_rank, selector_version, selector_feature_digest,
                          source_transition_unit_id, created_sequence
                   FROM semantic_v3_runtime_deferred WHERE run_id = ? ORDER BY cursor_order, node_id""",
                (freeze.run_id,),
            )
            runtime_count, runtime_root, runtime_cursor_root, runtime_cursor = self._v3_runtime_roots(
                (
                    SemanticRuntimeDeferredRow(
                        SemanticDeferredAuditRow(*tuple(row)[:10]),
                        row["source_transition_unit_id"], row["created_sequence"],
                    )
                    for row in runtime_rows
                )
            )
            stored_runtime = conn.execute(
                """SELECT deferred_count, deferred_root, deferred_cursor_root, current_cursor_json
                   FROM semantic_v3_deferred WHERE run_id = ? AND set_kind = 'runtime_transition'""",
                (freeze.run_id,),
            ).fetchone()
            if runtime_count and stored_runtime is None:
                raise RepositoryError("semantic audit population authority is invalid")
            if stored_runtime is not None and (
                stored_runtime["deferred_count"], stored_runtime["deferred_root"],
                stored_runtime["deferred_cursor_root"], stored_runtime["current_cursor_json"],
            ) != (runtime_count, runtime_root, runtime_cursor_root, runtime_cursor):
                raise RepositoryError("semantic audit population authority is invalid")
            population_root = bytes(32)
            population_count = 0
            remaining = {(member.node_id, member.node_digest) for member in ordered_members}
            population_node_ids: set[str] = set()
            for audit in freeze.initial_deferred:
                if audit.node_id in population_node_ids:
                    raise RepositoryError("semantic audit population contains a duplicate node")
                population_node_ids.add(audit.node_id)
                population_root = sha256(
                    b"portfolio-maker-semantic-v3-audit-population\0" + population_root
                    + semantic_canonical_json_bytes({"set_kind": "initial_catalog", "row": audit})
                ).digest()
                remaining.discard((audit.node_id, audit.node_digest))
                population_count += 1
            for runtime in freeze.runtime_deferred:
                if runtime.audit.node_id in population_node_ids:
                    raise RepositoryError("semantic audit population contains a duplicate node")
                population_node_ids.add(runtime.audit.node_id)
                population_root = sha256(
                    b"portfolio-maker-semantic-v3-audit-population\0" + population_root
                    + semantic_canonical_json_bytes({"set_kind": "runtime_transition", "row": runtime.audit})
                ).digest()
                remaining.discard((runtime.audit.node_id, runtime.audit.node_digest))
                population_count += 1
            if remaining or population_count < len(ordered_members):
                raise RepositoryError("semantic audit membership is invalid")
            population_root_hex = population_root.hex()
            checkpoint = freeze.checkpoint
            if checkpoint.audit_population_sha256 not in (None, population_root_hex) or checkpoint.audit_ordered_set_sha256 not in (None, ordered_root):
                raise RepositoryError("semantic audit population authority is invalid")
            checkpoint = SemanticCheckpoint(
                **{**checkpoint.__dict__, "audit_population_sha256": population_root_hex,
                   "audit_ordered_set_sha256": ordered_root}
            )
            for member in ordered_members:
                if member.ordinal < 0 or member.stratum not in {"relevance", "coverage", "deterministic_random"}:
                    raise RepositoryError("semantic audit membership is invalid")
                self._v3_digest(member.node_digest, "audit node digest")
                audit_unit = conn.execute(
                    """SELECT 1 FROM semantic_v3_units
                       WHERE run_id = ? AND unit_id = ? AND phase = 'audit'""",
                    (freeze.run_id, member.audit_unit_id),
                ).fetchone()
                if audit_unit is None:
                    raise RepositoryError("semantic audit membership is invalid")
                conn.execute(
                    """INSERT INTO semantic_v3_audit_members(run_id, ordinal, stratum, node_id, node_digest, audit_unit_id)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (freeze.run_id, member.ordinal, member.stratum, member.node_id, member.node_digest, member.audit_unit_id),
                )
            self._semantic_v3_insert_checkpoint(conn, checkpoint)

    def pause_semantic_v3_run(self, run_id: str, reason: str) -> None:
        if not isinstance(reason, str) or not reason:
            raise RepositoryError("semantic pause reason is invalid")
        target = SemanticRunState.PAUSED_BUDGET.value if reason == "budget" else SemanticRunState.PAUSED_USER.value
        with self._connection() as conn:
            if conn.execute("SELECT 1 FROM semantic_v3_execution_lock WHERE singleton = 1 AND run_id = ?", (run_id,)).fetchone() is not None:
                raise RepositoryError("semantic execution is active")
            result = conn.execute("UPDATE semantic_v3_runs SET state = ? WHERE run_id = ? AND state = 'running'", (target, run_id))
            if result.rowcount != 1:
                raise RepositoryError("semantic run cannot be paused")

    def create_vision_request(self, request: PreparedVisionRequest) -> None:
        self._validate_record_identifier(request.id)
        try:
            status = VisionRequestStatus(request.status)
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if status is not VisionRequestStatus.PENDING:
            raise RepositoryError("new vision requests must be pending")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO vision_analysis_requests
                    (id, source_id, source_content_hash, source_size, adapter, status,
                     confirmation_at, requested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.id,
                    request.source_id,
                    request.source_content_hash,
                    request.source_size,
                    request.adapter,
                    status.value,
                    request.confirmation_at,
                    request.requested_at,
                ),
            )

    def get_vision_request(self, request_id: str) -> dict[str, object]:
        with self._read_connection() as conn:
            row = conn.execute(
                """
                SELECT id, source_id, source_content_hash, source_size, adapter, status,
                       confirmation_at, requested_at, completed_at, reason_code
                FROM vision_analysis_requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError("vision request was not found")
        try:
            status = VisionRequestStatus(self._required_text(row, "status")).value
            return {
                "id": self._required_text(row, "id"),
                "source_id": self._required_int(row, "source_id"),
                "source_content_hash": self._required_text(row, "source_content_hash"),
                "source_size": self._required_int(row, "source_size"),
                "adapter": self._required_text(row, "adapter"),
                "status": status,
                "confirmation_at": self._required_text(row, "confirmation_at"),
                "requested_at": self._required_text(row, "requested_at"),
                "completed_at": self._optional_text(row, "completed_at"),
                "reason_code": self._optional_text(row, "reason_code"),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def finish_vision_request(
        self,
        request_id: str,
        status: VisionRequestStatus,
        reason_code: str | None = None,
    ) -> None:
        try:
            resolved_status = VisionRequestStatus(status)
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if resolved_status is VisionRequestStatus.PENDING:
            raise RepositoryError("finished vision request status cannot be pending")
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE vision_analysis_requests
                SET status = ?, completed_at = ?, reason_code = ?
                WHERE id = ? AND status = 'pending'
                """,
                (resolved_status.value, utc_now(), reason_code, request_id),
            )
            if cursor.rowcount != 1:
                row = conn.execute(
                    "SELECT status FROM vision_analysis_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise RepositoryError("vision request was not found")
                raise RepositoryError("vision request is no longer pending")

    def insert_vision_analysis(
        self,
        analysis: AppliedVisionAnalysisResult,
        result: VisionAnalysisResult,
    ) -> None:
        self._validate_record_identifier(analysis.id)
        try:
            status = VisionRequestStatus(result.status)
            review_status = VisionReviewStatus(analysis.review_status)
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if (
            status is not VisionRequestStatus.COMPLETED
            or type(result.confidence) is not str
            or result.confidence not in {"low", "medium", "high"}
        ):
            raise RepositoryError("vision analysis confidence is invalid")
        if (
            review_status is VisionReviewStatus.INCLUDED
            and result.confidence not in {"medium", "high"}
        ):
            raise RepositoryError("vision analysis confidence is not eligible")
        relative_path, _, _, _, stored_hash = self._managed_history_file_metadata(
            "vision_analysis",
            str(analysis.analysis_path),
        )
        if (
            stored_hash != analysis.result_hash
            or relative_path
            != f"derived/vision/analysis-{result.request_id}-{analysis.result_hash}.json"
        ):
            raise RepositoryError("vision analysis does not match its managed result")
        with self._connection() as conn:
            request = conn.execute(
                """
                SELECT request.source_id,
                       request.source_content_hash,
                       request.adapter,
                       request.status,
                       source.type AS source_type,
                       source.status AS source_status
                FROM vision_analysis_requests AS request
                JOIN sources AS source ON source.id = request.source_id
                WHERE request.id = ?
                """,
                (result.request_id,),
            ).fetchone()
            if request is None:
                raise RepositoryError("vision request was not found")
            try:
                source_id = self._required_int(request, "source_id")
                request_hash = self._required_text(request, "source_content_hash")
                request_adapter = self._required_text(request, "adapter")
                request_status = VisionRequestStatus(
                    self._required_text(request, "status")
                )
                source_type = self._required_text(request, "source_type")
                source_status = self._required_text(request, "source_status")
            except (KeyError, TypeError, ValueError) as error:
                raise self._semantic_error() from error
            if (
                request_status
                not in {
                    VisionRequestStatus.PENDING,
                    VisionRequestStatus.COMPLETED,
                }
                or (
                    review_status is VisionReviewStatus.INCLUDED
                    and request_status is not VisionRequestStatus.COMPLETED
                )
                or result.source_content_hash != request_hash
                or result.adapter != request_adapter
                or source_type != SourceType.LOCAL_FILE.value
                or source_status
                not in {
                    SourceStatus.APPROVED.value,
                    SourceStatus.INGESTED.value,
                }
            ):
                raise RepositoryError("vision analysis does not match its request")
            binding = self._read_vision_analysis_binding(
                relative_path,
                analysis.result_hash,
            )
            if (
                binding.request_id != result.request_id
                or binding.source_content_hash != request_hash
                or binding.adapter != request_adapter
                or binding.status != VisionRequestStatus.COMPLETED.value
                or binding.confidence != result.confidence
            ):
                raise RepositoryError("vision analysis does not match its managed result")
            conn.execute(
                """
                INSERT INTO vision_analyses
                    (id, request_id, source_id, source_content_hash, result_hash,
                     relative_path, adapter, status, confidence, review_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis.id,
                    result.request_id,
                    source_id,
                    result.source_content_hash,
                    analysis.result_hash,
                    relative_path,
                    result.adapter,
                    status.value,
                    result.confidence,
                    review_status.value,
                    utc_now(),
                ),
            )

    def get_vision_analysis(self, analysis_id: str) -> dict[str, object]:
        with self._read_connection() as conn:
            row = conn.execute(
                """
                SELECT id, request_id, source_id, source_content_hash, result_hash,
                       relative_path, adapter, status, confidence, review_status,
                       created_at, reviewed_at
                FROM vision_analyses
                WHERE id = ?
                """,
                (analysis_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError("vision analysis was not found")
        return self._hydrate_vision_analysis(row)

    def list_vision_analyses(
        self,
        review_status: VisionReviewStatus | None = None,
    ) -> list[dict[str, object]]:
        with self._read_connection() as conn:
            if review_status is None:
                rows = conn.execute(
                    """
                    SELECT id, request_id, source_id, source_content_hash, result_hash,
                           relative_path, adapter, status, confidence, review_status,
                           created_at, reviewed_at
                    FROM vision_analyses
                    ORDER BY created_at, id
                    """
                ).fetchall()
            else:
                try:
                    resolved_status = VisionReviewStatus(review_status)
                except (TypeError, ValueError) as error:
                    raise self._semantic_error() from error
                rows = conn.execute(
                    """
                    SELECT id, request_id, source_id, source_content_hash, result_hash,
                           relative_path, adapter, status, confidence, review_status,
                           created_at, reviewed_at
                    FROM vision_analyses
                    WHERE review_status = ?
                    ORDER BY created_at, id
                    """,
                    (resolved_status.value,),
                ).fetchall()
        return [self._hydrate_vision_analysis(row) for row in rows]

    def mark_vision_analysis_included(self, analysis_id: str, evidence_id: int) -> None:
        with self._connection() as conn:
            try:
                self._bound_vision_analysis(
                    conn,
                    analysis_id,
                    require_inclusion=True,
                )
            except RepositoryError as error:
                raise RepositoryError(
                    "vision analysis evidence does not match a completed analysis"
                ) from error
            row = conn.execute(
                """
                SELECT evidence_items.id
                FROM vision_analyses
                JOIN evidence_items
                  ON evidence_items.id = ?
                 AND evidence_items.source_id = vision_analyses.source_id
                 AND evidence_items.locator =
                     'derived:vision-analysis:' || vision_analyses.id
                 AND evidence_items.stable_id =
                     'vision-analysis:' || vision_analyses.id || ':' ||
                     vision_analyses.result_hash
                 AND evidence_items.content_hash = vision_analyses.result_hash
                WHERE vision_analyses.id = ?
                """,
                (evidence_id, analysis_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(
                    "vision analysis evidence does not match a completed analysis"
                )
            conn.execute(
                """
                UPDATE vision_analyses
                SET review_status = 'included', reviewed_at = ?
                WHERE id = ?
                """,
                (utc_now(), analysis_id),
            )

    def set_vision_analysis_state(self, analysis_id: str, state: str) -> None:
        self._validate_record_identifier(analysis_id)
        try:
            review_status = VisionReviewStatus(state)
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if review_status not in {
            VisionReviewStatus.INCLUDED,
            VisionReviewStatus.EXCLUDED,
        }:
            raise RepositoryError("vision analysis review state is invalid")
        with self._connection() as conn:
            source_id, result_hash = self._bound_vision_analysis(
                conn,
                analysis_id,
                require_inclusion=review_status is VisionReviewStatus.INCLUDED,
            )
            if review_status is VisionReviewStatus.INCLUDED:
                locator = f"derived:vision-analysis:{analysis_id}"
                stable_id = f"vision-analysis:{analysis_id}:{result_hash}"
                if {"origin_type", "origin_visibility"} <= self._table_columns(
                    conn, "evidence_items"
                ):
                    conn.execute(
                        """
                        INSERT INTO evidence_items
                            (source_id, snapshot_id, github_activity_id, locator, stable_id,
                             content_hash, public_safe, origin_type, origin_visibility)
                        VALUES (?, NULL, NULL, ?, ?, ?, 0, 'local', 'private')
                        ON CONFLICT(stable_id) DO UPDATE SET
                            source_id = excluded.source_id,
                            snapshot_id = excluded.snapshot_id,
                            github_activity_id = excluded.github_activity_id,
                            locator = excluded.locator,
                            content_hash = excluded.content_hash,
                            public_safe = excluded.public_safe,
                            origin_type = excluded.origin_type,
                            origin_visibility = excluded.origin_visibility
                        """,
                        (source_id, locator, stable_id, result_hash),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO evidence_items
                            (source_id, snapshot_id, github_activity_id, locator, stable_id,
                             content_hash, public_safe)
                        VALUES (?, NULL, NULL, ?, ?, ?, 0)
                        ON CONFLICT(stable_id) DO UPDATE SET
                            source_id = excluded.source_id,
                            snapshot_id = excluded.snapshot_id,
                            github_activity_id = excluded.github_activity_id,
                            locator = excluded.locator,
                            content_hash = excluded.content_hash,
                            public_safe = excluded.public_safe
                        """,
                        (source_id, locator, stable_id, result_hash),
                    )
            else:
                self._exclude_unreferenced_vision_analysis(conn, analysis_id)
            cursor = conn.execute(
                """
                UPDATE vision_analyses
                SET review_status = ?, reviewed_at = ?
                WHERE id = ? AND status = 'completed'
                """,
                (review_status.value, utc_now(), analysis_id),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("completed vision analysis was not found")

    def _bound_vision_analysis(
        self,
        conn: sqlite3.Connection,
        analysis_id: str,
        *,
        require_inclusion: bool,
    ) -> tuple[int, str]:
        analysis = conn.execute(
            """
            SELECT analysis.id AS analysis_id,
                   analysis.request_id AS analysis_request_id,
                   analysis.source_id AS analysis_source_id,
                   analysis.source_content_hash AS analysis_source_hash,
                   analysis.result_hash AS result_hash,
                   analysis.relative_path AS relative_path,
                   analysis.adapter AS analysis_adapter,
                   analysis.status AS analysis_status,
                   analysis.confidence AS confidence,
                   request.id AS request_id,
                   request.source_id AS request_source_id,
                   request.source_content_hash AS request_source_hash,
                   request.adapter AS request_adapter,
                   request.status AS request_status,
                   source.type AS source_type,
                   source.status AS source_status
            FROM vision_analyses AS analysis
            JOIN vision_analysis_requests AS request
              ON request.id = analysis.request_id
            JOIN sources AS source
              ON source.id = analysis.source_id
            WHERE analysis.id = ?
            """,
            (analysis_id,),
        ).fetchone()
        if analysis is None:
            raise RepositoryError("vision analysis was not found")
        try:
            stored_analysis_id = self._required_text(analysis, "analysis_id")
            request_id = self._required_text(analysis, "analysis_request_id")
            source_id = self._required_int(analysis, "analysis_source_id")
            source_hash = self._required_text(analysis, "analysis_source_hash")
            result_hash = self._required_text(analysis, "result_hash")
            relative_path = self._required_text(analysis, "relative_path")
            adapter = self._required_text(analysis, "analysis_adapter")
            confidence = self._required_text(analysis, "confidence")
            status = VisionRequestStatus(
                self._required_text(analysis, "analysis_status")
            )
            stored_request_id = self._required_text(analysis, "request_id")
            request_source_id = self._required_int(analysis, "request_source_id")
            request_source_hash = self._required_text(
                analysis,
                "request_source_hash",
            )
            request_adapter = self._required_text(analysis, "request_adapter")
            request_status = VisionRequestStatus(
                self._required_text(analysis, "request_status")
            )
            source_type = self._required_text(analysis, "source_type")
            source_status = self._required_text(analysis, "source_status")
            self._validate_record_identifier(request_id)
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if (
            stored_analysis_id != analysis_id
            or status is not VisionRequestStatus.COMPLETED
            or request_status is not VisionRequestStatus.COMPLETED
            or request_id != stored_request_id
            or source_id != request_source_id
            or source_hash != request_source_hash
            or adapter != request_adapter
            or relative_path
            != f"derived/vision/analysis-{request_id}-{result_hash}.json"
            or confidence not in {"low", "medium", "high"}
            or source_type != SourceType.LOCAL_FILE.value
            or source_status
            not in {
                SourceStatus.APPROVED.value,
                SourceStatus.INGESTED.value,
            }
        ):
            raise RepositoryError("completed vision analysis was not found")
        binding = self._read_vision_analysis_binding(relative_path, result_hash)
        if (
            binding.request_id != request_id
            or binding.source_content_hash != source_hash
            or binding.adapter != adapter
            or binding.status != status.value
            or binding.confidence != confidence
        ):
            raise RepositoryError("completed vision analysis was not found")
        if require_inclusion and confidence not in {"medium", "high"}:
            raise RepositoryError("vision analysis confidence is not eligible")
        return source_id, result_hash

    def _read_vision_analysis_binding(
        self,
        relative_path: str,
        result_hash: str,
    ) -> PersistedVisionAnalysisBinding:
        paths = WorkspacePaths.from_root(self.db_path.parent.parent)
        try:
            return VisionStore(paths).read_analysis_binding(
                relative_path,
                result_hash,
            )
        except (OSError, TypeError, ValueError) as error:
            raise RepositoryError(
                "vision analysis does not match its managed result"
            ) from error

    def exclude_unreferenced_vision_analysis(self, analysis_id: str) -> None:
        with self._connection() as conn:
            self._exclude_unreferenced_vision_analysis(conn, analysis_id)
            conn.execute(
                """
                UPDATE vision_analyses
                SET review_status = 'excluded', reviewed_at = ?
                WHERE id = ?
                """,
                (utc_now(), analysis_id),
            )

    def _exclude_unreferenced_vision_analysis(
        self,
        conn: sqlite3.Connection,
        analysis_id: str,
    ) -> None:
        row = conn.execute(
                """
                SELECT vision_analyses.id,
                       EXISTS (
                           SELECT 1
                           FROM evidence_items
                           JOIN claim_evidence
                             ON claim_evidence.evidence_id = evidence_items.id
                           WHERE evidence_items.source_id =
                               vision_analyses.source_id
                             AND evidence_items.locator =
                               'derived:vision-analysis:' || vision_analyses.id
                             AND evidence_items.stable_id =
                               'vision-analysis:' || vision_analyses.id || ':' ||
                               vision_analyses.result_hash
                             AND evidence_items.content_hash =
                               vision_analyses.result_hash
                       ) AS claim_referenced,
                       EXISTS (
                           SELECT 1
                           FROM evidence_items
                           JOIN portfolio_project_evidence
                             ON portfolio_project_evidence.evidence_id = evidence_items.id
                           WHERE evidence_items.source_id =
                               vision_analyses.source_id
                             AND evidence_items.locator =
                               'derived:vision-analysis:' || vision_analyses.id
                             AND evidence_items.stable_id =
                               'vision-analysis:' || vision_analyses.id || ':' ||
                               vision_analyses.result_hash
                             AND evidence_items.content_hash =
                               vision_analyses.result_hash
                       ) AS project_referenced
                FROM vision_analyses
                WHERE vision_analyses.id = ?
                  AND vision_analyses.status = 'completed'
                  AND vision_analyses.result_hash IS NOT NULL
                """,
                (analysis_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError("completed vision analysis was not found")
        try:
            claim_referenced = self._required_boolean(row, "claim_referenced")
            project_referenced = self._required_boolean(row, "project_referenced")
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if claim_referenced or project_referenced:
            raise RepositoryError("vision analysis evidence is still referenced")
        self._delete_unreferenced_vision_evidence(conn, analysis_id)

    @staticmethod
    def _delete_unreferenced_vision_evidence(
        conn: sqlite3.Connection,
        analysis_id: str,
    ) -> None:
        conn.execute(
            """
            DELETE FROM evidence_items
            WHERE EXISTS (
                    SELECT 1
                    FROM vision_analyses
                    WHERE vision_analyses.id = ?
                      AND vision_analyses.result_hash IS NOT NULL
                      AND evidence_items.source_id = vision_analyses.source_id
                      AND evidence_items.locator =
                          'derived:vision-analysis:' || vision_analyses.id
                      AND evidence_items.stable_id =
                          'vision-analysis:' || vision_analyses.id || ':' ||
                          vision_analyses.result_hash
                      AND evidence_items.content_hash =
                          vision_analyses.result_hash
                )
              AND NOT EXISTS (
                    SELECT 1
                    FROM claim_evidence
                    WHERE claim_evidence.evidence_id = evidence_items.id
                )
              AND NOT EXISTS (
                    SELECT 1
                    FROM portfolio_project_evidence
                    WHERE portfolio_project_evidence.evidence_id =
                          evidence_items.id
                )
            """,
            (analysis_id,),
        )

    def evidence_id_for_stable_id(self, stable_id: str) -> int | None:
        with self._read_connection() as conn:
            row = conn.execute(
                "SELECT id FROM evidence_items WHERE stable_id = ?",
                (stable_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return self._required_int(row, "id")
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def history_candidates(self, policy: HistoryPolicy) -> list[HistoryUnit]:
        self._validate_history_policy(policy)
        units: list[HistoryUnit] = []
        with self._read_connection() as conn:
            snapshot_rows, analysis_rows, request_rows = (
                self._history_candidate_rows(conn, policy)
            )
            try:
                for row in snapshot_rows:
                    units.append(
                        self._history_unit(
                            "source_snapshot",
                            str(self._required_int(row, "id")),
                            self._required_text(row, "snapshot_path"),
                            "expired_unreferenced_snapshot",
                        )
                    )
                for row in analysis_rows:
                    units.append(
                        self._history_unit(
                            "vision_analysis",
                            self._required_text(row, "id"),
                            self._required_text(row, "relative_path"),
                            "expired_unreferenced_vision_analysis",
                        )
                    )
                for row in request_rows:
                    request_id = self._required_text(row, "id")
                    units.append(
                        self._history_unit(
                            "vision_request",
                            request_id,
                            f"reviews/vision-requests/request-{request_id}.json",
                            "expired_finished_vision_request",
                        )
                    )
            except (KeyError, TypeError, ValueError) as error:
                raise self._semantic_error() from error
        return sorted(units, key=self._history_unit_sort_key)

    def _history_candidate_rows(
        self,
        conn: sqlite3.Connection,
        policy: HistoryPolicy,
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
        self._validate_history_policy(policy)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=policy.minimum_age_days)
        ).isoformat().replace("+00:00", "Z")
        keep_snapshots = max(1, policy.keep_latest_text_snapshots_per_source)
        snapshot_rows = conn.execute(
            """
            SELECT snapshots.id, snapshots.snapshot_path
            FROM source_snapshots AS snapshots
            LEFT JOIN evidence_items
              ON evidence_items.snapshot_id = snapshots.id
            WHERE datetime(snapshots.extracted_at) <= datetime(?)
              AND evidence_items.id IS NULL
              AND (
                  SELECT COUNT(*)
                  FROM source_snapshots AS newer
                  WHERE newer.source_id = snapshots.source_id
                    AND newer.id > snapshots.id
              ) >= ?
            ORDER BY snapshots.id
            """,
            (cutoff, keep_snapshots),
        ).fetchall()
        analysis_rows = conn.execute(
            """
            SELECT analyses.id, analyses.relative_path, analyses.result_hash
            FROM vision_analyses AS analyses
            LEFT JOIN evidence_items
              ON evidence_items.source_id = analyses.source_id
             AND evidence_items.locator =
                 'derived:vision-analysis:' || analyses.id
             AND evidence_items.stable_id =
                 'vision-analysis:' || analyses.id || ':' ||
                 analyses.result_hash
             AND evidence_items.content_hash = analyses.result_hash
            LEFT JOIN claim_evidence
              ON claim_evidence.evidence_id = evidence_items.id
            LEFT JOIN portfolio_project_evidence
              ON portfolio_project_evidence.evidence_id = evidence_items.id
            WHERE datetime(analyses.created_at) <= datetime(?)
              AND analyses.relative_path IS NOT NULL
              AND analyses.result_hash IS NOT NULL
              AND (analyses.review_status <> 'included' OR ? = 0)
              AND (analyses.review_status <> 'pending' OR ? = 0)
            GROUP BY analyses.id, analyses.relative_path, analyses.result_hash
            HAVING COUNT(claim_evidence.claim_id) = 0
               AND COUNT(portfolio_project_evidence.project_id) = 0
            ORDER BY analyses.id
            """,
            (
                cutoff,
                int(policy.retain_included_vision_analyses),
                int(policy.retain_pending_review_vision_analyses),
            ),
        ).fetchall()
        request_rows = conn.execute(
            """
            SELECT requests.id
            FROM vision_analysis_requests AS requests
            LEFT JOIN vision_analyses AS analyses
              ON analyses.request_id = requests.id
            LEFT JOIN evidence_items
              ON evidence_items.source_id = analyses.source_id
             AND evidence_items.locator =
                 'derived:vision-analysis:' || analyses.id
             AND evidence_items.stable_id =
                 'vision-analysis:' || analyses.id || ':' ||
                 analyses.result_hash
             AND evidence_items.content_hash = analyses.result_hash
            LEFT JOIN claim_evidence
              ON claim_evidence.evidence_id = evidence_items.id
            LEFT JOIN portfolio_project_evidence
              ON portfolio_project_evidence.evidence_id = evidence_items.id
            WHERE datetime(requests.requested_at) <= datetime(?)
              AND (requests.status <> 'pending' OR ? = 0)
              AND (
                  analyses.id IS NULL
                  OR (
                      datetime(analyses.created_at) <= datetime(?)
                      AND analyses.relative_path IS NOT NULL
                      AND analyses.result_hash IS NOT NULL
                      AND (analyses.review_status <> 'included' OR ? = 0)
                      AND (analyses.review_status <> 'pending' OR ? = 0)
                  )
              )
            GROUP BY requests.id
            HAVING COUNT(claim_evidence.claim_id) = 0
               AND COUNT(portfolio_project_evidence.project_id) = 0
            ORDER BY requests.id
            """,
            (
                cutoff,
                int(policy.retain_incomplete_vision_requests),
                cutoff,
                int(policy.retain_included_vision_analyses),
                int(policy.retain_pending_review_vision_analyses),
            ),
        ).fetchall()
        return snapshot_rows, analysis_rows, request_rows

    def create_history_plan(self, plan: HistoryPlan, relative_path: Path) -> None:
        (
            canonical_policy_json,
            canonical_units_json,
            policy_hash,
            manifest_hash,
        ) = self._canonical_history_plan_metadata(plan.policy, plan.units)
        if plan.manifest_hash != manifest_hash:
            raise RepositoryError("history plan manifest hash does not match its units")
        manifest_relative_path = self._canonical_history_manifest_path(relative_path)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO history_cleanup_plans
                    (id, policy_hash, manifest_hash, canonical_policy_json,
                     canonical_units_json, manifest_relative_path, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    plan.id,
                    policy_hash,
                    manifest_hash,
                    canonical_policy_json,
                    canonical_units_json,
                    manifest_relative_path,
                    utc_now(),
                ),
            )

    def find_history_plan(self, plan_id: str) -> dict[str, object] | None:
        with self._read_connection() as conn:
            row = conn.execute(
                """
                SELECT id, policy_hash, manifest_hash, canonical_policy_json,
                       canonical_units_json, manifest_relative_path, status,
                       created_at, export_receipt_hash, export_archive_binding_json,
                       execution_id, completed_at
                FROM history_cleanup_plans
                WHERE id = ?
                """,
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return {
                "id": self._required_text(row, "id"),
                "policy_hash": self._required_text(row, "policy_hash"),
                "manifest_hash": self._required_text(row, "manifest_hash"),
                "canonical_policy_json": self._optional_text(
                    row,
                    "canonical_policy_json",
                ),
                "canonical_units_json": self._optional_text(
                    row,
                    "canonical_units_json",
                ),
                "manifest_relative_path": self._optional_text(
                    row,
                    "manifest_relative_path",
                ),
                "status": self._required_text(row, "status"),
                "created_at": self._required_text(row, "created_at"),
                "export_receipt_hash": self._optional_text(row, "export_receipt_hash"),
                "export_archive_binding": self._history_export_archive_binding_from_json(
                    self._optional_text(row, "export_archive_binding_json")
                ),
                "execution_id": self._optional_text(row, "execution_id"),
                "completed_at": self._optional_text(row, "completed_at"),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def get_history_plan(self, plan_id: str) -> dict[str, object]:
        plan = self.find_history_plan(plan_id)
        if plan is None:
            raise RepositoryError("history cleanup plan was not found")
        return plan

    def assert_history_plan_binding(
        self,
        plan: HistoryPlan,
        *,
        allowed_statuses: tuple[str, ...] = ("planned",),
    ) -> dict[str, object]:
        (
            canonical_policy_json,
            canonical_units_json,
            policy_hash,
            manifest_hash,
        ) = self._canonical_history_plan_metadata(plan.policy, plan.units)
        if plan.manifest_hash != manifest_hash:
            raise RepositoryError("history cleanup plan binding is inconsistent")
        stored = self.find_history_plan(plan.id)
        expected_manifest_path = (
            Path("reviews")
            / "history-plans"
            / f"plan-{plan.id}-{plan.manifest_hash}.json"
        ).as_posix()
        if (
            stored is None
            or stored["id"] != plan.id
            or stored["policy_hash"] != policy_hash
            or stored["manifest_hash"] != manifest_hash
            or stored["canonical_policy_json"] != canonical_policy_json
            or stored["canonical_units_json"] != canonical_units_json
            or stored["manifest_relative_path"] != expected_manifest_path
            or stored["status"] not in allowed_statuses
        ):
            raise RepositoryError("history cleanup plan binding is inconsistent")
        return stored

    def record_history_export_receipt(
        self,
        plan_id: str,
        manifest_hash: str,
        receipt_hash: str,
        archive_binding: HistoryExportArchiveBinding,
    ) -> None:
        self._validate_history_hex(plan_id, 32)
        self._validate_history_hex(manifest_hash, 64)
        self._validate_history_hex(receipt_hash, 64)
        binding_json = self._canonical_history_export_archive_binding(archive_binding)
        if receipt_hash != archive_binding.sha256:
            raise RepositoryError(
                "history export receipt does not match its archive binding"
            )
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE history_cleanup_plans
                SET export_receipt_hash = ?, export_archive_binding_json = ?
                WHERE id = ?
                  AND manifest_hash = ?
                  AND status = 'planned'
                  AND (
                      (export_receipt_hash IS NULL
                       AND export_archive_binding_json IS NULL)
                      OR (export_receipt_hash = ?
                          AND export_archive_binding_json = ?)
                  )
                """,
                (
                    receipt_hash,
                    binding_json,
                    plan_id,
                    manifest_hash,
                    receipt_hash,
                    binding_json,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(
                    "history export receipt could not be bound to the exact plan"
                )

    def export_receipt_matches(
        self,
        plan_id: str,
        manifest_hash: str,
        receipt_hash: str,
        archive_binding: HistoryExportArchiveBinding,
    ) -> bool:
        try:
            self._validate_history_hex(plan_id, 32)
            self._validate_history_hex(manifest_hash, 64)
            self._validate_history_hex(receipt_hash, 64)
            binding_json = self._canonical_history_export_archive_binding(archive_binding)
            if receipt_hash != archive_binding.sha256:
                return False
            with self._existing_read_connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, manifest_hash, status, export_receipt_hash,
                           export_archive_binding_json
                    FROM history_cleanup_plans
                    WHERE id = ?
                    """,
                    (plan_id,),
                ).fetchone()
                if row is None:
                    return False
                stored_id = self._required_text(row, "id")
                stored_manifest_hash = self._required_text(
                    row,
                    "manifest_hash",
                )
                status = self._required_text(row, "status")
                stored_receipt_hash = self._optional_text(
                    row,
                    "export_receipt_hash",
                )
                stored_binding_json = self._optional_text(
                    row,
                    "export_archive_binding_json",
                )
                if stored_binding_json is not None:
                    self._history_export_archive_binding_from_json(
                        stored_binding_json
                    )
        except Exception:
            return False
        return (
            stored_id == plan_id
            and stored_manifest_hash == manifest_hash
            and status == "planned"
            and stored_receipt_hash == receipt_hash
            and stored_binding_json == binding_json
        )

    def assert_export_receipt(
        self,
        plan_id: str,
        receipt_hash: str | None,
    ) -> None:
        if receipt_hash is None:
            raise RepositoryError("history export receipt is required")
        try:
            self._validate_history_hex(receipt_hash, 64)
        except RepositoryError as error:
            raise RepositoryError("history export receipt does not match") from error
        stored = self.find_history_plan(plan_id)
        if (
            stored is None
            or stored["status"] != "planned"
            or stored["export_receipt_hash"] != receipt_hash
        ):
            raise RepositoryError("history export receipt does not match")

    def authorize_history_export_receipt(
        self,
        plan_id: str,
        receipt_hash: str | None,
    ) -> HistoryExportArchiveBinding:
        self.assert_export_receipt(plan_id, receipt_hash)
        stored = self.get_history_plan(plan_id)
        binding = stored["export_archive_binding"]
        if (
            not isinstance(binding, HistoryExportArchiveBinding)
            or binding.sha256 != receipt_hash
        ):
            raise RepositoryError("history export archive binding is unavailable")
        return binding

    def backup_bytes(self) -> bytes:
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(":memory:")
            with self._existing_read_connection() as source:
                source.backup(destination)
            backup = destination.serialize()
            if not backup:
                raise RepositoryError("history export database backup is empty")
            return backup
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            raise self._repository_error(error) from error
        finally:
            if destination is not None:
                destination.close()

    def classify_history_cleanup_outcome(
        self,
        plan_id: str,
        unit_ids: tuple[tuple[str, str], ...],
        *,
        expected_plan: HistoryPlan | None = None,
        expected_receipt_hash: str | None = None,
        expected_execution_id: str | None = None,
    ) -> Literal["not_committed", "committed", "indeterminate"]:
        try:
            self._validate_history_hex(plan_id, 32)
            self._validate_history_unit_ids(unit_ids)
            if expected_plan is not None:
                if expected_receipt_hash is None:
                    return "indeterminate"
                self._validate_history_hex(expected_receipt_hash, 64)
                if expected_execution_id is not None:
                    self._validate_history_hex(expected_execution_id, 32)
            elif (
                expected_receipt_hash is not None
                or expected_execution_id is not None
            ):
                return "indeterminate"
            with self._existing_read_connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, policy_hash, manifest_hash,
                           canonical_policy_json, canonical_units_json,
                           manifest_relative_path, status,
                           export_receipt_hash, execution_id
                    FROM history_cleanup_plans
                    WHERE id = ?
                    """,
                    (plan_id,),
                ).fetchone()
                if row is None:
                    return "indeterminate"
                status = self._required_text(row, "status")
                if status not in {"planned", "completed"}:
                    return "indeterminate"
                if expected_plan is None:
                    _, units = self._hydrate_history_plan_binding(row)
                    stored_execution_id = self._optional_text(
                        row,
                        "execution_id",
                    )
                else:
                    _, units, stored_execution_id = (
                        self._assert_exact_history_plan_row(
                            row,
                            expected_plan,
                            expected_receipt_hash,
                            allowed_statuses=("planned", "completed"),
                        )
                    )
                stored_unit_ids = tuple(
                    (unit.kind, unit.row_id)
                    for unit in units
                )
                if not unit_ids or stored_unit_ids != unit_ids:
                    return "indeterminate"
                presence = tuple(
                    self._history_unit_exists(conn, kind, row_id)
                    for kind, row_id in unit_ids
                )
        except Exception:
            return "indeterminate"
        if (
            status == "planned"
            and stored_execution_id is None
            and all(presence)
        ):
            return "not_committed"
        if (
            status == "completed"
            and (
                expected_plan is None
                or (
                    expected_execution_id is not None
                    and stored_execution_id == expected_execution_id
                )
            )
            and not any(presence)
        ):
            return "committed"
        return "indeterminate"

    def vacuum(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            with self._existing_database_operation() as (
                directory_descriptor,
                identity,
            ):
                try:
                    conn = self._connect_validated(
                        directory_descriptor,
                        identity,
                        read_only=False,
                    )
                    conn.execute("VACUUM")
                    self._validate_database_main_identity(
                        directory_descriptor,
                        identity,
                        "after VACUUM",
                    )
                finally:
                    if conn is not None:
                        conn.close()
                        conn = None
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            raise self._repository_error(error) from error
        except OSError as error:
            raise self._unsafe_path_error(self.db_path.name) from error

    def record_history_journal_entry(
        self,
        execution_id: str,
        sequence: int,
        stage: CleanupExecutionState,
        unit: HistoryUnit,
    ) -> None:
        if type(sequence) is not int or sequence < 1:
            raise RepositoryError("history journal sequence must be a positive integer")
        self._validate_history_unit_ids(((unit.kind, unit.row_id),))
        try:
            resolved_stage = CleanupExecutionState(stage)
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO history_cleanup_journal_entries
                    (execution_id, sequence, stage, unit_kind, row_id, relative_path,
                     content_hash, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    sequence,
                    resolved_stage.value,
                    unit.kind,
                    unit.row_id,
                    unit.relative_path,
                    unit.content_hash,
                    utc_now(),
                ),
            )

    def delete_planned_history_units(
        self,
        plan_id: str,
        unit_ids: tuple[tuple[str, str], ...],
        execution_id: str | None = None,
    ) -> None:
        if execution_id is not None:
            self._validate_history_hex(execution_id, 32)
        with self._connection() as conn:
            policy = self._assert_history_plan_is_current(conn, plan_id, unit_ids)
            self._assert_no_history_unit_is_now_protected(conn, unit_ids, policy)
            self._delete_history_units(conn, unit_ids)
            conn.execute(
                """
                UPDATE history_cleanup_plans
                SET status = 'completed', execution_id = ?, completed_at = ?
                WHERE id = ?
                """,
                (execution_id, utc_now(), plan_id),
            )

    def _hydrate_vision_analysis(self, row: sqlite3.Row) -> dict[str, object]:
        try:
            status = VisionRequestStatus(self._required_text(row, "status")).value
            review_status = VisionReviewStatus(
                self._required_text(row, "review_status")
            ).value
            return {
                "id": self._required_text(row, "id"),
                "request_id": self._required_text(row, "request_id"),
                "source_id": self._required_int(row, "source_id"),
                "source_content_hash": self._required_text(
                    row, "source_content_hash"
                ),
                "result_hash": self._optional_text(row, "result_hash"),
                "relative_path": self._optional_text(row, "relative_path"),
                "adapter": self._required_text(row, "adapter"),
                "status": status,
                "confidence": self._optional_text(row, "confidence"),
                "review_status": review_status,
                "created_at": self._required_text(row, "created_at"),
                "reviewed_at": self._optional_text(row, "reviewed_at"),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    @staticmethod
    def _validate_record_identifier(identifier: str) -> None:
        if (
            not isinstance(identifier, str)
            or not identifier
            or "/" in identifier
            or identifier in {".", ".."}
        ):
            raise RepositoryError("stored record identifier is invalid")

    @staticmethod
    def _validate_history_policy(policy: HistoryPolicy) -> None:
        if (
            type(policy.minimum_age_days) is not int
            or policy.minimum_age_days < 0
            or type(policy.keep_latest_text_snapshots_per_source) is not int
            or policy.keep_latest_text_snapshots_per_source < 0
            or type(policy.retain_included_vision_analyses) is not bool
            or type(policy.retain_pending_review_vision_analyses) is not bool
            or type(policy.retain_incomplete_vision_requests) is not bool
        ):
            raise RepositoryError("history policy is invalid")
        if not (
            policy.retain_included_vision_analyses
            and policy.retain_pending_review_vision_analyses
            and policy.retain_incomplete_vision_requests
        ):
            raise RepositoryError("history policy disables mandatory retention")

    @staticmethod
    def _validate_history_hex(value: object, length: int) -> None:
        if (
            not isinstance(value, str)
            or len(value) != length
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise RepositoryError("history cleanup identifier or hash is invalid")

    @classmethod
    def _canonical_history_export_archive_binding(
        cls,
        binding: HistoryExportArchiveBinding,
    ) -> str:
        if not isinstance(binding, HistoryExportArchiveBinding):
            raise RepositoryError("history export archive binding is invalid")
        if not isinstance(binding.archive_path, str) or "\x00" in binding.archive_path:
            raise RepositoryError("history export archive binding is invalid")
        archive_path = Path(binding.archive_path)
        if (
            not archive_path.is_absolute()
            or archive_path.parent == archive_path
            or any(part in {"", ".", ".."} for part in archive_path.parts)
            or archive_path.as_posix() != binding.archive_path
        ):
            raise RepositoryError("history export archive binding is invalid")
        for value in (
            binding.parent_device,
            binding.parent_inode,
            binding.export_directory_device,
            binding.export_directory_inode,
            binding.archive_device,
            binding.archive_inode,
            binding.size,
        ):
            if type(value) is not int or value < 0:
                raise RepositoryError("history export archive binding is invalid")
        try:
            cls._validate_history_hex(binding.sha256, 64)
        except RepositoryError as error:
            raise RepositoryError("history export archive binding is invalid") from error
        return json.dumps(
            asdict(binding),
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _history_export_archive_binding_from_json(
        cls,
        value: str | None,
    ) -> HistoryExportArchiveBinding | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("history export archive binding must be text")
        try:
            payload = json.loads(value)
            if (
                type(payload) is not dict
                or frozenset(payload)
                != frozenset(
                    {
                        "archive_path",
                        "parent_device",
                        "parent_inode",
                        "export_directory_device",
                        "export_directory_inode",
                        "archive_device",
                        "archive_inode",
                        "size",
                        "sha256",
                    }
                )
            ):
                raise ValueError("history export archive binding shape is invalid")
            binding = HistoryExportArchiveBinding(**payload)
            if cls._canonical_history_export_archive_binding(binding) != value:
                raise ValueError("history export archive binding is not canonical")
            return binding
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("history export archive binding is invalid") from error

    @staticmethod
    def _validate_history_unit_ids(
        unit_ids: tuple[tuple[str, str], ...],
    ) -> None:
        seen: set[tuple[str, str]] = set()
        for unit_id in unit_ids:
            if (
                not isinstance(unit_id, tuple)
                or len(unit_id) != 2
                or unit_id[0] not in _HISTORY_UNIT_KINDS
                or not isinstance(unit_id[1], str)
                or not unit_id[1]
            ):
                raise RepositoryError("history unit kind or row id is invalid")
            if unit_id in seen:
                raise RepositoryError("history unit ids must be unique")
            seen.add(unit_id)

    @classmethod
    def _validate_history_unit(cls, unit: HistoryUnit) -> None:
        if not isinstance(unit, HistoryUnit):
            raise RepositoryError("history cleanup plan binding metadata is invalid")
        cls._validate_history_unit_ids(((unit.kind, unit.row_id),))
        if (
            not isinstance(unit.relative_path, str)
            or not unit.relative_path
            or not isinstance(unit.content_hash, str)
            or not unit.content_hash
            or type(unit.size) is not int
            or unit.size < 0
            or type(unit.device) is not int
            or unit.device < 0
            or type(unit.inode) is not int
            or unit.inode < 0
            or not isinstance(unit.reason, str)
            or not unit.reason
        ):
            raise RepositoryError("history cleanup plan binding metadata is invalid")
        relative = Path(unit.relative_path)
        expected_root = _HISTORY_UNIT_ROOTS[unit.kind]
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.as_posix() != unit.relative_path
            or len(relative.parts) <= len(expected_root)
            or relative.parts[: len(expected_root)] != expected_root
        ):
            raise RepositoryError("history cleanup plan binding metadata is invalid")

    @staticmethod
    def _history_unit_sort_key(
        unit: HistoryUnit,
    ) -> tuple[str, str, str, str, int, int, int, str]:
        return (
            unit.kind,
            unit.row_id,
            unit.relative_path,
            unit.content_hash,
            unit.size,
            unit.device,
            unit.inode,
            unit.reason,
        )

    @classmethod
    def _canonical_history_plan_metadata(
        cls,
        policy: HistoryPolicy,
        units: tuple[HistoryUnit, ...],
    ) -> tuple[str, str, str, str]:
        cls._validate_history_policy(policy)
        if not isinstance(units, tuple):
            raise RepositoryError("history cleanup plan binding metadata is invalid")
        if not units:
            raise RepositoryError("history cleanup plan requires at least one unit")
        for unit in units:
            cls._validate_history_unit(unit)
        cls._validate_history_unit_ids(
            tuple((unit.kind, unit.row_id) for unit in units)
        )
        if units != tuple(sorted(units, key=cls._history_unit_sort_key)):
            raise RepositoryError("history cleanup plan units are not canonical")
        policy_payload = asdict(policy)
        units_payload = [asdict(unit) for unit in units]
        canonical_policy_json = json.dumps(
            policy_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        canonical_units_json = json.dumps(
            units_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        manifest_json = json.dumps(
            {"policy": policy_payload, "units": units_payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        policy_hash = sha256(canonical_policy_json.encode("utf-8")).hexdigest()
        manifest_hash = sha256(manifest_json.encode("utf-8")).hexdigest()
        return (
            canonical_policy_json,
            canonical_units_json,
            policy_hash,
            manifest_hash,
        )

    @staticmethod
    def _canonical_history_manifest_path(relative_path: Path) -> str:
        if (
            not isinstance(relative_path, Path)
            or relative_path.is_absolute()
            or not relative_path.parts
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise RepositoryError("history plan path must be managed and relative")
        return relative_path.as_posix()

    def _history_unit(
        self,
        kind: str,
        row_id: str,
        stored_path: str,
        reason: str,
    ) -> HistoryUnit:
        relative_path, size, device, inode, content_hash = (
            self._managed_history_file_metadata(kind, stored_path)
        )
        return HistoryUnit(
            kind,
            row_id,
            relative_path,
            content_hash,
            size,
            device,
            inode,
            reason,
        )

    def _managed_history_file_metadata(
        self,
        kind: str,
        stored_path: str,
    ) -> tuple[str, int, int, int, str]:
        if kind not in _HISTORY_UNIT_KINDS:
            raise RepositoryError("history unit kind or row id is invalid")
        root = self.db_path.parent.absolute()
        path = Path(stored_path)
        candidate = path if path.is_absolute() else root / path
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise RepositoryError(
                "managed history path is outside the workspace"
            ) from error
        if (
            not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise RepositoryError("managed history path is invalid")
        expected_root = _HISTORY_UNIT_ROOTS[kind]
        if (
            len(relative.parts) <= len(expected_root)
            or relative.parts[: len(expected_root)] != expected_root
        ):
            raise RepositoryError(
                "managed history path is outside its kind-specific managed root"
            )
        directory_descriptor: int | None = None
        file_descriptor: int | None = None
        try:
            directory_descriptor = open_managed_directory(
                root.joinpath(*relative.parts[:-1])
            )
            file_descriptor = os.open(
                relative.name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            stat_result = os.fstat(file_descriptor)
            if not S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1:
                raise OSError("managed history candidate must be a regular file")
            digest = sha256()
            while chunk := os.read(file_descriptor, 65_536):
                digest.update(chunk)
            return (
                relative.as_posix(),
                stat_result.st_size,
                stat_result.st_dev,
                stat_result.st_ino,
                digest.hexdigest(),
            )
        except OSError as error:
            raise RepositoryError(
                "managed history path is unsafe or unavailable"
            ) from error
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if directory_descriptor is not None:
                os.close(directory_descriptor)

    def _assert_history_plan_is_current(
        self,
        conn: sqlite3.Connection,
        plan_id: str,
        unit_ids: tuple[tuple[str, str], ...],
    ) -> HistoryPolicy:
        self._validate_history_unit_ids(unit_ids)
        row = conn.execute(
            """
            SELECT id, policy_hash, manifest_hash, canonical_policy_json,
                   canonical_units_json, manifest_relative_path, status,
                   export_receipt_hash, execution_id
            FROM history_cleanup_plans
            WHERE id = ?
            """,
            (plan_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("history cleanup plan was not found")
        try:
            status = self._required_text(row, "status")
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error
        if status != "planned":
            raise RepositoryError("history cleanup plan is no longer current")
        active_binding = self._active_history_plan_binding()
        if active_binding is None:
            policy, units = self._hydrate_history_plan_binding(row)
        else:
            expected_plan, expected_receipt_hash = active_binding
            if expected_plan.id != plan_id:
                raise RepositoryError(
                    "history cleanup plan binding is inconsistent"
                )
            policy, units, execution_id = (
                self._assert_exact_history_plan_row(
                    row,
                    expected_plan,
                    expected_receipt_hash,
                    allowed_statuses=("planned",),
                )
            )
            if execution_id is not None:
                raise RepositoryError(
                    "history cleanup plan binding is inconsistent"
                )
        stored_unit_ids = tuple((unit.kind, unit.row_id) for unit in units)
        if not unit_ids or unit_ids != stored_unit_ids:
            raise RepositoryError(
                "history cleanup requires the plan's exact ordered unit set"
            )
        return policy

    def _assert_exact_history_plan_row(
        self,
        row: sqlite3.Row,
        expected_plan: HistoryPlan,
        expected_receipt_hash: str,
        *,
        allowed_statuses: tuple[str, ...],
    ) -> tuple[HistoryPolicy, tuple[HistoryUnit, ...], str | None]:
        (
            canonical_policy_json,
            canonical_units_json,
            policy_hash,
            manifest_hash,
        ) = self._canonical_history_plan_metadata(
            expected_plan.policy,
            expected_plan.units,
        )
        self._validate_history_hex(expected_receipt_hash, 64)
        expected_manifest_path = (
            Path("reviews")
            / "history-plans"
            / f"plan-{expected_plan.id}-{expected_plan.manifest_hash}.json"
        ).as_posix()
        try:
            stored_id = self._required_text(row, "id")
            stored_policy_hash = self._required_text(row, "policy_hash")
            stored_manifest_hash = self._required_text(
                row,
                "manifest_hash",
            )
            stored_policy_json = self._optional_text(
                row,
                "canonical_policy_json",
            )
            stored_units_json = self._optional_text(
                row,
                "canonical_units_json",
            )
            stored_manifest_path = self._optional_text(
                row,
                "manifest_relative_path",
            )
            stored_status = self._required_text(row, "status")
            stored_receipt_hash = self._optional_text(
                row,
                "export_receipt_hash",
            )
            stored_execution_id = self._optional_text(
                row,
                "execution_id",
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryError(
                "history cleanup plan binding is inconsistent"
            ) from error
        policy, units = self._hydrate_history_plan_binding(row)
        if (
            expected_plan.manifest_hash != manifest_hash
            or stored_id != expected_plan.id
            or stored_policy_hash != policy_hash
            or stored_manifest_hash != manifest_hash
            or stored_policy_json != canonical_policy_json
            or stored_units_json != canonical_units_json
            or stored_manifest_path != expected_manifest_path
            or stored_status not in allowed_statuses
            or stored_receipt_hash != expected_receipt_hash
            or policy != expected_plan.policy
            or units != expected_plan.units
        ):
            raise RepositoryError(
                "history cleanup plan binding is inconsistent"
            )
        return policy, units, stored_execution_id

    def _hydrate_history_plan_binding(
        self,
        row: sqlite3.Row,
    ) -> tuple[HistoryPolicy, tuple[HistoryUnit, ...]]:
        try:
            policy_hash = self._required_text(row, "policy_hash")
            manifest_hash = self._required_text(row, "manifest_hash")
            canonical_policy_json = self._optional_text(
                row,
                "canonical_policy_json",
            )
            canonical_units_json = self._optional_text(
                row,
                "canonical_units_json",
            )
            manifest_relative_path = self._optional_text(
                row,
                "manifest_relative_path",
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryError(
                "history cleanup plan binding metadata is invalid"
            ) from error
        if (
            canonical_policy_json is None
            or canonical_units_json is None
            or manifest_relative_path is None
        ):
            raise RepositoryError(
                "history cleanup plan binding metadata is unavailable"
            )
        try:
            policy_payload = json.loads(canonical_policy_json)
            units_payload = json.loads(canonical_units_json)
            if (
                type(policy_payload) is not dict
                or frozenset(policy_payload) != _HISTORY_POLICY_FIELDS
                or type(units_payload) is not list
            ):
                raise ValueError("history plan metadata shape is invalid")
            policy = HistoryPolicy(**policy_payload)
            units: list[HistoryUnit] = []
            for unit_payload in units_payload:
                if (
                    type(unit_payload) is not dict
                    or frozenset(unit_payload) != _HISTORY_UNIT_FIELDS
                ):
                    raise ValueError("history unit metadata shape is invalid")
                units.append(HistoryUnit(**unit_payload))
            canonical = self._canonical_history_plan_metadata(
                policy,
                tuple(units),
            )
            stored_manifest_path = Path(manifest_relative_path)
            if (
                self._canonical_history_manifest_path(stored_manifest_path)
                != manifest_relative_path
                or canonical[0] != canonical_policy_json
                or canonical[1] != canonical_units_json
                or canonical[2] != policy_hash
                or canonical[3] != manifest_hash
            ):
                raise ValueError("history plan metadata hashes do not match")
        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            RepositoryError,
        ) as error:
            raise RepositoryError(
                "history cleanup plan binding metadata is invalid"
            ) from error
        return policy, tuple(units)

    def _assert_no_history_unit_is_now_protected(
        self,
        conn: sqlite3.Connection,
        unit_ids: tuple[tuple[str, str], ...],
        policy: HistoryPolicy,
    ) -> None:
        snapshot_rows, analysis_rows, request_rows = self._history_candidate_rows(
            conn,
            policy,
        )
        try:
            eligible_ids = {
                ("source_snapshot", str(self._required_int(row, "id")))
                for row in snapshot_rows
            }
            eligible_ids.update(
                ("vision_analysis", self._required_text(row, "id"))
                for row in analysis_rows
            )
            eligible_ids.update(
                ("vision_request", self._required_text(row, "id"))
                for row in request_rows
            )
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error
        planned_ids = set(unit_ids)
        for kind, row_id in unit_ids:
            if not self._history_unit_exists(conn, kind, row_id):
                raise RepositoryError("planned history unit is stale")
            if (kind, row_id) not in eligible_ids:
                raise RepositoryError("planned history unit is now protected")
            if kind == "vision_request":
                row = conn.execute(
                    """
                    SELECT analyses.id AS analysis_id
                    FROM vision_analysis_requests AS requests
                    LEFT JOIN vision_analyses AS analyses
                      ON analyses.request_id = requests.id
                    WHERE requests.id = ?
                    """,
                    (row_id,),
                ).fetchone()
                try:
                    analysis_id = self._optional_text(row, "analysis_id")
                except (KeyError, TypeError, ValueError) as error:
                    raise self._semantic_error() from error
                if (
                    analysis_id is not None
                    and ("vision_analysis", analysis_id) not in planned_ids
                ):
                    raise RepositoryError("planned history unit is now protected")

    def _history_unit_exists(
        self,
        conn: sqlite3.Connection,
        kind: str,
        row_id: str,
    ) -> bool:
        if kind == "source_snapshot":
            row = conn.execute(
                "SELECT 1 FROM source_snapshots WHERE id = ?",
                (self._history_integer_row_id(row_id),),
            ).fetchone()
        elif kind == "vision_analysis":
            row = conn.execute(
                "SELECT 1 FROM vision_analyses WHERE id = ?",
                (row_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM vision_analysis_requests WHERE id = ?",
                (row_id,),
            ).fetchone()
        return row is not None

    def _delete_history_units(
        self,
        conn: sqlite3.Connection,
        unit_ids: tuple[tuple[str, str], ...],
    ) -> None:
        for kind, row_id in unit_ids:
            if kind != "vision_analysis":
                continue
            self._delete_unreferenced_vision_evidence(conn, row_id)
            cursor = conn.execute(
                "DELETE FROM vision_analyses WHERE id = ?",
                (row_id,),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("planned history unit is stale")
        for kind, row_id in unit_ids:
            if kind != "vision_request":
                continue
            cursor = conn.execute(
                "DELETE FROM vision_analysis_requests WHERE id = ?",
                (row_id,),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("planned history unit is stale")
        for kind, row_id in unit_ids:
            if kind != "source_snapshot":
                continue
            cursor = conn.execute(
                "DELETE FROM source_snapshots WHERE id = ?",
                (self._history_integer_row_id(row_id),),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("planned history unit is stale")

    @staticmethod
    def _history_integer_row_id(row_id: str) -> int:
        try:
            value = int(row_id)
        except ValueError as error:
            raise RepositoryError("history unit row id is invalid") from error
        if value < 1 or str(value) != row_id:
            raise RepositoryError("history unit row id is invalid")
        return value

    def create_semantic_revision(
        self,
        revision_id: str,
        source_id: str,
        policy_hash: str,
        analyzer_version: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO semantic_index_revisions
                    (id, source_id, policy_hash, analyzer_version, status)
                VALUES (?, ?, ?, ?, 'staging')
                """,
                (revision_id, source_id, policy_hash, analyzer_version),
            )

    @staticmethod
    def _semantic_facts_payload(facts: SemanticFacts) -> dict[str, list[str]]:
        return {
            "purpose_signals": list(facts.purpose_signals),
            "parent_context_signals": list(facts.parent_context_signals),
            "independence_signals": list(facts.independence_signals),
            "triviality_signals": list(facts.triviality_signals),
            "grounding_signal_ids": list(facts.grounding_signal_ids),
        }

    @staticmethod
    def _semantic_coverage_payload(
        coverage: SemanticCoverage,
    ) -> dict[str, int | str]:
        return {
            "node_count": coverage.node_count,
            "content_node_count": coverage.content_node_count,
            "structural_only_count": coverage.structural_only_count,
            "failed_count": coverage.failed_count,
            "node_ids_sha256": coverage.node_ids_sha256,
        }

    @staticmethod
    def _semantic_sha256(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @classmethod
    def _validate_v2_semantic_node(
        cls,
        node: SemanticNode,
        *,
        source_id: str,
    ) -> None:
        facts = cls._semantic_facts_payload(node.semantic_facts)
        coverage = cls._semantic_coverage_payload(node.coverage)
        text_arrays = (
            node.semantic_roles,
            node.topics,
            node.semantic_facts.purpose_signals,
            node.semantic_facts.parent_context_signals,
            node.semantic_facts.independence_signals,
            node.semantic_facts.triviality_signals,
            node.semantic_facts.grounding_signal_ids,
            node.child_node_ids,
        )
        if (
            not node.node_id
            or node.source_id != source_id
            or node.analysis_protocol != "codex-semantic-v2"
            or node.analyzer_version != "semantic-input-v2"
            or not node.display_name
            or not node.relative_hierarchy
            or any(
                len(values) != len(set(values))
                or any(not isinstance(value, str) or not value for value in values)
                for values in text_arrays
            )
            or tuple(sorted(node.child_node_ids)) != node.child_node_ids
            or tuple(sorted(node.evidence_ids)) != node.evidence_ids
            or len(node.evidence_ids) != len(set(node.evidence_ids))
            or any(type(value) is not int or value < 1 for value in node.evidence_ids)
            or any(
                type(coverage[key]) is not int or int(coverage[key]) < 0
                for key in (
                    "node_count",
                    "content_node_count",
                    "structural_only_count",
                    "failed_count",
                )
            )
            or int(coverage["node_count"]) < 1
            or not cls._semantic_sha256(coverage["node_ids_sha256"])
            or (
                node.semantic_disposition
                is SemanticNodeDisposition.ANALYZED
                and (
                    not node.semantic_summary
                    or not facts["grounding_signal_ids"]
                )
            )
            or (
                node.semantic_disposition
                is not SemanticNodeDisposition.ANALYZED
                and (
                    node.semantic_summary
                    or node.semantic_roles
                    or node.topics
                    or any(facts.values())
                )
            )
        ):
            raise RepositoryError("semantic revision graph is invalid")

    @classmethod
    def _semantic_node_values(
        cls,
        revision_id: str,
        node: SemanticNode,
    ) -> tuple[object, ...]:
        return (
            revision_id,
            node.node_id,
            node.source_id,
            node.node_kind.value,
            node.parent_node_id,
            node.display_name,
            node.relative_hierarchy,
            node.content_fingerprint,
            node.semantic_summary,
            json.dumps(node.semantic_roles),
            json.dumps(node.topics),
            json.dumps(node.evidence_ids),
            node.analysis_status.value,
            node.analyzer_version,
            node.semantic_disposition.value,
            json.dumps(
                cls._semantic_facts_payload(node.semantic_facts),
                sort_keys=True,
                separators=(",", ":"),
            ),
            node.analysis_protocol,
            node.skip_reason,
            json.dumps(node.child_node_ids),
            json.dumps(
                cls._semantic_coverage_payload(node.coverage),
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    @staticmethod
    def _semantic_node_insert_sql() -> str:
        return """
            INSERT INTO semantic_nodes (
                revision_id, node_id, source_id, node_kind, parent_node_id,
                display_name, relative_hierarchy, content_fingerprint,
                semantic_summary, semantic_roles_json, topics_json,
                evidence_ids_json, analysis_status, analyzer_version,
                semantic_disposition, semantic_facts_json, analysis_protocol,
                skip_reason, child_node_ids_json, coverage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

    def import_semantic_revision_chunk(
        self,
        revision_id: str,
        chunk_index: int,
        chunk_sha256: str,
        locator_chunk_sha256: str,
        node_ids_sha256: str,
        nodes: tuple[SemanticNode, ...],
        locators: dict[str, tuple[str, int | None, int | None]],
        edges: tuple[SemanticEdge, ...],
    ) -> None:
        if (
            not revision_id
            or type(chunk_index) is not int
            or chunk_index < 1
            or not self._semantic_sha256(chunk_sha256)
            or not self._semantic_sha256(locator_chunk_sha256)
            or not self._semantic_sha256(node_ids_sha256)
            or not nodes
        ):
            raise RepositoryError("semantic import receipt is invalid")
        expected_receipt = SemanticImportReceipt(
            revision_id=revision_id,
            chunk_index=chunk_index,
            chunk_sha256=chunk_sha256,
            locator_chunk_sha256=locator_chunk_sha256,
            node_ids_sha256=node_ids_sha256,
            node_count=len(nodes),
        )
        node_ids = tuple(node.node_id for node in nodes)
        if (
            len(node_ids) != len(set(node_ids))
            or set(locators) != set(node_ids)
            or sha256(
                json.dumps(
                    list(node_ids),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            != node_ids_sha256
        ):
            raise RepositoryError("semantic revision graph is invalid")

        with self._connection() as conn:
            revision = conn.execute(
                """
                SELECT source_id, analyzer_version, status
                FROM semantic_index_revisions
                WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
            if (
                revision is None
                or self._required_text(revision, "status") != "staging"
                or self._required_text(revision, "analyzer_version")
                != "semantic-input-v2"
            ):
                raise RepositoryError("semantic revision is not staging")
            source_id = self._required_text(revision, "source_id")
            for node in nodes:
                self._validate_v2_semantic_node(node, source_id=source_id)

            edge_by_child: dict[str, SemanticEdge] = {}
            for edge in edges:
                if (
                    edge.revision_id != revision_id
                    or edge.relation != "contains"
                    or edge.confidence is not None
                    or edge.to_node_id not in node_ids
                    or edge.to_node_id in edge_by_child
                ):
                    raise RepositoryError("semantic revision graph is invalid")
                edge_by_child[edge.to_node_id] = edge
            if any(
                (
                    node.parent_node_id is None
                    and node.node_id in edge_by_child
                )
                or (
                    node.parent_node_id is not None
                    and (
                        node.node_id not in edge_by_child
                        or edge_by_child[node.node_id].from_node_id
                        != node.parent_node_id
                    )
                )
                for node in nodes
            ):
                raise RepositoryError("semantic revision graph is invalid")

            receipt_row = conn.execute(
                """
                SELECT revision_id, chunk_index, chunk_sha256,
                       locator_chunk_sha256, node_ids_sha256, node_count
                FROM semantic_import_receipts
                WHERE revision_id = ? AND chunk_index = ?
                """,
                (revision_id, chunk_index),
            ).fetchone()
            if receipt_row is not None:
                existing = self._semantic_import_receipt_from_row(receipt_row)
                if existing != expected_receipt:
                    raise RepositoryError("semantic import receipt conflicts")
                self._validate_imported_semantic_chunk(
                    conn,
                    revision_id,
                    nodes,
                    locators,
                    edges,
                )
                return

            for node in nodes:
                conn.execute(
                    self._semantic_node_insert_sql(),
                    self._semantic_node_values(revision_id, node),
                )
                locator, device, inode = locators[node.node_id]
                if (
                    not isinstance(locator, str)
                    or not locator
                    or device is not None
                    and (type(device) is not int or device < 0)
                    or inode is not None
                    and (type(inode) is not int or inode < 0)
                ):
                    raise RepositoryError("semantic node locator is invalid")
                conn.execute(
                    """
                    INSERT INTO semantic_node_locators
                        (revision_id, node_id, locator, device, inode)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (revision_id, node.node_id, locator, device, inode),
                )
            for edge in edges:
                conn.execute(
                    """
                    INSERT INTO semantic_edges
                        (revision_id, from_node_id, relation, to_node_id, confidence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        edge.from_node_id,
                        edge.relation,
                        edge.to_node_id,
                        edge.confidence,
                    ),
                )
            self._validate_imported_semantic_chunk(
                conn,
                revision_id,
                nodes,
                locators,
                edges,
            )
            conn.execute(
                """
                INSERT INTO semantic_import_receipts (
                    revision_id, chunk_index, chunk_sha256,
                    locator_chunk_sha256, node_ids_sha256, node_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    chunk_index,
                    chunk_sha256,
                    locator_chunk_sha256,
                    node_ids_sha256,
                    len(nodes),
                ),
            )

    @classmethod
    def _semantic_import_receipt_from_row(
        cls,
        row: sqlite3.Row,
    ) -> SemanticImportReceipt:
        receipt = SemanticImportReceipt(
            revision_id=cls._required_text(row, "revision_id"),
            chunk_index=cls._required_int(row, "chunk_index"),
            chunk_sha256=cls._required_text(row, "chunk_sha256"),
            locator_chunk_sha256=cls._required_text(
                row, "locator_chunk_sha256"
            ),
            node_ids_sha256=cls._required_text(row, "node_ids_sha256"),
            node_count=cls._required_int(row, "node_count"),
        )
        if (
            receipt.chunk_index < 1
            or receipt.node_count < 1
            or not cls._semantic_sha256(receipt.chunk_sha256)
            or not cls._semantic_sha256(receipt.locator_chunk_sha256)
            or not cls._semantic_sha256(receipt.node_ids_sha256)
        ):
            raise cls._semantic_error()
        return receipt

    @classmethod
    def _validate_imported_semantic_chunk(
        cls,
        conn: sqlite3.Connection,
        revision_id: str,
        nodes: tuple[SemanticNode, ...],
        locators: dict[str, tuple[str, int | None, int | None]],
        edges: tuple[SemanticEdge, ...],
    ) -> None:
        for node in nodes:
            row = conn.execute(
                """
                SELECT *
                FROM semantic_nodes
                WHERE revision_id = ? AND node_id = ?
                """,
                (revision_id, node.node_id),
            ).fetchone()
            if (
                row is None
                or tuple(row[key] for key in (
                    "revision_id",
                    "node_id",
                    "source_id",
                    "node_kind",
                    "parent_node_id",
                    "display_name",
                    "relative_hierarchy",
                    "content_fingerprint",
                    "semantic_summary",
                    "semantic_roles_json",
                    "topics_json",
                    "evidence_ids_json",
                    "analysis_status",
                    "analyzer_version",
                    "semantic_disposition",
                    "semantic_facts_json",
                    "analysis_protocol",
                    "skip_reason",
                    "child_node_ids_json",
                    "coverage_json",
                ))
                != cls._semantic_node_values(revision_id, node)
            ):
                raise RepositoryError("semantic imported rows are invalid")
            locator_row = conn.execute(
                """
                SELECT locator, device, inode
                FROM semantic_node_locators
                WHERE revision_id = ? AND node_id = ?
                """,
                (revision_id, node.node_id),
            ).fetchone()
            if (
                locator_row is None
                or (
                    locator_row["locator"],
                    locator_row["device"],
                    locator_row["inode"],
                )
                != locators[node.node_id]
            ):
                raise RepositoryError("semantic imported rows are invalid")
        actual_edges = {
            (
                cls._required_text(row, "from_node_id"),
                cls._required_text(row, "relation"),
                cls._required_text(row, "to_node_id"),
                cls._optional_text(row, "confidence"),
            )
            for row in conn.execute(
                """
                SELECT from_node_id, relation, to_node_id, confidence
                FROM semantic_edges
                WHERE revision_id = ?
                  AND to_node_id IN (
                      SELECT value FROM json_each(?)
                  )
                """,
                (revision_id, json.dumps([node.node_id for node in nodes])),
            ).fetchall()
        }
        expected_edges = {
            (
                edge.from_node_id,
                edge.relation,
                edge.to_node_id,
                edge.confidence,
            )
            for edge in edges
        }
        if actual_edges != expected_edges:
            raise RepositoryError("semantic imported rows are invalid")

    def list_semantic_import_receipts(
        self,
        revision_id: str,
    ) -> tuple[SemanticImportReceipt, ...]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """
                SELECT revision_id, chunk_index, chunk_sha256,
                       locator_chunk_sha256, node_ids_sha256, node_count
                FROM semantic_import_receipts
                WHERE revision_id = ?
                ORDER BY chunk_index
                """,
                (revision_id,),
            ).fetchall()
        try:
            return tuple(
                self._semantic_import_receipt_from_row(row) for row in rows
            )
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def activate_imported_semantic_revision(
        self,
        revision_id: str,
        expected_receipts: tuple[SemanticImportReceipt, ...],
        expected_node_count: int,
        expected_edge_count: int,
        source_id: str,
        policy_hash: str,
        analyzer_version: str,
    ) -> None:
        if (
            not revision_id
            or not source_id
            or not self._semantic_sha256(policy_hash)
            or analyzer_version != "semantic-input-v2"
            or type(expected_node_count) is not int
            or expected_node_count < 1
            or type(expected_edge_count) is not int
            or expected_edge_count < 0
            or tuple(
                receipt.chunk_index for receipt in expected_receipts
            )
            != tuple(range(1, len(expected_receipts) + 1))
            or sum(receipt.node_count for receipt in expected_receipts)
            != expected_node_count
        ):
            raise RepositoryError("semantic import activation is invalid")

        with self._connection() as conn:
            revision = conn.execute(
                """
                SELECT source_id, policy_hash, analyzer_version, status
                FROM semantic_index_revisions
                WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
            if (
                revision is None
                or self._required_text(revision, "source_id") != source_id
                or self._required_text(revision, "policy_hash") != policy_hash
                or self._required_text(revision, "analyzer_version")
                != analyzer_version
                or self._required_text(revision, "status")
                not in {"staging", "active"}
            ):
                raise RepositoryError("semantic import activation is invalid")
            status = self._required_text(revision, "status")
            active_count_row = conn.execute(
                """
                SELECT COUNT(*) AS active_count
                FROM semantic_index_revisions
                WHERE source_id = ? AND status = 'active'
                """,
                (source_id,),
            ).fetchone()
            active_count = (
                self._required_int(active_count_row, "active_count")
                if active_count_row is not None
                else -1
            )
            if active_count > 1 or (
                status == "active" and active_count != 1
            ):
                raise RepositoryError("semantic import activation is invalid")

            receipt_rows = conn.execute(
                """
                SELECT revision_id, chunk_index, chunk_sha256,
                       locator_chunk_sha256, node_ids_sha256, node_count
                FROM semantic_import_receipts
                WHERE revision_id = ?
                ORDER BY chunk_index
                """,
                (revision_id,),
            ).fetchall()
            actual_receipts = tuple(
                self._semantic_import_receipt_from_row(row)
                for row in receipt_rows
            )
            if actual_receipts != expected_receipts:
                raise RepositoryError("semantic import activation is invalid")

            counts = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM semantic_nodes WHERE revision_id = ?) AS nodes,
                    (SELECT COUNT(*) FROM semantic_node_locators WHERE revision_id = ?) AS locators,
                    (SELECT COUNT(*) FROM semantic_edges WHERE revision_id = ?) AS edges
                """,
                (revision_id, revision_id, revision_id),
            ).fetchone()
            if (
                counts is None
                or self._required_int(counts, "nodes") != expected_node_count
                or self._required_int(counts, "locators")
                != expected_node_count
                or self._required_int(counts, "edges") != expected_edge_count
                or expected_edge_count != expected_node_count - 1
            ):
                raise RepositoryError("semantic import activation is invalid")

            node_rows = conn.execute(
                """
                SELECT *
                FROM semantic_nodes
                WHERE revision_id = ?
                ORDER BY relative_hierarchy, node_id
                """,
                (revision_id,),
            )
            root_count = 0
            hydrated_count = 0
            for row in node_rows:
                node = self._semantic_node_from_row(row)
                self._validate_v2_semantic_node(node, source_id=source_id)
                root_count += int(node.parent_node_id is None)
                hydrated_count += 1
            if hydrated_count != expected_node_count or root_count != 1:
                raise RepositoryError("semantic import activation is invalid")

            invalid_parent_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM semantic_nodes AS child
                LEFT JOIN semantic_nodes AS parent
                  ON parent.revision_id = child.revision_id
                 AND parent.node_id = child.parent_node_id
                WHERE child.revision_id = ?
                  AND child.parent_node_id IS NOT NULL
                  AND parent.node_id IS NULL
                """,
                (revision_id,),
            ).fetchone()
            invalid_edge_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM semantic_edges AS edge
                LEFT JOIN semantic_nodes AS child
                  ON child.revision_id = edge.revision_id
                 AND child.node_id = edge.to_node_id
                WHERE edge.revision_id = ?
                  AND (
                      edge.relation != 'contains'
                      OR edge.confidence IS NOT NULL
                      OR child.node_id IS NULL
                      OR child.parent_node_id != edge.from_node_id
                  )
                """,
                (revision_id,),
            ).fetchone()
            missing_edge_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM semantic_nodes AS child
                LEFT JOIN semantic_edges AS edge
                  ON edge.revision_id = child.revision_id
                 AND edge.from_node_id = child.parent_node_id
                 AND edge.relation = 'contains'
                 AND edge.to_node_id = child.node_id
                WHERE child.revision_id = ?
                  AND child.parent_node_id IS NOT NULL
                  AND edge.to_node_id IS NULL
                """,
                (revision_id,),
            ).fetchone()
            child_binding_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM semantic_edges AS edge
                JOIN semantic_nodes AS parent
                  ON parent.revision_id = edge.revision_id
                 AND parent.node_id = edge.from_node_id
                WHERE edge.revision_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM json_each(parent.child_node_ids_json)
                      WHERE json_each.value = edge.to_node_id
                  )
                """,
                (revision_id,),
            ).fetchone()
            listed_child_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM semantic_nodes AS parent
                JOIN json_each(parent.child_node_ids_json) AS child_id
                LEFT JOIN semantic_edges AS edge
                  ON edge.revision_id = parent.revision_id
                 AND edge.from_node_id = parent.node_id
                 AND edge.relation = 'contains'
                 AND edge.to_node_id = child_id.value
                WHERE parent.revision_id = ?
                  AND edge.to_node_id IS NULL
                """,
                (revision_id,),
            ).fetchone()
            if any(
                row is None or row[0] != 0
                for row in (
                    invalid_parent_count,
                    invalid_edge_count,
                    missing_edge_count,
                    child_binding_count,
                    listed_child_count,
                )
            ):
                raise RepositoryError("semantic import activation is invalid")

            reachable_count = conn.execute(
                """
                WITH RECURSIVE reachable(node_id) AS (
                    SELECT node_id
                    FROM semantic_nodes
                    WHERE revision_id = ? AND parent_node_id IS NULL
                    UNION
                    SELECT edge.to_node_id
                    FROM semantic_edges AS edge
                    JOIN reachable
                      ON reachable.node_id = edge.from_node_id
                    WHERE edge.revision_id = ?
                      AND edge.relation = 'contains'
                )
                SELECT COUNT(*) AS reachable_count
                FROM reachable
                """,
                (revision_id, revision_id),
            ).fetchone()
            if (
                reachable_count is None
                or self._required_int(
                    reachable_count,
                    "reachable_count",
                )
                != expected_node_count
            ):
                raise RepositoryError("semantic import activation is invalid")

            if status == "active":
                return
            conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'superseded'
                WHERE source_id = ? AND status = 'active'
                """,
                (source_id,),
            )
            result = conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'active', completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            )
            if result.rowcount != 1:
                raise RepositoryError("semantic import activation is invalid")

    def replace_semantic_revision_graph(
        self,
        revision_id: str,
        nodes: tuple[SemanticNode, ...],
        locators: dict[str, tuple[str, int | None, int | None]],
        edges: tuple[SemanticEdge, ...],
    ) -> None:
        with self._connection() as conn:
            revision = conn.execute(
                """
                SELECT source_id FROM semantic_index_revisions
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise RepositoryError("semantic revision is not staging")
            source_id = self._required_text(revision, "source_id")
            node_ids = {node.node_id for node in nodes}
            if len(node_ids) != len(nodes) or set(locators) != node_ids:
                raise RepositoryError("semantic revision graph is invalid")
            if any(node.source_id != source_id for node in nodes):
                raise RepositoryError("semantic revision graph is invalid")
            if any(
                edge.revision_id != revision_id
                or edge.from_node_id not in node_ids
                or edge.to_node_id not in node_ids
                for edge in edges
            ):
                raise RepositoryError("semantic revision graph is invalid")

            conn.execute("DELETE FROM semantic_edges WHERE revision_id = ?", (revision_id,))
            conn.execute("DELETE FROM semantic_node_locators WHERE revision_id = ?", (revision_id,))
            conn.execute("DELETE FROM semantic_nodes WHERE revision_id = ?", (revision_id,))
            for node in nodes:
                conn.execute(
                    """
                    INSERT INTO semantic_nodes (
                        revision_id, node_id, source_id, node_kind, parent_node_id,
                        display_name, relative_hierarchy, content_fingerprint,
                        semantic_summary, semantic_roles_json, topics_json,
                        evidence_ids_json, analysis_status, analyzer_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        node.node_id,
                        node.source_id,
                        node.node_kind.value,
                        node.parent_node_id,
                        node.display_name,
                        node.relative_hierarchy,
                        node.content_fingerprint,
                        node.semantic_summary,
                        json.dumps(node.semantic_roles),
                        json.dumps(node.topics),
                        json.dumps(node.evidence_ids),
                        node.analysis_status.value,
                        node.analyzer_version,
                    ),
                )
                locator, device, inode = locators[node.node_id]
                if (
                    not isinstance(locator, str)
                    or (device is not None and type(device) is not int)
                    or (inode is not None and type(inode) is not int)
                ):
                    raise RepositoryError("semantic node locator is invalid")
                conn.execute(
                    """
                    INSERT INTO semantic_node_locators
                        (revision_id, node_id, locator, device, inode)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (revision_id, node.node_id, locator, device, inode),
                )
            for edge in edges:
                conn.execute(
                    """
                    INSERT INTO semantic_edges
                        (revision_id, from_node_id, relation, to_node_id, confidence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        edge.from_node_id,
                        edge.relation,
                        edge.to_node_id,
                        edge.confidence,
                    ),
                )

    def activate_semantic_revision(self, revision_id: str) -> None:
        with self._connection() as conn:
            revision = conn.execute(
                """
                SELECT source_id FROM semantic_index_revisions
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise RepositoryError("semantic revision is not staging")
            source_id = self._required_text(revision, "source_id")
            conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'superseded'
                WHERE source_id = ? AND status = 'active'
                """,
                (source_id,),
            )
            conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'active', completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            )

    def replace_and_activate_semantic_revision(
        self, revision_id: str, nodes: tuple[SemanticNode, ...]
    ) -> None:
        """Atomically replace staged semantic annotations and activate the revision."""
        with self._connection() as conn:
            revision = conn.execute(
                """
                SELECT source_id FROM semantic_index_revisions
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise RepositoryError("semantic revision is not staging")
            source_id = self._required_text(revision, "source_id")
            existing_rows = conn.execute(
                "SELECT node_id FROM semantic_nodes WHERE revision_id = ?",
                (revision_id,),
            ).fetchall()
            existing_node_ids = {self._required_text(row, "node_id") for row in existing_rows}
            node_ids = {node.node_id for node in nodes}
            if (
                len(nodes) != len(node_ids)
                or node_ids != existing_node_ids
                or any(node.source_id != source_id for node in nodes)
            ):
                raise RepositoryError("semantic revision graph is invalid")

            for node in nodes:
                result = conn.execute(
                    """
                    UPDATE semantic_nodes
                    SET semantic_summary = ?, semantic_roles_json = ?, topics_json = ?,
                        evidence_ids_json = ?, analysis_status = ?, analyzer_version = ?
                    WHERE revision_id = ? AND node_id = ?
                    """,
                    (
                        node.semantic_summary,
                        json.dumps(node.semantic_roles),
                        json.dumps(node.topics),
                        json.dumps(node.evidence_ids),
                        node.analysis_status.value,
                        node.analyzer_version,
                        revision_id,
                        node.node_id,
                    ),
                )
                if result.rowcount != 1:
                    raise RepositoryError("semantic revision graph is invalid")

            conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'superseded'
                WHERE source_id = ? AND status = 'active'
                """,
                (source_id,),
            )
            result = conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'active', completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            )
            if result.rowcount != 1:
                raise RepositoryError("semantic revision is not staging")

    def fail_semantic_revision(self, revision_id: str) -> None:
        with self._connection() as conn:
            result = conn.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'failed', completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'staging'
                """,
                (revision_id,),
            )
            if result.rowcount != 1:
                raise RepositoryError("semantic revision is not staging")

    def get_active_semantic_revision(self, source_id: str) -> dict[str, str | None] | None:
        with self._read_connection() as conn:
            row = conn.execute(
                """
                SELECT id, source_id, policy_hash, analyzer_version, status, started_at, completed_at
                FROM semantic_index_revisions
                WHERE source_id = ? AND status = 'active'
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return {
                "id": self._required_text(row, "id"),
                "source_id": self._required_text(row, "source_id"),
                "policy_hash": self._required_text(row, "policy_hash"),
                "analyzer_version": self._required_text(row, "analyzer_version"),
                "status": self._required_text(row, "status"),
                "started_at": self._optional_text(row, "started_at"),
                "completed_at": self._optional_text(row, "completed_at"),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    @classmethod
    def _semantic_node_from_row(cls, row: sqlite3.Row) -> SemanticNode:
        facts_value = json.loads(cls._required_text(row, "semantic_facts_json"))
        coverage_value = json.loads(cls._required_text(row, "coverage_json"))
        if facts_value == {}:
            facts = SemanticFacts()
        elif (
            isinstance(facts_value, dict)
            and set(facts_value)
            == {
                "purpose_signals",
                "parent_context_signals",
                "independence_signals",
                "triviality_signals",
                "grounding_signal_ids",
            }
            and all(
                isinstance(value, list)
                and all(isinstance(item, str) for item in value)
                for value in facts_value.values()
            )
        ):
            facts = SemanticFacts(
                purpose_signals=tuple(facts_value["purpose_signals"]),
                parent_context_signals=tuple(
                    facts_value["parent_context_signals"]
                ),
                independence_signals=tuple(
                    facts_value["independence_signals"]
                ),
                triviality_signals=tuple(facts_value["triviality_signals"]),
                grounding_signal_ids=tuple(
                    facts_value["grounding_signal_ids"]
                ),
            )
        else:
            raise TypeError("semantic_facts_json must be exact semantic facts")
        if coverage_value == {}:
            coverage = SemanticCoverage()
        elif (
            isinstance(coverage_value, dict)
            and set(coverage_value)
            == {
                "node_count",
                "content_node_count",
                "structural_only_count",
                "failed_count",
                "node_ids_sha256",
            }
            and all(
                type(coverage_value[key]) is int
                for key in (
                    "node_count",
                    "content_node_count",
                    "structural_only_count",
                    "failed_count",
                )
            )
            and isinstance(coverage_value["node_ids_sha256"], str)
        ):
            coverage = SemanticCoverage(
                node_count=coverage_value["node_count"],
                content_node_count=coverage_value["content_node_count"],
                structural_only_count=coverage_value[
                    "structural_only_count"
                ],
                failed_count=coverage_value["failed_count"],
                node_ids_sha256=coverage_value["node_ids_sha256"],
            )
        else:
            raise TypeError("coverage_json must be exact semantic coverage")
        return SemanticNode(
            node_id=cls._required_text(row, "node_id"),
            source_id=cls._required_text(row, "source_id"),
            node_kind=SemanticNodeKind(cls._required_text(row, "node_kind")),
            parent_node_id=cls._optional_text(row, "parent_node_id"),
            display_name=cls._required_text(row, "display_name"),
            relative_hierarchy=cls._required_text(row, "relative_hierarchy"),
            content_fingerprint=cls._optional_text(
                row, "content_fingerprint"
            ),
            semantic_summary=cls._required_text(row, "semantic_summary"),
            semantic_roles=tuple(
                cls._required_text_list(row, "semantic_roles_json")
            ),
            topics=tuple(cls._required_text_list(row, "topics_json")),
            evidence_ids=tuple(
                cls._required_int_list(row, "evidence_ids_json")
            ),
            analysis_status=AnalysisStatus(
                cls._required_text(row, "analysis_status")
            ),
            analyzer_version=cls._required_text(row, "analyzer_version"),
            updated_at="",
            semantic_disposition=SemanticNodeDisposition(
                cls._required_text(row, "semantic_disposition")
            ),
            semantic_facts=facts,
            analysis_protocol=cls._required_text(row, "analysis_protocol"),
            skip_reason=cls._optional_text(row, "skip_reason"),
            child_node_ids=tuple(
                cls._required_text_list(row, "child_node_ids_json")
            ),
            coverage=coverage,
        )

    def list_semantic_nodes(self, revision_id: str) -> list[dict[str, object]]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """
                SELECT node_id, source_id, node_kind, parent_node_id, display_name,
                       relative_hierarchy, content_fingerprint, semantic_summary,
                       semantic_roles_json, topics_json, evidence_ids_json,
                       analysis_status, analyzer_version, semantic_disposition,
                       semantic_facts_json, analysis_protocol, skip_reason,
                       child_node_ids_json, coverage_json
                FROM semantic_nodes
                WHERE revision_id = ?
                ORDER BY relative_hierarchy, node_id
                """,
                (revision_id,),
            ).fetchall()
        try:
            return [
                {
                    "node_id": node.node_id,
                    "source_id": node.source_id,
                    "node_kind": node.node_kind.value,
                    "parent_node_id": node.parent_node_id,
                    "display_name": node.display_name,
                    "relative_hierarchy": node.relative_hierarchy,
                    "content_fingerprint": node.content_fingerprint,
                    "semantic_summary": node.semantic_summary,
                    "semantic_roles": list(node.semantic_roles),
                    "topics": list(node.topics),
                    "evidence_ids": list(node.evidence_ids),
                    "analysis_status": node.analysis_status.value,
                    "analyzer_version": node.analyzer_version,
                    "semantic_disposition": node.semantic_disposition.value,
                    "semantic_facts": self._semantic_facts_payload(
                        node.semantic_facts
                    )
                    if node.analysis_protocol == "codex-semantic-v2"
                    else {},
                    "analysis_protocol": node.analysis_protocol,
                    "skip_reason": node.skip_reason,
                    "child_node_ids": list(node.child_node_ids),
                    "coverage": self._semantic_coverage_payload(node.coverage)
                    if node.analysis_protocol == "codex-semantic-v2"
                    else {},
                }
                for node in (
                    self._semantic_node_from_row(row) for row in rows
                )
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise self._semantic_error() from error

    def upsert_source(self, source: Source) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "sources")
            origin_type = source.origin_type or (
                "public_github"
                if source.type == SourceType.GITHUB_REPOSITORY
                else "local"
            )
            origin_visibility = source.origin_visibility or (
                "public"
                if source.type == SourceType.GITHUB_REPOSITORY
                else "private"
            )
            if {"origin_type", "origin_visibility"} <= columns:
                conn.execute(
                    """
                    INSERT INTO sources
                        (type, uri, display_name, owner, status, origin_type, origin_visibility)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uri) DO UPDATE SET
                        type = excluded.type,
                        display_name = excluded.display_name,
                        owner = excluded.owner,
                        origin_type = excluded.origin_type,
                        origin_visibility = excluded.origin_visibility,
                        status = CASE
                            WHEN sources.status = ? AND excluded.status = ? THEN sources.status
                            ELSE excluded.status
                        END
                    """,
                    (
                        source.type.value,
                        source.uri,
                        source.display_name,
                        source.owner,
                        source.status.value,
                        origin_type,
                        origin_visibility,
                        SourceStatus.INGESTED.value,
                        SourceStatus.DISCOVERED.value,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sources (type, uri, display_name, owner, status)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(uri) DO UPDATE SET
                        type = excluded.type,
                        display_name = excluded.display_name,
                        owner = excluded.owner,
                        status = CASE
                            WHEN sources.status = ? AND excluded.status = ? THEN sources.status
                            ELSE excluded.status
                        END
                    """,
                    (
                        source.type.value,
                        source.uri,
                        source.display_name,
                        source.owner,
                        source.status.value,
                        SourceStatus.INGESTED.value,
                        SourceStatus.DISCOVERED.value,
                    ),
                )
            row = conn.execute(
                "SELECT id FROM sources WHERE uri = ?",
                (source.uri,),
            ).fetchone()
        return int(row["id"])

    def insert_github_activity(self, activity: GitHubActivity) -> int:
        if not activity.state.strip():
            raise RepositoryError("GitHub activity state must be non-empty")
        if not is_valid_github_activity_state(
            activity.activity_type, activity.state, activity.state_field
        ):
            raise RepositoryError("GitHub activity state or provenance is invalid")
        try:
            repository_name = canonical_repository_name(activity.repo)
        except ValueError as error:
            raise RepositoryError("GitHub activity repository is invalid") from error
        activity_url = canonical_public_github_activity_url(activity.url)
        if activity_url is None:
            raise RepositoryError("GitHub activity URL is invalid")
        with self._connection() as conn:
            columns = self._table_columns(conn, "github_activities")
            origin_type = activity.origin_type or (
                "private_github" if activity.is_private else "public_github"
            )
            origin_visibility = activity.origin_visibility or (
                "private" if activity.is_private else "public"
            )
            values = (
                activity.source_id,
                repository_name,
                activity.activity_type,
                activity_url,
                activity.title,
                activity.state,
                activity.author,
                activity.created_at,
                activity.merged_at,
                int(activity.is_private),
                activity.state_field,
            )
            if {"origin_type", "origin_visibility"} <= columns:
                values = (*values, origin_type, origin_visibility, int(activity.is_current))
                if activity.source_id is not None:
                    conn.execute(
                        """
                        UPDATE sources
                        SET origin_type = ?, origin_visibility = ?
                        WHERE id = ? AND type = 'github_repository'
                        """,
                        (origin_type, origin_visibility, activity.source_id),
                    )
            candidate_rows = conn.execute(
                """
                SELECT id, url FROM github_activities
                WHERE repo COLLATE NOCASE = ?
                  AND activity_type = ?
                  AND url COLLATE NOCASE = ?
                ORDER BY id
                """,
                (repository_name, activity.activity_type, activity_url),
            ).fetchall()
            canonical_rows = [
                candidate
                for candidate in candidate_rows
                if isinstance(candidate["url"], str)
                and canonical_public_github_activity_url(candidate["url"]) == activity_url
            ]
            if canonical_rows:
                survivor_id = canonical_rows[0]["id"]
                duplicate_ids = tuple(
                    candidate["id"]
                    for candidate in canonical_rows[1:]
                )
                if duplicate_ids:
                    placeholders = ", ".join("?" for _ in duplicate_ids)
                    conn.execute(
                        f"UPDATE evidence_items SET github_activity_id = ? "
                        f"WHERE github_activity_id IN ({placeholders})",
                        (survivor_id, *duplicate_ids),
                    )
                    conn.execute(
                        f"DELETE FROM github_activities WHERE id IN ({placeholders})",
                        duplicate_ids,
                    )
                if {"origin_type", "origin_visibility"} <= columns:
                    conn.execute(
                        """
                        UPDATE github_activities SET
                            source_id = ?, repo = ?, activity_type = ?, url = ?,
                            title = ?, state = ?, author = ?, created_at = ?,
                            merged_at = ?, is_private = ?, state_field = ?,
                            origin_type = ?, origin_visibility = ?, is_current = ?
                        WHERE id = ?
                        """,
                        (*values, survivor_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE github_activities SET
                            source_id = ?, repo = ?, activity_type = ?, url = ?,
                            title = ?, state = ?, author = ?, created_at = ?,
                            merged_at = ?, is_private = ?, state_field = ?
                        WHERE id = ?
                        """,
                        (*values, survivor_id),
                    )
            else:
                if {"origin_type", "origin_visibility"} <= columns:
                    conn.execute(
                        """
                        INSERT INTO github_activities
                            (source_id, repo, activity_type, url, title, state, author,
                             created_at, merged_at, is_private, state_field,
                             origin_type, origin_visibility, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO github_activities
                            (source_id, repo, activity_type, url, title, state, author,
                             created_at, merged_at, is_private, state_field)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
            row = conn.execute(
                """
                SELECT id FROM github_activities
                WHERE repo = ? AND activity_type = ? AND url = ?
                """,
                (repository_name, activity.activity_type, activity_url),
            ).fetchone()
        return int(row["id"])

    def invalidate_github_activity_visibility(self) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE github_activities SET is_private = 1")

    def invalidate_unobserved_github_activities(
        self,
        observed_activities: tuple[tuple[str, str, str], ...],
    ) -> None:
        with self._connection() as conn:
            conn.execute("DROP TABLE IF EXISTS temp.observed_github_activity_keys")
            conn.execute(
                """
                CREATE TEMP TABLE observed_github_activity_keys (
                    repo TEXT COLLATE NOCASE NOT NULL,
                    activity_type TEXT NOT NULL,
                    url TEXT COLLATE NOCASE NOT NULL,
                    PRIMARY KEY (repo, activity_type, url)
                )
                """
            )
            try:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO observed_github_activity_keys
                        (repo, activity_type, url)
                    VALUES (?, ?, ?)
                    """,
                    observed_activities,
                )
                stale_clause = """
                    NOT EXISTS (
                        SELECT 1
                        FROM temp.observed_github_activity_keys AS observed
                        WHERE observed.repo = github_activities.repo
                          AND observed.activity_type = github_activities.activity_type
                          AND observed.url = github_activities.url
                    )
                """
                conn.execute(
                    f"""
                    UPDATE github_activities
                    SET is_private = 1,
                        is_current = 0
                    WHERE {stale_clause}
                    """
                )
                conn.execute(
                    f"""
                    UPDATE evidence_items
                    SET public_safe = 0
                    WHERE github_activity_id IN (
                        SELECT id FROM github_activities
                        WHERE {stale_clause}
                    )
                    """
                )
                conn.execute(
                    f"""
                    UPDATE career_claims
                    SET public_safe = 0
                    WHERE id IN (
                        SELECT claim_evidence.claim_id
                        FROM claim_evidence
                        JOIN evidence_items
                          ON evidence_items.id = claim_evidence.evidence_id
                        JOIN github_activities
                          ON github_activities.id = evidence_items.github_activity_id
                        WHERE {stale_clause}
                    )
                    """
                )
            finally:
                conn.execute("DROP TABLE temp.observed_github_activity_keys")

    def invalidate_unconfirmed_github_activity_visibility(
        self,
        confirmed_repositories: tuple[str, ...],
    ) -> None:
        if not confirmed_repositories:
            self.invalidate_github_activity_visibility()
            return
        placeholders = ", ".join("?" for _ in confirmed_repositories)
        with self._connection() as conn:
            conn.execute(
                f"UPDATE github_activities SET is_private = 1 "
                f"WHERE lower(repo) NOT IN ({placeholders})",
                confirmed_repositories,
            )

    def invalidate_github_activity_visibility_for_repositories(
        self,
        repositories: tuple[str, ...],
    ) -> None:
        if not repositories:
            return
        placeholders = ", ".join("?" for _ in repositories)
        with self._connection() as conn:
            conn.execute(
                f"UPDATE github_activities SET is_private = 1 "
                f"WHERE lower(repo) IN ({placeholders})",
                repositories,
            )

    def invalidate_github_activity_visibility_for_endpoints(
        self,
        completed_endpoints: tuple[tuple[str, str], ...],
    ) -> None:
        if not completed_endpoints:
            return
        predicates = " OR ".join(
            "(lower(repo) = ? AND activity_type = ?)" for _ in completed_endpoints
        )
        parameters = tuple(
            value for endpoint in completed_endpoints for value in endpoint
        )
        with self._connection() as conn:
            conn.execute(
                f"""
                UPDATE github_activities
                SET is_private = 1,
                    is_current = 0
                WHERE {predicates}
                """,
                parameters,
            )
            conn.execute(
                f"""
                UPDATE evidence_items
                SET public_safe = 0
                WHERE github_activity_id IN (
                    SELECT id FROM github_activities WHERE {predicates}
                )
                """,
                parameters,
            )
            conn.execute(
                f"""
                UPDATE career_claims
                SET public_safe = 0
                WHERE id IN (
                    SELECT claim_evidence.claim_id
                    FROM claim_evidence
                    JOIN evidence_items
                      ON evidence_items.id = claim_evidence.evidence_id
                    JOIN github_activities
                      ON github_activities.id = evidence_items.github_activity_id
                    WHERE {predicates}
                )
                """,
                parameters,
            )

    def list_github_activities(self) -> list[GitHubActivity]:
        with self._read_connection() as conn:
            columns = self._table_columns(conn, "github_activities")
            origin_type = "origin_type" if "origin_type" in columns else "'public_github'"
            origin_visibility = (
                "origin_visibility" if "origin_visibility" in columns else "'unknown'"
            )
            rows = conn.execute(
                f"""
                SELECT id, source_id, repo, activity_type, url, title, state, author,
                       created_at, merged_at, is_private, state_field,
                       {origin_type} AS origin_type, {origin_visibility} AS origin_visibility,
                       is_current
                FROM github_activities
                ORDER BY id
                """
            ).fetchall()
        try:
            activities: list[GitHubActivity] = []
            for row in rows:
                raw_url = self._required_text(row, "url")
                activities.append(
                    GitHubActivity(
                        id=self._required_int(row, "id"),
                        source_id=self._optional_int(row, "source_id"),
                        repo=self._required_text(row, "repo"),
                        activity_type=self._required_text(row, "activity_type"),
                        url=canonical_public_github_activity_url(raw_url) or raw_url,
                        title=self._required_text(row, "title"),
                        state=self._required_text(row, "state"),
                        author=self._required_text(row, "author"),
                        created_at=self._required_text(row, "created_at"),
                        merged_at=self._optional_text(row, "merged_at"),
                        is_private=bool(self._required_boolean(row, "is_private")),
                        state_field=self._optional_text(row, "state_field"),
                        origin_type=self._required_text(row, "origin_type"),
                        origin_visibility=self._required_text(row, "origin_visibility"),
                        is_current=bool(self._required_boolean(row, "is_current")),
                    )
                )
            return activities
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_public_evidence_records(
        self, *, include_unsafe: bool = False
    ) -> list[PublicEvidenceRecord]:
        safety_clause = "" if include_unsafe else """
                WHERE projects.public_safe = 1
                  AND career_claims.public_safe = 1
                  AND evidence_items.public_safe = 1
        """
        with self._read_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    projects.id AS project_id,
                    projects.name AS project_name,
                    career_claims.id AS claim_id,
                    career_claims.text AS claim_text,
                    evidence_items.id AS evidence_id,
                    evidence_items.stable_id AS evidence_stable_id,
                    evidence_items.locator AS evidence_locator,
                    evidence_items.origin_type AS evidence_origin_type,
                    evidence_items.origin_visibility AS evidence_origin_visibility,
                    sources.id AS source_id,
                    sources.type AS source_type,
                    sources.uri AS source_uri,
                    sources.display_name AS source_display_name,
                    sources.status AS source_status,
                    sources.origin_type AS source_origin_type,
                    sources.origin_visibility AS source_origin_visibility,
                    github_activities.id AS activity_id,
                    github_activities.repo AS activity_repo,
                    github_activities.activity_type AS activity_type,
                    github_activities.url AS activity_url,
                    github_activities.title AS activity_title,
                    github_activities.state AS activity_state,
                    github_activities.state_field AS activity_state_field,
                    github_activities.author AS activity_author,
                    github_activities.created_at AS activity_created_at,
                    github_activities.is_private AS activity_is_private,
                    github_activities.is_current AS activity_is_current,
                    github_activities.origin_type AS activity_origin_type,
                    github_activities.origin_visibility AS activity_origin_visibility
                FROM projects
                JOIN career_claims
                  ON career_claims.project_id = projects.id
                JOIN claim_evidence
                  ON claim_evidence.claim_id = career_claims.id
                JOIN evidence_items
                  ON evidence_items.id = claim_evidence.evidence_id
                LEFT JOIN github_activities
                  ON github_activities.id = evidence_items.github_activity_id
                LEFT JOIN sources
                  ON sources.id = COALESCE(
                      evidence_items.source_id,
                      github_activities.source_id
                  )
                {safety_clause}
                ORDER BY projects.id, career_claims.id, evidence_items.id
                """
            ).fetchall()
        try:
            records: list[PublicEvidenceRecord] = []
            for row in rows:
                activity_id = self._optional_int(row, "activity_id")
                activity_is_private = (
                    None
                    if activity_id is None
                    else self._required_boolean(row, "activity_is_private")
                )
                activity_is_current = (
                    None
                    if activity_id is None
                    else bool(self._required_boolean(row, "activity_is_current"))
                )
                records.append(
                    PublicEvidenceRecord(
                        project_id=self._required_int(row, "project_id"),
                        project_name=self._required_text(row, "project_name"),
                        claim_id=self._required_int(row, "claim_id"),
                        claim_text=self._required_text(row, "claim_text"),
                        evidence_id=self._required_int(row, "evidence_id"),
                        evidence_stable_id=self._required_text(row, "evidence_stable_id"),
                        evidence_locator=self._required_text(row, "evidence_locator"),
                        source_id=self._optional_int(row, "source_id"),
                        source_type=self._optional_text(row, "source_type"),
                        source_uri=self._optional_text(row, "source_uri"),
                        source_display_name=self._optional_text(row, "source_display_name"),
                        source_status=self._optional_text(row, "source_status"),
                        activity_id=activity_id,
                        activity_repo=self._optional_text(row, "activity_repo"),
                        activity_type=self._optional_text(row, "activity_type"),
                        activity_url=self._optional_text(row, "activity_url"),
                        activity_title=self._optional_text(row, "activity_title"),
                        activity_state=self._optional_text(row, "activity_state"),
                        activity_state_field=self._optional_text(row, "activity_state_field"),
                        activity_author=self._optional_text(row, "activity_author"),
                        activity_created_at=self._optional_text(row, "activity_created_at"),
                        activity_is_private=activity_is_private,
                        activity_is_current=activity_is_current,
                        evidence_origin_type=self._required_text(row, "evidence_origin_type"),
                        evidence_origin_visibility=self._required_text(
                            row, "evidence_origin_visibility"
                        ),
                        source_origin_type=self._optional_text(row, "source_origin_type"),
                        source_origin_visibility=self._optional_text(
                            row, "source_origin_visibility"
                        ),
                        activity_origin_type=self._optional_text(row, "activity_origin_type"),
                        activity_origin_visibility=self._optional_text(
                            row, "activity_origin_visibility"
                        ),
                    )
                )
            return records
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_evidence_selection_records(self) -> list[PublicEvidenceRecord]:
        return self.list_public_evidence_records(include_unsafe=True)

    def replace_portfolio_projects(
        self,
        projects: tuple[dict[str, object], ...],
        approval_sha256: str,
        review_input_sha256: str,
    ) -> None:
        """Replace only the user-approved semantic project graph atomically."""
        with self._connection() as conn:
            conn.execute("DELETE FROM portfolio_project_evidence")
            conn.execute("DELETE FROM portfolio_projects")
            for project in projects:
                project_id = project.get("id")
                title = project.get("title")
                overview = project.get("overview")
                evidence_ids = project.get("evidence_ids")
                if not all(isinstance(value, str) for value in (project_id, title, overview)):
                    raise RepositoryError("semantic project values are invalid")
                if not isinstance(evidence_ids, (tuple, list)):
                    raise RepositoryError("semantic project evidence is invalid")
                conn.execute(
                    """
                    INSERT INTO portfolio_projects
                        (id, title, overview, status, approval_sha256, review_input_sha256)
                    VALUES (?, ?, ?, 'approved', ?, ?)
                    """,
                    (project_id, title, overview, approval_sha256, review_input_sha256),
                )
                for evidence_id in evidence_ids:
                    if not isinstance(evidence_id, int) or isinstance(evidence_id, bool):
                        raise RepositoryError("semantic project evidence is invalid")
                    conn.execute(
                        """
                        INSERT INTO portfolio_project_evidence
                            (project_id, evidence_id, support_level)
                        VALUES (?, ?, 'direct')
                        """,
                        (project_id, evidence_id),
                    )

    def replace_portfolio_project_decisions(
        self,
        projects: tuple[dict[str, object], ...],
        *,
        candidate_input_sha256: str,
        index_revision: str,
    ) -> None:
        """Upsert current v2 decisions without reinterpreting legacy projects."""
        normalized = self._normalize_portfolio_project_decisions(
            projects,
            candidate_input_sha256=candidate_input_sha256,
            index_revision=index_revision,
        )
        with self._connection() as conn:
            existing_rows = conn.execute(
                """
                SELECT id, decision_status, decision_origin, boundary_fingerprint
                FROM portfolio_projects
                """
            ).fetchall()
            existing = {
                self._required_text(row, "id"): {
                    "decision_status": self._required_text(row, "decision_status"),
                    "decision_origin": self._required_text(row, "decision_origin"),
                    "boundary_fingerprint": self._optional_text(row, "boundary_fingerprint"),
                }
                for row in existing_rows
            }
            manual_exclusions_by_boundary = {
                state["boundary_fingerprint"]
                for state in existing.values()
                if state["decision_status"] == "excluded"
                and state["decision_origin"] == "manual"
                and state["boundary_fingerprint"] is not None
            }

            for project in normalized:
                project_id = project["id"]
                current = existing.get(project_id)
                state = self._resolved_portfolio_project_decision_state(
                    project,
                    current=current,
                    existing=existing,
                    manual_exclusions_by_boundary=manual_exclusions_by_boundary,
                )
                conn.execute(
                    """
                    INSERT INTO portfolio_projects (
                        id, title, overview, status, approval_sha256, review_input_sha256,
                        decision_status, decision_origin, confidence, boundary_fingerprint,
                        candidate_input_sha256, index_revision, decision_updated_at
                    )
                    VALUES (?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        overview = excluded.overview,
                        approval_sha256 = excluded.approval_sha256,
                        review_input_sha256 = excluded.review_input_sha256,
                        decision_status = excluded.decision_status,
                        decision_origin = excluded.decision_origin,
                        confidence = excluded.confidence,
                        boundary_fingerprint = excluded.boundary_fingerprint,
                        candidate_input_sha256 = excluded.candidate_input_sha256,
                        index_revision = excluded.index_revision,
                        decision_updated_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_id,
                        project["title"],
                        project["overview"],
                        project["approval_sha256"],
                        project["review_input_sha256"],
                        state["decision_status"],
                        state["decision_origin"],
                        project["confidence"],
                        project["boundary_fingerprint"],
                        candidate_input_sha256,
                        index_revision,
                    ),
                )
                conn.execute(
                    "DELETE FROM portfolio_project_evidence WHERE project_id = ?",
                    (project_id,),
                )
                for evidence_id in project["evidence_ids"]:
                    conn.execute(
                        """
                        DELETE FROM portfolio_project_evidence
                        WHERE evidence_id = ? AND project_id != ?
                        """,
                        (evidence_id, project_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO portfolio_project_evidence
                            (project_id, evidence_id, support_level)
                        VALUES (?, ?, 'direct')
                        """,
                        (project_id, evidence_id),
                    )

            current_ids = tuple(project["id"] for project in normalized)
            if current_ids:
                placeholders = ", ".join("?" for _ in current_ids)
                conn.execute(
                    f"""
                    UPDATE portfolio_projects
                    SET decision_status = 'inactive',
                        decision_updated_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE decision_origin = 'automatic'
                      AND decision_status != 'inactive'
                      AND id NOT IN ({placeholders})
                    """,
                    current_ids,
                )
            else:
                conn.execute(
                    """
                    UPDATE portfolio_projects
                    SET decision_status = 'inactive',
                        decision_updated_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE decision_origin = 'automatic'
                      AND decision_status != 'inactive'
                    """
                )

    def set_project_decision_state(self, project_id: str, state: str) -> None:
        if not isinstance(project_id, str) or not project_id:
            raise RepositoryError("portfolio project ID is invalid")
        if state not in {"excluded", "included"}:
            raise RepositoryError("portfolio project decision state is invalid")
        decision_status = "excluded" if state == "excluded" else "manually_approved"
        with self._connection() as conn:
            result = conn.execute(
                """
                UPDATE portfolio_projects
                SET decision_status = ?,
                    decision_origin = 'manual',
                    decision_updated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (decision_status, project_id),
            )
            if result.rowcount != 1:
                raise RepositoryError("portfolio project is unknown")

    @classmethod
    def _normalize_portfolio_project_decisions(
        cls,
        projects: tuple[dict[str, object], ...],
        *,
        candidate_input_sha256: str,
        index_revision: str,
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(candidate_input_sha256, str) or not candidate_input_sha256:
            raise RepositoryError("candidate input hash is invalid")
        if not isinstance(index_revision, str) or not index_revision:
            raise RepositoryError("semantic index revision is invalid")

        normalized: list[dict[str, object]] = []
        project_ids: set[str] = set()
        evidence_project_ids: dict[int, str] = {}
        for project in projects:
            if not isinstance(project, dict):
                raise RepositoryError("portfolio project decision is invalid")
            values = {
                field: project.get(field)
                for field in (
                    "id",
                    "title",
                    "overview",
                    "approval_sha256",
                    "review_input_sha256",
                    "decision_status",
                    "decision_origin",
                    "boundary_fingerprint",
                )
            }
            if not all(isinstance(value, str) and value for value in values.values()):
                raise RepositoryError("portfolio project decision is invalid")
            project_id = values["id"]
            if project_id in project_ids:
                raise RepositoryError("portfolio project decisions must be unique")
            project_ids.add(project_id)

            decision_status = values["decision_status"]
            decision_origin = values["decision_origin"]
            if decision_status not in _PORTFOLIO_PROJECT_DECISION_STATUSES:
                raise RepositoryError("portfolio project decision status is invalid")
            if decision_origin not in _PORTFOLIO_PROJECT_DECISION_ORIGINS:
                raise RepositoryError("portfolio project decision origin is invalid")
            confidence = project.get("confidence")
            if confidence is not None and confidence not in _PORTFOLIO_PROJECT_CONFIDENCE_LEVELS:
                raise RepositoryError("portfolio project confidence is invalid")
            evidence_ids = project.get("evidence_ids")
            if (
                not isinstance(evidence_ids, (tuple, list))
                or any(
                    not isinstance(evidence_id, int)
                    or isinstance(evidence_id, bool)
                    or evidence_id <= 0
                    for evidence_id in evidence_ids
                )
                or len(set(evidence_ids)) != len(evidence_ids)
            ):
                raise RepositoryError("portfolio project evidence is invalid")
            for evidence_id in evidence_ids:
                if evidence_id in evidence_project_ids:
                    raise RepositoryError("portfolio project evidence must be unique")
                evidence_project_ids[evidence_id] = project_id
            lineage_project_ids = project.get("lineage_project_ids", ())
            if (
                not isinstance(lineage_project_ids, (tuple, list))
                or any(not isinstance(value, str) or not value for value in lineage_project_ids)
                or len(set(lineage_project_ids)) != len(lineage_project_ids)
            ):
                raise RepositoryError("portfolio project lineage is invalid")
            normalized.append(
                {
                    **values,
                    "confidence": confidence,
                    "evidence_ids": tuple(evidence_ids),
                    "lineage_project_ids": tuple(lineage_project_ids),
                }
            )
        return tuple(normalized)

    @staticmethod
    def _resolved_portfolio_project_decision_state(
        project: dict[str, object],
        *,
        current: dict[str, str | None] | None,
        existing: dict[str, dict[str, str | None]],
        manual_exclusions_by_boundary: set[str | None],
    ) -> dict[str, str]:
        incoming_status = str(project["decision_status"])
        incoming_origin = str(project["decision_origin"])
        boundary_fingerprint = str(project["boundary_fingerprint"])
        lineage_project_ids = project["lineage_project_ids"]
        if not isinstance(lineage_project_ids, tuple):
            raise RepositoryError("portfolio project lineage is invalid")
        lineage = [existing.get(project_id) for project_id in lineage_project_ids]
        states = [state for state in (current, *lineage) if state is not None]
        boundary_changed = any(
            state is not None
            and state["boundary_fingerprint"] != boundary_fingerprint
            for state in states
        )
        if boundary_changed:
            return {"decision_status": "review_required", "decision_origin": "automatic"}
        if (
            current is not None
            and current["decision_origin"] == "manual"
            and current["decision_status"] in {"excluded", "manually_approved"}
        ):
            return {
                "decision_status": str(current["decision_status"]),
                "decision_origin": "manual",
            }
        if boundary_fingerprint in manual_exclusions_by_boundary:
            return {"decision_status": "excluded", "decision_origin": "manual"}
        if current is not None and current["decision_status"] == "review_required":
            return {"decision_status": "review_required", "decision_origin": "automatic"}
        if lineage_project_ids:
            return {"decision_status": "review_required", "decision_origin": "automatic"}
        return {"decision_status": incoming_status, "decision_origin": incoming_origin}

    def list_portfolio_projects(self, *, active_only: bool = True) -> list[dict[str, object]]:
        active_clause = ""
        parameters: tuple[str, ...] = ()
        if active_only:
            placeholders = ", ".join("?" for _ in _ACTIVE_PORTFOLIO_PROJECT_DECISION_STATUSES)
            active_clause = f"WHERE decision_status IN ({placeholders})"
            parameters = _ACTIVE_PORTFOLIO_PROJECT_DECISION_STATUSES
        with self._read_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, title, overview, status, approval_sha256,
                       review_input_sha256, decision_status, decision_origin,
                       confidence, boundary_fingerprint, candidate_input_sha256,
                       index_revision, decision_updated_at, created_at, updated_at
                FROM portfolio_projects
                {active_clause}
                ORDER BY id
                """,
                parameters,
            ).fetchall()
            links = conn.execute(
                """
                SELECT project_id, evidence_id, support_level
                FROM portfolio_project_evidence
                ORDER BY project_id, evidence_id
                """
            ).fetchall()
        grouped: dict[str, list[dict[str, object]]] = {}
        for link in links:
            project_id = self._required_text(link, "project_id")
            grouped.setdefault(project_id, []).append(
                {
                    "evidence_id": self._required_int(link, "evidence_id"),
                    "support_level": self._required_text(link, "support_level"),
                }
            )
        try:
            return [
                {
                    "id": self._required_text(row, "id"),
                    "title": self._required_text(row, "title"),
                    "overview": self._required_text(row, "overview"),
                    "status": self._required_text(row, "status"),
                    "approval_sha256": self._required_text(row, "approval_sha256"),
                    "review_input_sha256": self._required_text(row, "review_input_sha256"),
                    "decision_status": self._required_text(row, "decision_status"),
                    "decision_origin": self._required_text(row, "decision_origin"),
                    "confidence": self._optional_text(row, "confidence"),
                    "boundary_fingerprint": self._optional_text(row, "boundary_fingerprint"),
                    "candidate_input_sha256": self._optional_text(row, "candidate_input_sha256"),
                    "index_revision": self._optional_text(row, "index_revision"),
                    "decision_updated_at": self._optional_text(row, "decision_updated_at"),
                    "evidence": grouped.get(self._required_text(row, "id"), []),
                }
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def upsert_evidence_item(
        self,
        *,
        source_id: int | None,
        snapshot_id: int | None,
        github_activity_id: int | None,
        locator: str,
        stable_id: str,
        content_hash: str | None,
        public_safe: bool,
        origin_type: str | None = None,
        origin_visibility: str | None = None,
    ) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "evidence_items")
            if github_activity_id is not None and (
                origin_type is None or origin_visibility is None
            ):
                activity_row = conn.execute(
                    "SELECT is_private FROM github_activities WHERE id = ?",
                    (github_activity_id,),
                ).fetchone()
                is_private = activity_row is not None and activity_row["is_private"] == 1
                origin_type = origin_type or (
                    "private_github" if is_private else "public_github"
                )
                origin_visibility = origin_visibility or (
                    "private" if is_private else "public"
                )
            origin_type = origin_type or "local"
            origin_visibility = origin_visibility or "private"
            base_values = (
                source_id,
                snapshot_id,
                github_activity_id,
                locator,
                stable_id,
                content_hash,
                int(public_safe),
            )
            if {"origin_type", "origin_visibility"} <= columns:
                values = (*base_values, origin_type, origin_visibility)
            else:
                values = base_values
            if {"kind", "summary", "confidence", "origin_type", "origin_visibility"} <= columns:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id,
                         content_hash, public_safe, origin_type, origin_visibility,
                         kind, summary, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'evidence', ?, 'medium')
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe,
                        origin_type = excluded.origin_type,
                        origin_visibility = excluded.origin_visibility
                    """,
                    (*values, locator),
                )
            elif {"kind", "summary", "confidence"} <= columns:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id,
                         content_hash, public_safe, kind, summary, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'evidence', ?, 'medium')
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe
                    """,
                    (*base_values[:5], base_values[5], base_values[6], locator),
                )
            elif {"origin_type", "origin_visibility"} <= columns:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id,
                         content_hash, public_safe, origin_type, origin_visibility)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe,
                        origin_type = excluded.origin_type,
                        origin_visibility = excluded.origin_visibility
                    """,
                    values,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id, content_hash, public_safe)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe
                    """,
                    base_values,
                )
            row = conn.execute(
                "SELECT id FROM evidence_items WHERE stable_id = ?", (stable_id,)
            ).fetchone()
        return self._required_int(row, "id")

    def upsert_project(self, name: str, public_safe: bool) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "projects")
            row = conn.execute(
                "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1", (name,)
            ).fetchone()
            if row is not None:
                project_id = self._required_int(row, "id")
                conn.execute(
                    "UPDATE projects SET public_safe = ? WHERE id = ?",
                    (int(public_safe), project_id),
                )
                return project_id
            if {"summary", "status", "visibility"} <= columns:
                cursor = conn.execute(
                    """
                    INSERT INTO projects
                        (name, summary, status, visibility, public_safe)
                    VALUES (?, '', 'draft', 'public', ?)
                    """,
                    (name, int(public_safe)),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO projects (name, public_safe) VALUES (?, ?)",
                    (name, int(public_safe)),
                )
            return int(cursor.lastrowid)

    def upsert_career_claim(self, project_id: int, text: str, public_safe: bool) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "career_claims")
            row = conn.execute(
                """
                SELECT id FROM career_claims
                WHERE project_id = ? AND text = ? AND public_safe = ?
                ORDER BY id LIMIT 1
                """,
                (project_id, text, int(public_safe)),
            ).fetchone()
            if row is not None:
                return self._required_int(row, "id")
            if {"claim_type", "confidence"} <= columns:
                cursor = conn.execute(
                    """
                    INSERT INTO career_claims
                        (project_id, claim_type, text, confidence, public_safe)
                    VALUES (?, 'project_evidence', ?, 'medium', ?)
                    """,
                    (project_id, text, int(public_safe)),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO career_claims (project_id, text, public_safe) VALUES (?, ?, ?)",
                    (project_id, text, int(public_safe)),
                )
        return int(cursor.lastrowid)

    def reconcile_github_artifact_safety(self) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE projects SET public_safe = 0 "
                "WHERE public_safe = 1 AND name LIKE 'github:%'"
            )
            conn.execute(
                "UPDATE evidence_items SET public_safe = 0 "
                "WHERE github_activity_id IS NOT NULL"
            )
            conn.execute(
                """
                UPDATE career_claims SET public_safe = 0
                WHERE public_safe = 1 AND id IN (
                    SELECT claim_evidence.claim_id
                    FROM claim_evidence
                    JOIN evidence_items
                      ON evidence_items.id = claim_evidence.evidence_id
                    WHERE evidence_items.github_activity_id IS NOT NULL
                )
                """
            )

    def upsert_github_activity_claim(
        self,
        evidence_id: int,
        project_id: int,
        text: str,
    ) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "career_claims")
            row = conn.execute(
                """
                SELECT career_claims.id
                FROM career_claims
                JOIN claim_evidence ON claim_evidence.claim_id = career_claims.id
                WHERE claim_evidence.evidence_id = ?
                ORDER BY career_claims.id
                LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
            if row is None:
                if {"claim_type", "confidence"} <= columns:
                    cursor = conn.execute(
                        """
                        INSERT INTO career_claims
                            (project_id, claim_type, text, confidence, public_safe)
                        VALUES (?, 'approved_github_activity', ?, 'high', 1)
                        """,
                        (project_id, text),
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO career_claims (project_id, text, public_safe) VALUES (?, ?, 1)",
                        (project_id, text),
                    )
                claim_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO claim_evidence (claim_id, evidence_id, support_level)
                    VALUES (?, ?, 'direct')
                    """,
                    (claim_id, evidence_id),
                )
                return claim_id
            claim_id = self._required_int(row, "id")
            conn.execute(
                """
                UPDATE career_claims
                SET project_id = ?, text = ?, public_safe = 1
                WHERE id = ?
                """,
                (project_id, text, claim_id),
            )
        return claim_id

    def link_claim_evidence(self, claim_id: int, evidence_id: int, support_level: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO claim_evidence (claim_id, evidence_id, support_level)
                VALUES (?, ?, ?)
                """,
                (claim_id, evidence_id, support_level),
            )

    def record_artifact(self, kind: str, version: int, input_manifest: str) -> None:
        with self._connection() as conn:
            columns = self._table_columns(conn, "artifacts")
            if {"type", "path", "source_profile_version"} <= columns:
                conn.execute(
                    """
                    INSERT INTO artifacts
                        (type, path, source_profile_version, kind, version, input_manifest)
                    VALUES (?, '', ?, ?, ?, ?)
                    """,
                    (kind, str(version), kind, version, input_manifest),
                )
            else:
                conn.execute(
                    "INSERT INTO artifacts (kind, version, input_manifest) VALUES (?, ?, ?)",
                    (kind, version, input_manifest),
                )

    def insert_source_snapshot(
        self,
        source_id: int,
        snapshot_path: Path,
        content_hash: str,
        extractor: str,
    ) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO source_snapshots
                    (source_id, snapshot_path, content_hash, extractor)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, str(snapshot_path), content_hash, extractor),
            )
        return int(cursor.lastrowid)

    def update_source_snapshot(
        self,
        snapshot_id: int,
        snapshot_path: Path,
        content_hash: str,
        extractor: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE source_snapshots
                SET snapshot_path = ?, content_hash = ?, extractor = ?, extracted_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(snapshot_path), content_hash, extractor, snapshot_id),
            )

    def delete_source_snapshot(self, snapshot_id: int) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM source_snapshots WHERE id = ?", (snapshot_id,))

    def snapshot_metadata_for_source(self, source_id: int) -> list[tuple[int, Path, str, str]]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, snapshot_path, content_hash, extractor
                FROM source_snapshots
                WHERE source_id = ?
                ORDER BY id
                """,
                (source_id,),
            ).fetchall()
        try:
            return [
                (
                    self._required_int(row, "id"),
                    Path(self._required_text(row, "snapshot_path")),
                    self._required_text(row, "content_hash"),
                    self._required_text(row, "extractor"),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def latest_snapshot_metadata_by_source_id(self) -> dict[int, tuple[int, Path, str, str]]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, source_id, snapshot_path, content_hash, extractor
                FROM source_snapshots
                WHERE id IN (
                    SELECT MAX(id)
                    FROM source_snapshots
                    GROUP BY source_id
                )
                """
            ).fetchall()
        try:
            return {
                self._required_int(row, "source_id"): (
                    self._required_int(row, "id"),
                    Path(self._required_text(row, "snapshot_path")),
                    self._required_text(row, "content_hash"),
                    self._required_text(row, "extractor"),
                )
                for row in rows
            }
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_sources(self, status: SourceStatus | None = None) -> list[Source]:
        with self._read_connection() as conn:
            columns = self._table_columns(conn, "sources")
            origin_type = "origin_type" if "origin_type" in columns else "'local'"
            origin_visibility = (
                "origin_visibility" if "origin_visibility" in columns else "'unknown'"
            )
            sql = (
                "SELECT id, type, uri, display_name, owner, status, "
                f"{origin_type} AS origin_type, {origin_visibility} AS origin_visibility "
                "FROM sources"
            )
            params: tuple[str, ...] = ()
            if status is not None:
                sql += " WHERE status = ?"
                params = (status.value,)
            sql += " ORDER BY id"
            rows = conn.execute(sql, params).fetchall()

        try:
            return [
                Source(
                    id=self._required_int(row, "id"),
                    type=SourceType(self._required_text(row, "type")),
                    uri=self._required_text(row, "uri"),
                    display_name=self._required_text(row, "display_name"),
                    owner=self._optional_text(row, "owner"),
                    status=SourceStatus(self._required_text(row, "status")),
                    origin_type=self._required_text(row, "origin_type"),
                    origin_visibility=self._required_text(row, "origin_visibility"),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    @staticmethod
    def _required_int(row: sqlite3.Row, name: str) -> int:
        value = row[name]
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")
        return value

    @staticmethod
    def _required_text(row: sqlite3.Row, name: str) -> str:
        value = row[name]
        if not isinstance(value, str):
            raise TypeError(f"{name} must be text")
        return value

    @classmethod
    def _required_text_list(cls, row: sqlite3.Row, name: str) -> list[str]:
        value = json.loads(cls._required_text(row, name))
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TypeError(f"{name} must be a text list")
        return value

    @classmethod
    def _required_int_list(cls, row: sqlite3.Row, name: str) -> list[int]:
        value = json.loads(cls._required_text(row, name))
        if not isinstance(value, list) or not all(type(item) is int for item in value):
            raise TypeError(f"{name} must be an integer list")
        return value

    @staticmethod
    def _optional_int(row: sqlite3.Row, name: str) -> int | None:
        value = row[name]
        if value is not None and type(value) is not int:
            raise TypeError(f"{name} must be an integer or null")
        return value

    @staticmethod
    def _required_boolean(row: sqlite3.Row, name: str) -> bool:
        value = row[name]
        if value not in (0, 1) or type(value) is not int:
            raise TypeError(f"{name} must be a boolean integer")
        return bool(value)

    @staticmethod
    def _optional_text(row: sqlite3.Row, name: str) -> str | None:
        value = row[name]
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be text or null")
        return value

    def update_source_status(self, source_id: int, status: SourceStatus) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (status.value, source_id),
            )
