from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import Iterator
from uuid import uuid4

from portfolio_maker.domain.semantic_frontier import (
    BaselineCoverage,
    PackRecordRef,
    SemanticDeferredAuditRow,
)
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    open_managed_directory,
)


_ROOT_DOMAIN = b"portfolio-maker-semantic-catalog-v1\0"
_AUTHORITATIVE_TABLES = (
    ("nodes", ("node_id",)),
    ("children", ("parent_node_id", "child_node_id")),
    ("local_baselines", ("node_id",)),
    ("payload_refs", ("payload_ref_id",)),
    ("content_identities", ("node_id",)),
    ("evidence_links", ("node_id", "evidence_id", "relation")),
    ("frontier_features", ("node_id",)),
    ("frontier_order", ("node_id",)),
    ("frontier_deferred", ("node_id",)),
)
_EXPECTED_TABLES = {name for name, _ in _AUTHORITATIVE_TABLES}
_EXPECTED_INDEXES = {
    "nodes_parent_order",
    "nodes_source_order",
    "children_traversal_order",
    "local_baselines_coverage",
    "frontier_ready_order",
    "frontier_order_page",
    "frontier_deferred_audit_order",
}
_EXPECTED_COLUMNS = {
    "nodes": ("node_id", "parent_node_id", "source_id", "node_kind", "display_name", "relative_hierarchy", "structural_ref_json"),
    "children": ("parent_node_id", "child_node_id", "traversal_order", "structural_payload_ref_id"),
    "local_baselines": ("node_id", "coverage", "roles_json", "topics_json", "inspected_bytes", "sampled_ranges_json", "sample_sha256", "full_content_sha256", "analyzer_version", "payload_ref_json"),
    "payload_refs": ("payload_ref_id", "node_id", "pack_kind", "byte_offset", "byte_length", "payload_sha256"),
    "content_identities": ("node_id", "metadata_identity_json", "sample_identity_sha256", "full_content_sha256"),
    "evidence_links": ("node_id", "evidence_id", "relation"),
    "frontier_features": ("node_id", "feature_version", "project_like", "ambiguous", "independent_child", "evidence_linked", "uncertainty", "top_level_lane", "feature_digest"),
    "frontier_order": ("node_id", "fair_round", "top_level_lane", "project_like", "ambiguous", "independent_child", "evidence_linked", "uncertainty"),
    "frontier_deferred": ("node_id", "reason", "node_digest", "cursor_json", "cursor_order", "source_id", "coverage_stratum", "relevance_rank", "selector_version", "selector_feature_digest"),
}
_EXPECTED_INDEX_LAYOUT = {
    "nodes_parent_order": (("parent_node_id", 0), ("node_id", 0)),
    "nodes_source_order": (("source_id", 0), ("node_id", 0)),
    "children_traversal_order": (("parent_node_id", 0), ("traversal_order", 0)),
    "local_baselines_coverage": (("coverage", 0), ("node_id", 0)),
    "frontier_ready_order": (("top_level_lane", 0), ("project_like", 1), ("ambiguous", 1), ("node_id", 0)),
    "frontier_order_page": (("fair_round", 0), ("top_level_lane", 0), ("project_like", 1), ("ambiguous", 1), ("independent_child", 1), ("evidence_linked", 1), ("uncertainty", 1), ("node_id", 0)),
    "frontier_deferred_audit_order": (("relevance_rank", 0), ("coverage_stratum", 0), ("source_id", 0), ("cursor_order", 0), ("node_id", 0)),
}
_EXPECTED_UNIQUE_INDEXES = {"children_traversal_order", "frontier_order_page"}


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogSeal:
    byte_length: int
    file_sha256: str
    logical_root: str
    node_count: int


@dataclass(frozen=True)
class NodeRecord:
    node_id: str
    parent_node_id: str | None
    node_kind: str
    relative_hierarchy: str
    source_id: str
    structural_ref: PackRecordRef | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class BaselineRecord:
    node_id: str
    coverage: BaselineCoverage
    roles: tuple[str, ...]
    topics: tuple[str, ...]
    inspected_bytes: int
    sampled_ranges: tuple[tuple[int, int], ...] | None
    sample_sha256: str | None = None
    full_content_sha256: str | None = None
    analyzer_version: str = "local-baseline-v1"
    payload_ref: PackRecordRef | None = None


@dataclass(frozen=True)
class PayloadRefRecord:
    payload_ref_id: str
    node_id: str
    pack_kind: str
    reference: PackRecordRef


@dataclass(frozen=True)
class ContentIdentityRecord:
    node_id: str
    metadata_identity: object
    sample_identity_sha256: str | None = None
    full_content_sha256: str | None = None


@dataclass(frozen=True)
class EvidenceLinkRecord:
    node_id: str
    evidence_id: str
    relation: str


@dataclass(frozen=True)
class FrontierFeatureRecord:
    node_id: str
    feature_version: str
    project_like: bool
    ambiguous: bool
    independent_child: bool
    evidence_linked: bool
    uncertainty: bool
    top_level_lane: str
    feature_digest: str


@dataclass(frozen=True)
class FrontierDeferredRecord:
    node_id: str
    reason: str
    node_digest: str
    cursor_json: str
    cursor_order: int
    source_id: str
    coverage_stratum: str
    relevance_rank: int
    selector_version: str
    selector_feature_digest: str


@dataclass(frozen=True)
class FrontierOrderRow:
    node_id: str
    fair_round: int
    top_level_lane: str
    project_like: bool
    ambiguous: bool
    independent_child: bool
    evidence_linked: bool
    uncertainty: bool

    @property
    def cursor(self) -> tuple[int, str]:
        return (self.fair_round, self.top_level_lane)


@dataclass(frozen=True)
class DeferredSummary:
    count: int
    root: str
    cursor_root: str


def canonical_json_text(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CatalogError("catalog value cannot be canonically encoded") from error


class SemanticCatalogWriter:
    """One-shot writer for an immutable, private semantic catalog."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        _validate_filename(self._path.name)
        ensure_managed_directory(self._path.parent)
        self._directory_descriptor = open_managed_directory(self._path.parent, create=False)
        self._temporary_name = f".{self._path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        self._temporary_path = self._path.with_name(self._temporary_name)
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._sealed = False
        self._seal: CatalogSeal | None = None
        try:
            _require_missing_regular_target(self._directory_descriptor, self._path.name)
            descriptor = os.open(
                self._temporary_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._directory_descriptor,
            )
            os.close(descriptor)
            self._connection = sqlite3.connect(self._temporary_path)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._create_schema()
        except Exception:
            self._abort()
            raise

    def __enter__(self) -> SemanticCatalogWriter:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        if exc_type is None:
            self.close()
        else:
            self._abort()

    @property
    def seal_value(self) -> CatalogSeal | None:
        return self._seal

    def add_node(self, record: NodeRecord) -> None:
        connection = self._require_writable()
        _require_text(record.node_id, "node_id")
        _require_text(record.node_kind, "node_kind")
        _require_text(record.source_id, "source_id")
        if record.parent_node_id is not None:
            _require_text(record.parent_node_id, "parent_node_id")
        display_name = record.display_name
        if display_name is None:
            display_name = record.relative_hierarchy.rsplit("/", 1)[-1] or record.node_id
        _require_text(display_name, "display_name")
        structural_ref_json = _reference_json(record.structural_ref)
        try:
            connection.execute(
                """
                INSERT INTO nodes(
                    node_id, parent_node_id, source_id, node_kind, display_name,
                    relative_hierarchy, structural_ref_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.node_id,
                    record.parent_node_id,
                    record.source_id,
                    record.node_kind,
                    display_name,
                    record.relative_hierarchy,
                    structural_ref_json,
                ),
            )
            if record.parent_node_id is not None:
                next_order = connection.execute(
                    "SELECT COALESCE(MAX(traversal_order), -1) + 1 FROM children WHERE parent_node_id = ?",
                    (record.parent_node_id,),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO children(parent_node_id, child_node_id, traversal_order, structural_payload_ref_id)
                    VALUES (?, ?, ?, NULL)
                    """,
                    (record.parent_node_id, record.node_id, next_order),
                )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog node topology is invalid") from error

    def add_baseline(self, record: BaselineRecord) -> None:
        connection = self._require_writable()
        if record.inspected_bytes < 0:
            raise CatalogError("baseline inspected bytes must be non-negative")
        sampled_ranges = record.sampled_ranges or ()
        for start, end in sampled_ranges:
            if start < 0 or end < start:
                raise CatalogError("baseline sampled range is invalid")
        _validate_digest(record.sample_sha256, "sample sha256")
        _validate_digest(record.full_content_sha256, "full content sha256")
        try:
            connection.execute(
                """
                INSERT INTO local_baselines(
                    node_id, coverage, roles_json, topics_json, inspected_bytes,
                    sampled_ranges_json, sample_sha256, full_content_sha256,
                    analyzer_version, payload_ref_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.node_id,
                    record.coverage.value,
                    canonical_json_text(record.roles),
                    canonical_json_text(record.topics),
                    record.inspected_bytes,
                    canonical_json_text(sampled_ranges),
                    record.sample_sha256,
                    record.full_content_sha256,
                    record.analyzer_version,
                    _reference_json(record.payload_ref),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog baseline is invalid") from error

    def add_payload_ref(self, record: PayloadRefRecord) -> None:
        connection = self._require_writable()
        _validate_reference(record.reference)
        try:
            connection.execute(
                """
                INSERT INTO payload_refs(
                    payload_ref_id, node_id, pack_kind, byte_offset, byte_length, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.payload_ref_id,
                    record.node_id,
                    record.pack_kind,
                    record.reference.offset,
                    record.reference.length,
                    record.reference.sha256,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog payload reference is invalid") from error

    def add_content_identity(self, record: ContentIdentityRecord) -> None:
        connection = self._require_writable()
        _validate_digest(record.sample_identity_sha256, "sample identity sha256")
        _validate_digest(record.full_content_sha256, "full content sha256")
        try:
            connection.execute(
                """
                INSERT INTO content_identities(
                    node_id, metadata_identity_json, sample_identity_sha256, full_content_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    record.node_id,
                    canonical_json_text(record.metadata_identity),
                    record.sample_identity_sha256,
                    record.full_content_sha256,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog content identity is invalid") from error

    def add_evidence_link(self, record: EvidenceLinkRecord) -> None:
        connection = self._require_writable()
        try:
            connection.execute(
                "INSERT INTO evidence_links(node_id, evidence_id, relation) VALUES (?, ?, ?)",
                (record.node_id, record.evidence_id, record.relation),
            )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog evidence link is invalid") from error

    def add_frontier_feature(self, record: FrontierFeatureRecord) -> None:
        connection = self._require_writable()
        _validate_digest(record.feature_digest, "frontier feature digest")
        try:
            connection.execute(
                """
                INSERT INTO frontier_features(
                    node_id, feature_version, project_like, ambiguous, independent_child,
                    evidence_linked, uncertainty, top_level_lane, feature_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.node_id,
                    record.feature_version,
                    int(record.project_like),
                    int(record.ambiguous),
                    int(record.independent_child),
                    int(record.evidence_linked),
                    int(record.uncertainty),
                    record.top_level_lane,
                    record.feature_digest,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog frontier feature is invalid") from error

    def add_frontier_deferred(self, record: FrontierDeferredRecord) -> None:
        connection = self._require_writable()
        _validate_digest(record.node_digest, "frontier deferred node digest")
        _validate_digest(record.selector_feature_digest, "frontier deferred feature digest")
        if record.cursor_order < 0 or record.relevance_rank < 0:
            raise CatalogError("frontier deferred ordering is invalid")
        _canonical_json_from_text(record.cursor_json)
        try:
            connection.execute(
                """
                INSERT INTO frontier_deferred(
                    node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                    coverage_stratum, relevance_rank, selector_version, selector_feature_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.node_id,
                    record.reason,
                    record.node_digest,
                    record.cursor_json,
                    record.cursor_order,
                    record.source_id,
                    record.coverage_stratum,
                    record.relevance_rank,
                    record.selector_version,
                    record.selector_feature_digest,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CatalogError("catalog frontier deferred row is invalid") from error

    def seal(self) -> CatalogSeal:
        if self._seal is not None:
            return self._seal
        connection = self._require_writable()
        try:
            self._validate_before_seal(connection)
            connection.execute("PRAGMA temp_store = FILE")
            connection.execute("DELETE FROM frontier_order")
            connection.execute(
                """
                INSERT INTO frontier_order(
                    node_id, fair_round, top_level_lane, project_like, ambiguous,
                    independent_child, evidence_linked, uncertainty
                )
                SELECT node_id,
                    row_number() OVER (
                        PARTITION BY top_level_lane
                        ORDER BY project_like DESC, ambiguous DESC, independent_child DESC,
                            evidence_linked DESC, uncertainty DESC, node_id
                    ) - 1,
                    top_level_lane, project_like, ambiguous, independent_child,
                    evidence_linked, uncertainty
                FROM frontier_features
                """
            )
            connection.commit()
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise CatalogError("catalog WAL checkpoint failed")
            connection.execute("VACUUM")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise CatalogError("catalog integrity check failed")
            connection.commit()
            _verify_schema(connection)
            logical_root = _logical_root(connection)
            node_count = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            connection.close()
            self._connection = None
            _reject_sidecars(self._path.parent, self._temporary_path.name)
            _fsync_regular_file(self._temporary_path)
            _require_missing_regular_target(self._directory_descriptor, self._path.name)
            os.replace(
                self._temporary_name,
                self._path.name,
                src_dir_fd=self._directory_descriptor,
                dst_dir_fd=self._directory_descriptor,
            )
            os.fsync(self._directory_descriptor)
            self._temporary_name = ""
            self._seal = CatalogSeal(
                byte_length=self._path.stat().st_size,
                file_sha256=_file_sha256(self._path),
                logical_root=logical_root,
                node_count=node_count,
            )
            self._sealed = True
            return self._seal
        except CatalogError:
            self._abort()
            raise
        except (OSError, sqlite3.Error) as error:
            self._abort()
            raise CatalogError("catalog sealing failed") from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            if not self._sealed:
                self.seal()
        finally:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._directory_descriptor is not None:
                os.close(self._directory_descriptor)
                self._directory_descriptor = None
            self._closed = True

    def _abort(self) -> None:
        if self._closed:
            return
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._temporary_name and self._directory_descriptor is not None:
                try:
                    os.unlink(self._temporary_name, dir_fd=self._directory_descriptor)
                except FileNotFoundError:
                    pass
                else:
                    os.fsync(self._directory_descriptor)
        finally:
            if self._directory_descriptor is not None:
                os.close(self._directory_descriptor)
                self._directory_descriptor = None
            self._closed = True

    def _require_writable(self) -> sqlite3.Connection:
        if self._sealed:
            raise CatalogError("semantic catalog is sealed")
        if self._closed or self._connection is None:
            raise CatalogError("semantic catalog is closed")
        return self._connection

    def _create_schema(self) -> None:
        if self._connection is None:
            raise CatalogError("semantic catalog is closed")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    @staticmethod
    def _validate_before_seal(connection: sqlite3.Connection) -> None:
        invalid = connection.execute(
            """
            SELECT deferred.node_id
            FROM frontier_deferred AS deferred
            LEFT JOIN nodes AS node ON node.node_id = deferred.node_id
            LEFT JOIN local_baselines AS baseline ON baseline.node_id = deferred.node_id
            LEFT JOIN frontier_features AS feature ON feature.node_id = deferred.node_id
            WHERE node.node_id IS NULL
               OR node.source_id != deferred.source_id
               OR baseline.node_id IS NULL
               OR baseline.coverage != deferred.coverage_stratum
               OR feature.node_id IS NULL
               OR feature.feature_digest != deferred.selector_feature_digest
            LIMIT 1
            """
        ).fetchone()
        if invalid is not None:
            raise CatalogError("frontier deferred feature digest or selector fields are invalid")


class SemanticCatalogReader:
    """Read-only query surface over a verified, sealed catalog."""

    def __init__(self, path: Path, seal: CatalogSeal) -> None:
        self._path = Path(path)
        self._seal = seal
        self._connection: sqlite3.Connection | None = None
        _validate_regular_path(self._path)
        _reject_sidecars(self._path.parent, self._path.name)
        try:
            self._connection = sqlite3.connect(
                f"{self._path.absolute().as_uri()}?mode=ro", uri=True
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA query_only = ON")
            if self._connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise CatalogError("catalog read-only query mode is unavailable")
            _verify_schema(self._connection)
            if _logical_root(self._connection) != seal.logical_root:
                raise CatalogError("catalog logical root does not match seal")
            if self._connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] != seal.node_count:
                raise CatalogError("catalog node count does not match seal")
            if self._path.stat().st_size != seal.byte_length:
                raise CatalogError("catalog byte length does not match seal")
            if _file_sha256(self._path) != seal.file_sha256:
                raise CatalogError("catalog file sha256 does not match seal")
            _reject_sidecars(self._path.parent, self._path.name)
        except CatalogError:
            self.close()
            raise
        except (OSError, sqlite3.Error) as error:
            self.close()
            raise CatalogError("catalog verification failed") from error

    def __enter__(self) -> SemanticCatalogReader:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CatalogError("semantic catalog reader is closed")
        return self._connection

    @property
    def query_only(self) -> bool:
        return bool(self.connection.execute("PRAGMA query_only").fetchone()[0])

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def node_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])

    def node(self, node_id: str) -> NodeRecord | None:
        row = self.connection.execute(
            """
            SELECT node_id, parent_node_id, node_kind, relative_hierarchy, source_id,
                   structural_ref_json, display_name
            FROM nodes WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        return None if row is None else _node_from_row(row)

    def nodes_page(self, after_node_id: str | None, limit: int) -> list[NodeRecord]:
        _page_limit(limit)
        if after_node_id is None:
            rows = self.connection.execute(
                """
                SELECT node_id, parent_node_id, node_kind, relative_hierarchy, source_id,
                       structural_ref_json, display_name
                FROM nodes ORDER BY node_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT node_id, parent_node_id, node_kind, relative_hierarchy, source_id,
                       structural_ref_json, display_name
                FROM nodes WHERE node_id > ? ORDER BY node_id LIMIT ?
                """,
                (after_node_id, limit),
            ).fetchall()
        return [_node_from_row(row) for row in rows]

    def source_page(
        self, source_id: str, after_node_id: str | None, limit: int
    ) -> list[NodeRecord]:
        _page_limit(limit)
        if after_node_id is None:
            rows = self.connection.execute(
                """
                SELECT node_id, parent_node_id, node_kind, relative_hierarchy, source_id,
                       structural_ref_json, display_name
                FROM nodes WHERE source_id = ? ORDER BY node_id LIMIT ?
                """,
                (source_id, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT node_id, parent_node_id, node_kind, relative_hierarchy, source_id,
                       structural_ref_json, display_name
                FROM nodes WHERE source_id = ? AND node_id > ? ORDER BY node_id LIMIT ?
                """,
                (source_id, after_node_id, limit),
            ).fetchall()
        return [_node_from_row(row) for row in rows]

    def baseline(self, node_id: str) -> BaselineRecord | None:
        row = self.connection.execute(
            """
            SELECT node_id, coverage, roles_json, topics_json, inspected_bytes,
                   sampled_ranges_json, sample_sha256, full_content_sha256,
                   analyzer_version, payload_ref_json
            FROM local_baselines WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        if row is None:
            return None
        roles = _canonical_json_from_text(row["roles_json"])
        topics = _canonical_json_from_text(row["topics_json"])
        ranges = _canonical_json_from_text(row["sampled_ranges_json"])
        if not isinstance(roles, list) or not isinstance(topics, list) or not isinstance(ranges, list):
            raise CatalogError("catalog baseline is invalid")
        return BaselineRecord(
            row["node_id"], BaselineCoverage(row["coverage"]), tuple(roles), tuple(topics),
            row["inspected_bytes"], tuple(tuple(item) for item in ranges),
            row["sample_sha256"], row["full_content_sha256"], row["analyzer_version"],
            _reference_from_json(row["payload_ref_json"]),
        )

    def children_page(
        self, parent_node_id: str, after_node_id: str | None, limit: int
    ) -> list[NodeRecord]:
        _page_limit(limit)
        if after_node_id is None:
            rows = self.connection.execute(
                """
                SELECT node.node_id, node.parent_node_id, node.node_kind, node.relative_hierarchy,
                       node.source_id, node.structural_ref_json, node.display_name
                FROM children JOIN nodes AS node ON node.node_id = children.child_node_id
                WHERE children.parent_node_id = ?
                ORDER BY children.child_node_id
                LIMIT ?
                """,
                (parent_node_id, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT node.node_id, node.parent_node_id, node.node_kind, node.relative_hierarchy,
                       node.source_id, node.structural_ref_json, node.display_name
                FROM children JOIN nodes AS node ON node.node_id = children.child_node_id
                WHERE children.parent_node_id = ? AND children.child_node_id > ?
                ORDER BY children.child_node_id
                LIMIT ?
                """,
                (parent_node_id, after_node_id, limit),
            ).fetchall()
        return [_node_from_row(row) for row in rows]

    def frontier_page(
        self, cursor: tuple[int, str] | None, limit: int
    ) -> list[FrontierOrderRow]:
        _page_limit(limit)
        if cursor is None:
            rows = self.connection.execute(
                _FRONTIER_PAGE_SELECT + _FRONTIER_PAGE_ORDER + " LIMIT ?", (limit,)
            ).fetchall()
        else:
            fair_round, lane = cursor
            rows = self.connection.execute(
                _FRONTIER_PAGE_SELECT
                + " WHERE fair_round > ? OR (fair_round = ? AND top_level_lane > ?)"
                + _FRONTIER_PAGE_ORDER
                + " LIMIT ?",
                (fair_round, fair_round, lane, limit),
            ).fetchall()
        return [
            FrontierOrderRow(
                row["node_id"], row["fair_round"], row["top_level_lane"],
                bool(row["project_like"]), bool(row["ambiguous"]),
                bool(row["independent_child"]), bool(row["evidence_linked"]),
                bool(row["uncertainty"]),
            )
            for row in rows
        ]

    def iter_frontier_deferred_for_audit(
        self, *, selector_version: str, fetch_size: int
    ) -> Iterator[SemanticDeferredAuditRow]:
        _page_limit(fetch_size)
        cursor = self.connection.execute(
            """
            SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                   coverage_stratum, relevance_rank, selector_version, selector_feature_digest
            FROM frontier_deferred
            WHERE selector_version = ?
            ORDER BY relevance_rank, coverage_stratum, source_id, cursor_order, node_id
            """,
            (selector_version,),
        )
        while True:
            rows = cursor.fetchmany(fetch_size)
            if not rows:
                return
            for row in rows:
                yield SemanticDeferredAuditRow(*tuple(row))

    def deferred_summary(self, *, selector_version: str) -> DeferredSummary:
        root = bytes(32)
        cursor_root = bytes(32)
        count = 0
        cursor = self.connection.execute(
            """
            SELECT node_id, reason, node_digest, cursor_json, cursor_order, source_id,
                   coverage_stratum, relevance_rank, selector_version, selector_feature_digest
            FROM frontier_deferred
            WHERE selector_version = ?
            ORDER BY relevance_rank, coverage_stratum, source_id, cursor_order, node_id
            """,
            (selector_version,),
        )
        for row in cursor:
            row_json = canonical_json_text(dict(row)).encode("utf-8")
            root = hashlib.sha256(_ROOT_DOMAIN + b"deferred\0" + root + row_json).digest()
            cursor_root = hashlib.sha256(
                _ROOT_DOMAIN + b"deferred-cursor\0" + cursor_root + row["cursor_json"].encode("utf-8")
            ).digest()
            count += 1
        return DeferredSummary(count, root.hex(), cursor_root.hex())


def _node_from_row(row: sqlite3.Row) -> NodeRecord:
    structural_ref = _reference_from_json(row["structural_ref_json"])
    return NodeRecord(
        row["node_id"], row["parent_node_id"], row["node_kind"],
        row["relative_hierarchy"], row["source_id"], structural_ref, row["display_name"],
    )


def _reference_json(reference: PackRecordRef | None) -> str | None:
    if reference is None:
        return None
    _validate_reference(reference)
    return canonical_json_text(
        {"length": reference.length, "offset": reference.offset, "sha256": reference.sha256}
    )


def _reference_from_json(value: str | None) -> PackRecordRef | None:
    if value is None:
        return None
    parsed = _canonical_json_from_text(value)
    if not isinstance(parsed, dict) or set(parsed) != {"length", "offset", "sha256"}:
        raise CatalogError("catalog payload reference is invalid")
    reference = PackRecordRef(parsed["offset"], parsed["length"], parsed["sha256"])
    _validate_reference(reference)
    return reference


def _canonical_json_from_text(value: str) -> object:
    try:
        parsed = json.loads(value, object_pairs_hook=_no_duplicate_keys)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CatalogError("catalog JSON is invalid") from error
    if canonical_json_text(parsed) != value:
        raise CatalogError("catalog JSON must be canonical")
    return parsed


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _validate_reference(reference: PackRecordRef) -> None:
    if reference.offset < 0 or reference.length < 0:
        raise CatalogError("catalog payload reference is invalid")
    _validate_digest(reference.sha256, "catalog payload sha256")


def _validate_digest(value: str | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) != 64:
        raise CatalogError(f"{name} is invalid")
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise CatalogError(f"{name} is invalid") from error
    if raw.hex() != value:
        raise CatalogError(f"{name} is invalid")


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"catalog {name} is invalid")


def _page_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 0 < limit <= 64:
        raise CatalogError("catalog page limit must be between 1 and 64")


def _logical_root(connection: sqlite3.Connection) -> str:
    root = bytes(32)
    for table, primary_key in _AUTHORITATIVE_TABLES:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        order_by = ", ".join(primary_key)
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
        table_name = table.encode("utf-8")
        root = hashlib.sha256(_ROOT_DOMAIN + b"table\0" + root + table_name).digest()
        for row in cursor:
            value = {column: row[index] for index, column in enumerate(columns)}
            encoded = canonical_json_text(value).encode("utf-8")
            root = hashlib.sha256(_ROOT_DOMAIN + b"row\0" + root + encoded).digest()
    return root.hex()


def _verify_schema(connection: sqlite3.Connection) -> None:
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')"
    ).fetchall()
    tables = {row[1] for row in objects if row[0] == "table" and not row[1].startswith("sqlite_")}
    indexes = {row[1] for row in objects if row[0] == "index" and not row[1].startswith("sqlite_autoindex")}
    if tables != _EXPECTED_TABLES or indexes != _EXPECTED_INDEXES:
        raise CatalogError("catalog schema drift detected")
    for table, _ in _AUTHORITATIVE_TABLES:
        columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if columns != _EXPECTED_COLUMNS[table]:
            raise CatalogError("catalog schema drift detected")
    for index_name, expected_layout in _EXPECTED_INDEX_LAYOUT.items():
        index_rows = connection.execute(f"PRAGMA index_xinfo({index_name})").fetchall()
        layout = tuple(
            (row[2], row[3])
            for row in index_rows
            if row[5] == 1
        )
        if layout != expected_layout:
            raise CatalogError("catalog schema drift detected")
        unique = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)
        ).fetchone()
        if unique is None or ("UNIQUE INDEX" in unique[0].upper()) != (index_name in _EXPECTED_UNIQUE_INDEXES):
            raise CatalogError("catalog schema drift detected")


def _file_sha256(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        stat_result = os.fstat(descriptor)
        if not S_ISREG(stat_result.st_mode):
            raise CatalogError("catalog target must be a regular file")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)
    finally:
        os.close(descriptor)


def _fsync_regular_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not S_ISREG(os.fstat(descriptor).st_mode):
            raise CatalogError("catalog target must be a regular file")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_filename(filename: str) -> None:
    if not filename or filename in {".", ".."} or "/" in filename:
        raise CatalogError("catalog filename is invalid")


def _validate_regular_path(path: Path) -> None:
    try:
        stat_result = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as error:
        raise CatalogError("catalog file is missing") from error
    if not S_ISREG(stat_result.st_mode):
        raise CatalogError("catalog target must be a regular file")


def _require_missing_regular_target(directory_descriptor: int | None, filename: str) -> None:
    if directory_descriptor is None:
        raise CatalogError("catalog directory is closed")
    try:
        stat_result = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not S_ISREG(stat_result.st_mode):
        raise CatalogError("catalog target must be a regular file")
    raise CatalogError("catalog target already exists")


def _reject_sidecars(directory: Path, filename: str) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = directory / f"{filename}{suffix}"
        try:
            stat_result = os.stat(sidecar, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not S_ISREG(stat_result.st_mode):
            raise CatalogError("catalog sidecar is invalid")
        raise CatalogError("catalog sidecar is present")


_FRONTIER_PAGE_SELECT = """
    SELECT node_id, fair_round, top_level_lane, project_like, ambiguous,
           independent_child, evidence_linked, uncertainty
    FROM frontier_order
"""

_FRONTIER_PAGE_ORDER = """
    ORDER BY fair_round, top_level_lane, project_like DESC, ambiguous DESC,
             independent_child DESC, evidence_linked DESC, uncertainty DESC, node_id
"""


_SCHEMA = """
CREATE TABLE nodes (
  node_id TEXT PRIMARY KEY,
  parent_node_id TEXT,
  source_id TEXT NOT NULL,
  node_kind TEXT NOT NULL,
  display_name TEXT NOT NULL,
  relative_hierarchy TEXT NOT NULL,
  structural_ref_json TEXT,
  FOREIGN KEY(parent_node_id) REFERENCES nodes(node_id)
);
CREATE INDEX nodes_parent_order ON nodes(parent_node_id, node_id);
CREATE INDEX nodes_source_order ON nodes(source_id, node_id);
CREATE TABLE children (
  parent_node_id TEXT NOT NULL REFERENCES nodes(node_id),
  child_node_id TEXT NOT NULL REFERENCES nodes(node_id),
  traversal_order INTEGER NOT NULL,
  structural_payload_ref_id TEXT,
  PRIMARY KEY(parent_node_id, child_node_id)
);
CREATE UNIQUE INDEX children_traversal_order ON children(parent_node_id, traversal_order);
CREATE TABLE local_baselines (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  coverage TEXT NOT NULL,
  roles_json TEXT NOT NULL,
  topics_json TEXT NOT NULL,
  inspected_bytes INTEGER NOT NULL,
  sampled_ranges_json TEXT NOT NULL,
  sample_sha256 TEXT,
  full_content_sha256 TEXT,
  analyzer_version TEXT NOT NULL,
  payload_ref_json TEXT
);
CREATE INDEX local_baselines_coverage ON local_baselines(coverage, node_id);
CREATE TABLE payload_refs (
  payload_ref_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL REFERENCES nodes(node_id),
  pack_kind TEXT NOT NULL,
  byte_offset INTEGER NOT NULL,
  byte_length INTEGER NOT NULL,
  payload_sha256 TEXT NOT NULL
);
CREATE TABLE content_identities (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  metadata_identity_json TEXT NOT NULL,
  sample_identity_sha256 TEXT,
  full_content_sha256 TEXT
);
CREATE TABLE evidence_links (
  node_id TEXT NOT NULL REFERENCES nodes(node_id),
  evidence_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  PRIMARY KEY(node_id, evidence_id, relation)
);
CREATE TABLE frontier_features (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  feature_version TEXT NOT NULL,
  project_like INTEGER NOT NULL,
  ambiguous INTEGER NOT NULL,
  independent_child INTEGER NOT NULL,
  evidence_linked INTEGER NOT NULL,
  uncertainty INTEGER NOT NULL,
  top_level_lane TEXT NOT NULL,
  feature_digest TEXT NOT NULL
);
CREATE INDEX frontier_ready_order ON frontier_features(top_level_lane, project_like DESC, ambiguous DESC, node_id);
CREATE TABLE frontier_order (
  node_id TEXT PRIMARY KEY REFERENCES frontier_features(node_id),
  fair_round INTEGER NOT NULL,
  top_level_lane TEXT NOT NULL,
  project_like INTEGER NOT NULL,
  ambiguous INTEGER NOT NULL,
  independent_child INTEGER NOT NULL,
  evidence_linked INTEGER NOT NULL,
  uncertainty INTEGER NOT NULL
);
CREATE UNIQUE INDEX frontier_order_page ON frontier_order(
  fair_round, top_level_lane, project_like DESC, ambiguous DESC,
  independent_child DESC, evidence_linked DESC, uncertainty DESC, node_id
);
CREATE TABLE frontier_deferred (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  reason TEXT NOT NULL,
  node_digest TEXT NOT NULL,
  cursor_json TEXT NOT NULL,
  cursor_order INTEGER NOT NULL UNIQUE,
  source_id TEXT NOT NULL,
  coverage_stratum TEXT NOT NULL,
  relevance_rank INTEGER NOT NULL,
  selector_version TEXT NOT NULL,
  selector_feature_digest TEXT NOT NULL
);
CREATE INDEX frontier_deferred_audit_order ON frontier_deferred(
  relevance_rank, coverage_stratum, source_id, cursor_order, node_id
);
"""
