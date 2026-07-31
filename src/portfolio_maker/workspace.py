from __future__ import annotations

import unicodedata
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
    derived_dir: Path
    vision_analyses_dir: Path
    vision_requests_dir: Path
    history_policy_path: Path
    history_plans_dir: Path
    history_journal_dir: Path
    history_quarantine_dir: Path
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
    semantic_work_plan_path: Path
    semantic_work_input_dir: Path
    semantic_work_output_dir: Path
    semantic_work_receipts_dir: Path
    semantic_final_dir: Path
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
        derived_dir = root / "derived"
        return cls(
            workspace=workspace,
            root=root,
            db_path=root / "portfolio.db",
            raw_dir=raw_dir,
            snapshots_dir=snapshots_dir,
            local_snapshots_dir=snapshots_dir / "local",
            artifacts_dir=artifacts_dir,
            reviews_dir=reviews_dir,
            derived_dir=derived_dir,
            vision_analyses_dir=derived_dir / "vision",
            vision_requests_dir=reviews_dir / "vision-requests",
            history_policy_path=reviews_dir / "history-policy.json",
            history_plans_dir=reviews_dir / "history-plans",
            history_journal_dir=reviews_dir / "history-journal",
            history_quarantine_dir=root / "quarantine",
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
            semantic_work_plan_path=reviews_dir / "semantic-index" / "work-plan.json",
            semantic_work_input_dir=reviews_dir / "semantic-index" / "work-input",
            semantic_work_output_dir=reviews_dir / "semantic-index" / "work-output",
            semantic_work_receipts_dir=reviews_dir / "semantic-index" / "receipts",
            semantic_final_dir=reviews_dir / "semantic-index" / "final",
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
            self.derived_dir,
            self.vision_analyses_dir,
            self.vision_requests_dir,
            self.history_plans_dir,
            self.history_journal_dir,
            self.history_quarantine_dir,
            self.semantic_work_input_dir,
            self.semantic_work_output_dir,
            self.semantic_work_receipts_dir,
            self.semantic_final_dir,
        ):
            ensure_managed_directory(path)

    def semantic_revision_raw_dir(self, revision_id: str) -> Path:
        _validate_revision_id(revision_id)
        return self.raw_dir / "semantic-index" / revision_id

    def semantic_revision_structural_dir(self, revision_id: str) -> Path:
        return self.semantic_revision_raw_dir(revision_id) / "structural"

    def semantic_revision_locator_dir(self, revision_id: str) -> Path:
        return self.semantic_revision_raw_dir(revision_id) / "locators"


def _validate_revision_id(revision_id: str) -> None:
    if (
        not isinstance(revision_id, str)
        or not revision_id
        or revision_id in {".", ".."}
        or "/" in revision_id
        or "\\" in revision_id
        or any(
            unicodedata.category(character).startswith("C")
            for character in revision_id
        )
    ):
        raise ValueError("semantic revision identifier is unsafe")
