from pathlib import Path

import pytest

from portfolio_maker.application.models import (
    ApplyVisionAnalysisRequest,
    PrepareVisionAnalysisRequest,
    SetVisionAnalysisStateRequest,
)
from portfolio_maker.domain.vision_models import (
    AppliedVisionAnalysisResult,
    PreparedVisionRequest,
    VisionAnalysisStateResult,
    VisionAnalysisResult,
    VisionRequestStatus,
    VisionReviewStatus,
)
from portfolio_maker.workspace import WorkspacePaths


def test_workspace_creates_history_and_vision_private_directories(workspace) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()

    assert paths.vision_requests_dir == paths.reviews_dir / "vision-requests"
    assert paths.vision_analyses_dir == paths.root / "derived" / "vision"
    assert paths.history_plans_dir == paths.reviews_dir / "history-plans"
    assert paths.history_policy_path == paths.reviews_dir / "history-policy.json"
    assert paths.history_journal_dir == paths.reviews_dir / "history-journal"
    assert paths.history_quarantine_dir == paths.root / "quarantine"
    assert paths.vision_requests_dir.is_dir()
    assert paths.vision_analyses_dir.is_dir()
    assert paths.history_plans_dir.is_dir()
    assert paths.history_journal_dir.is_dir()
    assert paths.history_quarantine_dir.is_dir()


def test_prepared_vision_request_defaults_to_pending_codex_adapter() -> None:
    request = PreparedVisionRequest.new(12, "a" * 64, 42)

    assert request.source_id == 12
    assert request.source_content_hash == "a" * 64
    assert request.source_size == 42
    assert request.adapter == "codex-app-vision-v1"
    assert request.status is VisionRequestStatus.PENDING
    assert request.confirmation_at.endswith("Z")
    assert request.requested_at.endswith("Z")


def test_vision_result_is_immutable_and_statuses_are_explicit() -> None:
    result = VisionAnalysisResult(
        request_id="request-1",
        source_content_hash="a" * 64,
        adapter="codex-app-vision-v1",
        status=VisionRequestStatus.COMPLETED,
        confidence="high",
        summary="Summary",
        observations=({"kind": "document"},),
        requires_manual_review=True,
    )

    with pytest.raises(Exception):
        result.summary = "Changed"

    assert VisionRequestStatus.PENDING.value == "pending"
    assert VisionRequestStatus.STALE_SOURCE.value == "stale_source"
    assert VisionReviewStatus.INCLUDED.value == "included"


def test_applied_vision_result_keeps_review_and_state_contracts() -> None:
    applied = AppliedVisionAnalysisResult(
        id="analysis-1",
        result_hash="a" * 64,
        analysis_path=Path("/tmp/analysis.json"),
        review_status=VisionReviewStatus.PENDING,
    )

    assert applied.analysis_path == Path("/tmp/analysis.json")
    assert VisionAnalysisStateResult("analysis-1", "included").state == "included"


def test_vision_requests_keep_path_and_confirmation_values() -> None:
    workspace = Path("/tmp/workspace")
    result_path = Path("/tmp/result.json")

    assert PrepareVisionAnalysisRequest(workspace, 7, True).confirm_codex_share is True
    assert ApplyVisionAnalysisRequest(workspace, result_path).result_path == result_path
    assert SetVisionAnalysisStateRequest(workspace, "analysis-1", "included").state == "included"
