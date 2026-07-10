from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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
    if snapshot_path.exists():
        return snapshot_path
    payload = {
        "source_id": source_id,
        "source_uri": source_uri or source_path.resolve().as_uri(),
        "display_name": source_path.name,
        "content_hash": extracted.content_hash,
        "extractor": extracted.extractor,
        "extracted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": extracted.text,
    }
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot_path
