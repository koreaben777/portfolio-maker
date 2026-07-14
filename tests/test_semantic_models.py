from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    RevisionStatus,
    SemanticEdge,
    SemanticNode,
    SemanticNodeKind,
    SemanticRevision,
    boundary_fingerprint,
    stable_node_id,
    stable_source_id,
)


def test_semantic_ids_are_stable_and_do_not_contain_locator() -> None:
    source_id = stable_source_id("local", "approved-root-key")
    node_id = stable_node_id(source_id, "1048577:99123")

    assert source_id == stable_source_id("local", "approved-root-key")
    assert node_id == stable_node_id(source_id, "1048577:99123")
    assert source_id.startswith("sha256:")
    assert node_id.startswith("sha256:")
    assert "/Users/" not in node_id


def test_boundary_fingerprint_is_order_independent() -> None:
    assert boundary_fingerprint("directory_root", ("b", "a")) == boundary_fingerprint(
        "directory_root", ("a", "b")
    )


def test_semantic_enums_expose_frozen_contract_values() -> None:
    assert [item.value for item in SemanticNodeKind] == [
        "source",
        "directory",
        "file",
        "github_activity",
    ]
    assert [item.value for item in AnalysisStatus] == [
        "pending",
        "complete",
        "partial",
        "unsupported",
        "unreadable",
        "failed",
    ]
    assert [item.value for item in RevisionStatus] == [
        "staging",
        "active",
        "superseded",
        "failed",
    ]


def test_semantic_value_types_are_frozen_and_locator_free() -> None:
    node = SemanticNode(
        node_id="node-1",
        source_id="source-1",
        node_kind=SemanticNodeKind.FILE,
        parent_node_id=None,
        display_name="README.md",
        relative_hierarchy="README.md",
        content_fingerprint="sha256:fingerprint",
        semantic_summary="Project overview",
        semantic_roles=("docs",),
        topics=("portfolio",),
        evidence_ids=(1,),
        analysis_status=AnalysisStatus.COMPLETE,
        analyzer_version="semantic-v1",
        updated_at="2026-07-14T00:00:00Z",
    )
    edge = SemanticEdge(
        revision_id="revision-1",
        from_node_id="node-1",
        relation="contains",
        to_node_id="node-2",
        confidence="high",
    )
    revision = SemanticRevision(
        id="revision-1",
        source_id="source-1",
        policy_hash="sha256:policy",
        analyzer_version="semantic-v1",
        status=RevisionStatus.STAGING,
    )

    assert "locator" not in {field.name for field in fields(node)}
    assert node.semantic_roles == ("docs",)
    assert edge.relation == "contains"
    assert revision.status is RevisionStatus.STAGING
    with pytest.raises(FrozenInstanceError):
        node.display_name = "changed"  # type: ignore[misc]
