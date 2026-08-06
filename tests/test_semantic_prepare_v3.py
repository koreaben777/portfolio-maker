from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_source_reader as source_reader
from portfolio_maker.infrastructure.policy import SourcePathPolicyError
from portfolio_maker.infrastructure.semantic_source_reader import (
    SemanticSourceChangedError,
    read_verified_representative,
)


def test_verified_representative_uses_same_descriptor_for_hash_and_slice(
    tmp_path: Path,
) -> None:
    """Breaks if the hash and sample are read through separate path opens."""
    source = tmp_path / "source.md"
    source.write_text("alpha\nbeta\ngamma", encoding="utf-8")

    observed = read_verified_representative(
        source,
        ranges=((0, 5),),
        max_materialized_bytes=64,
    )

    assert observed.full_content_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert observed.slices == (b"alpha",)
    assert observed.pre_identity == observed.post_identity


def test_verified_representative_rejects_same_inode_tail_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if post-read identity ignores an in-place tail rewrite."""
    source = tmp_path / "source.md"
    source.write_bytes(b"alpha\nbeta\ngamma")
    original = source_reader._hash_and_collect_ranges

    def rewrite_tail_after_read(
        descriptor: int,
        ranges: tuple[tuple[int, int], ...],
        max_materialized_bytes: int,
    ):
        result = original(descriptor, ranges, max_materialized_bytes)
        with source.open("r+b") as handle:
            handle.seek(-5, os.SEEK_END)
            handle.write(b"delta")
            handle.flush()
            os.fsync(handle.fileno())
        return result

    monkeypatch.setattr(source_reader, "_hash_and_collect_ranges", rewrite_tail_after_read)

    with pytest.raises(SemanticSourceChangedError, match="changed during verified read"):
        read_verified_representative(
            source,
            ranges=((0, 5),),
            max_materialized_bytes=64,
        )


def test_verified_representative_does_not_follow_symbolic_link(tmp_path: Path) -> None:
    """Breaks if the verified reader follows a replaced representative path."""
    target = tmp_path / "target.md"
    target.write_text("outside", encoding="utf-8")
    link = tmp_path / "source.md"
    link.symlink_to(target)

    with pytest.raises(SourcePathPolicyError):
        read_verified_representative(
            link,
            ranges=((0, 5),),
            max_materialized_bytes=64,
        )
