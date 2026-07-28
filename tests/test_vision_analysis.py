from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.extractors as extractors
import portfolio_maker.application.vision_analysis as vision_analysis
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import PrepareVisionAnalysisRequest
from portfolio_maker.application.vision_analysis import (
    VisionAnalysisError,
    prepare_vision_analysis,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.domain.vision_models import VisionRequestStatus
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def approve_binary_source(
    workspace: Path,
    source_path: Path,
    content: bytes,
    *,
    display_name: str | None = None,
    approval_overrides: dict[str, object] | None = None,
) -> tuple[WorkspacePaths, int]:
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(content)
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    approval.update(approval_overrides or {})
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name=display_name or source_path.name,
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    return paths, source_id


def request_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM vision_analysis_requests").fetchone()[0]
        )


def test_prepare_requires_confirmation_without_side_effect(workspace, tmp_path):
    paths, source_id = approve_binary_source(
        workspace, tmp_path / "resume.pdf", b"%PDF-synthetic"
    )
    repository = SQLiteRepository(paths.db_path)

    with pytest.raises(VisionAnalysisError, match="confirmation is required"):
        prepare_vision_analysis(
            PrepareVisionAnalysisRequest(workspace, source_id, False)
        )

    assert request_count(repository) == 0
    assert list(paths.vision_requests_dir.iterdir()) == []


@pytest.mark.parametrize("suffix", (".pdf", ".bin"))
def test_prepare_accepts_approved_binary_regardless_of_extension(
    workspace, tmp_path, suffix
):
    _, source_id = approve_binary_source(
        workspace, tmp_path / f"approved{suffix}", b"\x00binary\xffcontent"
    )

    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )

    assert prepared.status is VisionRequestStatus.PENDING


def test_prepare_streams_large_binary_without_text_limit_or_path_read_bytes(
    workspace, tmp_path, monkeypatch
):
    content = b"x" * 2_000_001
    _, source_id = approve_binary_source(workspace, tmp_path / "large.dat", content)
    original_read = extractors.os.read
    reads: list[int] = []

    def bounded_read(descriptor: int, size: int) -> bytes:
        reads.append(size)
        return original_read(descriptor, size)

    def no_path_read_bytes(_path: Path) -> bytes:
        raise AssertionError("vision preparation must stream the descriptor")

    monkeypatch.setattr(extractors.os, "read", bounded_read)
    monkeypatch.setattr(Path, "read_bytes", no_path_read_bytes)

    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )

    assert prepared.source_size == len(content)
    assert prepared.source_content_hash == hashlib.sha256(content).hexdigest()
    assert len(reads) > 2
    assert max(reads) <= 65_536


def test_prepare_rejects_final_symlink(workspace, tmp_path):
    source_path = tmp_path / "source.pdf"
    _, source_id = approve_binary_source(workspace, source_path, b"approved")
    replacement = tmp_path / "replacement.pdf"
    replacement.write_bytes(b"replacement")
    source_path.unlink()
    source_path.symlink_to(replacement)

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


def test_prepare_rejects_symlinked_parent(workspace, tmp_path):
    source_parent = tmp_path / "approved"
    source_path = source_parent / "source.bin"
    _, source_id = approve_binary_source(workspace, source_path, b"approved")
    replacement_parent = tmp_path / "replacement"
    replacement_parent.mkdir()
    (replacement_parent / "source.bin").write_bytes(b"replacement")
    source_parent.rename(tmp_path / "approved-original")
    source_parent.symlink_to(replacement_parent, target_is_directory=True)

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


def test_prepare_rejects_source_mutation_during_descriptor_stream(
    workspace, tmp_path, monkeypatch
):
    source_path = tmp_path / "changing.bin"
    _, source_id = approve_binary_source(workspace, source_path, b"a" * 131_072)
    original_read = extractors.os.read
    source_stat = source_path.stat()
    changed = False

    def mutate_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        descriptor_stat = os.fstat(descriptor)
        if (
            chunk
            and not changed
            and (descriptor_stat.st_dev, descriptor_stat.st_ino)
            == (source_stat.st_dev, source_stat.st_ino)
        ):
            source_path.write_bytes(b"b" * 131_073)
            changed = True
        return chunk

    monkeypatch.setattr(extractors.os, "read", mutate_after_first_read)

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


@pytest.mark.parametrize(
    ("source_kind", "approval_overrides", "filename"),
    (
        ("missing", {}, "missing.bin"),
        ("github", {}, "source.bin"),
        ("unapproved", {}, "source.bin"),
        ("forbidden", {"excluded_directories": ["blocked"]}, "blocked/source.bin"),
        ("excluded", {"excluded_file_patterns": ["*.bin"]}, "source.bin"),
        ("sensitive", {}, "credentials.json"),
    ),
)
def test_prepare_rejects_invalid_current_source_policy(
    workspace, tmp_path, source_kind, approval_overrides, filename
):
    paths = WorkspacePaths.from_root(workspace)
    if source_kind == "missing":
        write_sample_approval(paths)
        source_id = 999
    elif source_kind == "github":
        write_sample_approval(paths)
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        source_id = repository.upsert_source(
            Source(
                id=None,
                type=SourceType.GITHUB_REPOSITORY,
                uri="https://github.com/example/synthetic",
                display_name="example/synthetic",
                owner="example",
                status=SourceStatus.APPROVED,
            )
        )
    else:
        source_path = tmp_path / filename
        paths, source_id = approve_binary_source(
            workspace,
            source_path,
            b"approved",
            approval_overrides=approval_overrides,
        )
        if source_kind == "unapproved":
            approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
            approval["approved_source_uris"] = []
            paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


def test_prepare_persists_matching_private_request_and_safe_receipt(
    workspace, tmp_path
):
    source_path = tmp_path / "private" / "slide.pdf"
    content = b"synthetic binary data"
    paths, source_id = approve_binary_source(
        workspace,
        source_path,
        content,
        display_name="/untrusted/private/display-name",
    )

    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )

    row = SQLiteRepository(paths.db_path).get_vision_request(prepared.id)
    receipt_path = paths.vision_requests_dir / f"request-{prepared.id}.json"
    receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert row["id"] == prepared.id
    assert row["source_id"] == source_id
    assert row["source_content_hash"] == prepared.source_content_hash
    assert row["source_size"] == prepared.source_size
    assert row["adapter"] == prepared.adapter
    assert row["status"] == "pending"
    assert row["requested_at"] == prepared.requested_at
    assert set(receipt) == {
        "request_id",
        "source_id",
        "display_name",
        "source_content_hash",
        "source_size",
        "adapter",
        "status",
        "confirmation_at",
        "requested_at",
    }
    assert receipt == {
        "request_id": prepared.id,
        "source_id": source_id,
        "display_name": source_path.name,
        "source_content_hash": hashlib.sha256(content).hexdigest(),
        "source_size": len(content),
        "adapter": "codex-app-vision-v1",
        "status": "pending",
        "confirmation_at": prepared.confirmation_at,
        "requested_at": prepared.requested_at,
    }
    assert source_path.resolve().as_uri() not in receipt_text
    assert str(source_path.resolve()) not in receipt_text
    assert content.decode("utf-8") not in receipt_text
    assert "device" not in receipt_text
    assert "inode" not in receipt_text
    assert "/untrusted/private/display-name" not in receipt_text


@pytest.mark.parametrize("unsafe_target", (False, True))
def test_prepare_does_not_overwrite_existing_or_unsafe_receipt_target(
    workspace, tmp_path, monkeypatch, unsafe_target
):
    paths, source_id = approve_binary_source(workspace, tmp_path / "source.pdf", b"approved")
    request_id = "request-collision"
    target = paths.vision_requests_dir / f"request-{request_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"existing": true}', encoding="utf-8")
    if unsafe_target:
        replacement = tmp_path / "replacement.json"
        replacement.write_text('{"replacement": true}', encoding="utf-8")
        target.unlink()
        target.symlink_to(replacement)

    def fixed_request(_cls, _source_id, content_hash, size):
        from portfolio_maker.domain.vision_models import PreparedVisionRequest

        return PreparedVisionRequest(
            request_id,
            _source_id,
            content_hash,
            size,
            "codex-app-vision-v1",
            VisionRequestStatus.PENDING,
            "2026-07-28T00:00:00Z",
            "2026-07-28T00:00:01Z",
        )

    monkeypatch.setattr(
        vision_analysis.PreparedVisionRequest,
        "new",
        classmethod(fixed_request),
    )

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))

    if unsafe_target:
        assert target.is_symlink()
    else:
        assert target.read_text(encoding="utf-8") == '{"existing": true}'
