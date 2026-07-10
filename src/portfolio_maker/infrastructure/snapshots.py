from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from tempfile import NamedTemporaryFile

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
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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


def load_verified_managed_legacy_snapshot(
    paths: WorkspacePaths,
    source_id: int,
    source_uri: str,
    content_hash: str | None = None,
) -> dict[str, object] | None:
    filename = f"source-{source_id}.json"
    directory_descriptor = _open_directory_descriptor(paths.local_snapshots_dir)
    try:
        payload = _read_regular_json(directory_descriptor, filename)
    finally:
        os.close(directory_descriptor)
    if payload is None:
        return None
    if not (
        isinstance(payload, dict)
        and payload.get("source_id") == source_id
        and payload.get("source_uri") == source_uri
        and isinstance(payload.get("content_hash"), str)
        and payload.get("extractor") == "text-v1"
        and isinstance(payload.get("display_name"), str)
        and isinstance(payload.get("extracted_at"), str)
        and isinstance(payload.get("text"), str)
    ):
        return None
    if content_hash is not None and payload["content_hash"] != content_hash:
        return None
    return payload


def migrate_verified_managed_legacy_snapshot(
    paths: WorkspacePaths,
    source_id: int,
    source_uri: str,
    payload: dict[str, object],
) -> tuple[Path, ExtractedText]:
    display_name = str(payload["display_name"])
    extracted = ExtractedText(
        text=mask_secrets(str(payload["text"])),
        content_hash=str(payload["content_hash"]),
        extractor=EXTRACTOR_VERSION,
    )
    snapshot_path = write_local_snapshot(
        paths,
        source_id,
        Path(display_name),
        extracted,
        source_uri=source_uri,
    )
    return snapshot_path, extracted


def remove_verified_managed_legacy_snapshot(
    paths: WorkspacePaths,
    source_id: int,
    source_uri: str,
    content_hash: str,
) -> bool:
    filename = f"source-{source_id}.json"
    directory_descriptor = _open_directory_descriptor(paths.local_snapshots_dir)
    try:
        before = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if not S_ISREG(before.st_mode):
            return False
        payload = _read_regular_json(directory_descriptor, filename)
        if payload is None or not _is_verified_legacy_payload(
            payload,
            source_id,
            source_uri,
            content_hash,
        ):
            return False
        after = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not S_ISREG(after.st_mode)
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise OSError("managed legacy snapshot changed before cleanup")
        os.unlink(filename, dir_fd=directory_descriptor)
        current_directory = os.stat(paths.local_snapshots_dir, follow_symlinks=False)
        descriptor_directory = os.fstat(directory_descriptor)
        if (
            not S_ISDIR(current_directory.st_mode)
            or (current_directory.st_dev, current_directory.st_ino)
            != (descriptor_directory.st_dev, descriptor_directory.st_ino)
        ):
            raise OSError("managed snapshot directory changed during cleanup")
        return True
    except FileNotFoundError:
        return False
    finally:
        os.close(directory_descriptor)


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
    content_hash: str,
) -> bool:
    return (
        payload.get("source_id") == source_id
        and payload.get("source_uri") == source_uri
        and payload.get("content_hash") == content_hash
        and payload.get("extractor") == "text-v1"
        and isinstance(payload.get("display_name"), str)
        and isinstance(payload.get("extracted_at"), str)
        and isinstance(payload.get("text"), str)
    )


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
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
