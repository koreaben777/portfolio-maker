from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.infrastructure.managed_files import ensure_managed_directory


@dataclass(frozen=True)
class WorkspacePaths:
    workspace: Path
    root: Path
    db_path: Path
    raw_dir: Path
    snapshots_dir: Path
    local_snapshots_dir: Path
    artifacts_dir: Path
    reviews_dir: Path
    discovery_report_path: Path
    approval_path: Path
    master_profile_json_path: Path
    master_profile_md_path: Path
    portfolio_draft_path: Path

    @classmethod
    def from_root(cls, workspace: Path) -> "WorkspacePaths":
        workspace = workspace.resolve()
        root = workspace / ".portfolio-maker"
        raw_dir = root / "raw"
        snapshots_dir = raw_dir / "snapshots"
        artifacts_dir = root / "artifacts"
        reviews_dir = root / "reviews"
        return cls(
            workspace=workspace,
            root=root,
            db_path=root / "portfolio.db",
            raw_dir=raw_dir,
            snapshots_dir=snapshots_dir,
            local_snapshots_dir=snapshots_dir / "local",
            artifacts_dir=artifacts_dir,
            reviews_dir=reviews_dir,
            discovery_report_path=reviews_dir / "discovery-report.md",
            approval_path=reviews_dir / "source-approval.json",
            master_profile_json_path=artifacts_dir / "master-profile.json",
            master_profile_md_path=artifacts_dir / "master-profile.md",
            portfolio_draft_path=artifacts_dir / "portfolio-draft.md",
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.raw_dir,
            self.snapshots_dir,
            self.local_snapshots_dir,
            self.artifacts_dir,
            self.reviews_dir,
        ):
            ensure_managed_directory(path)
