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
    artifact_approval_path: Path
    project_review_input_path: Path
    project_review_input_v2_path: Path
    project_candidates_path: Path
    project_candidates_markdown_path: Path
    project_approval_path: Path
    semantic_index_dir: Path
    semantic_index_input_dir: Path
    semantic_index_manifest_path: Path
    semantic_index_report_path: Path
    master_profile_json_path: Path
    master_profile_md_path: Path
    portfolio_draft_path: Path
    portfolio_public_json_path: Path
    portfolio_html_path: Path

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
            artifact_approval_path=reviews_dir / "artifact-approval.json",
            project_review_input_path=reviews_dir / "project-review-input.json",
            project_review_input_v2_path=reviews_dir / "project-review-input-v2.json",
            project_candidates_path=reviews_dir / "project-candidates.json",
            project_candidates_markdown_path=reviews_dir / "project-candidates.md",
            project_approval_path=reviews_dir / "project-approval.json",
            semantic_index_dir=reviews_dir / "semantic-index",
            semantic_index_input_dir=reviews_dir / "semantic-index" / "input",
            semantic_index_manifest_path=reviews_dir / "semantic-index" / "input-manifest.json",
            semantic_index_report_path=reviews_dir / "semantic-index-report.md",
            master_profile_json_path=artifacts_dir / "master-profile.json",
            master_profile_md_path=artifacts_dir / "master-profile.md",
            portfolio_draft_path=artifacts_dir / "portfolio-draft.md",
            portfolio_public_json_path=artifacts_dir / "portfolio-public.json",
            portfolio_html_path=artifacts_dir / "portfolio.html",
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
