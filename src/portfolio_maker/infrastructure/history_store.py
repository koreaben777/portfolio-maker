from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from portfolio_maker.domain.history_models import HistoryPlan, HistoryUnit
from portfolio_maker.infrastructure.managed_files import (
    read_managed_relative_file,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.workspace import WorkspacePaths


_KIND_ROOTS = {
    "source_snapshot": ("raw", "snapshots", "local"),
    "vision_analysis": ("derived", "vision"),
    "vision_request": ("reviews", "vision-requests"),
}


class HistoryStore:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def write_plan(self, plan: HistoryPlan) -> Path:
        self.revalidate_units(plan.units)
        _validate_plan(plan)
        payload = {
            "version": 1,
            "id": plan.id,
            "policy": asdict(plan.policy),
            "units": [asdict(unit) for unit in plan.units],
            "manifest_hash": plan.manifest_hash,
            "confirmation_phrase": plan.confirmation_phrase,
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        plan_path = self.paths.history_plans_dir / (
            f"plan-{plan.id}-{plan.manifest_hash}.json"
        )
        return write_managed_text(plan_path, content, overwrite=False)

    def remove_plan(self, plan_path: Path) -> None:
        if plan_path.parent != self.paths.history_plans_dir:
            raise OSError("managed history plan path is invalid")
        remove_managed_file(plan_path)

    def revalidate_units(
        self,
        units: tuple[HistoryUnit, ...] | list[HistoryUnit],
    ) -> None:
        for unit in units:
            root, relative_path = self._unit_root_and_path(unit)
            snapshot = read_managed_relative_file(root, relative_path)
            if (
                snapshot.size != unit.size
                or snapshot.device != unit.device
                or snapshot.inode != unit.inode
                or snapshot.content_hash != unit.content_hash
            ):
                raise OSError("managed history candidate identity changed")

    def _unit_root_and_path(self, unit: HistoryUnit) -> tuple[Path, Path]:
        expected_root = _KIND_ROOTS.get(unit.kind)
        if expected_root is None:
            raise OSError("managed history candidate kind is invalid")
        relative_path = Path(unit.relative_path)
        if (
            relative_path.is_absolute()
            or relative_path.as_posix() != unit.relative_path
            or len(relative_path.parts) <= len(expected_root)
            or relative_path.parts[: len(expected_root)] != expected_root
        ):
            raise OSError("managed history candidate root is invalid")
        managed_root = self.paths.root.joinpath(*expected_root)
        child_path = Path(*relative_path.parts[len(expected_root) :])
        return managed_root, child_path


def _validate_plan(plan: HistoryPlan) -> None:
    if (
        len(plan.id) != 32
        or any(character not in "0123456789abcdef" for character in plan.id)
        or len(plan.manifest_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in plan.manifest_hash
        )
        or plan.confirmation_phrase != "DELETE MANAGED HISTORY"
        or not plan.units
    ):
        raise OSError("managed history plan is invalid")
    manifest_payload = {
        "policy": asdict(plan.policy),
        "units": [asdict(unit) for unit in plan.units],
    }
    canonical_manifest = json.dumps(
        manifest_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if sha256(canonical_manifest).hexdigest() != plan.manifest_hash:
        raise OSError("managed history plan hash is invalid")
