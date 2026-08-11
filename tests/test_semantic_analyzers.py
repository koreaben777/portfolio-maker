from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_analyzers as semantic_analyzers_module
import portfolio_maker.infrastructure.semantic_source_reader as source_reader
from portfolio_maker.domain.semantic_frontier import BaselineCoverage
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_analyzers import (
    analyze_file_input,
    analyze_local_baseline,
)
from portfolio_maker.infrastructure.semantic_crawler import StructuralEntry


def entry_for(path: Path) -> StructuralEntry:
    path_stat = path.lstat()
    return StructuralEntry(
        node_id="node-1",
        source_id="source-1",
        parent_node_id="parent-1",
        kind=SemanticNodeKind.FILE,
        display_name=path.name,
        relative_hierarchy=path.name,
        absolute_path=path,
        provider_item_key="item-1",
        content_fingerprint=None,
        device=path_stat.st_dev,
        inode=path_stat.st_ino,
        status=AnalysisStatus.PENDING,
    )


def test_python_analysis_input_is_masked_and_role_labeled(tmp_path: Path) -> None:
    path = tmp_path / "app.py"
    path.write_text("API_TOKEN=secret\ndef main(): pass\n", encoding="utf-8")

    result = analyze_file_input(entry_for(path))

    assert result.node_id == "node-1"
    assert "secret" not in result.masked_excerpt
    assert "code" in result.semantic_roles
    assert result.content_fingerprint.startswith("sha256:")
    assert result.status == AnalysisStatus.COMPLETE


def test_analysis_input_is_partial_when_content_exceeds_byte_cap(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"abcdef")

    result = analyze_file_input(entry_for(path), max_bytes=4)

    assert result.masked_excerpt == "abcd"
    assert result.content_fingerprint == f"sha256:{hashlib.sha256(b'abcd').hexdigest()}"
    assert result.status == AnalysisStatus.PARTIAL


def test_prefix_analysis_is_sampled_not_full_identity(tmp_path: Path) -> None:
    """Breaks if a bounded prefix is represented as an exhaustive file identity."""
    path = tmp_path / "large.md"
    path.write_bytes(b"a" * 131_072 + b"tail")

    baseline = analyze_local_baseline(entry_for(path))

    assert baseline.coverage is BaselineCoverage.SAMPLED
    assert baseline.sample_sha256 is not None
    assert baseline.full_content_sha256 is None


def test_local_baseline_reads_only_the_bounded_sample_of_a_large_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if baseline sampling regresses to an EOF hash/read loop."""
    path = tmp_path / "large.md"
    with path.open("wb") as handle:
        handle.write(b"a" * 4096)
        handle.truncate(1_048_576)
    original_read = source_reader.os.read
    observed_bytes = 0

    def record_read(descriptor: int, requested_bytes: int) -> bytes:
        nonlocal observed_bytes
        result = original_read(descriptor, requested_bytes)
        observed_bytes += len(result)
        return result

    monkeypatch.setattr(source_reader.os, "read", record_read)

    baseline = analyze_local_baseline(entry_for(path), max_bytes=4096)

    assert baseline.coverage is BaselineCoverage.SAMPLED
    assert baseline.inspected_bytes == 4096
    assert baseline.full_content_sha256 is None
    assert observed_bytes <= 4096


def test_baseline_uses_verified_bounded_reader_and_masks_managed_excerpt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if local baseline reopens the path outside its verified reader."""
    path = tmp_path / "note.md"
    path.write_text("API_TOKEN=secret", encoding="utf-8")
    entry = entry_for(path)
    calls = 0
    expected_identities: list[tuple[int | None, int | None]] = []
    original = semantic_analyzers_module.read_verified_sample

    def record_verified_read(
        source: Path,
        max_bytes: int,
        *,
        expected_device: int | None = None,
        expected_inode: int | None = None,
    ):
        nonlocal calls
        calls += 1
        expected_identities.append((expected_device, expected_inode))
        return original(
            source,
            max_bytes,
            expected_device=expected_device,
            expected_inode=expected_inode,
        )

    monkeypatch.setattr(
        semantic_analyzers_module,
        "read_verified_sample",
        record_verified_read,
    )

    baseline = analyze_local_baseline(entry)

    assert calls == 1
    assert expected_identities == [(entry.device, entry.inode)]
    assert "secret" not in baseline.masked_excerpt


def test_local_baseline_rejects_atomic_path_replacement_after_crawl_stat(
    tmp_path: Path,
) -> None:
    """Breaks if a new inode can be analyzed under the crawler's old identity."""
    path = tmp_path / "note.md"
    path.write_text("original evidence", encoding="utf-8")
    entry = entry_for(path)
    replacement = tmp_path / "replacement.md"
    replacement.write_text("replacement evidence", encoding="utf-8")
    os.replace(replacement, path)

    baseline = analyze_local_baseline(entry)

    assert baseline.coverage is BaselineCoverage.FAILED
    assert baseline.source_changed is True
    assert baseline.inspected_bytes == 0


def test_binary_input_is_metadata_only(tmp_path: Path) -> None:
    path = tmp_path / "archive.bin"
    path.write_bytes(b"\x00\x01binary")

    result = analyze_file_input(entry_for(path))

    assert result.node_id == "node-1"
    assert result.status == AnalysisStatus.UNSUPPORTED
    assert result.masked_excerpt == ""
    assert result.content_fingerprint.startswith("sha256:")


def test_analysis_does_not_follow_symbolic_links(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("API_TOKEN=secret\n", encoding="utf-8")
    link = tmp_path / "link.py"
    link.symlink_to(target)

    result = analyze_file_input(entry_for(link))

    assert result.status == AnalysisStatus.UNSUPPORTED
    assert result.masked_excerpt == ""
    assert result.content_fingerprint is None


def test_unsupported_regular_file_is_not_opened_for_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "excluded.py"
    path.write_text("API_TOKEN=synthetic-placeholder\n", encoding="utf-8")

    def fail_if_opened(*args: object, **kwargs: object) -> int:
        raise AssertionError("unsupported file must not be opened")

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_analyzers.os.open", fail_if_opened
    )

    result = analyze_file_input(
        replace(entry_for(path), status=AnalysisStatus.UNSUPPORTED)
    )

    assert result.status == AnalysisStatus.UNSUPPORTED
    assert result.masked_excerpt == ""
    assert result.content_fingerprint is None


def test_analysis_does_not_follow_ancestor_symlink_replaced_after_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    captured_parent = root / "captured"
    captured_parent.mkdir(parents=True)
    path = captured_parent / "note.py"
    path.write_text("API_TOKEN=synthetic-placeholder\n", encoding="utf-8")
    entry = entry_for(path)

    outside = tmp_path / "outside"
    outside.mkdir()
    os.link(path, outside / path.name)
    captured_parent.rename(tmp_path / "captured-original")
    captured_parent.symlink_to(outside, target_is_directory=True)

    original_read = os.read
    reads: list[int] = []

    def record_read(descriptor: int, max_bytes: int) -> bytes:
        reads.append(descriptor)
        return original_read(descriptor, max_bytes)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_analyzers.os.read", record_read
    )

    result = analyze_file_input(entry)

    assert reads == []
    assert result.status == AnalysisStatus.UNSUPPORTED
    assert result.masked_excerpt == ""
    assert result.content_fingerprint is None
