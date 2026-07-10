from __future__ import annotations

import os
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from uuid import uuid4


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


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
    try:
        _validate_filename(path.name)
        _validate_regular_target(directory_descriptor, path.name, allow_missing=True)
        _write_temporary(directory_descriptor, temporary_name, content.encode("utf-8"))
        if overwrite:
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
        else:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        return path
    finally:
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
