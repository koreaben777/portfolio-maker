from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from portfolio_maker.infrastructure.extractors import ExtractedText
from portfolio_maker.workspace import WorkspacePaths


class SnapshotStore:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def write_local_snapshot(self, source_id: str, source_path: Path, extracted: ExtractedText) -> Path:
        self.paths.ensure()
        snapshot_path = self.paths.local_snapshots_dir / f"source-{source_id}.json"
        payload = {
            "source_id": source_id,
            "source_uri": source_path.resolve().as_uri(),
            "display_name": source_path.name,
            "content_hash": extracted.content_hash,
            "extractor": extracted.extractor,
            "extracted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "text": extracted.text,
        }
        snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return snapshot_path
