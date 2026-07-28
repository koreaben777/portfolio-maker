from __future__ import annotations

import json
from pathlib import Path

from portfolio_maker.domain.vision_models import PreparedVisionRequest
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.workspace import WorkspacePaths


class VisionStore:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def write_request_receipt(
        self,
        request: PreparedVisionRequest,
        display_name: str,
    ) -> Path:
        if Path(display_name).name != display_name or display_name in {"", ".", ".."}:
            raise ValueError("vision request display name is invalid")
        receipt = {
            "request_id": request.id,
            "source_id": request.source_id,
            "display_name": display_name,
            "source_content_hash": request.source_content_hash,
            "source_size": request.source_size,
            "adapter": request.adapter,
            "status": request.status.value,
            "confirmation_at": request.confirmation_at,
            "requested_at": request.requested_at,
        }
        path = self.paths.vision_requests_dir / f"request-{request.id}.json"
        return write_managed_text(
            path,
            json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
            overwrite=False,
        )
