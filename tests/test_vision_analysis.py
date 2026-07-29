from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.extractors as extractors
import portfolio_maker.application.vision_analysis as vision_analysis
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import (
    ApplyVisionAnalysisRequest,
    PrepareVisionAnalysisRequest,
    SetVisionAnalysisStateRequest,
)
from portfolio_maker.application.vision_analysis import (
    VisionAnalysisError,
    apply_vision_analysis,
    prepare_vision_analysis,
    set_vision_analysis_state,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.domain.vision_models import (
    AppliedVisionAnalysisResult,
    VisionAnalysisResult,
    VisionRequestStatus,
    VisionReviewStatus,
)
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.infrastructure.vision_store import VisionStore
from portfolio_maker.workspace import WorkspacePaths


def approve_binary_source(
    workspace: Path,
    source_path: Path,
    content: bytes,
    *,
    display_name: str | None = None,
    approval_overrides: dict[str, object] | None = None,
) -> tuple[WorkspacePaths, int]:
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(content)
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    approval.update(approval_overrides or {})
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name=display_name or source_path.name,
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    return paths, source_id


def request_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM vision_analysis_requests").fetchone()[0]
        )


def prepare_approved_vision_request(
    workspace: Path,
    tmp_path: Path,
    *,
    content: bytes = b"approved source",
) -> tuple[WorkspacePaths, int, object, Path]:
    source_path = tmp_path / "source.pdf"
    paths, source_id = approve_binary_source(workspace, source_path, content)
    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )
    return paths, source_id, prepared, source_path


def write_vision_result(
    tmp_path: Path,
    prepared: object,
    **overrides: object,
) -> Path:
    payload: dict[str, object] = {
        "schema_version": "vision-analysis-v1",
        "request_id": prepared.id,
        "source_content_hash": prepared.source_content_hash,
        "adapter": "codex-app-vision-v1",
        "status": "completed",
        "confidence": "medium",
        "summary": "Delivered a portfolio document",
        "observations": [
            {"kind": "deliverable", "text": "Produced a document", "confidence": "high"}
        ],
        "requires_manual_review": True,
    }
    payload.update(overrides)
    result_path = tmp_path / f"result-{prepared.id}.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    return result_path


def analysis_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM vision_analyses").fetchone()[0])


def evidence_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0])


def apply_completed_vision_analysis(
    workspace: Path,
    tmp_path: Path,
) -> tuple[WorkspacePaths, int, object, object]:
    paths, source_id, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    applied = apply_vision_analysis(
        ApplyVisionAnalysisRequest(workspace, write_vision_result(tmp_path, prepared))
    )
    assert applied is not None
    return paths, source_id, prepared, applied


def apply_completed_vision_analysis_with_source(
    workspace: Path,
    tmp_path: Path,
) -> tuple[WorkspacePaths, int, object, object, Path]:
    paths, source_id, prepared, source_path = prepare_approved_vision_request(
        workspace,
        tmp_path,
    )
    applied = apply_vision_analysis(
        ApplyVisionAnalysisRequest(workspace, write_vision_result(tmp_path, prepared))
    )
    assert applied is not None
    return paths, source_id, prepared, applied, source_path


def vision_evidence_stable_id(applied: object) -> str:
    return f"vision-analysis:{applied.id}:{applied.result_hash}"


def replace_analysis_with_duplicate_key_json(
    paths: WorkspacePaths,
    repository: SQLiteRepository,
    applied: object,
    *,
    nested: bool,
) -> str:
    analysis = repository.get_vision_analysis(applied.id)
    original_path = paths.root / analysis["relative_path"]
    original = original_path.read_text(encoding="utf-8")
    if nested:
        replacement = original.replace(
            '"text":"Produced a document"',
            '"text":"Produced a document","text":"Duplicate nested summary"',
        )
    else:
        replacement = original[:-2] + ',"summary":"Duplicate top-level summary"}\n'
    result_hash = hashlib.sha256(replacement.encode("utf-8")).hexdigest()
    replacement_path = paths.vision_analyses_dir / (
        f"analysis-{analysis['request_id']}-{result_hash}.json"
    )
    replacement_path.write_text(replacement, encoding="utf-8")
    original_path.unlink()
    old_stable_id = f"vision-analysis:{analysis['id']}:{analysis['result_hash']}"
    new_stable_id = f"vision-analysis:{analysis['id']}:{result_hash}"
    with repository._connection() as connection:
        connection.execute(
            """
            UPDATE vision_analyses
            SET result_hash = ?, relative_path = ?
            WHERE id = ?
            """,
            (str(result_hash), str(replacement_path.relative_to(paths.root)), applied.id),
        )
        connection.execute(
            """
            UPDATE evidence_items
            SET stable_id = ?, content_hash = ?
            WHERE stable_id = ?
            """,
            (new_stable_id, result_hash, old_stable_id),
        )
    return new_stable_id


def test_completed_pending_analysis_has_no_derived_evidence(workspace, tmp_path):
    paths, _, _, applied = apply_completed_vision_analysis(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)

    assert repository.get_vision_analysis(applied.id)["review_status"] == "pending"
    assert repository.evidence_id_for_stable_id(vision_evidence_stable_id(applied)) is None
    assert evidence_count(repository) == 0


def test_explicit_inclusion_creates_one_exact_derived_evidence(workspace, tmp_path):
    paths, source_id, _, applied = apply_completed_vision_analysis(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)

    result = set_vision_analysis_state(
        SetVisionAnalysisStateRequest(workspace, applied.id, "included")
    )
    evidence_id = repository.evidence_id_for_stable_id(vision_evidence_stable_id(applied))

    assert result.analysis_id == applied.id
    assert result.state == "included"
    assert repository.get_vision_analysis(applied.id)["review_status"] == "included"
    assert evidence_id is not None
    with repository._read_connection() as connection:
        evidence = connection.execute(
            """
            SELECT source_id, locator, stable_id, content_hash, public_safe,
                   origin_type, origin_visibility
            FROM evidence_items WHERE id = ?
            """,
            (evidence_id,),
        ).fetchone()
    assert tuple(evidence) == (
        source_id,
        f"derived:vision-analysis:{applied.id}",
        vision_evidence_stable_id(applied),
        applied.result_hash,
        0,
        "local",
        "private",
    )

    set_vision_analysis_state(SetVisionAnalysisStateRequest(workspace, applied.id, "included"))
    assert evidence_count(repository) == 1


def test_low_confidence_analysis_cannot_be_included(workspace, tmp_path):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    applied = apply_vision_analysis(
        ApplyVisionAnalysisRequest(
            workspace,
            write_vision_result(tmp_path, prepared, confidence="low"),
        )
    )
    assert applied is not None
    repository = SQLiteRepository(paths.db_path)

    with pytest.raises(VisionAnalysisError, match="vision analysis is unsafe"):
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, "included")
        )

    assert repository.get_vision_analysis(applied.id)["review_status"] == "pending"
    assert repository.evidence_id_for_stable_id(
        vision_evidence_stable_id(applied)
    ) is None
    assert evidence_count(repository) == 0


@pytest.mark.parametrize(
    ("confidence", "state"),
    (
        ("low", "excluded"),
        ("medium", "included"),
        ("high", "included"),
    ),
)
def test_low_confidence_exclusion_and_medium_high_inclusion_decisions(
    workspace,
    tmp_path,
    confidence,
    state,
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    applied = apply_vision_analysis(
        ApplyVisionAnalysisRequest(
            workspace,
            write_vision_result(tmp_path, prepared, confidence=confidence),
        )
    )
    assert applied is not None
    repository = SQLiteRepository(paths.db_path)

    result = set_vision_analysis_state(
        SetVisionAnalysisStateRequest(workspace, applied.id, state)
    )

    assert result.state == state
    assert repository.get_vision_analysis(applied.id)["review_status"] == state
    evidence_id = repository.evidence_id_for_stable_id(
        vision_evidence_stable_id(applied)
    )
    if state == "included":
        assert evidence_id is not None
    else:
        assert evidence_id is None
        assert evidence_count(repository) == 0


def test_repository_mark_included_rejects_low_confidence_analysis(
    workspace,
    tmp_path,
):
    paths, source_id, prepared, _ = prepare_approved_vision_request(
        workspace,
        tmp_path,
    )
    applied = apply_vision_analysis(
        ApplyVisionAnalysisRequest(
            workspace,
            write_vision_result(tmp_path, prepared, confidence="low"),
        )
    )
    assert applied is not None
    repository = SQLiteRepository(paths.db_path)
    evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator=f"derived:vision-analysis:{applied.id}",
        stable_id=vision_evidence_stable_id(applied),
        content_hash=applied.result_hash,
        public_safe=False,
        origin_type="local",
        origin_visibility="private",
    )

    with pytest.raises(
        RepositoryError,
        match="vision analysis evidence does not match",
    ):
        repository.mark_vision_analysis_included(applied.id, evidence_id)

    assert repository.get_vision_analysis(applied.id)["review_status"] == "pending"
    assert repository.evidence_id_for_stable_id(
        vision_evidence_stable_id(applied)
    ) == evidence_id


def test_repository_insert_rejects_low_confidence_included_analysis(
    workspace,
    tmp_path,
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    content = b'{"synthetic":"low-confidence-included"}'
    result_hash = hashlib.sha256(content).hexdigest()
    analysis_path = (
        paths.vision_analyses_dir
        / f"analysis-{prepared.id}-{result_hash}.json"
    )
    analysis_path.write_bytes(content)
    repository = SQLiteRepository(paths.db_path)
    analysis = AppliedVisionAnalysisResult(
        id="1" * 32,
        result_hash=result_hash,
        analysis_path=analysis_path,
        review_status=VisionReviewStatus.INCLUDED,
    )
    result = VisionAnalysisResult(
        request_id=prepared.id,
        source_content_hash=prepared.source_content_hash,
        adapter="codex-app-vision-v1",
        status=VisionRequestStatus.COMPLETED,
        confidence="low",
        summary="Low-confidence summary",
        observations=(),
        requires_manual_review=True,
    )

    with pytest.raises(RepositoryError, match="confidence is not eligible"):
        repository.insert_vision_analysis(analysis, result)

    assert analysis_count(repository) == 0
    assert repository.get_vision_request(prepared.id)["status"] == "pending"


@pytest.mark.parametrize("transition", ("set", "mark"))
def test_repository_confidence_binding_rejects_managed_low_after_db_tamper(
    workspace,
    tmp_path,
    transition,
):
    paths, source_id, prepared, _ = prepare_approved_vision_request(
        workspace,
        tmp_path,
    )
    applied = apply_vision_analysis(
        ApplyVisionAnalysisRequest(
            workspace,
            write_vision_result(tmp_path, prepared, confidence="low"),
        )
    )
    assert applied is not None
    repository = SQLiteRepository(paths.db_path)
    with repository._connection() as connection:
        connection.execute(
            "UPDATE vision_analyses SET confidence = 'medium' WHERE id = ?",
            (applied.id,),
        )

    matching_evidence_id = None
    if transition == "mark":
        matching_evidence_id = repository.upsert_evidence_item(
            source_id=source_id,
            snapshot_id=None,
            github_activity_id=None,
            locator=f"derived:vision-analysis:{applied.id}",
            stable_id=vision_evidence_stable_id(applied),
            content_hash=applied.result_hash,
            public_safe=False,
            origin_type="local",
            origin_visibility="private",
        )
    unrelated_evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator="derived:unrelated",
        stable_id=f"unrelated-{transition}",
        content_hash=None,
        public_safe=False,
        origin_type="local",
        origin_visibility="private",
    )
    before_evidence = evidence_count(repository)

    with pytest.raises(RepositoryError):
        if transition == "set":
            repository.set_vision_analysis_state(applied.id, "included")
        else:
            assert matching_evidence_id is not None
            repository.mark_vision_analysis_included(
                applied.id,
                matching_evidence_id,
            )

    assert repository.get_vision_analysis(applied.id)["review_status"] == "pending"
    assert evidence_count(repository) == before_evidence
    assert repository.evidence_id_for_stable_id(
        f"unrelated-{transition}"
    ) == unrelated_evidence_id
    assert repository.evidence_id_for_stable_id(
        vision_evidence_stable_id(applied)
    ) == matching_evidence_id


class _SyntheticConfidenceValue:
    pass


@pytest.mark.parametrize(
    "confidence",
    ("certain", None, True, _SyntheticConfidenceValue()),
    ids=("unexpected-string", "none", "bool", "value-object"),
)
def test_repository_confidence_insert_rejects_noncanonical_values(
    workspace,
    tmp_path,
    confidence,
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)
    repository.finish_vision_request(
        prepared.id,
        VisionRequestStatus.COMPLETED,
    )
    payload = {
        "schema_version": "vision-analysis-v1",
        "request_id": prepared.id,
        "source_content_hash": prepared.source_content_hash,
        "adapter": prepared.adapter,
        "status": "completed",
        "confidence": "medium",
        "summary": "Synthetic summary",
        "observations": [],
        "requires_manual_review": True,
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    result_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    analysis_path = (
        paths.vision_analyses_dir
        / f"analysis-{prepared.id}-{result_hash}.json"
    )
    analysis_path.write_text(content, encoding="utf-8")
    analysis = AppliedVisionAnalysisResult(
        id="2" * 32,
        result_hash=result_hash,
        analysis_path=analysis_path,
        review_status=VisionReviewStatus.INCLUDED,
    )
    result = VisionAnalysisResult(
        request_id=prepared.id,
        source_content_hash=prepared.source_content_hash,
        adapter=prepared.adapter,
        status=VisionRequestStatus.COMPLETED,
        confidence=confidence,
        summary="Synthetic summary",
        observations=(),
        requires_manual_review=True,
    )

    with pytest.raises(RepositoryError):
        repository.insert_vision_analysis(analysis, result)

    assert analysis_count(repository) == 0
    assert evidence_count(repository) == 0


def test_repository_transition_failure_keeps_pending_analysis_without_evidence(
    workspace, tmp_path
):
    paths, _, _, applied = apply_completed_vision_analysis(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)
    with repository._connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_vision_review_transition
            BEFORE UPDATE OF review_status ON vision_analyses
            BEGIN
                SELECT RAISE(ABORT, 'synthetic transition failure');
            END
            """
        )
    with pytest.raises(VisionAnalysisError, match="vision analysis is unsafe"):
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, "included")
        )

    assert repository.get_vision_analysis(applied.id)["review_status"] == "pending"
    assert repository.evidence_id_for_stable_id(vision_evidence_stable_id(applied)) is None


@pytest.mark.parametrize("state", ("included", "excluded"))
@pytest.mark.parametrize(
    "mutation",
    (
        "source_changed",
        "analysis_source_id",
        "source_hash",
        "adapter",
        "request_id",
        "request_status",
        "result_hash",
        "confidence",
        "relative_path",
        "missing",
        "tampered",
        "symlink",
        "malformed",
        "approval_revoked",
        "source_status",
    ),
)
def test_state_transition_rejects_stale_or_unbound_completed_analysis(
    workspace,
    tmp_path,
    state,
    mutation,
):
    paths, source_id, prepared, applied, source_path = (
        apply_completed_vision_analysis_with_source(workspace, tmp_path)
    )
    repository = SQLiteRepository(paths.db_path)
    original_stable_id = vision_evidence_stable_id(applied)
    if state == "excluded":
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, "included")
        )
    initial_state = repository.get_vision_analysis(applied.id)["review_status"]
    initial_evidence_id = repository.evidence_id_for_stable_id(original_stable_id)

    analysis = repository.get_vision_analysis(applied.id)
    analysis_path = paths.root / analysis["relative_path"]
    if mutation == "source_changed":
        source_path.write_bytes(b"changed source")
    elif mutation == "analysis_source_id":
        alternate_path = tmp_path / "alternate-source.pdf"
        alternate_path.write_bytes(b"alternate source")
        alternate_id = repository.upsert_source(
            Source(
                None,
                SourceType.LOCAL_FILE,
                alternate_path.resolve().as_uri(),
                alternate_path.name,
                None,
                SourceStatus.APPROVED,
            )
        )
        approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
        approval["approved_source_uris"].append(alternate_path.resolve().as_uri())
        paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET source_id = ? WHERE id = ?",
                (alternate_id, applied.id),
            )
    elif mutation == "source_hash":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET source_content_hash = ? WHERE id = ?",
                ("0" * 64, applied.id),
            )
    elif mutation == "adapter":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET adapter = ? WHERE id = ?",
                ("other-adapter", applied.id),
            )
    elif mutation == "request_id":
        mismatched = prepare_vision_analysis(
            PrepareVisionAnalysisRequest(workspace, source_id, True)
        )
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET request_id = ? WHERE id = ?",
                (mismatched.id, applied.id),
            )
    elif mutation == "request_status":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analysis_requests SET status = 'pending' WHERE id = ?",
                (prepared.id,),
            )
    elif mutation == "result_hash":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET result_hash = ? WHERE id = ?",
                ("0" * 64, applied.id),
            )
    elif mutation == "confidence":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET confidence = 'high' WHERE id = ?",
                (applied.id,),
            )
    elif mutation == "relative_path":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE vision_analyses SET relative_path = ? WHERE id = ?",
                ("reviews/source-approval.json", applied.id),
            )
    elif mutation == "missing":
        analysis_path.unlink()
    elif mutation == "tampered":
        analysis_path.write_text("{\"tampered\": true}", encoding="utf-8")
    elif mutation == "symlink":
        replacement = tmp_path / "replacement-analysis.json"
        replacement.write_bytes(analysis_path.read_bytes())
        analysis_path.unlink()
        analysis_path.symlink_to(replacement)
    elif mutation == "malformed":
        analysis_path.write_text("{\"status\": \"completed\"}", encoding="utf-8")
    elif mutation == "approval_revoked":
        approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
        approval["approved_source_uris"] = []
        paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    else:
        with repository._connection() as connection:
            connection.execute(
                "UPDATE sources SET status = 'discovered' WHERE id = ?",
                (source_id,),
            )

    with pytest.raises(VisionAnalysisError, match="vision analysis is unsafe"):
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, state)
        )

    assert repository.get_vision_analysis(applied.id)["review_status"] == initial_state
    assert repository.evidence_id_for_stable_id(original_stable_id) == initial_evidence_id


@pytest.mark.parametrize("state", ("included", "excluded"))
@pytest.mark.parametrize("nested", (False, True))
def test_state_transition_rejects_duplicate_key_managed_result(
    workspace,
    tmp_path,
    state,
    nested,
):
    paths, _, _, applied = apply_completed_vision_analysis(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)
    if state == "excluded":
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, "included")
        )
    initial_state = repository.get_vision_analysis(applied.id)["review_status"]
    stable_id = replace_analysis_with_duplicate_key_json(
        paths,
        repository,
        applied,
        nested=nested,
    )
    initial_evidence_id = repository.evidence_id_for_stable_id(stable_id)

    with pytest.raises(VisionAnalysisError, match="vision analysis is unsafe"):
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, state)
        )

    assert repository.get_vision_analysis(applied.id)["review_status"] == initial_state
    assert repository.evidence_id_for_stable_id(stable_id) == initial_evidence_id


def test_exclusion_removes_only_unreferenced_evidence_and_refuses_references(workspace, tmp_path):
    paths, source_id, _, applied = apply_completed_vision_analysis(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)
    set_vision_analysis_state(SetVisionAnalysisStateRequest(workspace, applied.id, "included"))
    stable_id = vision_evidence_stable_id(applied)
    evidence_id = repository.evidence_id_for_stable_id(stable_id)
    assert evidence_id is not None
    unrelated_evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator="derived:unrelated",
        stable_id="unrelated-evidence",
        content_hash=None,
        public_safe=False,
        origin_type="local",
        origin_visibility="private",
    )

    result = set_vision_analysis_state(
        SetVisionAnalysisStateRequest(workspace, applied.id, "excluded")
    )
    assert result.state == "excluded"
    assert repository.get_vision_analysis(applied.id)["review_status"] == "excluded"
    assert repository.evidence_id_for_stable_id(stable_id) is None
    assert repository.evidence_id_for_stable_id("unrelated-evidence") == unrelated_evidence_id

    set_vision_analysis_state(SetVisionAnalysisStateRequest(workspace, applied.id, "included"))
    evidence_id = repository.evidence_id_for_stable_id(stable_id)
    assert evidence_id is not None
    project_id = repository.upsert_project(f"local:{source_id}", public_safe=False)
    claim_id = repository.upsert_career_claim(project_id, "Reviewed a safe summary", False)
    repository.link_claim_evidence(claim_id, evidence_id, "direct")

    with pytest.raises(VisionAnalysisError, match="vision analysis is unsafe"):
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, applied.id, "excluded")
        )
    assert repository.get_vision_analysis(applied.id)["review_status"] == "included"
    assert repository.evidence_id_for_stable_id(stable_id) == evidence_id


@pytest.mark.parametrize(
    ("analysis_id", "state"),
    (
        ("unknown-analysis", "included"),
        (".", "included"),
        ("", "excluded"),
        ("pending", "included"),
        ("pending", "excluded"),
        ("pending", "automatic"),
    ),
)
def test_state_changes_require_a_completed_known_analysis(
    workspace,
    tmp_path,
    analysis_id,
    state,
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)
    resolved_id = prepared.id if analysis_id == "pending" else analysis_id

    with pytest.raises(VisionAnalysisError, match="vision analysis is unsafe"):
        set_vision_analysis_state(
            SetVisionAnalysisStateRequest(workspace, resolved_id, state)
        )

    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert evidence_count(repository) == 0


def test_apply_completed_result_masks_secret_and_starts_pending(workspace, tmp_path):
    paths, _, prepared, source_path = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(
        tmp_path,
        prepared,
        summary="api_key=synthetic-secret-value delivered a document",
        observations=[
            {
                "kind": "technology",
                "text": "Bearer synthetic-token-value",
                "confidence": "medium",
            }
        ],
    )

    applied = apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    request = repository.get_vision_request(prepared.id)
    analysis = repository.get_vision_analysis(applied.id)
    persisted_text = applied.analysis_path.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert applied.review_status is VisionReviewStatus.PENDING
    assert request["status"] == "completed"
    assert analysis["request_id"] == prepared.id
    assert analysis["source_content_hash"] == prepared.source_content_hash
    assert analysis["result_hash"] == applied.result_hash
    assert hashlib.sha256(persisted_text.encode("utf-8")).hexdigest() == applied.result_hash
    assert applied.analysis_path.name == f"analysis-{prepared.id}-{applied.result_hash}.json"
    assert persisted["summary"] == "api_key=[REDACTED]"
    assert persisted["observations"][0]["text"] == "[REDACTED]"
    assert "synthetic-secret-value" not in persisted_text
    assert "synthetic-token-value" not in persisted_text
    assert str(result_path) not in persisted_text
    assert str(paths.workspace) not in persisted_text
    assert source_path.resolve().as_uri() not in persisted_text
    assert str(source_path.resolve()) not in persisted_text
    assert evidence_count(repository) == 0


@pytest.mark.parametrize(
    "overrides",
    (
        {"extra": "unexpected"},
        {"schema_version": "vision-analysis-v2"},
        {"adapter": "wrong-adapter"},
        {"status": "pending"},
        {"source_content_hash": "invalid"},
        {"confidence": "certain"},
        {"requires_manual_review": False},
        {"summary": True},
        {"observations": [{"kind": "unsafe", "text": "text", "confidence": "low"}]},
        {"observations": [{}] * 51},
        {"summary": "x" * 4_001},
        {"summary": ""},
    ),
)
def test_apply_rejects_nonclosed_completed_schema_without_mutation(
    workspace, tmp_path, overrides
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared, **overrides)

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0
    assert list(paths.vision_analyses_dir.iterdir()) == []


def test_apply_rejects_duplicate_result_keys_without_mutation(workspace, tmp_path):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared)
    result_path.write_text(
        result_path.read_text(encoding="utf-8")[:-1]
        + f', "request_id": "{prepared.id}"}}',
        encoding="utf-8",
    )

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0


def test_apply_rejects_missing_key_and_unknown_request_without_mutation(
    workspace, tmp_path
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    missing_key_path = write_vision_result(tmp_path, prepared)
    missing_payload = json.loads(missing_key_path.read_text(encoding="utf-8"))
    missing_payload.pop("summary")
    missing_key_path.write_text(json.dumps(missing_payload), encoding="utf-8")
    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, missing_key_path))

    unknown_path = write_vision_result(
        tmp_path,
        prepared,
        request_id="unknown-request",
    )
    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, unknown_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0


def test_apply_rejects_payload_hash_mismatch_without_managed_output(workspace, tmp_path):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(
        tmp_path,
        prepared,
        source_content_hash="0" * 64,
    )

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0
    assert list(paths.vision_analyses_dir.iterdir()) == []


@pytest.mark.parametrize("mutation", ("unapproved", "excluded", "missing", "symlink"))
def test_apply_revalidates_current_source_before_persistence(
    workspace, tmp_path, mutation
):
    paths, _, prepared, source_path = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared)
    if mutation == "unapproved":
        approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
        approval["approved_source_uris"] = []
        paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    elif mutation == "excluded":
        approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
        approval["excluded_file_patterns"] = ["*.pdf"]
        paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    elif mutation == "missing":
        source_path.unlink()
    else:
        replacement = tmp_path / "replacement.pdf"
        replacement.write_bytes(b"replacement")
        source_path.unlink()
        source_path.symlink_to(replacement)

    with pytest.raises(VisionAnalysisError, match="unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0
    assert list(paths.vision_analyses_dir.iterdir()) == []


@pytest.mark.parametrize("malformation", ("invalid_utf8", "oversized", "parent_symlink"))
def test_apply_rejects_unsafe_result_file_without_request_mutation(
    workspace, tmp_path, malformation
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_parent = tmp_path / "results"
    result_parent.mkdir()
    result_path = write_vision_result(result_parent, prepared)
    if malformation == "invalid_utf8":
        result_path.write_bytes(b"\xff")
    elif malformation == "oversized":
        result_path.write_bytes(b"x" * 262_145)
    else:
        real_parent = tmp_path / "real-results"
        result_parent.rename(real_parent)
        result_parent.symlink_to(real_parent, target_is_directory=True)
        result_path = result_parent / result_path.name

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0


def test_apply_normalizes_completed_text_and_rejects_locator(workspace, tmp_path):
    _, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    normalized_path = write_vision_result(
        tmp_path,
        prepared,
        summary="Cafe\u0301 delivery",
    )
    applied = apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, normalized_path))
    assert json.loads(applied.analysis_path.read_text(encoding="utf-8"))["summary"] == "Café delivery"

    second_paths, _, second_prepared, _ = prepare_approved_vision_request(
        tmp_path / "second-workspace", tmp_path / "second-source"
    )
    for locator in (
        "See /synthetic/private/locator before review",
        "See /tmp before review",
        "See /Users/example/My Project/source.pdf before review",
        "See file:///synthetic/private/locator before review",
        "See https://example.invalid/private before review",
        r"See \\server\\share before review",
        r"See C:\\synthetic\\private before review",
    ):
        locator_path = write_vision_result(
            tmp_path,
            second_prepared,
            summary=locator,
        )
        with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
            apply_vision_analysis(
                ApplyVisionAnalysisRequest(second_paths.workspace, locator_path)
            )
    assert analysis_count(SQLiteRepository(second_paths.db_path)) == 0


def test_apply_allows_ordinary_slash_prose(workspace, tmp_path):
    _, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(
        tmp_path,
        prepared,
        summary="Reviewed API/CLI interoperability",
    )

    applied = apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    assert applied is not None


@pytest.mark.parametrize(
    "locator",
    (
        "Keep the review notes in /tmp",
        r"Use \\server\\share for the result",
    ),
)
def test_apply_rejects_locator_in_observation(workspace, tmp_path, locator):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(
        tmp_path,
        prepared,
        observations=[
            {
                "kind": "context",
                "text": locator,
                "confidence": "medium",
            }
        ],
    )

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    assert analysis_count(SQLiteRepository(paths.db_path)) == 0


def test_apply_maps_deeply_nested_json_to_controlled_error(workspace, tmp_path):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = tmp_path / "deep-result.json"
    nesting = 10_000
    result_path.write_text("[" * nesting + "]" * nesting, encoding="utf-8")

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0


@pytest.mark.parametrize("status", ("unsupported", "unavailable", "failed", "cancelled"))
def test_apply_status_only_finishes_request_without_analysis(workspace, tmp_path, status):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(
        tmp_path,
        prepared,
        status=status,
        reason_code="capability_unavailable",
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    for key in ("confidence", "summary", "observations", "requires_manual_review"):
        payload.pop(key)
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    assert apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path)) is None

    repository = SQLiteRepository(paths.db_path)
    request = repository.get_vision_request(prepared.id)
    assert request["status"] == status
    assert request["reason_code"] == "capability_unavailable"
    assert analysis_count(repository) == 0
    assert evidence_count(repository) == 0
    assert list(paths.vision_analyses_dir.iterdir()) == []


def test_apply_marks_changed_source_stale_without_analysis(workspace, tmp_path):
    paths, _, prepared, source_path = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared)
    source_path.write_bytes(b"changed source")

    with pytest.raises(VisionAnalysisError, match="vision source changed"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    request = repository.get_vision_request(prepared.id)
    assert request["status"] == "stale_source"
    assert analysis_count(repository) == 0
    assert evidence_count(repository) == 0
    assert list(paths.vision_analyses_dir.iterdir()) == []


def test_apply_rejects_replay_and_unsafe_result_symlink(workspace, tmp_path):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared)
    apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    second_workspace = tmp_path / "second-workspace"
    second_paths, _, second_prepared, _ = prepare_approved_vision_request(
        second_workspace, tmp_path / "second"
    )
    replacement = write_vision_result(tmp_path, second_prepared)
    unsafe_path = tmp_path / "unsafe-result.json"
    unsafe_path.symlink_to(replacement)
    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, unsafe_path))

    repository = SQLiteRepository(second_paths.db_path)
    assert repository.get_vision_request(second_prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0
    assert analysis_count(SQLiteRepository(paths.db_path)) == 1


@pytest.mark.parametrize("unsafe_target", (False, True))
def test_apply_does_not_overwrite_existing_or_unsafe_analysis_target(
    workspace, tmp_path, unsafe_target
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    result_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    target = paths.vision_analyses_dir / f"analysis-{prepared.id}-{result_hash}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing private data", encoding="utf-8")
    if unsafe_target:
        replacement = tmp_path / "replacement-analysis.json"
        replacement.write_text("replacement private data", encoding="utf-8")
        target.unlink()
        target.symlink_to(replacement)

    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0
    if unsafe_target:
        assert target.is_symlink()
    else:
        assert target.read_text(encoding="utf-8") == "existing private data"


def test_apply_maps_managed_write_and_repository_failures_without_success(
    workspace, tmp_path, monkeypatch
):
    paths, _, prepared, _ = prepare_approved_vision_request(workspace, tmp_path)
    result_path = write_vision_result(tmp_path, prepared)

    def fail_write(*_args, **_kwargs):
        raise OSError("synthetic managed write failure")

    monkeypatch.setattr(VisionStore, "write_analysis", fail_write)
    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))

    repository = SQLiteRepository(paths.db_path)
    assert repository.get_vision_request(prepared.id)["status"] == "pending"
    assert analysis_count(repository) == 0
    assert list(paths.vision_analyses_dir.iterdir()) == []

    monkeypatch.undo()
    second_paths, _, second_prepared, _ = prepare_approved_vision_request(
        tmp_path / "second-workspace", tmp_path / "second-source"
    )
    second_result_path = write_vision_result(tmp_path, second_prepared)

    def fail_insert(*_args, **_kwargs):
        raise RepositoryError("synthetic repository failure")

    monkeypatch.setattr(SQLiteRepository, "insert_vision_analysis", fail_insert)
    with pytest.raises(VisionAnalysisError, match="vision result is unsafe"):
        apply_vision_analysis(
            ApplyVisionAnalysisRequest(second_paths.workspace, second_result_path)
        )

    second_repository = SQLiteRepository(second_paths.db_path)
    assert second_repository.get_vision_request(second_prepared.id)["status"] == "pending"
    assert analysis_count(second_repository) == 0
    assert len(list(second_paths.vision_analyses_dir.iterdir())) == 1


def test_prepare_requires_confirmation_without_side_effect(workspace, tmp_path):
    paths, source_id = approve_binary_source(
        workspace, tmp_path / "resume.pdf", b"%PDF-synthetic"
    )
    repository = SQLiteRepository(paths.db_path)

    with pytest.raises(VisionAnalysisError, match="confirmation is required"):
        prepare_vision_analysis(
            PrepareVisionAnalysisRequest(workspace, source_id, False)
        )

    assert request_count(repository) == 0
    assert list(paths.vision_requests_dir.iterdir()) == []


@pytest.mark.parametrize("suffix", (".pdf", ".bin"))
def test_prepare_accepts_approved_binary_regardless_of_extension(
    workspace, tmp_path, suffix
):
    _, source_id = approve_binary_source(
        workspace, tmp_path / f"approved{suffix}", b"\x00binary\xffcontent"
    )

    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )

    assert prepared.status is VisionRequestStatus.PENDING


def test_prepare_streams_large_binary_without_text_limit_or_path_read_bytes(
    workspace, tmp_path, monkeypatch
):
    content = b"x" * 2_000_001
    _, source_id = approve_binary_source(workspace, tmp_path / "large.dat", content)
    original_read = extractors.os.read
    reads: list[int] = []

    def bounded_read(descriptor: int, size: int) -> bytes:
        reads.append(size)
        return original_read(descriptor, size)

    def no_path_read_bytes(_path: Path) -> bytes:
        raise AssertionError("vision preparation must stream the descriptor")

    monkeypatch.setattr(extractors.os, "read", bounded_read)
    monkeypatch.setattr(Path, "read_bytes", no_path_read_bytes)

    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )

    assert prepared.source_size == len(content)
    assert prepared.source_content_hash == hashlib.sha256(content).hexdigest()
    assert len(reads) > 2
    assert max(reads) <= 65_536


def test_prepare_rejects_final_symlink(workspace, tmp_path):
    source_path = tmp_path / "source.pdf"
    _, source_id = approve_binary_source(workspace, source_path, b"approved")
    replacement = tmp_path / "replacement.pdf"
    replacement.write_bytes(b"replacement")
    source_path.unlink()
    source_path.symlink_to(replacement)

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


def test_prepare_rejects_symlinked_parent(workspace, tmp_path):
    source_parent = tmp_path / "approved"
    source_path = source_parent / "source.bin"
    _, source_id = approve_binary_source(workspace, source_path, b"approved")
    replacement_parent = tmp_path / "replacement"
    replacement_parent.mkdir()
    (replacement_parent / "source.bin").write_bytes(b"replacement")
    source_parent.rename(tmp_path / "approved-original")
    source_parent.symlink_to(replacement_parent, target_is_directory=True)

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


def test_prepare_rejects_source_mutation_during_descriptor_stream(
    workspace, tmp_path, monkeypatch
):
    source_path = tmp_path / "changing.bin"
    _, source_id = approve_binary_source(workspace, source_path, b"a" * 131_072)
    original_read = extractors.os.read
    source_stat = source_path.stat()
    changed = False

    def mutate_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        descriptor_stat = os.fstat(descriptor)
        if (
            chunk
            and not changed
            and (descriptor_stat.st_dev, descriptor_stat.st_ino)
            == (source_stat.st_dev, source_stat.st_ino)
        ):
            source_path.write_bytes(b"b" * 131_073)
            changed = True
        return chunk

    monkeypatch.setattr(extractors.os, "read", mutate_after_first_read)

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


@pytest.mark.parametrize(
    ("source_kind", "approval_overrides", "filename"),
    (
        ("missing", {}, "missing.bin"),
        ("github", {}, "source.bin"),
        ("unapproved", {}, "source.bin"),
        ("forbidden", {"excluded_directories": ["blocked"]}, "blocked/source.bin"),
        ("excluded", {"excluded_file_patterns": ["*.bin"]}, "source.bin"),
        ("sensitive", {}, "credentials.json"),
    ),
)
def test_prepare_rejects_invalid_current_source_policy(
    workspace, tmp_path, source_kind, approval_overrides, filename
):
    paths = WorkspacePaths.from_root(workspace)
    if source_kind == "missing":
        write_sample_approval(paths)
        source_id = 999
    elif source_kind == "github":
        write_sample_approval(paths)
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        source_id = repository.upsert_source(
            Source(
                id=None,
                type=SourceType.GITHUB_REPOSITORY,
                uri="https://github.com/example/synthetic",
                display_name="example/synthetic",
                owner="example",
                status=SourceStatus.APPROVED,
            )
        )
    else:
        source_path = tmp_path / filename
        paths, source_id = approve_binary_source(
            workspace,
            source_path,
            b"approved",
            approval_overrides=approval_overrides,
        )
        if source_kind == "unapproved":
            approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
            approval["approved_source_uris"] = []
            paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))


def test_prepare_persists_matching_private_request_and_safe_receipt(
    workspace, tmp_path
):
    source_path = tmp_path / "private" / "slide.pdf"
    content = b"synthetic binary data"
    paths, source_id = approve_binary_source(
        workspace,
        source_path,
        content,
        display_name="/untrusted/private/display-name",
    )

    prepared = prepare_vision_analysis(
        PrepareVisionAnalysisRequest(workspace, source_id, True)
    )

    row = SQLiteRepository(paths.db_path).get_vision_request(prepared.id)
    receipt_path = paths.vision_requests_dir / f"request-{prepared.id}.json"
    receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert row["id"] == prepared.id
    assert row["source_id"] == source_id
    assert row["source_content_hash"] == prepared.source_content_hash
    assert row["source_size"] == prepared.source_size
    assert row["adapter"] == prepared.adapter
    assert row["status"] == "pending"
    assert row["requested_at"] == prepared.requested_at
    assert set(receipt) == {
        "request_id",
        "source_id",
        "display_name",
        "source_content_hash",
        "source_size",
        "adapter",
        "status",
        "confirmation_at",
        "requested_at",
    }
    assert receipt == {
        "request_id": prepared.id,
        "source_id": source_id,
        "display_name": source_path.name,
        "source_content_hash": hashlib.sha256(content).hexdigest(),
        "source_size": len(content),
        "adapter": "codex-app-vision-v1",
        "status": "pending",
        "confirmation_at": prepared.confirmation_at,
        "requested_at": prepared.requested_at,
    }
    assert source_path.resolve().as_uri() not in receipt_text
    assert str(source_path.resolve()) not in receipt_text
    assert content.decode("utf-8") not in receipt_text
    assert "device" not in receipt_text
    assert "inode" not in receipt_text
    assert "/untrusted/private/display-name" not in receipt_text


@pytest.mark.parametrize("unsafe_target", (False, True))
def test_prepare_does_not_overwrite_existing_or_unsafe_receipt_target(
    workspace, tmp_path, monkeypatch, unsafe_target
):
    paths, source_id = approve_binary_source(workspace, tmp_path / "source.pdf", b"approved")
    request_id = "request-collision"
    target = paths.vision_requests_dir / f"request-{request_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"existing": true}', encoding="utf-8")
    if unsafe_target:
        replacement = tmp_path / "replacement.json"
        replacement.write_text('{"replacement": true}', encoding="utf-8")
        target.unlink()
        target.symlink_to(replacement)

    def fixed_request(_cls, _source_id, content_hash, size):
        from portfolio_maker.domain.vision_models import PreparedVisionRequest

        return PreparedVisionRequest(
            request_id,
            _source_id,
            content_hash,
            size,
            "codex-app-vision-v1",
            VisionRequestStatus.PENDING,
            "2026-07-28T00:00:00Z",
            "2026-07-28T00:00:01Z",
        )

    monkeypatch.setattr(
        vision_analysis.PreparedVisionRequest,
        "new",
        classmethod(fixed_request),
    )

    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))

    if unsafe_target:
        assert target.is_symlink()
    else:
        assert target.read_text(encoding="utf-8") == '{"existing": true}'
