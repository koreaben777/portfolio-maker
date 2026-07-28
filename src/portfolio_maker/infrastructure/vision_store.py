from __future__ import annotations

import json
import os
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from portfolio_maker.domain.vision_models import PreparedVisionRequest
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.workspace import WorkspacePaths


MAX_VISION_RESULT_BYTES = 262_144
_RESULT_READ_CHUNK_SIZE = 65_536
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


class VisionStore:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def write_request_receipt(
        self,
        request: PreparedVisionRequest,
        display_name: str,
    ) -> Path:
        if Path(display_name).name != display_name or display_name in {"", ".", ".."}:
            raise ValueError("vision request display name is invalid")
        receipt = {
            "request_id": request.id,
            "source_id": request.source_id,
            "display_name": display_name,
            "source_content_hash": request.source_content_hash,
            "source_size": request.source_size,
            "adapter": request.adapter,
            "status": request.status.value,
            "confirmation_at": request.confirmation_at,
            "requested_at": request.requested_at,
        }
        path = self.paths.vision_requests_dir / f"request-{request.id}.json"
        return write_managed_text(
            path,
            json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
            overwrite=False,
        )

    def read_result_payload(self, result_path: object) -> object:
        raw = _read_bounded_result_file(result_path)
        try:
            return json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_object_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("vision result is not valid JSON") from error

    def write_analysis(
        self,
        request_id: str,
        result_hash: str,
        canonical_json: str,
    ) -> Path:
        if not _safe_component(request_id) or not _is_sha256(result_hash):
            raise ValueError("vision analysis identifier is invalid")
        path = self.paths.vision_analyses_dir / f"analysis-{request_id}-{result_hash}.json"
        return write_managed_text(path, canonical_json, overwrite=False)


def _read_bounded_result_file(result_path: object) -> bytes:
    if not isinstance(result_path, Path) or not result_path.is_absolute():
        raise ValueError("vision result path is invalid")
    components = result_path.parts[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise ValueError("vision result path is invalid")

    directory_descriptor = os.open(result_path.anchor, _DIRECTORY_FLAGS)
    file_descriptor: int | None = None
    try:
        for component in components[:-1]:
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=directory_descriptor,
            )
            try:
                if not S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise ValueError("vision result parent is invalid")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        filename = components[-1]
        target = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        if not S_ISREG(target.st_mode) or target.st_size > MAX_VISION_RESULT_BYTES:
            raise ValueError("vision result is not a bounded regular file")
        file_descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        before = os.fstat(file_descriptor)
        if not S_ISREG(before.st_mode) or before.st_size > MAX_VISION_RESULT_BYTES:
            raise ValueError("vision result is not a bounded regular file")
        chunks: list[bytes] = []
        byte_count = 0
        while chunk := os.read(file_descriptor, _RESULT_READ_CHUNK_SIZE):
            byte_count += len(chunk)
            if byte_count > MAX_VISION_RESULT_BYTES:
                raise ValueError("vision result exceeds the size limit")
            chunks.append(chunk)
        content = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if (
            not S_ISREG(after.st_mode)
            or _file_identity(before) != _file_identity(after)
            or byte_count != after.st_size
        ):
            raise ValueError("vision result changed while it was read")
        return content
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(directory_descriptor)


def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _safe_component(value: object) -> bool:
    return isinstance(value, str) and value not in {"", ".", ".."} and "/" not in value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _reject_duplicate_object_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("vision result has duplicate keys")
        result[key] = value
    return result
