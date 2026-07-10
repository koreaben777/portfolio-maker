from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from urllib.parse import unquote, urlparse

from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError, mask_secrets


EXTRACTOR_VERSION = "text-v2"


@dataclass(frozen=True)
class ExtractedText:
    text: str
    content_hash: str
    extractor: str


def extract_text(path: Path) -> ExtractedText:
    return _extract_raw(path.read_bytes())


def extract_approved_text(uri: str, policy: FilePolicy) -> tuple[Path, ExtractedText]:
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

    descriptor = _open_regular_file(path)
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


def _extract_raw(raw: bytes) -> ExtractedText:
    return ExtractedText(
        text=mask_secrets(raw.decode("utf-8", errors="replace")),
        content_hash=hashlib.sha256(raw).hexdigest(),
        extractor=EXTRACTOR_VERSION,
    )
