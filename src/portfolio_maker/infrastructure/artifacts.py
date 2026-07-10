from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from portfolio_maker.infrastructure.managed_files import write_managed_text


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    return write_managed_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def write_markdown(path: Path, content: str) -> Path:
    return write_managed_text(path, content)
