from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from uuid import uuid4

from portfolio_maker.infrastructure.extractors import EXTRACTOR_VERSION, ExtractedText
from portfolio_maker.infrastructure.policy import mask_secrets
from portfolio_maker.workspace import WorkspacePaths


def write_local_snapshot(
    paths: WorkspacePaths,
    source_id: int,
    source_path: Path,
    extracted: ExtractedText,
    source_uri: str | None = None,
) -> Path:
    paths.ensure()
    snapshot_path = paths.local_snapshots_dir / f"source-{source_id}-{extracted.content_hash}.json"
    expected_uri = source_uri or source_path.resolve().as_uri()
    if load_valid_local_snapshot(
        snapshot_path,
        source_id,
        expected_uri,
        source_path.name,
        extracted,
    ) is not None:
        return snapshot_path
    payload = {
        "source_id": source_id,
        "source_uri": expected_uri,
        "display_name": source_path.name,
        "content_hash": extracted.content_hash,
        "extractor": extracted.extractor,
        "extracted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": extracted.text,
    }
    _write_json_atomically(snapshot_path, payload)
    return snapshot_path


def load_valid_local_snapshot(
    snapshot_path: Path,
    source_id: int,
    source_uri: str,
    display_name: str,
    extracted: ExtractedText,
) -> dict[str, object] | None:
    try:
        directory_descriptor = _open_directory_descriptor(snapshot_path.parent)
        try:
            payload = _read_regular_json(directory_descriptor, snapshot_path.name)
        finally:
            os.close(directory_descriptor)
    except OSError:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("source_id") != source_id
        or payload.get("source_uri") != source_uri
        or payload.get("display_name") != display_name
        or payload.get("content_hash") != extracted.content_hash
        or payload.get("extractor") != extracted.extractor
        or payload.get("text") != extracted.text
        or not isinstance(payload.get("extracted_at"), str)
    ):
        return None
    return payload


def migrate_verified_managed_legacy_snapshot(
    paths: WorkspacePaths,
    source_id: int,
    source_uri: str,
) -> tuple[Path, ExtractedText] | None:
    filename = f"source-{source_id}.json"
    directory_descriptor = _open_directory_descriptor(paths.local_snapshots_dir)
    try:
        before = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if not S_ISREG(before.st_mode):
            return None
        payload = _read_regular_json(directory_descriptor, filename)
        if payload is None or not _is_verified_legacy_payload(
            payload,
            source_id,
            source_uri,
        ):
            return None
        after = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not S_ISREG(after.st_mode)
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise OSError("managed legacy snapshot changed before migration")
        _verify_current_directory(paths.local_snapshots_dir, directory_descriptor)
        snapshot_path, extracted = _write_migrated_snapshot_relative(
            paths,
            source_id,
            source_uri,
            directory_descriptor,
            payload,
        )
        _verify_current_directory(paths.local_snapshots_dir, directory_descriptor)
        current = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise OSError("managed legacy snapshot changed before cleanup")
        os.unlink(filename, dir_fd=directory_descriptor)
        _verify_current_directory(paths.local_snapshots_dir, directory_descriptor)
        return snapshot_path, extracted
    except FileNotFoundError:
        return None
    finally:
        os.close(directory_descriptor)


def _write_migrated_snapshot_relative(
    paths: WorkspacePaths,
    source_id: int,
    source_uri: str,
    directory_descriptor: int,
    payload: dict[str, object],
) -> tuple[Path, ExtractedText]:
    display_name = str(payload["display_name"])
    extracted = ExtractedText(
        text=mask_secrets(str(payload["text"])),
        content_hash=str(payload["content_hash"]),
        extractor=EXTRACTOR_VERSION,
    )
    snapshot_name = f"source-{source_id}-{extracted.content_hash}.json"
    snapshot_path = paths.local_snapshots_dir / snapshot_name
    snapshot_payload = {
        "source_id": source_id,
        "source_uri": source_uri,
        "display_name": display_name,
        "content_hash": extracted.content_hash,
        "extractor": extracted.extractor,
        "extracted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": extracted.text,
    }
    existing = _read_regular_json(directory_descriptor, snapshot_name)
    if existing is None or not _is_verified_migrated_payload(
        existing,
        source_id,
        source_uri,
        extracted,
    ):
        _write_json_atomically_relative(directory_descriptor, snapshot_name, snapshot_payload)
    return snapshot_path, extracted


def has_valid_migrated_snapshot(
    paths: WorkspacePaths,
    source_id: int,
    source_uri: str,
    content_hash: str,
) -> bool:
    filename = f"source-{source_id}-{content_hash}.json"
    directory_descriptor = _open_directory_descriptor(paths.local_snapshots_dir)
    try:
        payload = _read_regular_json(directory_descriptor, filename)
    finally:
        os.close(directory_descriptor)
    if payload is None:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("source_id") == source_id
        and payload.get("source_uri") == source_uri
        and payload.get("content_hash") == content_hash
        and payload.get("extractor") == EXTRACTOR_VERSION
        and isinstance(payload.get("display_name"), str)
        and isinstance(payload.get("extracted_at"), str)
        and isinstance(payload.get("text"), str)
    )


def _is_verified_legacy_payload(
    payload: dict[str, object],
    source_id: int,
    source_uri: str,
) -> bool:
    return (
        payload.get("source_id") == source_id
        and payload.get("source_uri") == source_uri
        and isinstance(payload.get("content_hash"), str)
        and payload.get("extractor") == "text-v1"
        and isinstance(payload.get("display_name"), str)
        and isinstance(payload.get("extracted_at"), str)
        and isinstance(payload.get("text"), str)
    )


def _is_verified_migrated_payload(
    payload: dict[str, object],
    source_id: int,
    source_uri: str,
    extracted: ExtractedText,
) -> bool:
    return (
        payload.get("source_id") == source_id
        and payload.get("source_uri") == source_uri
        and payload.get("content_hash") == extracted.content_hash
        and payload.get("extractor") == extracted.extractor
        and payload.get("text") == extracted.text
        and isinstance(payload.get("display_name"), str)
        and isinstance(payload.get("extracted_at"), str)
    )


def _verify_current_directory(path: Path, directory_descriptor: int) -> None:
    current_directory = os.stat(path, follow_symlinks=False)
    descriptor_directory = os.fstat(directory_descriptor)
    if (
        not S_ISDIR(current_directory.st_mode)
        or (current_directory.st_dev, current_directory.st_ino)
        != (descriptor_directory.st_dev, descriptor_directory.st_ino)
    ):
        raise OSError("managed snapshot directory changed during migration")


def _open_directory_descriptor(path: Path) -> int:
    absolute_path = path if path.is_absolute() else path.absolute()
    components = absolute_path.parts[1:]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(absolute_path.anchor, flags)
    try:
        for component in components:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            try:
                if not S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise OSError("managed snapshot path must be a directory")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_regular_json(directory_descriptor: int, filename: str) -> dict[str, object] | None:
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
    except FileNotFoundError:
        return None
    try:
        if not S_ISREG(os.fstat(descriptor).st_mode):
            return None
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        os.close(descriptor)
    return payload if isinstance(payload, dict) else None


def _write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    directory_descriptor = _open_directory_descriptor(path.parent)
    try:
        _write_json_atomically_relative(directory_descriptor, path.name, payload)
    finally:
        os.close(directory_descriptor)


def _write_json_atomically_relative(
    directory_descriptor: int,
    filename: str,
    payload: dict[str, object],
) -> None:
    if not filename or "/" in filename or filename in {".", ".."}:
        raise OSError("managed snapshot filename is invalid")
    temporary_name = f".{filename}.{os.getpid()}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(descriptor, "wb") as temporary:
            descriptor = None
            temporary.write(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            temporary.write(b"\n")
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
