from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from urllib.parse import unquote, urlparse

from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError, mask_secrets


EXTRACTOR_VERSION = "text-v2"
_VISION_READ_CHUNK_SIZE = 65_536


@dataclass(frozen=True)
class ExtractedText:
    text: str
    content_hash: str
    extractor: str


@dataclass(frozen=True)
class ApprovedVisionSource:
    path: Path
    content_hash: str
    byte_size: int
    device: int
    inode: int


def extract_text(path: Path) -> ExtractedText:
    return _extract_raw(path.read_bytes())


def extract_approved_text(uri: str, policy: FilePolicy) -> tuple[Path, ExtractedText]:
    path, descriptor = _open_approved_regular_file(uri, policy)
    try:
        stat = os.fstat(descriptor)
        if not S_ISREG(stat.st_mode):
            raise SourcePathPolicyError("Approved source must be a regular file")
        if stat.st_size > policy.max_file_size_bytes:
            raise SourcePathPolicyError("Approved source exceeds the size limit")
        raw = _read_descriptor(descriptor, stat.st_size)
    finally:
        os.close(descriptor)
    return path, _extract_raw(raw)


def validate_approved_vision_source(
    uri: str,
    policy: FilePolicy,
) -> ApprovedVisionSource:
    path, descriptor = _open_approved_regular_file(uri, policy)
    try:
        before = os.fstat(descriptor)
        if not S_ISREG(before.st_mode):
            raise SourcePathPolicyError("Approved source must be a regular file")
        digest = hashlib.sha256()
        byte_size = 0
        while chunk := os.read(descriptor, _VISION_READ_CHUNK_SIZE):
            digest.update(chunk)
            byte_size += len(chunk)
        after = os.fstat(descriptor)
        if (
            not S_ISREG(after.st_mode)
            or _file_identity(before) != _file_identity(after)
            or byte_size != after.st_size
        ):
            raise SourcePathPolicyError("Approved source changed while it was read")
        return ApprovedVisionSource(
            path=path,
            content_hash=digest.hexdigest(),
            byte_size=byte_size,
            device=after.st_dev,
            inode=after.st_ino,
        )
    finally:
        os.close(descriptor)


def _open_approved_regular_file(uri: str, policy: FilePolicy) -> tuple[Path, int]:
    path = _approved_source_path(uri, policy)
    return path, _open_regular_file(path)


def _approved_source_path(uri: str, policy: FilePolicy) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc:
        raise SourcePathPolicyError("Approved source must use a local file URI")

    path = Path(unquote(parsed.path))
    if (
        parsed.netloc
        or not path.is_absolute()
        or str(path) != os.path.normpath(str(path))
        or path.as_uri() != uri
    ):
        raise SourcePathPolicyError("Approved source URI is not canonical")
    if policy.classify_path(path) != "candidate":
        raise SourcePathPolicyError("Approved source is blocked by file policy")
    return path


def _open_regular_file(path: Path) -> int:
    components = path.parts[1:]
    if not components:
        raise SourcePathPolicyError("Approved source must be a regular file")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    directory_descriptor = os.open(path.anchor, directory_flags)
    try:
        for component in components[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_descriptor)
            try:
                if not S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise SourcePathPolicyError("Approved source parent must be a directory")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        filename = components[-1]
        before_open = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if not S_ISREG(before_open.st_mode):
            raise SourcePathPolicyError("Approved source must be a regular file")
        return os.open(
            filename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
    finally:
        os.close(directory_descriptor)


def _read_descriptor(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(remaining, 65_536))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _extract_raw(raw: bytes) -> ExtractedText:
    return ExtractedText(
        text=mask_secrets(raw.decode("utf-8", errors="replace")),
        content_hash=hashlib.sha256(raw).hexdigest(),
        extractor=EXTRACTOR_VERSION,
    )
