from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass

from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.extractors import _open_regular_file
from portfolio_maker.infrastructure.policy import SourcePathPolicyError
from portfolio_maker.infrastructure.policy import mask_secrets
from portfolio_maker.infrastructure.semantic_crawler import StructuralEntry


ANALYZER_VERSION = "semantic-input-v1"
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
