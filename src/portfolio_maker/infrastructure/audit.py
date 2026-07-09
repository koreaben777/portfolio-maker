from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    message: str
    data: dict[str, Any]


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(event)
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            f.write("\n")
