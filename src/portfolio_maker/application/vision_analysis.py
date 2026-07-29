from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from portfolio_maker.application.approval import (
    ApprovalFormatError,
    ApprovalMissingError,
    load_approval,
)
from portfolio_maker.application.models import (
    ApplyVisionAnalysisRequest,
    PrepareVisionAnalysisRequest,
    SetVisionAnalysisStateRequest,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.domain.vision_models import (
    AppliedVisionAnalysisResult,
    PreparedVisionRequest,
    VisionAnalysisStateResult,
    VisionAnalysisResult,
    VisionRequestStatus,
    VisionReviewStatus,
)
from portfolio_maker.infrastructure.extractors import validate_approved_vision_source
from portfolio_maker.infrastructure.managed_files import read_managed_relative_file
from portfolio_maker.infrastructure.policy import (
    FilePolicy,
    SourcePathPolicyError,
    contains_hidden_secret_shaped_public_value,
    mask_secrets,
)
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.infrastructure.vision_store import VisionStore
from portfolio_maker.workspace import WorkspacePaths


class VisionAnalysisError(RuntimeError):
    pass


_SCHEMA_VERSION = "vision-analysis-v1"
_ADAPTER = "codex-app-vision-v1"
_COMPLETED_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "source_content_hash",
        "adapter",
        "status",
        "confidence",
        "summary",
        "observations",
        "requires_manual_review",
    }
)
_STATUS_ONLY_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "source_content_hash",
        "adapter",
        "status",
        "reason_code",
    }
)
_STATUS_ONLY_VALUES = frozenset(
    {
        VisionRequestStatus.UNSUPPORTED.value,
        VisionRequestStatus.UNAVAILABLE.value,
        VisionRequestStatus.FAILED.value,
        VisionRequestStatus.CANCELLED.value,
    }
)
_CONFIDENCES = frozenset({"low", "medium", "high"})
_OBSERVATION_KINDS = frozenset({"role", "deliverable", "technology", "outcome", "context"})
_MAX_SUMMARY_LENGTH = 4_000
_MAX_OBSERVATIONS = 50
_MAX_OBSERVATION_TEXT_LENGTH = 1_000
_MAX_REASON_CODE_LENGTH = 80
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]*")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+(?:/[^\s/]+)*)")
_UNC_PATH = re.compile(r"(?<!\\)\\\\[^\\/\s]+[\\/]+[^\\/\s]+")


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


def apply_vision_analysis(
    request: ApplyVisionAnalysisRequest,
) -> AppliedVisionAnalysisResult | None:
    try:
        paths = WorkspacePaths.from_root(request.workspace)
        paths.ensure()
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        payload = VisionStore(paths).read_result_payload(request.result_path)
        parsed = _parse_vision_result(payload)
        with repository.exclusive_workspace_operation():
            stored_request = repository.get_vision_request(parsed["request_id"])
            if stored_request["status"] != VisionRequestStatus.PENDING.value:
                raise VisionAnalysisError("vision result is unsafe or unavailable")
            if (
                parsed["source_content_hash"] != stored_request["source_content_hash"]
                or parsed["adapter"] != stored_request["adapter"]
            ):
                raise VisionAnalysisError("vision result is unsafe or unavailable")
            approval = load_approval(paths)
            source = _approved_local_source(repository, stored_request["source_id"])
            if source.uri not in approval.approved_source_uris:
                raise VisionAnalysisError("vision source is unsafe or unavailable")
            policy = FilePolicy(
                forbidden_paths=approval.excluded_directories,
                excluded_file_patterns=approval.excluded_file_patterns,
            )
            checked = validate_approved_vision_source(source.uri, policy)
            if checked.content_hash != stored_request["source_content_hash"]:
                repository.finish_vision_request(
                    stored_request["id"],
                    VisionRequestStatus.STALE_SOURCE,
                    "source_changed",
                )
                raise VisionAnalysisError("vision source changed")
            if parsed["status"] in _STATUS_ONLY_VALUES:
                repository.finish_vision_request(
                    stored_request["id"],
                    VisionRequestStatus(parsed["status"]),
                    parsed["reason_code"],
                )
                return None

            result = _completed_vision_result(parsed)
            canonical_json = _canonical_result_json(result)
            result_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
            analysis_path = VisionStore(paths).write_analysis(
                result.request_id,
                result_hash,
                canonical_json,
            )
            applied = AppliedVisionAnalysisResult(
                uuid4().hex,
                result_hash,
                analysis_path,
                VisionReviewStatus.PENDING,
            )
            repository.insert_vision_analysis(applied, result)
            repository.finish_vision_request(
                result.request_id,
                VisionRequestStatus.COMPLETED,
            )
            return applied
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
        raise VisionAnalysisError("vision result is unsafe or unavailable") from error


def set_vision_analysis_state(
    request: SetVisionAnalysisStateRequest,
) -> VisionAnalysisStateResult:
    if (
        not _safe_identifier(request.analysis_id)
        or request.state
        not in {VisionReviewStatus.INCLUDED.value, VisionReviewStatus.EXCLUDED.value}
    ):
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")
    try:
        paths = WorkspacePaths.from_root(request.workspace)
        paths.ensure()
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        with repository.exclusive_workspace_operation():
            _validate_completed_analysis_binding(paths, repository, request.analysis_id)
            repository.set_vision_analysis_state(request.analysis_id, request.state)
        return VisionAnalysisStateResult(request.analysis_id, request.state)
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
        raise VisionAnalysisError("vision analysis is unsafe or unavailable") from error


def _validate_completed_analysis_binding(
    paths: WorkspacePaths,
    repository: SQLiteRepository,
    analysis_id: str,
) -> None:
    analysis = repository.get_vision_analysis(analysis_id)
    request_id = analysis.get("request_id")
    source_id = analysis.get("source_id")
    source_hash = analysis.get("source_content_hash")
    result_hash = analysis.get("result_hash")
    relative_path = analysis.get("relative_path")
    adapter = analysis.get("adapter")
    confidence = analysis.get("confidence")
    if (
        analysis.get("status") != VisionRequestStatus.COMPLETED.value
        or not _safe_identifier(request_id)
        or type(source_id) is not int
        or not _is_sha256(source_hash)
        or not _is_sha256(result_hash)
        or type(relative_path) is not str
        or type(adapter) is not str
        or confidence not in _CONFIDENCES
    ):
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")

    stored_request = repository.get_vision_request(request_id)
    if (
        stored_request["id"] != request_id
        or stored_request["source_id"] != source_id
        or stored_request["source_content_hash"] != source_hash
        or stored_request["adapter"] != adapter
        or stored_request["status"] != VisionRequestStatus.COMPLETED.value
    ):
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")

    expected_relative_path = (
        Path("derived") / "vision" / f"analysis-{request_id}-{result_hash}.json"
    )
    if Path(relative_path) != expected_relative_path:
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")

    source = _approved_local_source(repository, source_id)
    approval = load_approval(paths)
    if (
        source.uri not in approval.approved_source_uris
        or source.status not in {SourceStatus.APPROVED, SourceStatus.INGESTED}
    ):
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    checked = validate_approved_vision_source(source.uri, policy)
    if checked.content_hash != source_hash:
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")

    snapshot = read_managed_relative_file(paths.root, expected_relative_path)
    if snapshot.content_hash != result_hash:
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")
    try:
        parsed = parse_completed_vision_result_bytes(snapshot.content)
    except VisionAnalysisError as error:
        raise VisionAnalysisError("vision analysis is unsafe or unavailable") from error
    if (
        parsed["request_id"] != request_id
        or parsed["source_content_hash"] != source_hash
        or parsed["adapter"] != adapter
        or parsed["status"] != VisionRequestStatus.COMPLETED.value
        or parsed["confidence"] != confidence
    ):
        raise VisionAnalysisError("vision analysis is unsafe or unavailable")


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


def _parse_vision_result(payload: object) -> dict[str, object]:
    if type(payload) is not dict:
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    keys = frozenset(payload)
    status = payload.get("status")
    if status == VisionRequestStatus.COMPLETED.value:
        if keys != _COMPLETED_KEYS:
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        _require_result_header(payload)
        if payload["confidence"] not in _CONFIDENCES:
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        if payload["requires_manual_review"] is not True:
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        payload["summary"] = _safe_text(payload["summary"], _MAX_SUMMARY_LENGTH)
        observations = payload["observations"]
        if type(observations) is not list or len(observations) > _MAX_OBSERVATIONS:
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        payload["observations"] = [_safe_observation(item) for item in observations]
        return payload
    if status in _STATUS_ONLY_VALUES:
        if keys != _STATUS_ONLY_KEYS:
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        _require_result_header(payload)
        reason_code = payload["reason_code"]
        if (
            type(reason_code) is not str
            or not 1 <= len(reason_code) <= _MAX_REASON_CODE_LENGTH
            or _REASON_CODE.fullmatch(reason_code) is None
        ):
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        return payload
    raise VisionAnalysisError("vision result is unsafe or unavailable")


def parse_completed_vision_result(payload: object) -> dict[str, object]:
    parsed = _parse_vision_result(payload)
    if parsed["status"] != VisionRequestStatus.COMPLETED.value:
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    return parsed


def parse_completed_vision_result_bytes(content: bytes) -> dict[str, object]:
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except VisionAnalysisError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise VisionAnalysisError("vision result is unsafe or unavailable") from error
    return parse_completed_vision_result(payload)


def _reject_duplicate_json_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise VisionAnalysisError("vision result is unsafe or unavailable")
        result[key] = value
    return result


def _require_result_header(payload: dict[str, object]) -> None:
    if (
        payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("adapter") != _ADAPTER
        or not _safe_identifier(payload.get("request_id"))
        or not _is_sha256(payload.get("source_content_hash"))
    ):
        raise VisionAnalysisError("vision result is unsafe or unavailable")


def _safe_observation(value: object) -> dict[str, str]:
    if type(value) is not dict or frozenset(value) != {"kind", "text", "confidence"}:
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    kind = value["kind"]
    confidence = value["confidence"]
    if kind not in _OBSERVATION_KINDS or confidence not in _CONFIDENCES:
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    return {
        "kind": kind,
        "text": _safe_text(value["text"], _MAX_OBSERVATION_TEXT_LENGTH),
        "confidence": confidence,
    }


def _completed_vision_result(payload: dict[str, object]) -> VisionAnalysisResult:
    return VisionAnalysisResult(
        request_id=payload["request_id"],
        source_content_hash=payload["source_content_hash"],
        adapter=payload["adapter"],
        status=VisionRequestStatus.COMPLETED,
        confidence=payload["confidence"],
        summary=payload["summary"],
        observations=tuple(payload["observations"]),
        requires_manual_review=True,
    )


def _canonical_result_json(result: VisionAnalysisResult) -> str:
    payload = {
        "adapter": result.adapter,
        "confidence": result.confidence,
        "observations": list(result.observations),
        "request_id": result.request_id,
        "requires_manual_review": result.requires_manual_review,
        "schema_version": _SCHEMA_VERSION,
        "source_content_hash": result.source_content_hash,
        "status": result.status.value,
        "summary": result.summary,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def _safe_text(value: object, maximum_length: int) -> str:
    if type(value) is not str:
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > maximum_length
        or any(unicodedata.category(character).startswith("C") for character in normalized)
        or contains_hidden_secret_shaped_public_value(normalized)
    ):
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    masked = mask_secrets(normalized)
    if not masked or _looks_like_locator(masked):
        raise VisionAnalysisError("vision result is unsafe or unavailable")
    return masked


def _looks_like_locator(value: str) -> bool:
    lowered = value.casefold()
    return (
        "file://" in lowered
        or "http://" in lowered
        or "https://" in lowered
        or _POSIX_PATH.search(value) is not None
        or _WINDOWS_PATH.search(value) is not None
        or _UNC_PATH.search(value) is not None
    )


def _safe_identifier(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
