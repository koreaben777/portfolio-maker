from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from portfolio_maker.domain.semantic_frontier import (
    BaselineCoverage,
    SemanticDeferredAuditRow,
)
from portfolio_maker.infrastructure.semantic_catalog import (
    BaselineRecord,
    CatalogError,
    FrontierFeatureRecord,
    FrontierDeferredRecord,
    NodeRecord,
    SemanticCatalogReader,
    SemanticCatalogWriter,
    canonical_json_text,
)


def test_catalog_keeps_every_node_and_queries_children_by_keyset_page(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        for index in range(10_000):
            writer.add_node(
                NodeRecord(
                    f"n-{index:05}", "root", "file", f"f-{index}", "source-1"
                )
            )
        writer.add_baseline(
            BaselineRecord("root", BaselineCoverage.COMPLETE, (), (), 0, None)
        )
        seal = writer.seal()

    with SemanticCatalogReader(path, seal) as reader:
        assert reader.node_count() == 10_001
        assert [row.node_id for row in reader.children_page("root", None, 64)][:2] == [
            "n-00000",
            "n-00001",
        ]
        assert len(reader.children_page("root", "n-00063", 64)) == 64


@dataclass(frozen=True)
class _DeferredCatalog:
    reader: SemanticCatalogReader
    selector_version: str
    feature_digest: str
    expected_audit_row: SemanticDeferredAuditRow


@pytest.fixture
def catalog_with_frontier_deferred(tmp_path: Path) -> _DeferredCatalog:
    path = tmp_path / "catalog.sqlite"
    selector_version = "selector-v1"
    feature_digest = "f" * 64
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        writer.add_node(NodeRecord("child", "root", "file", "child.py", "source-1"))
        writer.add_baseline(
            BaselineRecord("child", BaselineCoverage.COMPLETE, (), (), 32, None)
        )
        writer.add_frontier_feature(
            FrontierFeatureRecord(
                "child",
                "features-v1",
                project_like=True,
                ambiguous=False,
                independent_child=True,
                evidence_linked=False,
                uncertainty=False,
                top_level_lane="source-1",
                feature_digest=feature_digest,
            )
        )
        expected = SemanticDeferredAuditRow(
            "child",
            "budget",
            "a" * 64,
            canonical_json_text({"lane": "source-1", "offset": 64}),
            0,
            "source-1",
            "complete",
            0,
            selector_version,
            feature_digest,
        )
        writer.add_frontier_deferred(FrontierDeferredRecord(*expected.__dict__.values()))
        seal = writer.seal()
    return _DeferredCatalog(
        SemanticCatalogReader(path, seal), selector_version, feature_digest, expected
    )


def test_catalog_frontier_deferred_audit_row_round_trips_exact_selector_fields(
    catalog_with_frontier_deferred: _DeferredCatalog,
) -> None:
    rows = list(
        catalog_with_frontier_deferred.reader.iter_frontier_deferred_for_audit(
            selector_version=catalog_with_frontier_deferred.selector_version,
            fetch_size=64,
        )
    )
    assert rows == [catalog_with_frontier_deferred.expected_audit_row]
    assert rows[0].cursor_json == canonical_json_text({"lane": "source-1", "offset": 64})
    assert rows[0].source_id == "source-1"
    assert rows[0].coverage_stratum == "complete"
    assert rows[0].relevance_rank == 0
    assert rows[0].selector_feature_digest == catalog_with_frontier_deferred.feature_digest


def test_catalog_builds_file_backed_fair_rounds_and_pages_by_keyset(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        for lane in ("a", "b"):
            for index in range(3):
                node_id = f"{lane}-{index}"
                writer.add_node(NodeRecord(node_id, "root", "file", node_id, lane))
                writer.add_frontier_feature(
                    FrontierFeatureRecord(
                        node_id,
                        "features-v1",
                        project_like=True,
                        ambiguous=False,
                        independent_child=False,
                        evidence_linked=False,
                        uncertainty=False,
                        top_level_lane=lane,
                        feature_digest=f"{index:064x}",
                    )
                )
        seal = writer.seal()
    with SemanticCatalogReader(path, seal) as reader:
        first = reader.frontier_page(None, 4)
        assert [(row.fair_round, row.top_level_lane) for row in first] == [
            (0, "a"),
            (0, "b"),
            (1, "a"),
            (1, "b"),
        ]
        assert [row.node_id for row in reader.frontier_page(first[-1].cursor, 4)] == [
            "a-2",
            "b-2",
        ]


def test_catalog_rejects_noncanonical_deferred_cursor_and_feature_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with pytest.raises(CatalogError, match="feature digest"):
        with SemanticCatalogWriter(path) as writer:
            writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
            writer.add_frontier_feature(
                FrontierFeatureRecord(
                    "root", "features-v1", False, False, False, False, False, "source-1", "f" * 64
                )
            )
            with pytest.raises(CatalogError, match="canonical"):
                writer.add_frontier_deferred(
                    FrontierDeferredRecord(
                        "root", "budget", "a" * 64, '{"offset":64, "lane":"source-1"}', 0,
                        "source-1", "complete", 0, "selector-v1", "f" * 64,
                    )
                )
            writer.add_frontier_deferred(
                FrontierDeferredRecord(
                    "root", "budget", "a" * 64, canonical_json_text({"lane": "source-1"}), 0,
                    "source-1", "complete", 0, "selector-v1", "0" * 64,
                )
            )


def test_catalog_reader_fails_closed_for_tamper_schema_drift_and_sidecars(tmp_path: Path) -> None:
    def sealed_path(name: str) -> tuple[Path, object]:
        path = tmp_path / name
        with SemanticCatalogWriter(path) as writer:
            writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
            seal = writer.seal()
        return path, seal

    tampered, tampered_seal = sealed_path("tampered.sqlite")
    with tampered.open("r+b") as handle:
        handle.seek(-1, 2)
        byte = handle.read(1)
        handle.seek(-1, 2)
        handle.write(bytes([byte[0] ^ 1]))
    with pytest.raises(CatalogError, match="file sha256"):
        SemanticCatalogReader(tampered, tampered_seal)

    drifted, drifted_seal = sealed_path("drifted.sqlite")
    with sqlite3.connect(drifted) as connection:
        connection.execute("CREATE TABLE drift (value TEXT)")
    with pytest.raises(CatalogError, match="schema"):
        SemanticCatalogReader(drifted, drifted_seal)

    sidecar, sidecar_seal = sealed_path("sidecar.sqlite")
    sidecar.with_name(f"{sidecar.name}-wal").write_bytes(b"not-authoritative")
    with pytest.raises(CatalogError, match="sidecar"):
        SemanticCatalogReader(sidecar, sidecar_seal)


def test_catalog_seal_blocks_later_writes_and_reader_is_query_only(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        seal = writer.seal()
        with pytest.raises(CatalogError, match="sealed"):
            writer.add_node(NodeRecord("later", None, "source", "", "source-1"))
    with SemanticCatalogReader(path, seal) as reader:
        assert reader.query_only is True
        with pytest.raises(sqlite3.OperationalError):
            reader.connection.execute("DELETE FROM nodes")
