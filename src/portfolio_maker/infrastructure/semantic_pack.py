from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import BinaryIO
from uuid import uuid4

from portfolio_maker.domain.semantic_frontier import PackRecordRef
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    open_managed_directory,
)


PACK_MAGIC = b"PMPACK1\0"
_LENGTH = struct.Struct(">Q")
_DIGEST_BYTES = 32
_ROOT_DOMAIN = b"portfolio-maker-pm-pack-v1\0"


class SemanticPackError(ValueError):
    pass


@dataclass(frozen=True)
class PackSeal:
    byte_length: int
    record_count: int
    record_root: str
    file_sha256: str


def canonical_json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SemanticPackError("semantic pack payload cannot be canonically encoded") from error


class PMPackWriter:
    """Durably append canonical JSON records to an immutable or runtime pack."""

    def __init__(self, path: Path, *, seal_on_close: bool = True) -> None:
        self._path = Path(path)
        _validate_filename(self._path.name)
        ensure_managed_directory(self._path.parent)
        self._directory_descriptor = open_managed_directory(
            self._path.parent, create=False
        )
        self._seal_on_close = seal_on_close
        self._sealed = False
        self._closed = False
        self._seal: PackSeal | None = None
        self._temporary_name: str | None = None
        self._handle: BinaryIO | None = None
        self._root = bytes(_DIGEST_BYTES)
        self._record_count = 0
        self._byte_length = len(PACK_MAGIC)

        try:
            if seal_on_close:
                _require_missing_target(self._directory_descriptor, self._path.name)
                self._temporary_name = (
                    f".{self._path.name}.{os.getpid()}.{uuid4().hex}.tmp"
                )
                self._handle = _open_new_file(
                    self._directory_descriptor, self._temporary_name
                )
                self._write(PACK_MAGIC)
                self._flush()
            else:
                self._open_result_pack()
        except Exception:
            self._close_descriptors()
            raise

    def __enter__(self) -> PMPackWriter:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        if exc_type is None:
            self.close()
        else:
            self._abort()

    @property
    def byte_length(self) -> int:
        return self._byte_length

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def record_root(self) -> str:
        return self._root.hex()

    def append(self, payload: object) -> PackRecordRef:
        if self._sealed:
            raise SemanticPackError("semantic pack is sealed")
        if self._closed:
            raise SemanticPackError("semantic pack is closed")
        canonical = canonical_json_bytes(payload)
        digest = hashlib.sha256(canonical).digest()
        offset = self._byte_length
        self._write(_LENGTH.pack(len(canonical)))
        self._write(canonical)
        self._write(digest)
        self._flush()
        self._byte_length += _LENGTH.size + len(canonical) + _DIGEST_BYTES
        self._record_count += 1
        self._root = _next_root(self._root, digest, len(canonical))
        return PackRecordRef(offset=offset, length=len(canonical), sha256=digest.hex())

    def fsync(self) -> None:
        self._flush()

    def seal(self) -> PackSeal:
        if self._seal is not None:
            return self._seal
        if self._closed:
            raise SemanticPackError("semantic pack is closed")
        self._flush()
        if self._temporary_name is not None:
            self._close_handle()
            temporary_path = self._path.with_name(self._temporary_name)
            expected = PackSeal(
                byte_length=self._byte_length,
                record_count=self._record_count,
                record_root=self.record_root,
                file_sha256=_file_sha256(temporary_path),
            )
            verify_pack(temporary_path, expected)
            _require_missing_target(self._directory_descriptor, self._path.name)
            os.replace(
                self._temporary_name,
                self._path.name,
                src_dir_fd=self._directory_descriptor,
                dst_dir_fd=self._directory_descriptor,
            )
            os.fsync(self._directory_descriptor)
            self._temporary_name = None
            self._seal = expected
        else:
            expected = PackSeal(
                byte_length=self._byte_length,
                record_count=self._record_count,
                record_root=self.record_root,
                file_sha256=_file_sha256(self._path),
            )
            self._seal = verify_pack(self._path, expected)
            if self._handle is None:
                raise SemanticPackError("semantic pack is closed")
            os.fchmod(self._handle.fileno(), 0o400)
            os.fsync(self._handle.fileno())
        self._sealed = True
        return self._seal

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._seal_on_close and not self._sealed:
                self.seal()
        finally:
            self._close_handle()
            self._close_descriptors()
            self._closed = True

    def _open_result_pack(self) -> None:
        target = _target_status(self._directory_descriptor, self._path.name)
        if target is None:
            self._handle = _open_new_file(self._directory_descriptor, self._path.name)
            self._write(PACK_MAGIC)
            self._flush()
            os.fsync(self._directory_descriptor)
            return
        if not S_ISREG(target.st_mode):
            raise OSError("managed output target must be a regular file")
        if not target.st_mode & 0o222:
            raise SemanticPackError("semantic pack is sealed")
        observed = verify_pack_prefix(self._path, target.st_size)
        self._byte_length = observed.byte_length
        self._record_count = observed.record_count
        self._root = bytes.fromhex(observed.record_root)
        descriptor = os.open(
            self._path.name,
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
            dir_fd=self._directory_descriptor,
        )
        try:
            if not S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("managed output target must be a regular file")
            self._handle = os.fdopen(descriptor, "wb")
        except Exception:
            os.close(descriptor)
            raise

    def _write(self, data: bytes) -> None:
        if self._handle is None:
            raise SemanticPackError("semantic pack is closed")
        self._handle.write(data)

    def _flush(self) -> None:
        if self._handle is None:
            raise SemanticPackError("semantic pack is closed")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def _close_handle(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _close_descriptors(self) -> None:
        if self._directory_descriptor is not None:
            os.close(self._directory_descriptor)
            self._directory_descriptor = None

    def _abort(self) -> None:
        if self._closed:
            return
        try:
            self._close_handle()
            if self._temporary_name is not None and self._directory_descriptor is not None:
                try:
                    os.unlink(self._temporary_name, dir_fd=self._directory_descriptor)
                except FileNotFoundError:
                    pass
                else:
                    os.fsync(self._directory_descriptor)
                self._temporary_name = None
        finally:
            self._close_descriptors()
            self._closed = True


class PMPackReader:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def read(self, reference: PackRecordRef, *, max_record_bytes: int | None = None) -> object:
        if max_record_bytes is not None and (type(max_record_bytes) is not int or max_record_bytes < 0):
            raise SemanticPackError("semantic pack record limit is invalid")
        if (
            type(reference.offset) is not int
            or type(reference.length) is not int
            or reference.offset < len(PACK_MAGIC)
            or reference.length < 0
            or len(reference.sha256) != _DIGEST_BYTES * 2
        ):
            raise SemanticPackError("semantic pack record reference is invalid")
        try:
            expected_digest = bytes.fromhex(reference.sha256)
        except ValueError as error:
            raise SemanticPackError("semantic pack record reference is invalid") from error
        if expected_digest.hex() != reference.sha256:
            raise SemanticPackError("semantic pack record reference is invalid")
        descriptor = _open_existing_file(self._path, os.O_RDONLY)
        try:
            before = os.fstat(descriptor)
            size = before.st_size
            if os.pread(descriptor, len(PACK_MAGIC), 0) != PACK_MAGIC:
                raise SemanticPackError("semantic pack magic is invalid")
            frame_length = _read_exact_at(descriptor, _LENGTH.size, reference.offset)
            if frame_length is None:
                raise SemanticPackError("semantic pack record is truncated")
            payload_length = _LENGTH.unpack(frame_length)[0]
            if payload_length != reference.length:
                raise SemanticPackError("semantic pack record reference is invalid")
            if max_record_bytes is not None and payload_length > max_record_bytes:
                raise SemanticPackError("semantic pack record exceeds byte limit")
            payload_offset = reference.offset + _LENGTH.size
            record_end = payload_offset + payload_length + _DIGEST_BYTES
            if record_end > size:
                raise SemanticPackError("semantic pack record is truncated")
            payload = _read_exact_at(descriptor, payload_length, payload_offset)
            digest = _read_exact_at(descriptor, _DIGEST_BYTES, payload_offset + payload_length)
            if payload is None or digest is None:
                raise SemanticPackError("semantic pack record is truncated")
            if digest != expected_digest or hashlib.sha256(payload).digest() != digest:
                raise SemanticPackError("semantic pack record digest is invalid")
            decoded = _decode_canonical_payload(payload)
            _require_unchanged(self._path, descriptor, before)
            return decoded
        finally:
            os.close(descriptor)


def verify_pack(path: Path, seal: PackSeal) -> PackSeal:
    if not isinstance(seal, PackSeal):
        raise SemanticPackError("semantic pack seal is invalid")
    observed = _verify_pack_prefix(path, seal.byte_length, exact_length=True)
    if observed != seal:
        raise SemanticPackError("semantic pack seal is invalid")
    return observed


def verify_pack_prefix(path: Path, length: int) -> PackSeal:
    return _verify_pack_prefix(path, length, exact_length=False)


def _verify_pack_prefix(path: Path, length: int, *, exact_length: bool) -> PackSeal:
    if not isinstance(length, int) or length < len(PACK_MAGIC):
        raise SemanticPackError("semantic pack prefix length is invalid")
    descriptor = _open_existing_file(Path(path), os.O_RDONLY)
    try:
        before = os.fstat(descriptor)
        actual_size = before.st_size
        if actual_size < length:
            raise SemanticPackError("semantic pack prefix is truncated")
        if exact_length and actual_size != length:
            raise SemanticPackError("semantic pack seal is invalid")
        if _read_exact_at(descriptor, len(PACK_MAGIC), 0) != PACK_MAGIC:
            raise SemanticPackError("semantic pack magic is invalid")
        offset = len(PACK_MAGIC)
        root = bytes(_DIGEST_BYTES)
        record_count = 0
        file_hash = hashlib.sha256()
        _hash_range(descriptor, file_hash, length)
        while offset < length:
            frame = _read_exact_at(descriptor, _LENGTH.size, offset)
            if frame is None:
                raise SemanticPackError("semantic pack record is truncated")
            payload_length = _LENGTH.unpack(frame)[0]
            payload_offset = offset + _LENGTH.size
            record_end = payload_offset + payload_length + _DIGEST_BYTES
            if record_end > length:
                raise SemanticPackError("semantic pack record is truncated")
            payload = _read_exact_at(descriptor, payload_length, payload_offset)
            digest = _read_exact_at(descriptor, _DIGEST_BYTES, payload_offset + payload_length)
            if payload is None or digest is None:
                raise SemanticPackError("semantic pack record is truncated")
            if hashlib.sha256(payload).digest() != digest:
                raise SemanticPackError("semantic pack record digest is invalid")
            _decode_canonical_payload(payload)
            root = _next_root(root, digest, payload_length)
            record_count += 1
            offset = record_end
        if offset != length:
            raise SemanticPackError("semantic pack prefix is invalid")
        _require_unchanged(Path(path), descriptor, before)
        return PackSeal(
            byte_length=length,
            record_count=record_count,
            record_root=root.hex(),
            file_sha256=file_hash.hexdigest(),
        )
    finally:
        os.close(descriptor)


def recover_committed_prefix(path: Path, length: int, root: str) -> None:
    observed = verify_pack_prefix(path, length)
    if observed.record_root != root:
        raise SemanticPackError("semantic result pack prefix is invalid")
    descriptor = _open_existing_file(Path(path), os.O_RDWR)
    try:
        os.ftruncate(descriptor, length)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = open_managed_directory(Path(path).parent, create=False)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _next_root(current: bytes, payload_sha256: bytes, length: int) -> bytes:
    return hashlib.sha256(
        _ROOT_DOMAIN + current + length.to_bytes(8, "big") + payload_sha256
    ).digest()


def _decode_canonical_payload(payload: bytes) -> object:
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SemanticPackError("semantic pack payload is invalid") from error
    if canonical_json_bytes(decoded) != payload:
        raise SemanticPackError("semantic pack payload is not canonical")
    return decoded


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _open_new_file(directory_descriptor: int, filename: str) -> BinaryIO:
    descriptor = os.open(
        filename,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_descriptor,
    )
    return os.fdopen(descriptor, "wb")


def _open_existing_file(path: Path, flags: int) -> int:
    _validate_filename(path.name)
    directory_descriptor = open_managed_directory(path.parent, create=False)
    try:
        descriptor = os.open(
            path.name,
            flags | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        try:
            if not S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("managed file must be a regular file")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(directory_descriptor)


def _require_unchanged(path: Path, descriptor: int, before: os.stat_result) -> None:
    def identity(status):
        return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)

    check_descriptor = _open_existing_file(path, os.O_RDONLY)
    try:
        if identity(before) != identity(os.fstat(descriptor)) or identity(before) != identity(os.fstat(check_descriptor)):
            raise SemanticPackError("semantic pack changed during read")
    finally:
        os.close(check_descriptor)


def _target_status(directory_descriptor: int, filename: str) -> os.stat_result | None:
    try:
        return os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_missing_target(directory_descriptor: int | None, filename: str) -> None:
    if directory_descriptor is None:
        raise SemanticPackError("semantic pack is closed")
    target = _target_status(directory_descriptor, filename)
    if target is None:
        return
    if not S_ISREG(target.st_mode):
        raise OSError("managed output target must be a regular file")
    raise SemanticPackError("semantic pack target already exists")


def _validate_filename(filename: str) -> None:
    if not filename or "/" in filename or filename in {".", ".."}:
        raise OSError("managed filename is invalid")


def _read_exact_at(descriptor: int, size: int, offset: int) -> bytes | None:
    try:
        data = os.pread(descriptor, size, offset)
    except (OSError, OverflowError):
        return None
    return data if len(data) == size else None


def _hash_range(descriptor: int, digest: hashlib._Hash, length: int) -> None:
    offset = 0
    while offset < length:
        chunk = os.pread(descriptor, min(65_536, length - offset), offset)
        if not chunk:
            raise SemanticPackError("semantic pack prefix is truncated")
        digest.update(chunk)
        offset += len(chunk)


def _file_sha256(path: Path) -> str:
    descriptor = _open_existing_file(path, os.O_RDONLY)
    try:
        size = os.fstat(descriptor).st_size
        digest = hashlib.sha256()
        _hash_range(descriptor, digest, size)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _file_size(path: Path) -> int:
    descriptor = _open_existing_file(path, os.O_RDONLY)
    try:
        return os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)
