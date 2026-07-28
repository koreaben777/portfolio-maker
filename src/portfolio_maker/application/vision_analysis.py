from __future__ import annotations

from portfolio_maker.application.approval import (
    ApprovalFormatError,
    ApprovalMissingError,
    load_approval,
)
from portfolio_maker.application.models import PrepareVisionAnalysisRequest
from portfolio_maker.domain.models import Source, SourceType
from portfolio_maker.domain.vision_models import PreparedVisionRequest
from portfolio_maker.infrastructure.extractors import validate_approved_vision_source
from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.infrastructure.vision_store import VisionStore
from portfolio_maker.workspace import WorkspacePaths


class VisionAnalysisError(RuntimeError):
    pass


def prepare_vision_analysis(
    request: PrepareVisionAnalysisRequest,
) -> PreparedVisionRequest:
    if request.confirm_codex_share is not True:
        raise VisionAnalysisError("Codex analysis confirmation is required")
    try:
        paths = WorkspacePaths.from_root(request.workspace)
        paths.ensure()
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        with repository.exclusive_workspace_operation():
            approval = load_approval(paths)
            source = _approved_local_source(repository, request.source_id)
            if source.uri not in approval.approved_source_uris:
                raise VisionAnalysisError("approved source is unsafe or unavailable")
            policy = FilePolicy(
                forbidden_paths=approval.excluded_directories,
                excluded_file_patterns=approval.excluded_file_patterns,
            )
            checked = validate_approved_vision_source(source.uri, policy)
            prepared = PreparedVisionRequest.new(
                source.id,
                checked.content_hash,
                checked.byte_size,
            )
            repository.create_vision_request(prepared)
            VisionStore(paths).write_request_receipt(prepared, checked.path.name)
            return prepared
    except VisionAnalysisError:
        raise
    except (
        ApprovalMissingError,
        ApprovalFormatError,
        SourcePathPolicyError,
        RepositoryError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        raise VisionAnalysisError("approved source is unsafe or unavailable") from error


def _approved_local_source(repository: SQLiteRepository, source_id: object) -> Source:
    if type(source_id) is not int:
        raise VisionAnalysisError("approved source is unsafe or unavailable")
    matches = [
        source
        for source in repository.list_sources()
        if source.id == source_id and source.type is SourceType.LOCAL_FILE
    ]
    if len(matches) != 1 or matches[0].id is None:
        raise VisionAnalysisError("approved source is unsafe or unavailable")
    return matches[0]
