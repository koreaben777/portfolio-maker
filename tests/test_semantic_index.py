from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import sqlite3
import threading
from pathlib import Path

import pytest

import portfolio_maker.application.semantic_index as semantic_index_module
from portfolio_maker.application.approval import load_approval, write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    BuildProfileRequest,
    IngestSourcesRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.application.semantic_index import (
    SemanticIndexError,
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.domain.semantic_models import SemanticEdge
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.semantic_crawler import crawl_local_structure
from portfolio_maker.workspace import WorkspacePaths


def approve_root(workspace: Path, root: Path, *, excluded: tuple[Path, ...] = ()) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    approval["excluded_directories"] = [str(path) for path in excluded]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")


def prepare_fixture_revision(workspace: Path, tmp_path: Path):
    root = tmp_path / "approved-root"
    root.mkdir()
    nested = root / "docs" / "guides"
    nested.mkdir(parents=True)
    (root / "README.md").write_text("Project overview", encoding="utf-8")
    (nested / "setup.md").write_text("Setup guidance", encoding="utf-8")
    approve_root(workspace, root)
    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    publish_legacy_v1_fixture(workspace, root, prepared, chunk_size=1)
    return prepared


def read_protocol_v2_capsules(prepared) -> list[dict[str, object]]:
    return [
        capsule
        for path in sorted(prepared.manifest_path.parent.joinpath("input").glob("chunk-*.json"))
        for capsule in json.loads(path.read_text(encoding="utf-8"))["capsules"]
    ]


def read_private_structural_records(
    workspace: Path,
    revision_id: str,
) -> list[dict[str, object]]:
    paths = WorkspacePaths.from_root(workspace)
    return [
        node
        for path in sorted(
            paths.semantic_revision_structural_dir(revision_id).glob("chunk-*.json")
        )
        for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]
    ]


def publish_legacy_v1_fixture(
    workspace: Path,
    root: Path,
    prepared,
    *,
    chunk_size: int,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    approval = load_approval(paths)
    crawl = crawl_local_structure(root, approval)
    prepared_nodes = semantic_index_module._prepare_nodes(crawl.entries, {})
    repository = SQLiteRepository(paths.db_path)
    repository.replace_semantic_revision_graph(
        prepared.revision_id,
        tuple(item.node for item in prepared_nodes),
        {item.node.node_id: item.locator for item in prepared_nodes},
        tuple(
            SemanticEdge(
                prepared.revision_id,
                item.node.parent_node_id,
                "contains",
                item.node.node_id,
            )
            for item in prepared_nodes
            if item.node.parent_node_id is not None
        ),
    )
    payloads = tuple(
        {
            "version": 1,
            "revision_id": prepared.revision_id,
            "nodes": [
                item.chunk_node
                for item in prepared_nodes[index : index + chunk_size]
            ],
        }
        for index in range(0, len(prepared_nodes), chunk_size)
    )
    chunk_texts = tuple(
        semantic_index_module._canonical_json(payload) for payload in payloads
    )
    chunk_sha256s = [
        hashlib.sha256(text.encode("utf-8")).hexdigest()
        for text in chunk_texts
    ]
    for path in paths.semantic_index_input_dir.glob("chunk-*.json"):
        path.unlink()
    for index, text in enumerate(chunk_texts, start=1):
        (paths.semantic_index_input_dir / f"chunk-{index:04}.json").write_text(
            text,
            encoding="utf-8",
        )
    paths.semantic_index_manifest_path.write_text(
        semantic_index_module._canonical_json(
            {
                "version": 1,
                "revision_id": prepared.revision_id,
                "source_id": crawl.entries[0].source_id,
                "policy_hash": semantic_index_module._policy_hash(approval),
                "analyzer_version": semantic_index_module.ANALYZER_VERSION,
                "chunk_sha256s": chunk_sha256s,
                "node_count": len(prepared_nodes),
            }
        ),
        encoding="utf-8",
    )


def write_valid_outputs(prepared) -> None:
    input_dir = prepared.manifest_path.parent / "input"
    output_dir = prepared.manifest_path.parent / "output"
    output_dir.mkdir(exist_ok=True)
    for input_path in sorted(input_dir.glob("chunk-*.json")):
        input_text = input_path.read_text(encoding="utf-8")
        input_payload = json.loads(input_text)
        payload = {
            "version": 1,
            "revision_id": prepared.revision_id,
            "input_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "semantic_summary": (
                        ""
                        if node["analysis_status"] in {"unsupported", "unreadable"}
                        else f"Summary for {node['display_name']}"
                    ),
                    "semantic_roles": node["roles"],
                    "topics": ["semantic-index"],
                    "analysis_status": node["analysis_status"],
                    "child_node_ids": node["child_node_ids"],
                }
                for node in input_payload["nodes"]
            ],
        }
        payload["output_sha256"] = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        (output_dir / input_path.name).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )


def _two_node_input_fixture() -> dict[str, dict[str, object]]:
    return {
        "root": {
            "node_id": "root",
            "parent_node_id": None,
            "kind": "source",
            "display_name": "root",
            "relative_hierarchy": ".",
            "content_fingerprint": None,
            "roles": [],
            "masked_excerpt": "",
            "analysis_status": "pending",
            "child_node_ids": ["child"],
        },
        "child": {
            "node_id": "child",
            "parent_node_id": "root",
            "kind": "file",
            "display_name": "child",
            "relative_hierarchy": "child",
            "content_fingerprint": None,
            "roles": [],
            "masked_excerpt": "",
            "analysis_status": "pending",
            "child_node_ids": [],
        },
    }


def _two_node_staged_fixture() -> list[dict[str, object]]:
    return [
        {
            "node_id": node_id,
            "source_id": "source",
            "node_kind": node["kind"],
            "parent_node_id": node["parent_node_id"],
            "display_name": node["display_name"],
            "relative_hierarchy": node["relative_hierarchy"],
            "content_fingerprint": node["content_fingerprint"],
            "semantic_roles": node["roles"],
            "analysis_status": node["analysis_status"],
            "analyzer_version": "semantic-input-v1",
        }
        for node_id, node in _two_node_input_fixture().items()
    ]


def test_semantic_index_accepts_five_digit_chunk_names() -> None:
    for name in ("chunk-0001.json", "chunk-10000.json", "chunk-100000.json"):
        assert semantic_index_module._CHUNK_FILENAME.fullmatch(name)
    for name in (
        "chunk-001.json",
        "chunk-+10000.json",
        "chunk--10000.json",
        "chunk-10000-extra.json",
        "nested/chunk-10000.json",
        "chunk-1000a.json",
    ):
        assert semantic_index_module._CHUNK_FILENAME.fullmatch(name) is None


def test_validate_staged_structure_does_not_rescan_input_for_each_node() -> None:
    class CountingDict(dict):
        items_calls = 0

        def items(self):
            self.items_calls += 1
            if self.items_calls > 2:
                raise AssertionError("input nodes were rescanned")
            return super().items()

    input_nodes = CountingDict(_two_node_input_fixture())

    semantic_index_module._validate_staged_structure(
        input_nodes,
        _two_node_staged_fixture(),
        {"source_id": "source", "analyzer_version": "semantic-input-v1"},
    )

    assert input_nodes.items_calls == 2


def profile_approved_file(
    workspace: Path, tmp_path: Path
) -> tuple[Path, Path, WorkspacePaths, SQLiteRepository, int]:
    root = tmp_path / "approved-root"
    root.mkdir()
    source_path = root / "README.md"
    source_path.write_text("Approved project evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [
        root.resolve().as_uri(),
        source_path.resolve().as_uri(),
    ]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    write_sample_artifact_policy(paths)

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="README.md",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    assert ingest_sources(IngestSourcesRequest(workspace=workspace)).ingested_count == 1
    evidence_id = build_profile(
        BuildProfileRequest(workspace=workspace, write_artifacts=False)
    ).evidence_ids[0]
    return root, source_path, paths, repository, evidence_id


def test_prepare_semantic_index_links_current_approved_file_evidence_to_file_nodes(
    workspace: Path, tmp_path: Path
) -> None:
    root, _, _, repository, evidence_id = profile_approved_file(workspace, tmp_path)

    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )

    capsules = read_protocol_v2_capsules(prepared)
    staged_file = next(
        node for node in capsules if node["relative_hierarchy"] == "README.md"
    )
    assert staged_file["evidence_ids"] == [evidence_id]
    assert repository.list_semantic_nodes(prepared.revision_id) == []


def test_prepare_semantic_index_does_not_link_evidence_after_profiled_file_changes(
    workspace: Path, tmp_path: Path
) -> None:
    root, source_path, _, repository, evidence_id = profile_approved_file(
        workspace, tmp_path
    )
    source_path.write_text("Changed after the evidence snapshot", encoding="utf-8")
    source = repository.list_sources()[0]
    assert source.status is SourceStatus.INGESTED

    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )

    staged_file = next(
        node
        for node in read_protocol_v2_capsules(prepared)
        if node["relative_hierarchy"] == "README.md"
    )
    assert evidence_id not in staged_file["evidence_ids"]
    assert repository.list_sources()[0].status is SourceStatus.STALE_SOURCE


@pytest.mark.parametrize("artifact_policy_state", ("changed", "malformed"))
def test_prepare_semantic_index_evidence_mapping_ignores_artifact_policy(
    workspace: Path, tmp_path: Path, artifact_policy_state: str
) -> None:
    root, _, paths, repository, evidence_id = profile_approved_file(workspace, tmp_path)
    if artifact_policy_state == "changed":
        policy = json.loads(paths.artifact_approval_path.read_text(encoding="utf-8"))
        policy["artifacts"]["master_profile"]["include_local"] = False
        paths.artifact_approval_path.write_text(json.dumps(policy), encoding="utf-8")
    else:
        paths.artifact_approval_path.write_text("{not-json", encoding="utf-8")

    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )

    staged_file = next(
        node
        for node in read_protocol_v2_capsules(prepared)
        if node["relative_hierarchy"] == "README.md"
    )
    assert staged_file["evidence_ids"] == [evidence_id]


def test_canonical_file_uri_matching_accepts_empty_authority_and_encoded_path(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "approved evidence.md"
    source_path.write_text("evidence", encoding="utf-8")
    canonical_uri = source_path.resolve().as_uri()

    assert canonical_uri.startswith("file:///")
    assert "%20" in canonical_uri
    assert semantic_index_module._canonical_file_uri_from_uri(canonical_uri) == canonical_uri


def test_canonical_file_uri_matching_rejects_noncanonical_variants(tmp_path: Path) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "nested").mkdir()
    source_path = root / "README.md"
    source_path.write_text("evidence", encoding="utf-8")
    canonical_uri = source_path.resolve().as_uri()
    uri_path = canonical_uri.removeprefix("file://")
    invalid_uris = (
        f"file://localhost{uri_path}",
        canonical_uri.replace("README.md", "%52EADME.md"),
        f"{root.resolve().as_uri()}/nested/../README.md",
        f"{canonical_uri}?download=1",
        f"{canonical_uri}#section",
    )

    assert all(
        semantic_index_module._canonical_file_uri_from_uri(uri) is None
        for uri in invalid_uris
    )


def test_apply_semantic_index_activates_complete_bottom_up_output(
    workspace: Path, tmp_path: Path
) -> None:
    prepared = prepare_fixture_revision(workspace, tmp_path)
    with sqlite3.connect(WorkspacePaths.from_root(workspace).db_path) as connection:
        connection.execute(
            "INSERT INTO projects (name, public_safe) VALUES (?, ?)",
            ("legacy-technical-project", 0),
        )
    write_valid_outputs(prepared)

    result = apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    nodes = repository.list_semantic_nodes(prepared.revision_id)
    assert result.revision_id == prepared.revision_id
    assert result.active is True
    assert result.complete_count == sum(
        node["analysis_status"] == "complete" for node in nodes
    )
    assert result.partial_count == 0
    assert result.failed_count == 0
    assert [node["relative_hierarchy"] for node in nodes] == sorted(
        node["relative_hierarchy"] for node in nodes
    )
    assert all(node["semantic_summary"] for node in nodes)
    assert all("/" not in node["semantic_summary"] for node in nodes)
    with sqlite3.connect(WorkspacePaths.from_root(workspace).db_path) as connection:
        legacy_projects = connection.execute("SELECT name FROM projects").fetchall()
    assert legacy_projects == [("legacy-technical-project",)]


def test_apply_semantic_index_invalid_output_keeps_previous_active_revision(
    workspace: Path, tmp_path: Path
) -> None:
    first = prepare_fixture_revision(workspace, tmp_path)
    write_valid_outputs(first)
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    root = tmp_path / "approved-root"
    (root / "CHANGELOG.md").write_text("Second revision", encoding="utf-8")
    second = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    publish_legacy_v1_fixture(workspace, root, second, chunk_size=1)
    write_valid_outputs(second)
    output_path = next(
        path
        for path in (second.manifest_path.parent / "output").glob("chunk-*.json")
        if json.loads(path.read_text(encoding="utf-8"))["nodes"][0]["child_node_ids"]
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["nodes"][0]["child_node_ids"] = ["outside-this-revision"]
    payload["output_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in payload.items() if key != "output_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(SemanticIndexError, match="semantic index output"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    source_id = json.loads(first.manifest_path.read_text(encoding="utf-8"))["source_id"]
    active = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path).get_active_semantic_revision(
        source_id
    )
    assert active is not None
    assert active["id"] == first.revision_id


@pytest.mark.parametrize(
    "change",
    ("approved_source", "excluded_directory", "excluded_file_pattern"),
)
def test_apply_semantic_index_rejects_changed_approval_policy_and_keeps_previous_active_revision(
    workspace: Path, tmp_path: Path, change: str
) -> None:
    first = prepare_fixture_revision(workspace, tmp_path)
    write_valid_outputs(first)
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    root = tmp_path / "approved-root"
    (root / "CHANGELOG.md").write_text("Second revision", encoding="utf-8")
    second = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    publish_legacy_v1_fixture(workspace, root, second, chunk_size=1)
    write_valid_outputs(second)

    paths = WorkspacePaths.from_root(workspace)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    if change == "approved_source":
        approval["approved_source_uris"] = []
    elif change == "excluded_directory":
        approval["excluded_directories"] = [str(root / "excluded")]
    else:
        approval["excluded_file_patterns"] = ["*.private"]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    with pytest.raises(SemanticIndexError, match="semantic index approval policy has changed"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    source_id = json.loads(first.manifest_path.read_text(encoding="utf-8"))["source_id"]
    active = SQLiteRepository(paths.db_path).get_active_semantic_revision(source_id)
    assert active is not None
    assert active["id"] == first.revision_id


@pytest.mark.parametrize("field", ["semantic_summary", "semantic_roles", "topics"])
def test_apply_semantic_index_rejects_relative_file_locator_and_keeps_previous_active_revision(
    workspace: Path, tmp_path: Path, field: str
) -> None:
    first = prepare_fixture_revision(workspace, tmp_path)
    write_valid_outputs(first)
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    root = tmp_path / "approved-root"
    (root / "CHANGELOG.md").write_text("Second revision", encoding="utf-8")
    second = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    publish_legacy_v1_fixture(workspace, root, second, chunk_size=1)
    write_valid_outputs(second)
    output_path = next((second.manifest_path.parent / "output").glob("chunk-*.json"))
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    output_node = payload["nodes"][0]
    if field == "semantic_summary":
        output_node[field] = "See docs/private-plan.md for details"
    else:
        output_node[field] = ["See docs/private-plan.md for details"]
    payload["output_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in payload.items() if key != "output_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(SemanticIndexError, match="unsafe text"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    source_id = json.loads(first.manifest_path.read_text(encoding="utf-8"))["source_id"]
    active = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path).get_active_semantic_revision(
        source_id
    )
    assert active is not None
    assert active["id"] == first.revision_id


def test_apply_semantic_index_rejects_malformed_output_without_activation(
    workspace: Path, tmp_path: Path
) -> None:
    prepared = prepare_fixture_revision(workspace, tmp_path)
    write_valid_outputs(prepared)
    next((prepared.manifest_path.parent / "output").glob("chunk-*.json")).write_text(
        "{", encoding="utf-8"
    )

    with pytest.raises(SemanticIndexError, match="semantic index output"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    source_id = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))["source_id"]
    assert SQLiteRepository(WorkspacePaths.from_root(workspace).db_path).get_active_semantic_revision(
        source_id
    ) is None


def test_apply_semantic_index_preserves_partial_status(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "large.md").write_text("x" * 131_073, encoding="utf-8")
    approve_root(workspace, root)
    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    publish_legacy_v1_fixture(workspace, root, prepared, chunk_size=1)
    write_valid_outputs(prepared)

    result = apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    nodes = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path).list_semantic_nodes(
        prepared.revision_id
    )
    assert result.partial_count == 1
    assert next(node for node in nodes if node["relative_hierarchy"] == "large.md")[
        "analysis_status"
    ] == "partial"


def test_apply_semantic_index_interruption_keeps_previous_active_revision(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = prepare_fixture_revision(workspace, tmp_path)
    write_valid_outputs(first)
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    root = tmp_path / "approved-root"
    (root / "CHANGELOG.md").write_text("Second revision", encoding="utf-8")
    second = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    publish_legacy_v1_fixture(workspace, root, second, chunk_size=1)
    write_valid_outputs(second)

    def interrupted(*args, **kwargs) -> None:
        raise OSError("synthetic interruption")

    monkeypatch.setattr(SQLiteRepository, "replace_and_activate_semantic_revision", interrupted)

    with pytest.raises(OSError, match="synthetic interruption"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    source_id = json.loads(first.manifest_path.read_text(encoding="utf-8"))["source_id"]
    active = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path).get_active_semantic_revision(
        source_id
    )
    assert active is not None
    assert active["id"] == first.revision_id


def test_prepare_semantic_index_writes_locator_free_chunks_and_staging_revision(
    workspace: Path, tmp_path: Path
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text(
        (
            f"API_TOKEN=synthetic-secret\nSOURCE_PATH={root}\n"
            "CREDENTIAL=synthetic-credential\n"
            "Authorization: Basic synthetic-basic-credential\n"
            f"See ({root})\n# Evidence\n"
        ),
        encoding="utf-8",
    )
    excluded = root / "excluded"
    excluded.mkdir()
    (excluded / "credential.txt").write_text("private credential", encoding="utf-8")
    approve_root(workspace, root, excluded=(excluded,))

    result = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    chunk_paths = sorted(result.manifest_path.parent.joinpath("input").glob("chunk-*.json"))
    serialized = "\n".join(
        [json.dumps(manifest), *(path.read_text(encoding="utf-8") for path in chunk_paths)]
    )
    repository_paths = WorkspacePaths.from_root(workspace)

    assert set(manifest) == {
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
    assert manifest["version"] == 2
    assert manifest["revision_id"] == result.revision_id
    assert len(chunk_paths) == result.chunk_count
    assert len(manifest["capsule_chunk_sha256s"]) == result.chunk_count
    assert result.node_count > 0
    assert result.partial_count == 0
    assert str(root) not in serialized
    assert str(repository_paths.db_path) not in serialized
    assert str(repository_paths.snapshots_dir) not in serialized
    assert "synthetic-secret" not in serialized
    assert "synthetic-basic-credential" not in serialized
    assert "credential.txt" not in serialized

    nodes = [
        capsule
        for path in chunk_paths
        for capsule in json.loads(path.read_text(encoding="utf-8"))["capsules"]
    ]
    readme = next(node for node in nodes if node["relative_hierarchy"] == "README.md")
    assert readme["signals"][0]["text"] == (
        "API_TOKEN=[REDACTED]\nSOURCE_PATH=[REDACTED]\n"
        "CREDENTIAL=[REDACTED]\nAuthorization: [REDACTED]\n"
        "See ([REDACTED])\n# Evidence\n"
    )
    assert all("locator" not in node for node in nodes)
    assert all("excluded/credential.txt" != node["relative_hierarchy"] for node in nodes)

    with sqlite3.connect(repository_paths.db_path) as connection:
        revision = connection.execute(
            "SELECT status FROM semantic_index_revisions WHERE id = ?",
            (result.revision_id,),
        ).fetchone()
        node_count = connection.execute(
            "SELECT COUNT(*) FROM semantic_nodes WHERE revision_id = ?",
            (result.revision_id,),
        ).fetchone()
    assert revision == ("staging",)
    assert node_count == (0,)
    private_locators = [
        locator
        for path in repository_paths.semantic_revision_locator_dir(
            result.revision_id
        ).glob("chunk-*.json")
        for locator in json.loads(path.read_text(encoding="utf-8"))["locators"]
    ]
    assert len(private_locators) == result.node_count


def test_protocol_v2_prepare_publishes_bounded_inputs_without_staging_graph(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text(
        "OPENAI_API_KEY=sk-synthetic-placeholder\n"
        f"SOURCE_PATH={root}/private.txt\n"
        "# Safe evidence\nProject details",
        encoding="utf-8",
    )
    (root / "empty.md").write_text("", encoding="utf-8")
    (root / "archive.bin").write_bytes(b"\x00\x01binary")
    approve_root(workspace, root)
    graph_replacements: list[str] = []
    original_replace = SQLiteRepository.replace_semantic_revision_graph

    def record_graph_replacement(
        self: SQLiteRepository,
        revision_id: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        graph_replacements.append(revision_id)
        original_replace(self, revision_id, *args, **kwargs)

    monkeypatch.setattr(
        SQLiteRepository,
        "replace_semantic_revision_graph",
        record_graph_replacement,
    )

    result = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=2)
    )

    paths = WorkspacePaths.from_root(workspace)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    work_plan = json.loads(paths.semantic_work_plan_path.read_text(encoding="utf-8"))
    capsule_paths = sorted(paths.semantic_index_input_dir.glob("chunk-*.json"))
    structural_paths = sorted(
        paths.semantic_revision_structural_dir(result.revision_id).glob("chunk-*.json")
    )
    locator_paths = sorted(
        paths.semantic_revision_locator_dir(result.revision_id).glob("chunk-*.json")
    )
    public_texts = [
        result.manifest_path.read_text(encoding="utf-8"),
        paths.semantic_work_plan_path.read_text(encoding="utf-8"),
        *(path.read_text(encoding="utf-8") for path in capsule_paths),
    ]
    public_serialized = "\n".join(public_texts)
    managed_input_bytes = sum(
        len(text.encode("utf-8"))
        for text in (
            paths.semantic_work_plan_path.read_text(encoding="utf-8"),
            *(path.read_text(encoding="utf-8") for path in capsule_paths),
        )
    )
    capsules = [
        capsule
        for path in capsule_paths
        for capsule in json.loads(path.read_text(encoding="utf-8"))["capsules"]
    ]

    assert manifest["version"] == 2
    assert manifest["protocol"] == "codex-semantic-v2"
    assert manifest["revision_id"] == result.revision_id
    assert manifest["work_plan_sha256"] == hashlib.sha256(
        paths.semantic_work_plan_path.read_bytes()
    ).hexdigest()
    assert len(manifest["capsule_chunk_sha256s"]) == result.chunk_count
    assert work_plan["version"] == 2
    assert work_plan["revision_id"] == result.revision_id
    assert len(work_plan["units"]) == result.work_unit_count
    assert result.node_count == len(capsules)
    assert result.managed_input_bytes == managed_input_bytes
    assert (
        result.analysis_target_count + result.structural_only_count
        == result.node_count
    )
    assert structural_paths
    assert locator_paths
    assert str(root) in "\n".join(
        path.read_text(encoding="utf-8") for path in locator_paths
    )
    assert str(root) not in public_serialized
    assert "sk-synthetic-placeholder" not in public_serialized
    assert "semantic_summary" not in public_serialized
    assert graph_replacements == []
    repository = SQLiteRepository(paths.db_path)
    assert repository.list_semantic_nodes(result.revision_id) == []
    with sqlite3.connect(paths.db_path) as connection:
        revision = connection.execute(
            "SELECT status FROM semantic_index_revisions WHERE id = ?",
            (result.revision_id,),
        ).fetchone()
    assert revision == ("staging",)


def test_prepare_semantic_index_scans_only_the_requested_root(
    workspace: Path, tmp_path: Path
) -> None:
    root = tmp_path / "selected-root"
    root.mkdir()
    (root / "README.md").write_text("evidence", encoding="utf-8")
    approve_root(workspace, root)
    outside = tmp_path / "outside-root"
    outside.mkdir()
    (outside / "not-in-scope.md").write_text("outside", encoding="utf-8")

    result = prepare_semantic_index(PrepareSemanticIndexRequest(workspace=workspace, root=root))

    serialized = result.manifest_path.read_text(encoding="utf-8") + "\n".join(
        path.read_text(encoding="utf-8")
        for path in result.manifest_path.parent.joinpath("input").glob("chunk-*.json")
    )
    assert "not-in-scope.md" not in serialized


@pytest.mark.parametrize(
    "value",
    [True, "1", math.nan, math.inf, -math.inf, 0, -1, 300.01],
)
def test_prepare_rejects_invalid_directory_timeout_before_workspace_write(
    tmp_path: Path,
    value: object,
) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "root"
    workspace.mkdir()
    root.mkdir()

    with pytest.raises(SemanticIndexError, match="directory timeout"):
        prepare_semantic_index(
            PrepareSemanticIndexRequest(
                workspace=workspace,
                root=root,
                directory_timeout_seconds=value,
            )
        )

    assert not (workspace / ".portfolio-maker").exists()


def test_prepare_root_timeout_is_controlled_without_semantic_publication(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    approve_root(workspace, root)

    def root_timeout(*_args, **_kwargs):
        from portfolio_maker.infrastructure.semantic_crawler import (
            StructuralCrawlRootTimeoutError,
        )

        raise StructuralCrawlRootTimeoutError("synthetic timeout")

    monkeypatch.setattr(semantic_index_module, "crawl_local_structure", root_timeout)

    with pytest.raises(SemanticIndexError, match="timed out"):
        prepare_semantic_index(
            PrepareSemanticIndexRequest(workspace=workspace, root=root)
        )

    paths = WorkspacePaths.from_root(workspace)
    assert not paths.semantic_index_manifest_path.exists()
    assert not paths.semantic_work_plan_path.exists()
    assert not paths.semantic_index_report_path.exists()
    assert not (paths.raw_dir / "semantic-index").exists()
    assert not paths.db_path.exists()


def test_prepare_worker_failure_is_controlled_without_semantic_publication(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    approve_root(workspace, root)

    def worker_failure(*_args, **_kwargs):
        from portfolio_maker.infrastructure.semantic_crawler import (
            StructuralCrawlWorkerError,
        )

        raise StructuralCrawlWorkerError(
            f"synthetic worker failure at {root}"
        )

    monkeypatch.setattr(
        semantic_index_module,
        "crawl_local_structure",
        worker_failure,
    )

    with pytest.raises(SemanticIndexError) as captured:
        prepare_semantic_index(
            PrepareSemanticIndexRequest(workspace=workspace, root=root)
        )

    assert str(captured.value) == "semantic index crawl failed safely"
    assert str(root) not in str(captured.value)
    paths = WorkspacePaths.from_root(workspace)
    assert not paths.semantic_index_manifest_path.exists()
    assert not paths.semantic_work_plan_path.exists()
    assert not paths.semantic_index_report_path.exists()
    assert not (paths.raw_dir / "semantic-index").exists()
    assert not paths.db_path.exists()


def test_prepare_manifest_binds_crawl_watchdog_aggregates(
    workspace: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("evidence", encoding="utf-8")
    approve_root(workspace, root)

    result = prepare_semantic_index(
        PrepareSemanticIndexRequest(
            workspace=workspace,
            root=root,
            directory_timeout_seconds=12.5,
        )
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["directory_timeout_seconds"] == 12.5
    assert manifest["timed_out_directory_count"] == 0
    assert manifest["unreadable_directory_count"] == 0
    assert len(manifest["manifest_sha256"]) == 64
    assert result.directory_timeout_seconds == 12.5
    assert result.timed_out_directory_count == 0
    assert result.unreadable_directory_count == 0


def test_prepare_child_timeout_is_bound_and_responsive_rerun_is_fresh(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved-root"
    blocked = root / "blocked"
    sibling = root / "sibling"
    blocked.mkdir(parents=True)
    sibling.mkdir()
    (blocked / "hidden.md").write_text("hidden", encoding="utf-8")
    (sibling / "visible.md").write_text("visible", encoding="utf-8")
    approve_root(workspace, root)
    original_crawl = crawl_local_structure

    def controlled_crawl(root_path, approval, **kwargs):
        from portfolio_maker.infrastructure.semantic_crawler import (
            _materialize_directory,
        )

        def enumerator(path: Path):
            if path.name == "blocked":
                threading.Event().wait()
            return _materialize_directory(path)

        return original_crawl(
            root_path,
            approval,
            directory_timeout_seconds=kwargs["directory_timeout_seconds"],
            _process_context=multiprocessing.get_context("fork"),
            _enumerator=enumerator,
        )

    monkeypatch.setattr(
        semantic_index_module,
        "crawl_local_structure",
        controlled_crawl,
    )
    timed_out = prepare_semantic_index(
        PrepareSemanticIndexRequest(
            workspace=workspace,
            root=root,
            directory_timeout_seconds=0.05,
        )
    )
    manifest = json.loads(timed_out.manifest_path.read_text(encoding="utf-8"))
    capsules = read_protocol_v2_capsules(timed_out)

    assert timed_out.timed_out_directory_count == 1
    assert timed_out.unreadable_directory_count == 1
    assert manifest["timed_out_directory_count"] == 1
    assert next(
        capsule for capsule in capsules
        if capsule["relative_hierarchy"] == "blocked"
    )["analysis_status"] == "unreadable"
    assert all(
        capsule["relative_hierarchy"] != "blocked/hidden.md"
        for capsule in capsules
    )
    assert any(
        capsule["relative_hierarchy"] == "sibling/visible.md"
        for capsule in capsules
    )

    monkeypatch.setattr(
        semantic_index_module,
        "crawl_local_structure",
        original_crawl,
    )
    responsive = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root)
    )
    assert responsive.revision_id != timed_out.revision_id
    assert responsive.timed_out_directory_count == 0


def test_prepare_semantic_index_skips_unsupported_symlink_analysis(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("insurance evidence search", encoding="utf-8")
    (root / "broken-link").symlink_to(tmp_path / "missing-target")
    approve_root(workspace, root)
    original_analyze = semantic_index_module.analyze_file_input
    analyzed: list[str] = []

    def record_analysis(entry):
        analyzed.append(entry.relative_hierarchy)
        return original_analyze(entry)

    monkeypatch.setattr(semantic_index_module, "analyze_file_input", record_analysis)

    result = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root)
    )

    nodes = read_protocol_v2_capsules(result)
    broken = next(
        node for node in nodes if node["relative_hierarchy"] == "broken-link"
    )
    assert broken["analysis_status"] == "unsupported"
    assert broken["semantic_disposition"] == "unsupported"
    assert broken["signals"] == []
    assert broken["roles"] == []
    assert "README.md" in analyzed
    assert "broken-link" not in analyzed


def test_prepare_semantic_index_skips_evidence_for_file_missing_after_analysis(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("insurance evidence search", encoding="utf-8")
    approve_root(workspace, root)
    original_canonicalize = semantic_index_module._canonical_file_uri

    def disappear(path: Path) -> str:
        if path.name == "README.md":
            raise FileNotFoundError(path)
        return original_canonicalize(path)

    monkeypatch.setattr(semantic_index_module, "_canonical_file_uri", disappear)

    result = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root)
    )

    structural = read_private_structural_records(workspace, result.revision_id)
    readme = next(
        node for node in structural if node["relative_hierarchy"] == "README.md"
    )
    assert readme["evidence_ids"] == []
    capsule = next(
        node
        for node in read_protocol_v2_capsules(result)
        if node["relative_hierarchy"] == "README.md"
    )
    assert capsule["evidence_ids"] == []


def test_prepare_semantic_index_replaces_only_prior_managed_chunks(
    workspace: Path, tmp_path: Path
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("current evidence", encoding="utf-8")
    (root / "first-only.md").write_text("old chunk payload", encoding="utf-8")
    approve_root(workspace, root)

    first = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    input_dir = first.manifest_path.parent / "input"
    unrelated_path = input_dir / "user-note.txt"
    unrelated_path.write_text("preserve this file", encoding="utf-8")

    (root / "first-only.md").unlink()
    second = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )

    manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    chunk_paths = sorted(input_dir.glob("chunk-*.json"))
    serialized = "\n".join(
        [json.dumps(manifest), *(path.read_text(encoding="utf-8") for path in chunk_paths)]
    )

    assert len(chunk_paths) == second.chunk_count
    assert len(chunk_paths) == len(manifest["capsule_chunk_sha256s"])
    assert "old chunk payload" not in serialized
    assert unrelated_path.read_text(encoding="utf-8") == "preserve this file"


def test_prepare_semantic_index_reader_never_observes_incoherent_generation(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("current evidence", encoding="utf-8")
    (root / "first-only.md").write_text("old chunk payload", encoding="utf-8")
    approve_root(workspace, root)

    first = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    (root / "first-only.md").unlink()

    original_write = semantic_index_module.write_managed_text
    reader_started = threading.Event()
    reader_finished = threading.Event()
    reader_results: list[object] = []
    reader_thread: threading.Thread | None = None

    def read_during_publication() -> None:
        reader_started.set()
        try:
            reader_results.append(
                semantic_index_module._read_published_semantic_input(
                    WorkspacePaths.from_root(workspace)
                )
            )
        except Exception as error:
            reader_results.append(error)
        finally:
            reader_finished.set()

    def inject_reader_before_manifest(path: Path, content: str, *, overwrite: bool = True) -> Path:
        nonlocal reader_thread
        if path == WorkspacePaths.from_root(workspace).semantic_index_manifest_path:
            reader_thread = threading.Thread(target=read_during_publication)
            reader_thread.start()
            assert reader_started.wait(timeout=1)
            assert not reader_finished.wait(timeout=0.1)
        return original_write(path, content, overwrite=overwrite)

    monkeypatch.setattr(
        semantic_index_module, "write_managed_text", inject_reader_before_manifest
    )

    second = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )

    assert reader_thread is not None
    reader_thread.join(timeout=1)
    assert not reader_thread.is_alive()
    assert len(reader_results) == 1
    assert not isinstance(reader_results[0], BaseException)
    published = reader_results[0]
    assert isinstance(published, semantic_index_module._PublishedSemanticInput)
    manifest = json.loads(published.manifest_text or "{}")
    assert manifest["revision_id"] in {first.revision_id, second.revision_id}
    assert [
        hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        for chunk_text in published.chunk_texts.values()
    ] == manifest["capsule_chunk_sha256s"]
    assert published.work_plan_text is not None
    assert hashlib.sha256(
        published.work_plan_text.encode("utf-8")
    ).hexdigest() == manifest["work_plan_sha256"]
    assert all(
        json.loads(chunk_text)["revision_id"] == manifest["revision_id"]
        for chunk_text in published.chunk_texts.values()
    )


def test_published_semantic_input_reader_rejects_v2_manifest_self_hash_tamper(
    workspace: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("Project overview", encoding="utf-8")
    approve_root(workspace, root)
    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root)
    )
    paths = WorkspacePaths.from_root(workspace)
    manifest = json.loads(
        prepared.manifest_path.read_text(encoding="utf-8")
    )
    manifest["directory_timeout_seconds"] = 12.5
    prepared.manifest_path.write_text(
        semantic_index_module._canonical_json(manifest),
        encoding="utf-8",
    )

    with pytest.raises(SemanticIndexError, match="manifest"):
        semantic_index_module._read_published_semantic_input(paths)


def test_prepare_semantic_index_restores_previous_input_when_publication_fails(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("current evidence", encoding="utf-8")
    (root / "first-only.md").write_text("old chunk payload", encoding="utf-8")
    approve_root(workspace, root)

    first = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.activate_semantic_revision(first.revision_id)
    source_id = json.loads(first.manifest_path.read_text(encoding="utf-8"))["source_id"]
    input_dir = first.manifest_path.parent / "input"
    previous_manifest = first.manifest_path.read_text(encoding="utf-8")
    previous_work_plan = WorkspacePaths.from_root(
        workspace
    ).semantic_work_plan_path.read_text(encoding="utf-8")
    previous_chunks = {
        path.name: path.read_text(encoding="utf-8")
        for path in input_dir.glob("chunk-*.json")
    }
    (root / "first-only.md").unlink()

    original_write = semantic_index_module.write_managed_text
    original_create = SQLiteRepository.create_semantic_revision
    created_revision_ids: list[str] = []
    failed = False

    def fail_second_chunk(path: Path, content: str, *, overwrite: bool = True) -> Path:
        nonlocal failed
        if path.name == "chunk-0002.json" and not failed:
            failed = True
            raise OSError("synthetic publication failure")
        return original_write(path, content, overwrite=overwrite)

    def capture_created_revision(
        self: SQLiteRepository,
        revision_id: str,
        created_source_id: str,
        policy_hash: str,
        analyzer_version: str,
    ) -> None:
        created_revision_ids.append(revision_id)
        original_create(self, revision_id, created_source_id, policy_hash, analyzer_version)

    monkeypatch.setattr(semantic_index_module, "write_managed_text", fail_second_chunk)
    monkeypatch.setattr(SQLiteRepository, "create_semantic_revision", capture_created_revision)

    with pytest.raises(OSError, match="synthetic publication failure"):
        prepare_semantic_index(
            PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
        )

    assert first.manifest_path.read_text(encoding="utf-8") == previous_manifest
    assert (
        WorkspacePaths.from_root(workspace).semantic_work_plan_path.read_text(
            encoding="utf-8"
        )
        == previous_work_plan
    )
    assert {
        path.name: path.read_text(encoding="utf-8")
        for path in input_dir.glob("chunk-*.json")
    } == previous_chunks
    active = repository.get_active_semantic_revision(source_id)
    assert active is not None
    assert active["id"] == first.revision_id
    assert active["status"] == "active"
    assert created_revision_ids
    with sqlite3.connect(WorkspacePaths.from_root(workspace).db_path) as connection:
        statuses = connection.execute(
            "SELECT id, status FROM semantic_index_revisions WHERE source_id = ?",
            (source_id,),
        ).fetchall()
    assert (created_revision_ids[0], "failed") in statuses
    assert all(status != "staging" for _, status in statuses)
