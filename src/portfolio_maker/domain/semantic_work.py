from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SemanticWorkStage(StrEnum):
    LEAF = "leaf"
    REDUCE = "reduce"
    DIRECTORY = "directory"
    ROOT = "root"


class SemanticDisposition(StrEnum):
    ANALYSIS_REQUIRED = "analysis_required"
    STRUCTURAL_ONLY = "structural_only"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class SemanticWorkUnit:
    unit_id: str
    stage: SemanticWorkStage
    target_node_ids: tuple[str, ...]
    dependency_unit_ids: tuple[str, ...]
    template_sha256: str
    template_byte_count: int


@dataclass(frozen=True)
class SemanticWorkPlan:
    revision_id: str
    policy_hash: str
    protocol: str
    node_count: int
    analysis_target_count: int
    structural_owner_count: int
    max_work_unit_bytes: int
    reduce_fan_in: int
    node_assignment_sha256: str
    units: tuple[SemanticWorkUnit, ...]
