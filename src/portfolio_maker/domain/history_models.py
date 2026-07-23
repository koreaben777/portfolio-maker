from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from uuid import uuid4


@dataclass(frozen=True)
class HistoryPolicy:
    minimum_age_days: int
    keep_latest_text_snapshots_per_source: int
    retain_included_vision_analyses: bool
    retain_pending_review_vision_analyses: bool
    retain_incomplete_vision_requests: bool

    @classmethod
    def default(cls) -> HistoryPolicy:
        return cls(30, 1, True, True, True)


class CleanupExecutionState(StrEnum):
    PLANNED = "planned"
    EXPORTED = "exported"
    QUARANTINED = "quarantined"
    COMMITTED_PENDING_PURGE = "committed_pending_purge"
    PURGED = "purged"
    RESTORED = "restored"
    VACUUM_PENDING = "vacuum_pending"


@dataclass(frozen=True)
class HistoryUnit:
    kind: str
    row_id: str
    relative_path: str
    content_hash: str
    size: int
    device: int
    inode: int
    reason: str


@dataclass(frozen=True)
class HistoryPlan:
    id: str
    policy: HistoryPolicy
    units: tuple[HistoryUnit, ...]
    manifest_hash: str
    confirmation_phrase: str = "DELETE MANAGED HISTORY"

    @classmethod
    def create(cls, policy: HistoryPolicy, units: list[HistoryUnit]) -> HistoryPlan:
        canonical_units = tuple(
            sorted(
                units,
                key=lambda unit: (
                    unit.kind,
                    unit.row_id,
                    unit.relative_path,
                    unit.content_hash,
                    unit.size,
                    unit.device,
                    unit.inode,
                    unit.reason,
                ),
            )
        )
        manifest_hash = sha256(_canonical_json(policy, canonical_units)).hexdigest()
        return cls(uuid4().hex, policy, canonical_units, manifest_hash)

    def unit_ids(self) -> tuple[tuple[str, str], ...]:
        return tuple((unit.kind, unit.row_id) for unit in self.units)


def _canonical_json(policy: HistoryPolicy, units: tuple[HistoryUnit, ...]) -> bytes:
    return json.dumps(
        {"policy": asdict(policy), "units": [asdict(unit) for unit in units]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
