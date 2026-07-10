from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
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
    if policy.classify_path(path) != "candidate":
        raise SourcePathPolicyError("Approved source is blocked by file policy")
    if path.resolve(strict=True).as_uri() != uri:
        raise SourcePathPolicyError("Approved source URI is not canonical")

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
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
