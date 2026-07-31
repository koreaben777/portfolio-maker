from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import portfolio_maker.application.semantic_index as semantic_index_module
from portfolio_maker.adapters.cli import main
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    FinalizeSemanticIndexRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.application.semantic_index import (
    SemanticIndexError,
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.application.semantic_work import finalize_semantic_index
from portfolio_maker.infrastructure.managed_files import (
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths
from test_semantic_finalization import (
    PreparedFinalization,
    _approve_root,
    _complete_work,
)


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_text(payload: object) -> str:
    return _canonical_bytes(payload).decode("utf-8")


def _prepare_completed_v2(
    workspace: Path,
    tmp_path: Path,
    *,
    split_chunks: bool = False,
) -> tuple[PreparedFinalization, Path]:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text(
        "# Grounded project\n"
        "The application validates approved evidence before publication.",
        encoding="utf-8",
    )
    _approve_root(workspace, root)
    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(
            workspace=workspace,
            root=root,
            chunk_size=2,
        )
    )
    finalization = PreparedFinalization(
        workspace=workspace,
        paths=WorkspacePaths.from_root(workspace),
        revision_id=prepared.revision_id,
    )
    _complete_work(finalization)
    finalize_semantic_index(
        FinalizeSemanticIndexRequest(workspace=workspace)
    )
    if split_chunks:
        _split_final_and_locator_chunks(finalization)
    return finalization, root


def _split_final_and_locator_chunks(
    prepared: PreparedFinalization,
) -> None:
    final_path = prepared.paths.semantic_final_dir / "chunk-0001.json"
    final_payload = json.loads(final_path.read_text(encoding="utf-8"))
    locator_dir = prepared.paths.semantic_revision_locator_dir(
        prepared.revision_id
    )
    locator_path = locator_dir / "chunk-0001.json"
    locator_payload = json.loads(locator_path.read_text(encoding="utf-8"))
    assert len(final_payload["nodes"]) == len(locator_payload["locators"]) == 2

    final_texts: list[str] = []
    locator_texts: list[str] = []
    for index, (node, locator) in enumerate(
        zip(final_payload["nodes"], locator_payload["locators"], strict=True),
        start=1,
    ):
        final_chunk = {
            "version": 2,
            "protocol": "codex-semantic-v2",
            "revision_id": prepared.revision_id,
            "chunk_index": index,
            "nodes": [node],
        }
        locator_chunk = {
            "version": 2,
            "revision_id": prepared.revision_id,
            "locators": [locator],
        }
        final_texts.append(_canonical_text(final_chunk))
        locator_texts.append(_canonical_text(locator_chunk))
    for index, text in enumerate(final_texts, start=1):
        write_managed_text(
            prepared.paths.semantic_final_dir / f"chunk-{index:04}.json",
            text,
        )
    for index, text in enumerate(locator_texts, start=1):
        write_managed_text(
            locator_dir / f"chunk-{index:04}.json",
            text,
        )

    manifest_path = prepared.paths.semantic_final_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chunk_sha256s"] = [
        hashlib.sha256(text.encode("utf-8")).hexdigest()
        for text in final_texts
    ]
    manifest["chunk_node_counts"] = [1, 1]
    manifest["chunk_count"] = 2
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest()
    write_managed_text(manifest_path, _canonical_text(manifest))


def _activate_previous_revision(
    prepared: PreparedFinalization,
) -> str:
    manifest = json.loads(
        (
            prepared.paths.semantic_final_dir / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    previous_id = "previous-active-v1"
    repository = SQLiteRepository(prepared.paths.db_path)
    repository.create_semantic_revision(
        previous_id,
        manifest["source_id"],
        manifest["policy_hash"],
        "semantic-input-v1",
    )
    repository.activate_semantic_revision(previous_id)
    return previous_id


def _rewrite_final_chunk_and_manifest(
    prepared: PreparedFinalization,
    mutate,
) -> None:
    chunk_path = prepared.paths.semantic_final_dir / "chunk-0001.json"
    chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
    mutate(chunk)
    chunk_text = _canonical_text(chunk)
    write_managed_text(chunk_path, chunk_text)

    manifest_path = prepared.paths.semantic_final_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chunk_sha256s"][0] = hashlib.sha256(
        chunk_text.encode("utf-8")
    ).hexdigest()
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest()
    write_managed_text(manifest_path, _canonical_text(manifest))


def test_interrupted_semantic_import_resumes_without_replacing_active(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, root = _prepare_completed_v2(
        workspace,
        tmp_path,
        split_chunks=True,
    )
    previous_id = _activate_previous_revision(prepared)
    imported: list[int] = []
    original = SQLiteRepository.import_semantic_revision_chunk

    def interrupt_after_one(self, *args, **kwargs):
        chunk_index = kwargs.get("chunk_index", args[1] if len(args) > 1 else None)
        if imported:
            raise KeyboardInterrupt
        result = original(self, *args, **kwargs)
        imported.append(int(chunk_index))
        return result

    monkeypatch.setattr(
        SQLiteRepository,
        "import_semantic_revision_chunk",
        interrupt_after_one,
    )
    with pytest.raises(KeyboardInterrupt):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    active = repository.get_active_semantic_revision(
        json.loads(
            (
                prepared.paths.semantic_final_dir / "manifest.json"
            ).read_text(encoding="utf-8")
        )["source_id"]
    )
    assert active is not None
    assert active["id"] == previous_id
    assert len(repository.list_semantic_import_receipts(prepared.revision_id)) == 1

    monkeypatch.setattr(
        SQLiteRepository,
        "import_semantic_revision_chunk",
        original,
    )
    result = apply_semantic_index(
        ApplySemanticIndexRequest(workspace=workspace)
    )

    assert result.active is True
    assert result.revision_id == prepared.revision_id
    assert len(repository.list_semantic_import_receipts(prepared.revision_id)) == 2
    receipt_text = prepared.paths.semantic_activation_receipt_path.read_text(
        encoding="utf-8"
    )
    receipt = json.loads(receipt_text)
    assert set(receipt) == {
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
    assert receipt["revision_id"] == prepared.revision_id
    assert receipt["active"] is True
    assert str(root) not in receipt_text
    assert "file://" not in receipt_text
    assert "portfolio.db" not in receipt_text


def test_previous_activation_receipt_is_preserved_until_target_commit(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, _ = _prepare_completed_v2(
        workspace,
        tmp_path,
        split_chunks=True,
    )
    previous_id = _activate_previous_revision(prepared)
    manifest = json.loads(
        (
            prepared.paths.semantic_final_dir / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    previous_receipt = {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": previous_id,
        "source_id": manifest["source_id"],
        "policy_hash": manifest["policy_hash"],
        "final_manifest_sha256": "a" * 64,
        "chunk_count": 1,
        "node_count": 1,
        "active": True,
    }
    previous_text = _canonical_text(previous_receipt)
    write_managed_text(
        prepared.paths.semantic_activation_receipt_path,
        previous_text,
    )
    original = SQLiteRepository.import_semantic_revision_chunk
    import_count = 0

    def interrupt_second_chunk(self, *args, **kwargs):
        nonlocal import_count
        import_count += 1
        if import_count == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        SQLiteRepository,
        "import_semantic_revision_chunk",
        interrupt_second_chunk,
    )
    with pytest.raises(KeyboardInterrupt):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    assert (
        prepared.paths.semantic_activation_receipt_path.read_text(
            encoding="utf-8"
        )
        == previous_text
    )
    repository = SQLiteRepository(prepared.paths.db_path)
    assert repository.get_active_semantic_revision(manifest["source_id"])[
        "id"
    ] == previous_id

    monkeypatch.setattr(
        SQLiteRepository,
        "import_semantic_revision_chunk",
        original,
    )
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    current_receipt = json.loads(
        prepared.paths.semantic_activation_receipt_path.read_text(
            encoding="utf-8"
        )
    )
    assert current_receipt["revision_id"] == prepared.revision_id
    assert current_receipt != previous_receipt


@pytest.mark.parametrize(
    "mutation",
    (
        "manifest-hash",
        "final-chunk",
        "missing-locator",
        "alternate-root",
        "locator-symlink",
        "negative-device",
        "undeclared-locator",
        "locator-hardlink",
    ),
)
def test_v2_apply_rejects_final_or_locator_drift_before_database_mutation(
    workspace: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    prepared, root = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)
    manifest_path = prepared.paths.semantic_final_dir / "manifest.json"
    chunk_path = prepared.paths.semantic_final_dir / "chunk-0001.json"
    locator_dir = prepared.paths.semantic_revision_locator_dir(
        prepared.revision_id
    )
    locator_path = locator_dir / "chunk-0001.json"
    if mutation == "manifest-hash":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["node_count"] += 1
        write_managed_text(manifest_path, _canonical_text(manifest))
    elif mutation == "final-chunk":
        chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        chunk["nodes"][0]["display_name"] = "changed"
        write_managed_text(chunk_path, _canonical_text(chunk))
    elif mutation == "missing-locator":
        remove_managed_file(locator_path)
    elif mutation == "alternate-root":
        locator = json.loads(locator_path.read_text(encoding="utf-8"))
        locator["locators"][0]["locator"] = str(tmp_path / "alternate-root")
        write_managed_text(locator_path, _canonical_text(locator))
    elif mutation == "locator-symlink":
        external = tmp_path / "external-locator.json"
        external.write_text(locator_path.read_text(encoding="utf-8"), encoding="utf-8")
        remove_managed_file(locator_path)
        locator_path.symlink_to(external)
    elif mutation == "negative-device":
        locator = json.loads(locator_path.read_text(encoding="utf-8"))
        locator["locators"][0]["device"] = -1
        write_managed_text(locator_path, _canonical_text(locator))
    elif mutation == "undeclared-locator":
        (locator_dir / "notes.json").write_text("{}", encoding="utf-8")
    else:
        external = tmp_path / "external-locator.json"
        external.write_text(locator_path.read_text(encoding="utf-8"), encoding="utf-8")
        remove_managed_file(locator_path)
        os.link(external, locator_path)

    with pytest.raises(SemanticIndexError):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    manifest = json.loads(
        prepared.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    active = repository.get_active_semantic_revision(manifest["source_id"])
    assert active is not None
    assert active["id"] == previous_id
    assert repository.list_semantic_import_receipts(prepared.revision_id) == ()
    assert not prepared.paths.semantic_activation_receipt_path.exists()
    assert root.exists()


@pytest.mark.parametrize(
    "mutation",
    ("aggregate", "empty-grounding", "reordered"),
)
def test_v2_apply_rejects_rehashed_invalid_final_semantics(
    workspace: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)

    def mutate(chunk: dict[str, object]) -> None:
        nodes = chunk["nodes"]
        assert isinstance(nodes, list)
        if mutation == "aggregate":
            nodes[1]["result_kind"] = "aggregate"
        elif mutation == "empty-grounding":
            nodes[1]["semantic_facts"]["grounding_signal_ids"] = []
        else:
            nodes.reverse()

    _rewrite_final_chunk_and_manifest(prepared, mutate)

    with pytest.raises(SemanticIndexError):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    manifest = json.loads(
        prepared.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    assert repository.get_active_semantic_revision(manifest["source_id"])[
        "id"
    ] == previous_id
    assert repository.list_semantic_import_receipts(prepared.revision_id) == ()


def test_v2_apply_rejects_parent_child_drift_without_replacing_active(
    workspace: Path,
    tmp_path: Path,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)

    def remove_root_child(chunk: dict[str, object]) -> None:
        nodes = chunk["nodes"]
        assert isinstance(nodes, list)
        nodes[0]["child_node_ids"] = []

    _rewrite_final_chunk_and_manifest(prepared, remove_root_child)

    with pytest.raises(SemanticIndexError):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    manifest = json.loads(
        prepared.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    assert repository.get_active_semantic_revision(manifest["source_id"])[
        "id"
    ] == previous_id
    assert len(repository.list_semantic_import_receipts(prepared.revision_id)) == 1


def test_v2_apply_rejects_rehashed_disconnected_self_cycle(
    workspace: Path,
    tmp_path: Path,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)

    def make_disconnected_self_cycle(chunk: dict[str, object]) -> None:
        nodes = chunk["nodes"]
        assert isinstance(nodes, list) and len(nodes) == 2
        root, child = nodes
        root["child_node_ids"] = []
        child["parent_node_id"] = child["node_id"]
        child["child_node_ids"] = [child["node_id"]]

    _rewrite_final_chunk_and_manifest(
        prepared,
        make_disconnected_self_cycle,
    )

    with pytest.raises(SemanticIndexError):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    manifest = json.loads(
        prepared.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    active = repository.get_active_semantic_revision(manifest["source_id"])
    assert active is not None
    assert active["id"] == previous_id
    with repository._read_connection() as connection:
        status = connection.execute(
            "SELECT status FROM semantic_index_revisions WHERE id = ?",
            (prepared.revision_id,),
        ).fetchone()["status"]
    assert status == "staging"
    assert not prepared.paths.semantic_activation_receipt_path.exists()


def test_v2_apply_rejects_malformed_work_plan_binding_before_import(
    workspace: Path,
    tmp_path: Path,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)
    plan = json.loads(
        prepared.paths.semantic_work_plan_path.read_text(encoding="utf-8")
    )
    plan["units"][0]["template_sha256"] = "invalid"
    plan_text = _canonical_text(plan)
    write_managed_text(prepared.paths.semantic_work_plan_path, plan_text)
    plan_hash = hashlib.sha256(plan_text.encode("utf-8")).hexdigest()

    input_manifest = json.loads(
        prepared.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    input_manifest["work_plan_sha256"] = plan_hash
    unsigned_input_manifest = dict(input_manifest)
    unsigned_input_manifest.pop("manifest_sha256")
    input_manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned_input_manifest)
    ).hexdigest()
    write_managed_text(
        prepared.paths.semantic_index_manifest_path,
        _canonical_text(input_manifest),
    )
    final_manifest_path = prepared.paths.semantic_final_dir / "manifest.json"
    final_manifest = json.loads(
        final_manifest_path.read_text(encoding="utf-8")
    )
    final_manifest["work_plan_sha256"] = plan_hash
    final_manifest["input_manifest_sha256"] = input_manifest[
        "manifest_sha256"
    ]
    unsigned = {
        key: value
        for key, value in final_manifest.items()
        if key != "manifest_sha256"
    }
    final_manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest()
    write_managed_text(
        final_manifest_path,
        _canonical_text(final_manifest),
    )

    with pytest.raises(SemanticIndexError, match="work plan"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    active = repository.get_active_semantic_revision(
        final_manifest["source_id"]
    )
    assert active is not None
    assert active["id"] == previous_id


def test_v2_apply_rejects_rehashed_crawl_watchdog_binding_drift(
    workspace: Path,
    tmp_path: Path,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)
    manifest_path = prepared.paths.semantic_index_manifest_path
    input_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_manifest["timed_out_directory_count"] = 1
    input_manifest["unreadable_directory_count"] = 1
    unsigned = dict(input_manifest)
    unsigned.pop("manifest_sha256")
    input_manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest()
    write_managed_text(manifest_path, _canonical_text(input_manifest))

    with pytest.raises(SemanticIndexError, match="manifest"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    active = repository.get_active_semantic_revision(
        json.loads(
            (prepared.paths.semantic_final_dir / "manifest.json").read_text(
                encoding="utf-8"
            )
        )["source_id"]
    )
    assert active is not None
    assert active["id"] == previous_id
    assert repository.list_semantic_import_receipts(prepared.revision_id) == ()


def test_v2_apply_rejects_rehashed_receipt_binding_drift_before_import(
    workspace: Path,
    tmp_path: Path,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)
    receipt_path = next(prepared.paths.semantic_work_receipts_dir.iterdir())
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["output_sha256"] = "f" * 64
    unsigned_receipt = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_sha256"
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned_receipt)
    ).hexdigest()
    receipt_text = _canonical_text(receipt)
    write_managed_text(receipt_path, receipt_text)

    manifest_path = prepared.paths.semantic_final_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt_names = sorted(
        path.name
        for path in prepared.paths.semantic_work_receipts_dir.iterdir()
    )
    manifest["receipt_file_sha256s"] = [
        hashlib.sha256(
            (
                prepared.paths.semantic_work_receipts_dir / name
            ).read_bytes()
        ).hexdigest()
        for name in receipt_names
    ]
    unsigned_manifest = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned_manifest)
    ).hexdigest()
    write_managed_text(manifest_path, _canonical_text(manifest))

    with pytest.raises(SemanticIndexError, match="receipt"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    repository = SQLiteRepository(prepared.paths.db_path)
    assert repository.get_active_semantic_revision(manifest["source_id"])[
        "id"
    ] == previous_id
    assert repository.list_semantic_import_receipts(prepared.revision_id) == ()


def test_apply_repairs_activation_receipt_without_reimport_after_db_commit(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    original_write = semantic_index_module.write_managed_text

    def fail_activation_receipt(path: Path, content: str, **kwargs):
        if path == prepared.paths.semantic_activation_receipt_path:
            raise OSError("synthetic receipt interruption")
        return original_write(path, content, **kwargs)

    monkeypatch.setattr(
        semantic_index_module,
        "write_managed_text",
        fail_activation_receipt,
    )
    with pytest.raises(SemanticIndexError, match="activation receipt"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    manifest = json.loads(
        (
            prepared.paths.semantic_final_dir / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    repository = SQLiteRepository(prepared.paths.db_path)
    assert repository.get_active_semantic_revision(manifest["source_id"])[
        "id"
    ] == prepared.revision_id
    assert not prepared.paths.semantic_activation_receipt_path.exists()

    monkeypatch.setattr(
        semantic_index_module,
        "write_managed_text",
        original_write,
    )

    def unexpected_import(*args, **kwargs):
        raise AssertionError("active replay must not reimport chunks")

    monkeypatch.setattr(
        SQLiteRepository,
        "import_semantic_revision_chunk",
        unexpected_import,
    )
    result = apply_semantic_index(
        ApplySemanticIndexRequest(workspace=workspace)
    )
    assert result.active is True
    assert prepared.paths.semantic_activation_receipt_path.is_file()


def test_v2_apply_preserves_and_rejects_symlinked_activation_receipt(
    workspace: Path,
    tmp_path: Path,
) -> None:
    prepared, _ = _prepare_completed_v2(workspace, tmp_path)
    previous_id = _activate_previous_revision(prepared)
    external = tmp_path / "external-activation-receipt.json"
    external.write_text("{}", encoding="utf-8")
    prepared.paths.semantic_activation_receipt_path.symlink_to(external)

    with pytest.raises(SemanticIndexError, match="activation receipt"):
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

    assert prepared.paths.semantic_activation_receipt_path.is_symlink()
    assert external.read_text(encoding="utf-8") == "{}"
    repository = SQLiteRepository(prepared.paths.db_path)
    manifest = json.loads(
        prepared.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    assert repository.get_active_semantic_revision(manifest["source_id"])[
        "id"
    ] == previous_id
    assert repository.list_semantic_import_receipts(prepared.revision_id) == ()


def test_cli_v2_apply_reports_only_controlled_public_status(
    workspace: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared, root = _prepare_completed_v2(workspace, tmp_path)

    assert main(
        ["apply-semantic-index", "--workspace", str(workspace)]
    ) == 0

    captured = capsys.readouterr()
    assert captured.out == "Semantic index applied.\n"
    assert captured.err == ""
    assert str(root) not in captured.out
    assert str(workspace) not in captured.out
    assert "file://" not in captured.out
    assert prepared.revision_id not in captured.out


def test_cli_v2_apply_failure_does_not_disclose_private_locator(
    workspace: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared, root = _prepare_completed_v2(workspace, tmp_path)
    locator_path = (
        prepared.paths.semantic_revision_locator_dir(prepared.revision_id)
        / "chunk-0001.json"
    )
    locator = json.loads(locator_path.read_text(encoding="utf-8"))
    private_locator = str(tmp_path / "private-alternate-root")
    locator["locators"][0]["locator"] = private_locator
    write_managed_text(locator_path, _canonical_text(locator))

    assert main(
        ["apply-semantic-index", "--workspace", str(workspace)]
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "semantic private locator state is invalid\n"
    combined = captured.out + captured.err
    assert private_locator not in combined
    assert str(root) not in combined
    assert str(workspace) not in combined
    assert prepared.revision_id not in combined
    assert "file://" not in combined
