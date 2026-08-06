from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from portfolio_maker.infrastructure.extractors import _open_regular_file


_READ_CHUNK_BYTES = 65_536


class SemanticSourceChangedError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedRepresentative:
    full_content_sha256: str
    slices: tuple[bytes, ...]
    pre_identity: tuple[int, int, int, int, int]
    post_identity: tuple[int, int, int, int, int]


def read_verified_representative(
    path: Path,
    ranges: tuple[tuple[int, int], ...],
    max_materialized_bytes: int,
) -> VerifiedRepresentative:
    """Hash and sample one regular file through one no-follow descriptor."""
    _validate_ranges(ranges, max_materialized_bytes)
    descriptor = _open_regular_file(path)
    try:
        before = os.fstat(descriptor)
        if not S_ISREG(before.st_mode):
            raise SemanticSourceChangedError("semantic source is not a regular file")
        digest, slices = _hash_and_collect_ranges(
            descriptor,
            ranges,
            max_materialized_bytes,
        )
        after = os.fstat(descriptor)
        if file_identity(before) != file_identity(after):
            raise SemanticSourceChangedError("semantic source changed during verified read")
        return VerifiedRepresentative(
            full_content_sha256=digest.hexdigest(),
            slices=slices,
            pre_identity=file_identity(before),
            post_identity=file_identity(after),
        )
    finally:
        os.close(descriptor)


def _hash_and_collect_ranges(
    descriptor: int,
    ranges: tuple[tuple[int, int], ...],
    max_materialized_bytes: int,
) -> tuple[hashlib._Hash, tuple[bytes, ...]]:  # type: ignore[name-defined]
    buffers = [bytearray() for _ in ranges]
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
        digest.update(chunk)
        chunk_end = offset + len(chunk)
        for index, (start, end) in enumerate(ranges):
            overlap_start = max(start, offset)
            overlap_end = min(end, chunk_end)
            if overlap_start < overlap_end:
                buffers[index].extend(
                    chunk[overlap_start - offset : overlap_end - offset]
                )
        offset = chunk_end
    slices = tuple(bytes(buffer) for buffer in buffers)
    if sum(len(value) for value in slices) > max_materialized_bytes:
        raise ValueError("representative slices exceed max_materialized_bytes")
    return digest, slices


def file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _validate_ranges(
    ranges: tuple[tuple[int, int], ...],
    max_materialized_bytes: int,
) -> None:
    if type(max_materialized_bytes) is not int or max_materialized_bytes < 0:
        raise ValueError("max_materialized_bytes must be non-negative")
    requested_bytes = 0
    for interval in ranges:
        if (
            type(interval) is not tuple
            or len(interval) != 2
            or type(interval[0]) is not int
            or type(interval[1]) is not int
            or interval[0] < 0
            or interval[1] < interval[0]
        ):
            raise ValueError("representative range is invalid")
        requested_bytes += interval[1] - interval[0]
    if requested_bytes > max_materialized_bytes:
        raise ValueError("representative ranges exceed max_materialized_bytes")
