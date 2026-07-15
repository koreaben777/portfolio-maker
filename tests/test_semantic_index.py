from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import portfolio_maker.application.semantic_index as semantic_index_module
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import PrepareSemanticIndexRequest
from portfolio_maker.application.semantic_index import prepare_semantic_index
from portfolio_maker.workspace import WorkspacePaths


def approve_root(workspace: Path, root: Path, *, excluded: tuple[Path, ...] = ()) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["excluded_directories"] = [str(path) for path in excluded]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")


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
    write_sample_approval(WorkspacePaths.from_root(workspace))
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
    input_dir = first.manifest_path.parent / "input"
    previous_manifest = first.manifest_path.read_text(encoding="utf-8")
    previous_chunks = {
        path.name: path.read_text(encoding="utf-8")
        for path in input_dir.glob("chunk-*.json")
    }
    (root / "first-only.md").unlink()

    original_write = semantic_index_module.write_managed_text
    failed = False

    def fail_second_chunk(path: Path, content: str, *, overwrite: bool = True) -> Path:
        nonlocal failed
        if path.name == "chunk-0002.json" and not failed:
            failed = True
            raise OSError("synthetic publication failure")
        return original_write(path, content, overwrite=overwrite)

    monkeypatch.setattr(semantic_index_module, "write_managed_text", fail_second_chunk)

    with pytest.raises(OSError, match="synthetic publication failure"):
        prepare_semantic_index(
            PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
        )

    assert first.manifest_path.read_text(encoding="utf-8") == previous_manifest
    assert {
        path.name: path.read_text(encoding="utf-8")
        for path in input_dir.glob("chunk-*.json")
    } == previous_chunks
