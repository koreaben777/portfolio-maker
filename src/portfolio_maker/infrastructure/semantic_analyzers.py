from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass

from portfolio_maker.domain.semantic_frontier import BaselineCoverage
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.extractors import _open_regular_file
from portfolio_maker.infrastructure.policy import SourcePathPolicyError
from portfolio_maker.infrastructure.policy import mask_secrets
from portfolio_maker.infrastructure.semantic_crawler import StructuralEntry
from portfolio_maker.infrastructure.semantic_source_reader import (
    SemanticSourceReadError,
    SemanticSourceChangedError,
    read_verified_representative,
)


ANALYZER_VERSION = "semantic-input-v2"
ROLE_BY_NAME = {
    "readme.md": ("documentation", "project-description"),
    "dockerfile": ("configuration", "deployment"),
    "pyproject.toml": ("configuration", "package-manifest"),
    "package.json": ("configuration", "package-manifest"),
}
ROLE_BY_SUFFIX = {
    ".md": ("documentation",),
    ".py": ("code",),
    ".toml": ("configuration",),
    ".yaml": ("configuration",),
    ".yml": ("configuration",),
}


@dataclass(frozen=True)
class FileAnalysisInput:
    node_id: str
    content_fingerprint: str | None
    masked_excerpt: str
    semantic_roles: tuple[str, ...]
    status: AnalysisStatus


@dataclass(frozen=True)
class LocalBaseline:
    node_id: str
    coverage: BaselineCoverage
    roles: tuple[str, ...]
    topics: tuple[str, ...]
    inspected_bytes: int
    sampled_ranges: tuple[tuple[int, int], ...]
    sample_sha256: str | None
    full_content_sha256: str | None
    masked_excerpt: str
    source_changed: bool = False


def analyze_file_input(
    entry: StructuralEntry, max_bytes: int = 131_072
) -> FileAnalysisInput:
    if max_bytes < 0:
        raise ValueError("max_bytes must not be negative")

    if entry.kind != SemanticNodeKind.FILE:
        return _metadata_only(entry, AnalysisStatus.UNSUPPORTED)

    if entry.status != AnalysisStatus.PENDING:
        return _metadata_only(entry, entry.status)

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return _metadata_only(entry, AnalysisStatus.UNSUPPORTED)

    try:
        descriptor = _open_regular_file(entry.absolute_path)
    except SourcePathPolicyError:
        return _metadata_only(entry, AnalysisStatus.UNSUPPORTED)
    except OSError as error:
        status = (
            AnalysisStatus.UNSUPPORTED
            if error.errno in {errno.ELOOP, errno.ENOTDIR}
            else AnalysisStatus.UNREADABLE
        )
        return _metadata_only(entry, status)

    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            return _metadata_only(entry, AnalysisStatus.UNSUPPORTED)
        if not _matches_entry_identity(entry, opened_stat):
            return _metadata_only(entry, AnalysisStatus.UNREADABLE)
        content = os.read(descriptor, max_bytes)
    except OSError:
        return _metadata_only(entry, AnalysisStatus.UNREADABLE)
    finally:
        os.close(descriptor)

    content_fingerprint = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if b"\0" in content:
        return _metadata_only(
            entry,
            AnalysisStatus.UNSUPPORTED,
            content_fingerprint=content_fingerprint,
        )

    try:
        excerpt = content.decode("utf-8")
    except UnicodeDecodeError:
        return _metadata_only(
            entry,
            AnalysisStatus.UNSUPPORTED,
            content_fingerprint=content_fingerprint,
        )

    return FileAnalysisInput(
        node_id=entry.node_id,
        content_fingerprint=content_fingerprint,
        masked_excerpt=mask_secrets(excerpt),
        semantic_roles=_infer_roles(entry.absolute_path.name),
        status=(
            AnalysisStatus.PARTIAL
            if opened_stat.st_size > len(content)
            else AnalysisStatus.COMPLETE
        ),
    )


def analyze_local_baseline(
    entry: StructuralEntry,
    *,
    max_bytes: int = 131_072,
) -> LocalBaseline:
    """Return explicit bounded coverage without treating a prefix as full identity."""
    if max_bytes < 0:
        raise ValueError("max_bytes must not be negative")
    if entry.status is AnalysisStatus.UNSUPPORTED:
        return _baseline_metadata_only(entry, BaselineCoverage.UNSUPPORTED)
    if entry.status is AnalysisStatus.UNREADABLE:
        return _baseline_metadata_only(entry, BaselineCoverage.UNREADABLE)
    if entry.status is AnalysisStatus.FAILED:
        return _baseline_metadata_only(entry, BaselineCoverage.FAILED)
    if entry.kind is not SemanticNodeKind.FILE:
        return _baseline_metadata_only(entry, BaselineCoverage.COMPLETE)

    try:
        representative = read_verified_representative(
            entry.absolute_path,
            ranges=((0, max_bytes),),
            max_materialized_bytes=max_bytes,
        )
    except SemanticSourceChangedError:
        return _baseline_metadata_only(
            entry,
            BaselineCoverage.FAILED,
            source_changed=True,
        )
    except SourcePathPolicyError:
        return _baseline_metadata_only(entry, BaselineCoverage.UNSUPPORTED)
    except SemanticSourceReadError:
        return _baseline_metadata_only(entry, BaselineCoverage.UNREADABLE)

    content = representative.slices[0]
    if b"\0" in content:
        return _baseline_metadata_only(entry, BaselineCoverage.UNSUPPORTED)
    try:
        excerpt = content.decode("utf-8")
    except UnicodeDecodeError:
        return _baseline_metadata_only(entry, BaselineCoverage.UNSUPPORTED)
    inspected_bytes = len(content)
    sampled = representative.pre_identity[2] > inspected_bytes
    sample_sha256 = hashlib.sha256(content).hexdigest()
    return LocalBaseline(
        node_id=entry.node_id,
        coverage=(BaselineCoverage.SAMPLED if sampled else BaselineCoverage.COMPLETE),
        roles=_infer_roles(entry.absolute_path.name),
        topics=(),
        inspected_bytes=inspected_bytes,
        sampled_ranges=((0, inspected_bytes),) if sampled else (),
        sample_sha256=sample_sha256 if sampled else None,
        full_content_sha256=(
            None if sampled else representative.full_content_sha256
        ),
        masked_excerpt=mask_secrets(excerpt),
    )


def _metadata_only(
    entry: StructuralEntry,
    status: AnalysisStatus,
    *,
    content_fingerprint: str | None = None,
) -> FileAnalysisInput:
    return FileAnalysisInput(
        node_id=entry.node_id,
        content_fingerprint=content_fingerprint,
        masked_excerpt="",
        semantic_roles=_infer_roles(entry.absolute_path.name),
        status=status,
    )


def _baseline_metadata_only(
    entry: StructuralEntry,
    coverage: BaselineCoverage,
    *,
    source_changed: bool = False,
) -> LocalBaseline:
    return LocalBaseline(
        node_id=entry.node_id,
        coverage=coverage,
        roles=_infer_roles(entry.absolute_path.name),
        topics=(),
        inspected_bytes=0,
        sampled_ranges=(),
        sample_sha256=None,
        full_content_sha256=None,
        masked_excerpt="",
        source_changed=source_changed,
    )


def _matches_entry_identity(entry: StructuralEntry, path_stat: os.stat_result) -> bool:
    if entry.device is not None and path_stat.st_dev != entry.device:
        return False
    if entry.inode is not None and path_stat.st_ino != entry.inode:
        return False
    return True


def _infer_roles(filename: str) -> tuple[str, ...]:
    name = filename.casefold()
    if name in ROLE_BY_NAME:
        return ROLE_BY_NAME[name]
    return ROLE_BY_SUFFIX.get(os.path.splitext(name)[1], ())
