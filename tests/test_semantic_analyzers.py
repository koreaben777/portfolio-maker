from __future__ import annotations

import hashlib
from pathlib import Path

from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_analyzers import analyze_file_input
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
