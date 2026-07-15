from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

import portfolio_maker.application.semantic_index as semantic_index_module
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.application.semantic_index import (
    SemanticIndexError,
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
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
    return prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
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
        "revision_id",
        "source_id",
        "policy_hash",
        "analyzer_version",
        "chunk_sha256s",
        "node_count",
    }
    assert manifest["version"] == 1
    assert manifest["revision_id"] == result.revision_id
    assert len(chunk_paths) == result.chunk_count
    assert len(manifest["chunk_sha256s"]) == result.chunk_count
    assert result.node_count > 0
    assert result.partial_count == 0
    assert str(root) not in serialized
    assert str(repository_paths.db_path) not in serialized
    assert str(repository_paths.snapshots_dir) not in serialized
    assert "synthetic-secret" not in serialized
    assert "synthetic-basic-credential" not in serialized
    assert "credential.txt" not in serialized

    nodes = [
        node
        for path in chunk_paths
        for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]
    ]
    readme = next(node for node in nodes if node["relative_hierarchy"] == "README.md")
    assert readme["masked_excerpt"] == (
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
        locator_count = connection.execute(
            "SELECT COUNT(*) FROM semantic_node_locators WHERE revision_id = ?",
            (result.revision_id,),
        ).fetchone()
    assert revision == ("staging",)
    assert locator_count == (result.node_count,)


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
    assert len(chunk_paths) == len(manifest["chunk_sha256s"])
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
    ] == manifest["chunk_sha256s"]
    assert all(
        json.loads(chunk_text)["revision_id"] == manifest["revision_id"]
        for chunk_text in published.chunk_texts.values()
    )


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
