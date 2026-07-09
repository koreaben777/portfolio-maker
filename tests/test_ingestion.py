from __future__ import annotations

import hashlib
import json

from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.snapshots import SnapshotStore
from portfolio_maker.workspace import WorkspacePaths


def test_extract_text_masks_secrets_and_hashes_raw_bytes(tmp_path):
    source = tmp_path / "note.txt"
    raw = b"hello\napi_key=secret123\nbad:\xff"
    source.write_bytes(raw)

    extracted = extract_text(source)

    assert extracted.text == "hello\napi_key=[REDACTED]\nbad:\ufffd"
    assert extracted.content_hash == hashlib.sha256(raw).hexdigest()
    assert extracted.extractor == "text-v1"


def test_snapshot_store_writes_local_snapshot_json(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("password: hidden", encoding="utf-8")
    extracted = extract_text(source)
    paths = WorkspacePaths.from_root(tmp_path / "workspace")

    snapshot_path = SnapshotStore(paths).write_local_snapshot("abc123", source, extracted)

    assert snapshot_path == paths.local_snapshots_dir / "source-abc123.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["source_id"] == "abc123"
    assert payload["source_uri"] == source.resolve().as_uri()
    assert payload["display_name"] == "note.txt"
    assert payload["content_hash"] == extracted.content_hash
    assert payload["extractor"] == "text-v1"
    assert payload["text"] == "password: [REDACTED]"
    assert payload["extracted_at"].endswith("Z")
