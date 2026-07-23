from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from portfolio_maker.domain.history_models import utc_now


class VisionRequestStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE_SOURCE = "stale_source"


class VisionReviewStatus(StrEnum):
    PENDING = "pending"
    INCLUDED = "included"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class PreparedVisionRequest:
    id: str
    source_id: int
    source_content_hash: str
    source_size: int
    adapter: str
    status: VisionRequestStatus
    confirmation_at: str

    @classmethod
    def new(cls, source_id: int, content_hash: str, size: int) -> PreparedVisionRequest:
        return cls(
            uuid4().hex,
            source_id,
            content_hash,
            size,
            "codex-app-vision-v1",
            VisionRequestStatus.PENDING,
            utc_now(),
        )


@dataclass(frozen=True)
class VisionAnalysisResult:
    request_id: str
    source_content_hash: str
    adapter: str
    status: VisionRequestStatus
    confidence: str | None
    summary: str | None
    observations: tuple[dict[str, str], ...]
    requires_manual_review: bool


@dataclass(frozen=True)
class AppliedVisionAnalysisResult:
    id: str
    result_hash: str
    analysis_path: Path
    review_status: VisionReviewStatus


@dataclass(frozen=True)
class VisionAnalysisStateResult:
    analysis_id: str
    state: str
