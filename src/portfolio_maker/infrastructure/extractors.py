from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.infrastructure.policy import mask_secrets


@dataclass(frozen=True)
class ExtractedText:
    text: str
    content_hash: str
    extractor: str


def extract_text(path: Path) -> ExtractedText:
    raw = path.read_bytes()
    return ExtractedText(
        text=mask_secrets(raw.decode("utf-8", errors="replace")),
        content_hash=hashlib.sha256(raw).hexdigest(),
        extractor="text-v1",
    )
