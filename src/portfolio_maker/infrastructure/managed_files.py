from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from uuid import uuid4


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True)
class ManagedFileSnapshot:
    content: bytes
    size: int
    device: int
    inode: int
    content_hash: str


def ensure_managed_directory(path: Path) -> None:
    descriptor = _open_directory(path, create=True)
    try:
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def open_managed_directory(path: Path, *, create: bool = False) -> int:
    return _open_directory(path, create=create)


def write_managed_text(path: Path, content: str, *, overwrite: bool = True) -> Path:
    ensure_managed_directory(path.parent)
    directory_descriptor = _open_directory(path.parent, create=False)
    temporary_name = f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    temporary_may_exist = False
    exclusive_target_published = False
    try:
        _validate_filename(path.name)
        _validate_regular_target(directory_descriptor, path.name, allow_missing=True)
        temporary_may_exist = True
        _write_temporary(directory_descriptor, temporary_name, content.encode("utf-8"))
        if overwrite:
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            temporary_may_exist = False
        else:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            exclusive_target_published = True
            os.unlink(temporary_name, dir_fd=directory_descriptor)
            temporary_may_exist = False
        os.fsync(directory_descriptor)
        return path
    except Exception:
        if exclusive_target_published:
            try:
                os.unlink(path.name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            else:
                os.fsync(directory_descriptor)
        raise
    finally:
        if temporary_may_exist:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)


def read_managed_bytes(path: Path) -> bytes:
    directory_descriptor = _open_directory(path.parent, create=False)
    descriptor: int | None = None
    try:
        _validate_filename(path.name)
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        if not S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("managed file must be a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_descriptor)


def read_managed_relative_file(
    root: Path,
    relative_path: Path,
) -> ManagedFileSnapshot:
    parts = _validate_relative_path(relative_path)
    directory_descriptor = _open_directory(root, create=False)
    file_descriptor: int | None = None
    try:
        for component in parts[:-1]:
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=directory_descriptor,
            )
            try:
                if not S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise OSError("managed path component must be a directory")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        before = os.fstat(file_descriptor)
        if not S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OSError("managed file must be a private regular file")
        chunks: list[bytes] = []
        while chunk := os.read(file_descriptor, 65_536):
            chunks.append(chunk)
        content = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if (
            not S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or len(content) != after.st_size
        ):
            raise OSError("managed file changed while it was read")
        return ManagedFileSnapshot(
            content=content,
            size=after.st_size,
            device=after.st_dev,
            inode=after.st_ino,
            content_hash=sha256(content).hexdigest(),
        )
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(directory_descriptor)


def remove_managed_file(path: Path, *, missing_ok: bool = False) -> None:
    directory_descriptor = _open_directory(path.parent, create=False)
    try:
        _validate_filename(path.name)
        try:
            target = os.stat(path.name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if not S_ISREG(target.st_mode):
            raise OSError("managed output target must be a regular file")
        os.unlink(path.name, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _open_directory(path: Path, *, create: bool) -> int:
    absolute_path = path if path.is_absolute() else path.absolute()
    descriptor = os.open(absolute_path.anchor, _DIRECTORY_FLAGS)
    try:
        for component in absolute_path.parts[1:]:
            if component in {"", ".", ".."}:
                raise OSError("managed directory path is invalid")
            try:
                next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                if not S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise OSError("managed path component must be a directory")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_filename(filename: str) -> None:
    if not filename or "/" in filename or filename in {".", ".."}:
        raise OSError("managed filename is invalid")


def _validate_relative_path(relative_path: Path) -> tuple[str, ...]:
    if (
        not isinstance(relative_path, Path)
        or relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise OSError("managed relative path is invalid")
    return relative_path.parts


def _validate_regular_target(
    directory_descriptor: int,
    filename: str,
    *,
    allow_missing: bool,
) -> None:
    try:
        target = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    if not S_ISREG(target.st_mode):
        raise OSError("managed output target must be a regular file")


def _write_temporary(directory_descriptor: int, filename: str, content: bytes) -> None:
    descriptor = os.open(
        filename,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_descriptor,
    )
    with os.fdopen(descriptor, "wb") as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
