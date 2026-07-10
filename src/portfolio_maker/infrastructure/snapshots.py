from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from portfolio_maker.infrastructure.extractors import ExtractedText
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
