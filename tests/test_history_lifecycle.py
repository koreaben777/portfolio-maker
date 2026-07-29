from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from stat import S_IMODE, S_ISDIR, S_ISREG
import tarfile

import pytest

from portfolio_maker.application import history as history_application
from portfolio_maker.application import models as application_models
from portfolio_maker.application.history import (
    HistoryError,
    load_history_policy,
    plan_history_cleanup,
    write_sample_history_policy,
)
from portfolio_maker.application.models import HistoryPlanRequest
from portfolio_maker.domain.history_models import (
    CleanupExecutionState,
    HistoryPlan,
    HistoryPolicy,
    HistoryUnit,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure import managed_files
from portfolio_maker.infrastructure.history_store import HistoryStore
from portfolio_maker.infrastructure.sqlite_repository import (
    CommitOutcomeAmbiguousError,
    DatabaseFamilyChangedError,
    RepositoryError,
    SQLiteRepository,
)
from portfolio_maker.workspace import WorkspacePaths


@dataclass(frozen=True)
class _SnapshotState:
    paths: WorkspacePaths
    repository: SQLiteRepository
    source_uri: str
    old_path: Path
    latest_path: Path
    old_id: int


class _SyntheticFinalizerConnection(sqlite3.Connection):
    fail_close = False
    fail_close_after_commit = False
    close_error_type = RuntimeError
    commit_callback = None
    commit_succeeded = False

    def commit(self) -> None:
        super().commit()
        self.commit_succeeded = True
        if self.commit_callback is not None:
            self.commit_callback()

    def close(self) -> None:
        fail_after_close = self.fail_close or (
            self.fail_close_after_commit and self.commit_succeeded
        )
        super().close()
        if fail_after_close:
            stage = "before" if self.fail_close else "after"
            raise self.close_error_type(
                f"injected connection close failure {stage} commit"
            )


def test_write_and_load_history_policy_uses_exact_v1_schema(workspace) -> None:
    paths = WorkspacePaths.from_root(workspace)

    policy_path = write_sample_history_policy(paths)

    assert policy_path == paths.history_policy_path
    assert json.loads(policy_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "minimum_age_days": 30,
        "keep_latest_text_snapshots_per_source": 1,
        "retain_included_vision_analyses": True,
        "retain_pending_review_vision_analyses": True,
        "retain_incomplete_vision_requests": True,
    }
    assert S_IMODE(policy_path.stat().st_mode) & 0o077 == 0
    assert load_history_policy(paths) == HistoryPolicy.default()


@pytest.mark.parametrize(
    "payload",
    (
        b"\xff\xfe",
        b"{",
        b"[]",
        b"{}",
        json.dumps(
            {
                "version": 1,
                "minimum_age_days": 30,
                "keep_latest_text_snapshots_per_source": 1,
                "retain_included_vision_analyses": True,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
                "unknown": "unsafe",
            }
        ).encode(),
        json.dumps(
            {
                "version": 2,
                "minimum_age_days": 30,
                "keep_latest_text_snapshots_per_source": 1,
                "retain_included_vision_analyses": True,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
            }
        ).encode(),
        json.dumps(
            {
                "version": True,
                "minimum_age_days": 30,
                "keep_latest_text_snapshots_per_source": 1,
                "retain_included_vision_analyses": True,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
            }
        ).encode(),
        json.dumps(
            {
                "version": 1,
                "minimum_age_days": -1,
                "keep_latest_text_snapshots_per_source": 1,
                "retain_included_vision_analyses": True,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
            }
        ).encode(),
        json.dumps(
            {
                "version": 1,
                "minimum_age_days": 30,
                "keep_latest_text_snapshots_per_source": 0,
                "retain_included_vision_analyses": True,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
            }
        ).encode(),
        json.dumps(
            {
                "version": 1,
                "minimum_age_days": 30,
                "keep_latest_text_snapshots_per_source": 1,
                "retain_included_vision_analyses": False,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
            }
        ).encode(),
        (
            b'{"version":1,"version":1,"minimum_age_days":30,'
            b'"keep_latest_text_snapshots_per_source":1,'
            b'"retain_included_vision_analyses":true,'
            b'"retain_pending_review_vision_analyses":true,'
            b'"retain_incomplete_vision_requests":true}'
        ),
    ),
    ids=(
        "invalid-utf8",
        "invalid-json",
        "non-object",
        "missing-fields",
        "unknown-field",
        "wrong-version",
        "bool-version",
        "negative-age",
        "retain-zero-latest",
        "mandatory-retention-disabled",
        "duplicate-field",
    ),
)
def test_malformed_or_unsafe_history_policy_fails_closed(
    workspace,
    payload,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.history_policy_path.write_bytes(payload)

    with pytest.raises(HistoryError, match="history policy"):
        load_history_policy(paths)

    assert paths.history_policy_path.read_bytes() == payload


@pytest.mark.parametrize(
    ("field", "boundary"),
    (
        ("minimum_age_days", timedelta.max.days),
        ("keep_latest_text_snapshots_per_source", (1 << 63) - 1),
    ),
    ids=("timedelta-days", "sqlite-integer"),
)
def test_history_policy_integer_boundaries_are_processable(
    workspace,
    field,
    boundary,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    payload = _history_policy_payload()
    payload[field] = boundary
    if field == "keep_latest_text_snapshots_per_source":
        payload["minimum_age_days"] = 0
    state.paths.history_policy_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    policy = load_history_policy(state.paths)
    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert policy is not None
    assert getattr(policy, field) == boundary
    assert result.plan_id is None
    assert result.candidate_count == 0
    assert list(state.paths.history_plans_dir.iterdir()) == []
    assert _history_plan_count(state.repository) == 0


@pytest.mark.parametrize(
    ("field", "overflow"),
    (
        ("minimum_age_days", timedelta.max.days + 1),
        ("keep_latest_text_snapshots_per_source", 1 << 63),
    ),
    ids=("timedelta-days", "sqlite-integer"),
)
def test_history_policy_rejects_integer_overflow_before_repository_processing(
    workspace,
    field,
    overflow,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    payload = _history_policy_payload()
    payload[field] = overflow
    paths.history_policy_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HistoryError, match="history policy"):
        load_history_policy(paths)

    assert list(paths.history_plans_dir.iterdir()) == []


@pytest.mark.parametrize("unsafe_kind", ("symlink", "hardlink", "directory"))
def test_history_policy_rejects_unsafe_managed_file(
    workspace,
    unsafe_kind,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    external = workspace / "external-policy.json"
    external.write_text(
        json.dumps(
            {
                "version": 1,
                "minimum_age_days": 30,
                "keep_latest_text_snapshots_per_source": 1,
                "retain_included_vision_analyses": True,
                "retain_pending_review_vision_analyses": True,
                "retain_incomplete_vision_requests": True,
            }
        ),
        encoding="utf-8",
    )
    if unsafe_kind == "symlink":
        paths.history_policy_path.symlink_to(external)
    elif unsafe_kind == "hardlink":
        os.link(external, paths.history_policy_path)
    else:
        paths.history_policy_path.mkdir()

    with pytest.raises(HistoryError, match="history policy"):
        load_history_policy(paths)

    assert external.exists()


def test_missing_history_policy_retains_everything(workspace) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)

    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert result.plan_id is None
    assert result.candidate_count == 0
    assert result.total_bytes == 0
    assert result.kinds == ()
    assert result.reasons == ("history_policy_missing_retain_all",)
    assert state.old_path.exists()
    assert state.latest_path.exists()
    assert list(state.paths.history_plans_dir.iterdir()) == []
    assert _history_plan_count(state.repository) == 0
    report = result.report_path.read_text(encoding="utf-8")
    assert "Plan ID: none" in report
    assert "Candidates: 0" in report
    assert "history_policy_missing_retain_all" in report
    assert str(state.old_path) not in report
    assert state.old_path.relative_to(state.paths.root).as_posix() not in report
    assert state.source_uri not in report


def test_present_policy_without_candidates_writes_no_executable_plan(workspace) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_history_policy(paths)

    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert result.plan_id is None
    assert result.candidate_count == 0
    assert result.reasons == ("no_eligible_candidates",)
    assert list(paths.history_plans_dir.iterdir()) == []
    repository = SQLiteRepository(paths.db_path)
    assert _history_plan_count(repository) == 0


def test_history_plan_is_read_only_and_separates_private_plan_from_public_report(
    workspace,
) -> None:
    private_content = b'{"private":"candidate-content"}'
    state = _seed_old_unreferenced_snapshot(
        workspace,
        old_content=private_content,
    )
    write_sample_history_policy(state.paths)

    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert result.plan_id is not None
    assert result.candidate_count == 1
    assert result.total_bytes == len(private_content)
    assert result.kinds == ("source_snapshot",)
    assert result.reasons == ("expired_unreferenced_snapshot",)
    assert state.old_path.read_bytes() == private_content
    assert state.latest_path.exists()
    assert not hasattr(result, "plan_path")
    assert not hasattr(result, "units")

    report = result.report_path.read_text(encoding="utf-8")
    relative_candidate = state.old_path.relative_to(state.paths.root).as_posix()
    for locator in (
        str(state.old_path),
        relative_candidate,
        ".portfolio-maker/raw/",
        state.source_uri,
        private_content.decode("utf-8"),
    ):
        assert locator not in report
        assert locator not in repr(result)
    assert "Candidates: 1" in report
    assert "source_snapshot: 1" in report
    assert "expired_unreferenced_snapshot: 1" in report

    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    assert len(plan_files) == 1
    plan_path = plan_files[0]
    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_path.name == (
        f"plan-{result.plan_id}-{plan_payload['manifest_hash']}.json"
    )
    assert plan_payload["id"] == result.plan_id
    assert plan_payload["version"] == 1
    assert plan_payload["units"] == [
        {
            "kind": "source_snapshot",
            "row_id": str(state.old_id),
            "relative_path": relative_candidate,
            "content_hash": sha256(private_content).hexdigest(),
            "size": len(private_content),
            "device": state.old_path.stat().st_dev,
            "inode": state.old_path.stat().st_ino,
            "reason": "expired_unreferenced_snapshot",
        }
    ]
    assert plan_path.read_text(encoding="utf-8") == json.dumps(
        plan_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert S_IMODE(plan_path.stat().st_mode) & 0o077 == 0
    assert private_content.decode("utf-8") not in plan_path.read_text(encoding="utf-8")
    assert state.source_uri not in plan_path.read_text(encoding="utf-8")

    stored = state.repository.get_history_plan(result.plan_id)
    assert stored["manifest_hash"] == plan_payload["manifest_hash"]
    assert stored["manifest_relative_path"] == plan_path.relative_to(
        state.paths.root
    ).as_posix()
    assert stored["status"] == "planned"
    assert _snapshot_count(state.repository) == 2


def test_history_plan_selects_only_expired_unreferenced_managed_history(
    workspace,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    with state.repository._read_connection() as connection:
        source_id = int(
            connection.execute(
                "SELECT source_id FROM source_snapshots WHERE id = ?",
                (state.old_id,),
            ).fetchone()[0]
        )
        referenced_snapshot_id = int(
            connection.execute(
                "SELECT id FROM source_snapshots WHERE snapshot_path = ?",
                (str(state.latest_path),),
            ).fetchone()[0]
        )
    state.repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=referenced_snapshot_id,
        github_activity_id=None,
        locator="managed:referenced-snapshot",
        stable_id="history-verification:referenced-snapshot",
        content_hash="1" * 64,
        public_safe=False,
        origin_type="local",
        origin_visibility="private",
    )
    newest_path = state.paths.local_snapshots_dir / "history-current.json"
    newest_path.write_text('{"snapshot":"current"}', encoding="utf-8")
    state.repository.insert_source_snapshot(
        source_id,
        newest_path,
        "2" * 64,
        "text-v2",
    )

    request_rows = (
        ("a" * 32, "completed", "excluded"),
        ("b" * 32, "completed", "included"),
        ("c" * 32, "completed", "pending"),
        ("d" * 32, "unavailable", None),
        ("e" * 32, "pending", None),
    )
    with state.repository._connection() as connection:
        connection.execute(
            "UPDATE source_snapshots SET extracted_at = '2000-01-01T00:00:00Z'"
        )
        for request_id, request_status, review_status in request_rows:
            request_path = (
                state.paths.vision_requests_dir / f"request-{request_id}.json"
            )
            request_path.write_text(
                json.dumps({"version": 1, "status": request_status}),
                encoding="utf-8",
            )
            connection.execute(
                """
                INSERT INTO vision_analysis_requests
                    (id, source_id, source_content_hash, source_size, adapter,
                     status, confirmation_at, requested_at, completed_at, reason_code)
                VALUES (?, ?, ?, 1, 'codex-app-vision-v1', ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    source_id,
                    request_id[0] * 64,
                    request_status,
                    "2000-01-01T00:00:00Z",
                    "2000-01-01T00:00:00Z",
                    None if request_status == "pending" else "2000-01-01T00:00:00Z",
                    "capability_unavailable"
                    if request_status == "unavailable"
                    else None,
                ),
            )
            if review_status is None:
                continue
            result_content = json.dumps(
                {"request": request_id, "review_status": review_status},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            result_hash = sha256(result_content).hexdigest()
            relative_path = (
                Path("derived")
                / "vision"
                / f"analysis-{request_id}-{result_hash}.json"
            )
            (state.paths.root / relative_path).write_bytes(result_content)
            connection.execute(
                """
                INSERT INTO vision_analyses
                    (id, request_id, source_id, source_content_hash, result_hash,
                     relative_path, adapter, status, confidence, review_status,
                     created_at, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, 'codex-app-vision-v1', 'completed',
                        'medium', ?, '2000-01-01T00:00:00Z', ?)
                """,
                (
                    request_id,
                    request_id,
                    source_id,
                    request_id[0] * 64,
                    result_hash,
                    relative_path.as_posix(),
                    review_status,
                    None
                    if review_status == "pending"
                    else "2000-01-01T00:00:00Z",
                ),
            )

    state.paths.artifact_approval_path.write_text('{"version":1}', encoding="utf-8")
    state.paths.semantic_index_input_dir.mkdir(parents=True)
    state.paths.semantic_index_manifest_path.write_text(
        '{"version":1}', encoding="utf-8"
    )
    state.paths.semantic_index_report_path.write_text(
        "Synthetic semantic report", encoding="utf-8"
    )
    state.paths.portfolio_public_json_path.write_text(
        '{"projects":[]}', encoding="utf-8"
    )

    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert result.candidate_count == 4
    assert result.kinds == ("source_snapshot", "vision_analysis", "vision_request")
    plan_path = next(state.paths.history_plans_dir.glob("plan-*.json"))
    units = json.loads(plan_path.read_text(encoding="utf-8"))["units"]
    assert {
        (unit["kind"], unit["row_id"], unit["reason"]) for unit in units
    } == {
        (
            "source_snapshot",
            str(state.old_id),
            "expired_unreferenced_snapshot",
        ),
        ("vision_analysis", "a" * 32, "expired_unreferenced_vision_analysis"),
        ("vision_request", "a" * 32, "expired_finished_vision_request"),
        ("vision_request", "d" * 32, "expired_finished_vision_request"),
    }
    serialized = plan_path.read_text(encoding="utf-8")
    for excluded in (
        state.source_uri,
        str(state.latest_path),
        str(newest_path),
        state.paths.artifact_approval_path.name,
        state.paths.semantic_index_manifest_path.name,
        state.paths.semantic_index_report_path.name,
        state.paths.portfolio_public_json_path.name,
        "b" * 32,
        "c" * 32,
        "e" * 32,
    ):
        assert excluded not in serialized


def test_history_plan_removes_private_plan_when_database_binding_fails(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    observed: dict[str, object] = {}

    def fail_binding(self, plan, relative_path):
        observed["plan_exists"] = (state.paths.root / relative_path).is_file()
        observed["reports"] = list(
            state.paths.reviews_dir.glob("history-plan-*-report.md")
        )
        raise RepositoryError("injected history plan binding failure")

    monkeypatch.setattr(SQLiteRepository, "create_history_plan", fail_binding)

    with pytest.raises(HistoryError, match="planning failed safely"):
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert observed == {"plan_exists": True, "reports": []}
    assert list(state.paths.history_plans_dir.glob("plan-*.json")) == []
    assert list(state.paths.reviews_dir.glob("history-plan-*-report.md")) == []
    assert _history_plan_count(state.repository) == 0
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_preserves_exact_binding_after_post_commit_error(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    _fail_history_plan_after_commit(monkeypatch)

    with pytest.raises(HistoryError, match="planning failed safely"):
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )
    assert (
        _history_plan_count(state.repository),
        len(plan_files),
        len(report_files),
    ) == (1, 1, 0)
    plan_payload = json.loads(plan_files[0].read_text(encoding="utf-8"))
    stored = state.repository.get_history_plan(plan_payload["id"])
    assert stored["id"] == plan_payload["id"]
    assert stored["manifest_hash"] == plan_payload["manifest_hash"]
    assert stored["manifest_relative_path"] == plan_files[0].relative_to(
        state.paths.root
    ).as_posix()
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_preserves_manifest_after_connection_close_failure(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    original_connect = sqlite3.connect
    lookup_calls: list[str] = []

    def connect_with_close_failure(*args, **kwargs):
        connection = original_connect(
            *args,
            **kwargs,
            factory=_SyntheticFinalizerConnection,
        )
        connection.fail_close_after_commit = True
        return connection

    def count_lookup(self, plan_id):
        lookup_calls.append(plan_id)
        return None

    monkeypatch.setattr(SQLiteRepository, "initialize", lambda self: None)
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_close_failure,
    )
    monkeypatch.setattr(SQLiteRepository, "find_history_plan", count_lookup)

    with pytest.raises(HistoryError, match="planning failed safely") as captured:
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    ambiguous_error = captured.value.__cause__
    assert isinstance(ambiguous_error, CommitOutcomeAmbiguousError)
    assert isinstance(ambiguous_error.__cause__, RuntimeError)
    assert str(ambiguous_error.__cause__) == (
        "injected connection close failure after commit"
    )
    with original_connect(state.paths.db_path) as connection:
        durable_rows = connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )
    assert (durable_rows, len(plan_files), len(report_files), len(lookup_calls)) == (
        1,
        1,
        0,
        0,
    )
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_preserves_manifest_after_database_operation_close_failure(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    original_connect = sqlite3.connect
    original_open_database_family = SQLiteRepository._open_database_family
    original_close = os.close
    finalizer_state = {
        "commit_succeeded": False,
        "directory_descriptor": None,
        "failure_raised": False,
    }
    lookup_calls: list[str] = []

    def mark_commit_succeeded():
        finalizer_state["commit_succeeded"] = True

    def connect_with_commit_marker(*args, **kwargs):
        connection = original_connect(
            *args,
            **kwargs,
            factory=_SyntheticFinalizerConnection,
        )
        connection.commit_callback = mark_commit_succeeded
        return connection

    def record_database_directory(self, *, ensure_directory):
        descriptor, identity = original_open_database_family(
            self,
            ensure_directory=ensure_directory,
        )
        finalizer_state["directory_descriptor"] = descriptor
        return descriptor, identity

    def close_database_directory_with_failure(descriptor):
        should_fail = (
            finalizer_state["commit_succeeded"]
            and descriptor == finalizer_state["directory_descriptor"]
            and not finalizer_state["failure_raised"]
        )
        original_close(descriptor)
        if should_fail:
            finalizer_state["failure_raised"] = True
            raise RuntimeError(
                "injected database operation descriptor close failure after commit"
            )

    def count_lookup(self, plan_id):
        lookup_calls.append(plan_id)
        return None

    monkeypatch.setattr(SQLiteRepository, "initialize", lambda self: None)
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_commit_marker,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "_open_database_family",
        record_database_directory,
    )
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.os.close",
        close_database_directory_with_failure,
    )
    monkeypatch.setattr(SQLiteRepository, "find_history_plan", count_lookup)

    with pytest.raises(HistoryError, match="planning failed safely") as captured:
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    ambiguous_error = captured.value.__cause__
    assert isinstance(ambiguous_error, CommitOutcomeAmbiguousError)
    assert isinstance(ambiguous_error.__cause__, RuntimeError)
    assert str(ambiguous_error.__cause__) == (
        "injected database operation descriptor close failure after commit"
    )
    with original_connect(state.paths.db_path) as connection:
        durable_rows = connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )
    assert (
        finalizer_state["failure_raised"],
        durable_rows,
        len(plan_files),
        len(report_files),
        len(lookup_calls),
    ) == (True, 1, 1, 0, 0)
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_repository_does_not_classify_precommit_close_failure_as_ambiguous(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    original_connect = sqlite3.connect

    def connect_with_close_failure(*args, **kwargs):
        connection = original_connect(
            *args,
            **kwargs,
            factory=_SyntheticFinalizerConnection,
        )
        connection.fail_close = True
        return connection

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_close_failure,
    )

    with pytest.raises(
        RuntimeError,
        match="injected connection close failure before commit",
    ) as captured:
        with state.repository._connection():
            raise ValueError("injected operation failure before commit")

    assert not isinstance(captured.value, CommitOutcomeAmbiguousError)
    assert isinstance(captured.value.__context__, ValueError)


def test_history_plan_removes_manifest_after_precommit_database_change(
    workspace,
    tmp_path,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(state.paths.db_path) as visible:
        with sqlite3.connect(replacement) as replacement_connection:
            visible.backup(replacement_connection)
    original_connect = sqlite3.connect
    real_find_history_plan = SQLiteRepository.find_history_plan
    real_validate_database_family = SQLiteRepository._validate_database_family
    replaced = False
    lookup_calls: list[str] = []

    def replace_before_commit(
        self,
        directory_descriptor,
        identity,
        stage,
    ):
        nonlocal replaced
        if stage == "before commit" and not replaced:
            replaced = True
            os.replace(replacement, state.paths.db_path)
        return real_validate_database_family(
            self,
            directory_descriptor,
            identity,
            stage,
        )

    def count_lookup(self, plan_id):
        lookup_calls.append(plan_id)
        return real_find_history_plan(self, plan_id)

    monkeypatch.setattr(
        SQLiteRepository,
        "_lock_database_directory",
        lambda self, directory_descriptor: None,
    )
    monkeypatch.setattr(SQLiteRepository, "initialize", lambda self: None)
    monkeypatch.setattr(
        SQLiteRepository,
        "_validate_database_family",
        replace_before_commit,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "find_history_plan",
        count_lookup,
    )

    with pytest.raises(HistoryError, match="planning failed safely") as captured:
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert type(captured.value.__cause__) is DatabaseFamilyChangedError
    with original_connect(state.paths.db_path) as visible_connection:
        visible_rows = visible_connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )

    assert replaced is True
    assert (visible_rows, len(plan_files), len(report_files), len(lookup_calls)) == (
        0,
        0,
        0,
        1,
    )
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_preserves_manifest_when_database_replaced_during_commit(
    workspace,
    tmp_path,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    replacement = tmp_path / "replacement.db"
    displaced = tmp_path / "displaced.db"
    with sqlite3.connect(state.paths.db_path) as visible:
        with sqlite3.connect(replacement) as replacement_connection:
            visible.backup(replacement_connection)
    original_connect = sqlite3.connect
    real_find_history_plan = SQLiteRepository.find_history_plan
    replaced = False
    lookup_calls: list[str] = []

    def connect_with_replacement_trace(path, *args, **kwargs):
        connection = original_connect(path, *args, **kwargs)

        def replace_on_commit(statement):
            nonlocal replaced
            if statement == "COMMIT" and not replaced:
                replaced = True
                os.link(state.paths.db_path, displaced)
                os.replace(replacement, state.paths.db_path)

        connection.set_trace_callback(replace_on_commit)
        return connection

    def count_lookup(self, plan_id):
        lookup_calls.append(plan_id)
        return real_find_history_plan(self, plan_id)

    monkeypatch.setattr(SQLiteRepository, "initialize", lambda self: None)
    monkeypatch.setattr(
        SQLiteRepository,
        "_lock_database_directory",
        lambda self, directory_descriptor: None,
    )
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_replacement_trace,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "find_history_plan",
        count_lookup,
    )

    with pytest.raises(HistoryError, match="planning failed safely") as captured:
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    ambiguous_error = captured.value.__cause__
    assert type(ambiguous_error) is CommitOutcomeAmbiguousError
    assert type(ambiguous_error.__cause__) is DatabaseFamilyChangedError
    with original_connect(displaced) as displaced_connection:
        displaced_rows = displaced_connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    with original_connect(state.paths.db_path) as visible_connection:
        visible_rows = visible_connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )

    assert replaced is True
    assert (displaced_rows, visible_rows) == (1, 0)
    assert (len(plan_files), len(report_files), len(lookup_calls)) == (1, 0, 0)
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_preserves_manifest_after_post_commit_error_and_pre_lookup_switch(
    workspace,
    tmp_path,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    replacement = tmp_path / "replacement.db"
    displaced = tmp_path / "displaced.db"
    external_sidecar = tmp_path / "external-journal"
    external_sidecar.write_bytes(b"")
    with sqlite3.connect(state.paths.db_path) as visible:
        with sqlite3.connect(replacement) as replacement_connection:
            visible.backup(replacement_connection)
    original_connect = sqlite3.connect
    real_validate = SQLiteRepository._validate_database_family
    real_find_history_plan = SQLiteRepository.find_history_plan
    unsafe_sidecar_injected = False
    database_switched = False
    lookup_calls = 0

    def connect_with_unsafe_commit_sidecar(path, *args, **kwargs):
        connection = original_connect(path, *args, **kwargs)

        def inject_unsafe_sidecar(statement):
            nonlocal unsafe_sidecar_injected
            if statement == "COMMIT" and not unsafe_sidecar_injected:
                sidecar = state.paths.db_path.with_name(
                    f"{state.paths.db_path.name}-journal"
                )
                sidecar.unlink()
                os.link(external_sidecar, sidecar)
                unsafe_sidecar_injected = True

        connection.set_trace_callback(inject_unsafe_sidecar)
        return connection

    def switch_database_after_post_commit_error(
        self,
        directory_descriptor,
        identity,
        stage,
    ):
        nonlocal database_switched
        try:
            real_validate(self, directory_descriptor, identity, stage)
        except RepositoryError:
            if stage == "after commit" and not database_switched:
                sidecar = state.paths.db_path.with_name(
                    f"{state.paths.db_path.name}-journal"
                )
                sidecar.unlink()
                os.link(state.paths.db_path, displaced)
                os.replace(replacement, state.paths.db_path)
                database_switched = True
            raise

    def count_lookup(self, plan_id):
        nonlocal lookup_calls
        lookup_calls += 1
        return real_find_history_plan(self, plan_id)

    monkeypatch.setattr(SQLiteRepository, "initialize", lambda self: None)
    monkeypatch.setattr(
        SQLiteRepository,
        "_lock_database_directory",
        lambda self, directory_descriptor: None,
    )
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_unsafe_commit_sidecar,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "_validate_database_family",
        switch_database_after_post_commit_error,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "find_history_plan",
        count_lookup,
    )

    with pytest.raises(HistoryError, match="planning failed safely"):
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    with original_connect(displaced) as displaced_connection:
        displaced_rows = displaced_connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    with original_connect(state.paths.db_path) as visible_connection:
        visible_rows = visible_connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()[0]
    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )

    assert unsafe_sidecar_injected is True
    assert database_switched is True
    assert (
        displaced_rows,
        visible_rows,
        len(plan_files),
        len(report_files),
        lookup_calls,
    ) == (1, 0, 1, 0, 0)
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_preserves_manifest_when_binding_lookup_fails(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    lookup_calls: list[str] = []

    def fail_binding(self, plan, relative_path):
        raise RepositoryError("injected history plan binding failure")

    def fail_lookup(self, plan_id):
        lookup_calls.append(plan_id)
        raise RepositoryError("injected ambiguous history plan lookup failure")

    monkeypatch.setattr(SQLiteRepository, "create_history_plan", fail_binding)
    monkeypatch.setattr(
        SQLiteRepository,
        "find_history_plan",
        fail_lookup,
        raising=False,
    )

    with pytest.raises(HistoryError, match="planning failed safely"):
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    report_files = list(
        state.paths.reviews_dir.glob("history-plan-*-report.md")
    )
    assert len(lookup_calls) == 1
    assert (
        _history_plan_count(state.repository),
        len(plan_files),
        len(report_files),
    ) == (0, 1, 0)
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_plan_report_failure_keeps_only_database_bound_private_plan(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    observed: dict[str, int] = {}
    real_fsync = os.fsync
    directory_fsync_count = 0

    def fail_report_directory_fsync(descriptor):
        nonlocal directory_fsync_count
        if S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_count += 1
            if directory_fsync_count == 2:
                observed["rows_at_report"] = _history_plan_count(state.repository)
                raise OSError("injected history plan report publication failure")
        real_fsync(descriptor)

    monkeypatch.setattr(
        managed_files.os,
        "fsync",
        fail_report_directory_fsync,
    )

    with pytest.raises(HistoryError, match="report publication failed safely"):
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    assert observed == {"rows_at_report": 1}
    assert directory_fsync_count == 3
    assert len(plan_files) == 1
    assert list(state.paths.reviews_dir.glob("history-plan-*-report.md")) == []
    plan_payload = json.loads(plan_files[0].read_text(encoding="utf-8"))
    stored = state.repository.get_history_plan(plan_payload["id"])
    assert stored["manifest_hash"] == plan_payload["manifest_hash"]
    assert stored["manifest_relative_path"] == plan_files[0].relative_to(
        state.paths.root
    ).as_posix()
    assert stored["status"] == "planned"
    assert state.old_path.exists()
    assert state.latest_path.exists()


def test_history_store_never_overwrites_an_immutable_plan(workspace) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    policy = HistoryPolicy.default()
    units = state.repository.history_candidates(policy)
    plan = HistoryPlan.create(policy, units)
    store = HistoryStore(state.paths)

    plan_path = store.write_plan(plan)
    original = plan_path.read_bytes()

    with pytest.raises(FileExistsError):
        store.write_plan(plan)

    assert plan_path.read_bytes() == original


def test_managed_publication_and_removal_fsync_file_and_directory_descriptors(
    workspace,
    monkeypatch,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    target = paths.history_plans_dir / "descriptor-spy.json"
    fsync_targets: list[str] = []
    real_fsync = os.fsync

    def observe_fsync(descriptor):
        mode = os.fstat(descriptor).st_mode
        if S_ISREG(mode):
            fsync_targets.append("file")
        elif S_ISDIR(mode):
            fsync_targets.append("directory")
        else:
            fsync_targets.append("other")
        real_fsync(descriptor)

    monkeypatch.setattr(managed_files.os, "fsync", observe_fsync)

    managed_files.write_managed_text(target, "durable", overwrite=False)

    assert fsync_targets == ["file", "directory"]

    fsync_targets.clear()
    managed_files.remove_managed_file(target)

    assert fsync_targets == ["directory"]
    assert not target.exists()


@pytest.mark.parametrize(
    "unsafe_kind",
    (
        "symlink",
        "directory",
        "hardlink",
        "traversal",
        "outside-root",
        "wrong-kind-root",
    ),
)
def test_history_plan_rejects_unsafe_candidate_paths(
    workspace,
    unsafe_kind,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    if unsafe_kind == "symlink":
        external = workspace / "symlink-target.json"
        external.write_text('{"preserve":"symlink"}', encoding="utf-8")
        stored_path = paths.local_snapshots_dir / "unsafe-old.json"
        stored_path.symlink_to(external)
        preserved = external
    elif unsafe_kind == "directory":
        stored_path = paths.local_snapshots_dir / "unsafe-old.json"
        stored_path.mkdir()
        preserved = stored_path
    elif unsafe_kind == "hardlink":
        external = workspace / "hardlink-target.json"
        external.write_text('{"preserve":"hardlink"}', encoding="utf-8")
        stored_path = paths.local_snapshots_dir / "unsafe-old.json"
        os.link(external, stored_path)
        preserved = external
    elif unsafe_kind == "traversal":
        traversal_dir = paths.snapshots_dir / "github"
        traversal_dir.mkdir()
        stored_path = paths.local_snapshots_dir / ".." / "github" / "unsafe-old.json"
        stored_path.write_text('{"preserve":"traversal"}', encoding="utf-8")
        preserved = traversal_dir / "unsafe-old.json"
    elif unsafe_kind == "outside-root":
        stored_path = workspace / "outside-candidate.json"
        stored_path.write_text('{"preserve":"outside"}', encoding="utf-8")
        preserved = stored_path
    else:
        stored_path = paths.vision_analyses_dir / "wrong-kind.json"
        stored_path.write_text('{"preserve":"wrong-kind"}', encoding="utf-8")
        preserved = stored_path
    state = _seed_old_unreferenced_snapshot(
        workspace,
        old_path=stored_path,
        create_old=False,
    )
    write_sample_history_policy(paths)

    with pytest.raises(HistoryError) as raised:
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert str(stored_path) not in str(raised.value)
    assert preserved.exists()
    assert state.latest_path.exists()
    assert list(paths.history_plans_dir.iterdir()) == []
    assert _history_plan_count(state.repository) == 0


def test_history_plan_revalidates_candidate_identity_before_recording(
    workspace,
    monkeypatch,
) -> None:
    state = _seed_old_unreferenced_snapshot(workspace)
    write_sample_history_policy(state.paths)
    original_history_candidates = SQLiteRepository.history_candidates

    def replace_after_selection(self, policy):
        units = original_history_candidates(self, policy)
        state.old_path.unlink()
        state.old_path.write_text('{"replacement":true}', encoding="utf-8")
        return units

    monkeypatch.setattr(
        SQLiteRepository,
        "history_candidates",
        replace_after_selection,
    )

    with pytest.raises(HistoryError, match="unsafe"):
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert state.old_path.read_text(encoding="utf-8") == '{"replacement":true}'
    assert list(state.paths.history_plans_dir.iterdir()) == []
    assert _history_plan_count(state.repository) == 0


def test_history_export_binds_receipt_and_archives_exact_plan_files_and_database(
    workspace,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    backup_dir = _outside_backup_dir(workspace, "complete-export")

    exported = history_application.export_history_plan(
        application_models.HistoryExportRequest(
            workspace,
            plan.id,
            backup_dir,
        )
    )

    assert exported.plan_id == plan.id
    assert exported.archive_path.parent == backup_dir
    assert exported.receipt_hash == sha256(exported.archive_path.read_bytes()).hexdigest()
    assert state.repository.get_history_plan(plan.id)["export_receipt_hash"] == (
        exported.receipt_hash
    )
    binding = state.repository.get_history_plan(plan.id)["export_archive_binding"]
    assert binding is not None
    assert binding.archive_path == exported.archive_path.as_posix()
    assert binding.sha256 == exported.receipt_hash
    assert (binding.archive_device, binding.archive_inode) == (
        exported.archive_path.stat().st_dev,
        exported.archive_path.stat().st_ino,
    )
    with tarfile.open(exported.archive_path, "r") as archive:
        assert set(archive.getnames()) == {
            "history-plan.json",
            f"managed/{plan.units[0].relative_path}",
            "portfolio.db",
        }
        plan_member = archive.extractfile("history-plan.json")
        candidate_member = archive.extractfile(
            f"managed/{plan.units[0].relative_path}"
        )
        database_member = archive.extractfile("portfolio.db")
        assert plan_member is not None
        assert candidate_member is not None
        assert database_member is not None
        assert json.loads(plan_member.read())["id"] == plan.id
        assert candidate_member.read() == state.old_path.read_bytes()
        database_bytes = database_member.read()

    archived_database = workspace / "archived-portfolio.db"
    archived_database.write_bytes(database_bytes)
    with sqlite3.connect(archived_database) as connection:
        assert connection.execute(
            "SELECT status FROM history_cleanup_plans WHERE id = ?",
            (plan.id,),
        ).fetchone() == ("planned",)
        assert connection.execute(
            "SELECT COUNT(*) FROM source_snapshots"
        ).fetchone() == (2,)


@pytest.mark.parametrize(
    "unsafe_kind",
    ("inside-workspace", "non-empty", "symlink", "file", "symlink-parent"),
)
def test_history_export_rejects_unsafe_or_nonempty_destination_without_receipt(
    workspace,
    unsafe_kind,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    removed_sidecars: tuple[Path, ...] = ()
    rejected_mode: int | None = None
    outside = _outside_backup_dir(workspace, f"unsafe-{unsafe_kind}")
    if unsafe_kind == "inside-workspace":
        backup_dir = workspace / "backup"
        removed_sidecars = tuple(
            state.paths.db_path.with_name(f"{state.paths.db_path.name}{suffix}")
            for suffix in ("-journal", "-wal", "-shm")
        )
        for sidecar in removed_sidecars:
            sidecar.unlink(missing_ok=True)
    elif unsafe_kind == "non-empty":
        backup_dir = outside
        backup_dir.mkdir()
        backup_dir.chmod(0o755)
        rejected_mode = S_IMODE(backup_dir.stat().st_mode)
        (backup_dir / "preserve.txt").write_text("preserve", encoding="utf-8")
    elif unsafe_kind == "symlink":
        target = _outside_backup_dir(workspace, "symlink-target")
        target.mkdir()
        outside.symlink_to(target, target_is_directory=True)
        backup_dir = outside
    elif unsafe_kind == "file":
        outside.write_text("preserve", encoding="utf-8")
        backup_dir = outside
    else:
        target = _outside_backup_dir(workspace, "real-parent")
        target.mkdir()
        outside.symlink_to(target, target_is_directory=True)
        backup_dir = outside / "backup"

    with pytest.raises(HistoryError, match="export"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                backup_dir,
            )
        )

    assert state.old_path.exists()
    assert state.latest_path.exists()
    assert all(not sidecar.exists() for sidecar in removed_sidecars)
    if rejected_mode is not None:
        assert S_IMODE(backup_dir.stat().st_mode) == rejected_mode
    if removed_sidecars:
        uri = f"{state.paths.db_path.absolute().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            assert connection.execute(
                """
                SELECT export_receipt_hash
                FROM history_cleanup_plans
                WHERE id = ?
                """,
                (plan.id,),
            ).fetchone() == (None,)
    else:
        assert state.repository.get_history_plan(plan.id)[
            "export_receipt_hash"
        ] is None


@pytest.mark.parametrize(
    ("commit_receipt", "expect_success"),
    ((False, False), (True, True)),
    ids=("unconfirmed-write", "confirmed-ambiguous-write"),
)
def test_history_export_receipt_write_is_fail_closed_unless_exactly_confirmed(
    workspace,
    monkeypatch,
    commit_receipt,
    expect_success,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    backup_dir = _outside_backup_dir(
        workspace,
        f"receipt-{int(commit_receipt)}",
    )
    real_record = SQLiteRepository.record_history_export_receipt

    def fail_receipt_write(self, plan_id, manifest_hash, receipt_hash, binding):
        if commit_receipt:
            real_record(self, plan_id, manifest_hash, receipt_hash, binding)
        raise CommitOutcomeAmbiguousError("injected receipt outcome")

    monkeypatch.setattr(
        SQLiteRepository,
        "record_history_export_receipt",
        fail_receipt_write,
    )

    request = application_models.HistoryExportRequest(
        workspace,
        plan.id,
        backup_dir,
    )
    if expect_success:
        exported = history_application.export_history_plan(request)
        assert state.repository.get_history_plan(plan.id)[
            "export_receipt_hash"
        ] == exported.receipt_hash
    else:
        with pytest.raises(HistoryError, match="receipt"):
            history_application.export_history_plan(request)
        assert state.repository.get_history_plan(plan.id)[
            "export_receipt_hash"
        ] is None


def test_history_export_receipt_lookup_failure_is_controlled_and_fail_closed(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)

    def fail_receipt_write(self, plan_id, manifest_hash, receipt_hash, binding):
        raise CommitOutcomeAmbiguousError("injected receipt outcome")

    def fail_receipt_lookup(self, plan_id, manifest_hash, receipt_hash, binding):
        raise RuntimeError("injected receipt lookup failure")

    monkeypatch.setattr(
        SQLiteRepository,
        "record_history_export_receipt",
        fail_receipt_write,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "export_receipt_matches",
        fail_receipt_lookup,
    )

    with pytest.raises(HistoryError, match="receipt"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                _outside_backup_dir(workspace, "receipt-lookup-failure"),
            )
        )

    assert state.repository.get_history_plan(plan.id)["export_receipt_hash"] is None


def test_history_export_receipt_lookup_never_creates_a_missing_database_family(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    family_paths = (
        state.paths.db_path,
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-journal"),
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-wal"),
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-shm"),
    )

    def remove_database_then_fail(
        self,
        plan_id,
        manifest_hash,
        receipt_hash,
        binding,
    ):
        for path in family_paths:
            path.unlink(missing_ok=True)
        raise CommitOutcomeAmbiguousError("injected receipt outcome")

    monkeypatch.setattr(
        SQLiteRepository,
        "record_history_export_receipt",
        remove_database_then_fail,
    )

    with pytest.raises(HistoryError, match="receipt"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                _outside_backup_dir(workspace, "receipt-missing-family"),
            )
        )

    assert all(not path.exists() for path in family_paths)


def test_history_export_rechecks_exact_plan_binding_after_database_backup(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    real_backup = SQLiteRepository.backup_bytes
    backup_dir = _outside_backup_dir(workspace, "binding-after-backup")

    def tamper_binding_after_backup(self):
        backup = real_backup(self)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE history_cleanup_plans
                SET canonical_units_json = '[]'
                WHERE id = ?
                """,
                (plan.id,),
            )
        return backup

    monkeypatch.setattr(
        SQLiteRepository,
        "backup_bytes",
        tamper_binding_after_backup,
    )

    with pytest.raises(HistoryError, match="binding"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                backup_dir,
            )
        )

    assert list(backup_dir.iterdir()) == []


def test_history_export_rejects_case_alias_inside_workspace_by_identity(
    workspace,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    alias_name = "".join(
        character.swapcase() if character.isalpha() else character
        for character in workspace.name
    )
    alias_workspace = workspace.with_name(alias_name)
    if not alias_workspace.exists() or not alias_workspace.samefile(workspace):
        pytest.skip("filesystem does not provide a case-insensitive workspace alias")
    backup_dir = alias_workspace / "backup"

    with pytest.raises(HistoryError, match="export"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                backup_dir,
            )
        )

    assert not backup_dir.exists()
    assert state.repository.get_history_plan(plan.id)["export_receipt_hash"] is None


def test_history_export_revalidates_target_name_identity_before_receipt_binding(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    backup_dir = _outside_backup_dir(workspace, "renamed-export-target")
    moved_dir = backup_dir.with_name(f"{backup_dir.name}-moved")
    backup_dir.mkdir()
    target_identity = (backup_dir.stat().st_dev, backup_dir.stat().st_ino)
    real_fsync = os.fsync
    renamed = False

    def rename_target_after_archive_fsync(descriptor):
        nonlocal renamed
        real_fsync(descriptor)
        stat_result = os.fstat(descriptor)
        if (
            not renamed
            and S_ISDIR(stat_result.st_mode)
            and (stat_result.st_dev, stat_result.st_ino) == target_identity
            and (backup_dir / f"history-plan-{plan.id}.tar").exists()
        ):
            backup_dir.rename(moved_dir)
            backup_dir.mkdir()
            renamed = True

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.history_store.os.fsync",
        rename_target_after_archive_fsync,
    )

    with pytest.raises(HistoryError, match="export"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                backup_dir,
            )
        )

    assert renamed is True
    assert state.repository.get_history_plan(plan.id)["export_receipt_hash"] is None


def test_history_export_rechecks_target_identity_immediately_before_receipt_write(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    backup_dir = _outside_backup_dir(workspace, "pre-receipt-target-check")
    moved_dir = backup_dir.with_name(f"{backup_dir.name}-moved")
    real_create_archive = HistoryStore.create_export_archive

    def rename_target_after_archive(self, *args, **kwargs):
        result = real_create_archive(self, *args, **kwargs)
        backup_dir.rename(moved_dir)
        backup_dir.mkdir()
        return result

    monkeypatch.setattr(
        HistoryStore,
        "create_export_archive",
        rename_target_after_archive,
    )

    with pytest.raises(HistoryError, match="export"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                backup_dir,
            )
        )

    assert state.repository.get_history_plan(plan.id)["export_receipt_hash"] is None


def test_history_export_rejects_target_renamed_after_revalidation_before_receipt(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    backup_dir = _outside_backup_dir(workspace, "post-revalidation-rename")
    moved_dir = backup_dir.with_name(f"{backup_dir.name}-moved")
    real_revalidate = HistoryStore.revalidate_export_directory
    renamed = False

    def rename_target_after_revalidation(self, identity):
        nonlocal renamed
        real_revalidate(self, identity)
        backup_dir.rename(moved_dir)
        renamed = True

    monkeypatch.setattr(
        HistoryStore,
        "revalidate_export_directory",
        rename_target_after_revalidation,
    )

    exported = None
    export_error = None
    try:
        exported = history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                backup_dir,
            )
        )
    except HistoryError as error:
        export_error = error

    stored_receipt = state.repository.get_history_plan(plan.id)[
        "export_receipt_hash"
    ]
    unsafe_outcome = (
        exported is not None
        and stored_receipt == exported.receipt_hash
        and not exported.archive_path.exists()
        and (moved_dir / exported.archive_path.name).is_file()
    )
    assert not unsafe_outcome, (
        "unsafe outcome: export_success=True receipt_bound=True "
        "returned_archive_exists=False moved_archive_exists=True"
    )
    assert renamed is True
    assert export_error is not None
    assert "export" in str(export_error)
    assert stored_receipt is not None
    with pytest.raises(HistoryError, match="archive"):
        _apply_exported_plan(workspace, plan, stored_receipt)
    assert state.old_path.exists()
    assert list(state.paths.history_journal_dir.iterdir()) == []
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"


@pytest.mark.parametrize(
    "drift",
    ("missing", "replaced", "same-content-replaced", "content", "symlink"),
)
def test_history_apply_rejects_export_archive_drift_before_any_mutation(
    workspace,
    drift,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"archive-drift-{drift}",
    )
    archive_path = exported.archive_path
    if drift == "missing":
        archive_path.unlink()
    elif drift == "replaced":
        archive_path.unlink()
        archive_path.write_bytes(b"replacement")
    elif drift == "same-content-replaced":
        original_content = archive_path.read_bytes()
        archive_path.rename(archive_path.with_name("prior-archive"))
        archive_path.write_bytes(original_content)
    elif drift == "content":
        archive_path.write_bytes(archive_path.read_bytes() + b"content-drift")
    else:
        replacement = archive_path.with_name("replacement-archive")
        replacement.write_bytes(b"replacement")
        archive_path.unlink()
        archive_path.symlink_to(replacement)

    with pytest.raises(HistoryError, match="archive"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert state.old_path.exists()
    assert state.latest_path.exists()
    assert list(state.paths.history_journal_dir.iterdir()) == []
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"


def test_history_apply_rejects_moved_export_directory_before_any_mutation(
    workspace,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "moved-export-directory",
    )
    exported.archive_path.parent.rename(
        exported.archive_path.parent.with_name("moved-export-directory")
    )

    with pytest.raises(HistoryError, match="archive"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert state.old_path.exists()
    assert state.latest_path.exists()
    assert list(state.paths.history_journal_dir.iterdir()) == []
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"


def test_history_apply_keeps_verified_archive_context_open_through_first_move(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "pinned-archive-context",
    )
    real_context = HistoryStore.open_verified_export_archive
    real_move = HistoryStore.move_units_to_quarantine
    active_contexts = 0

    @contextmanager
    def track_verified_archive(self, binding):
        nonlocal active_contexts
        with real_context(self, binding):
            active_contexts += 1
            try:
                yield
            finally:
                active_contexts -= 1

    def assert_context_is_open_during_move(self, journal):
        assert active_contexts > 0
        return real_move(self, journal)

    monkeypatch.setattr(
        HistoryStore,
        "open_verified_export_archive",
        track_verified_archive,
    )
    monkeypatch.setattr(
        HistoryStore,
        "move_units_to_quarantine",
        assert_context_is_open_during_move,
    )

    result = _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert result.state is CleanupExecutionState.PURGED
    assert active_contexts == 0


def test_history_export_keeps_first_receipt_immutable_across_repeated_export(
    workspace,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    first = history_application.export_history_plan(
        application_models.HistoryExportRequest(
            workspace,
            plan.id,
            _outside_backup_dir(workspace, "first-receipt"),
        )
    )

    with pytest.raises(HistoryError, match="receipt"):
        history_application.export_history_plan(
            application_models.HistoryExportRequest(
                workspace,
                plan.id,
                _outside_backup_dir(workspace, "second-receipt"),
            )
        )

    assert state.repository.get_history_plan(plan.id)["export_receipt_hash"] == (
        first.receipt_hash
    )
    applied = _apply_exported_plan(workspace, plan, first.receipt_hash)
    assert applied.state is CleanupExecutionState.PURGED


@pytest.mark.parametrize(
    ("receipt_override", "confirmation", "message"),
    (
        (None, "DELETE MANAGED HISTORY", "receipt"),
        ("0" * 64, "DELETE MANAGED HISTORY", "receipt"),
        ("expected", "delete managed history", "confirmation"),
    ),
)
def test_history_apply_requires_exact_receipt_and_confirmation_before_move(
    workspace,
    receipt_override,
    confirmation,
    message,
) -> None:
    state, plan, exported = _seed_exported_history_plan(workspace, message)
    receipt = (
        exported.receipt_hash
        if receipt_override == "expected"
        else receipt_override
    )

    with pytest.raises(HistoryError, match=message):
        history_application.apply_history_plan(
            application_models.HistoryApplyRequest(
                workspace,
                plan.id,
                receipt,
                confirmation,
            )
        )

    assert state.old_path.exists()
    assert state.latest_path.exists()
    assert list(state.paths.history_journal_dir.iterdir()) == []
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"


def test_history_apply_quarantines_commits_purges_and_vacuums(
    workspace,
) -> None:
    state, plan, exported = _seed_exported_history_plan(workspace, "apply")

    result = _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert result.state is CleanupExecutionState.PURGED
    assert result.affected_count == 1
    assert not state.old_path.exists()
    assert state.latest_path.exists()
    assert _snapshot_count(state.repository) == 1
    stored = state.repository.get_history_plan(plan.id)
    assert stored["status"] == "completed"
    assert stored["execution_id"] == result.execution_id
    journal = HistoryStore(state.paths).load_journal(result.execution_id)
    assert journal.state is CleanupExecutionState.PURGED
    assert journal.moved_units == ()


def test_partial_quarantine_move_failure_restores_every_moved_candidate(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "partial-move",
        candidate_count=2,
    )
    real_move = HistoryStore._move_unit_to_quarantine
    move_calls = 0

    def fail_second_move(self, execution_id, unit):
        nonlocal move_calls
        move_calls += 1
        if move_calls == 2:
            raise OSError("injected second move failure")
        return real_move(self, execution_id, unit)

    monkeypatch.setattr(
        HistoryStore,
        "_move_unit_to_quarantine",
        fail_second_move,
    )

    with pytest.raises(HistoryError, match="restored"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert all((state.paths.root / unit.relative_path).exists() for unit in plan.units)
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RESTORED
    assert journal.moved_units == ()


def test_partial_move_checkpoint_failure_restores_every_candidate(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "partial-checkpoint",
        candidate_count=2,
    )
    real_write_journal = HistoryStore.write_journal

    def fail_second_move_checkpoint(self, journal, *, create=False):
        if (
            journal.state is CleanupExecutionState.QUARANTINED
            and len(journal.moved_units) == 2
        ):
            raise RuntimeError("injected journal checkpoint failure")
        return real_write_journal(self, journal, create=create)

    monkeypatch.setattr(
        HistoryStore,
        "write_journal",
        fail_second_move_checkpoint,
    )

    with pytest.raises(HistoryError, match="restored"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert all((state.paths.root / unit.relative_path).exists() for unit in plan.units)
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RESTORED
    assert journal.moved_units == ()


@pytest.mark.parametrize(
    "crash_point",
    ("before-rename", "after-rename"),
)
def test_abrupt_termination_around_quarantine_rename_is_recoverable(
    workspace,
    monkeypatch,
    crash_point,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"abrupt-{crash_point}",
    )
    real_move = HistoryStore._move_unit_to_quarantine

    class AbruptTermination(BaseException):
        pass

    def terminate_around_rename(self, execution_id, unit):
        if crash_point == "after-rename":
            real_move(self, execution_id, unit)
        raise AbruptTermination

    monkeypatch.setattr(
        HistoryStore,
        "_move_unit_to_quarantine",
        terminate_around_rename,
    )

    with pytest.raises(AbruptTermination):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    interrupted = _load_only_journal(state.paths)
    monkeypatch.setattr(
        HistoryStore,
        "_move_unit_to_quarantine",
        real_move,
    )
    recovered = history_application.recover_history_cleanup(
        application_models.HistoryExecutionRequest(
            workspace,
            interrupted.execution_id,
        )
    )

    assert recovered.state is CleanupExecutionState.RESTORED
    restored = HistoryStore(state.paths).load_journal(interrupted.execution_id)
    assert restored.state is CleanupExecutionState.RESTORED
    assert restored.moved_units == ()
    assert state.old_path.exists()
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"


def test_precommit_database_failure_restores_quarantined_files(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "precommit",
    )

    def fail_before_commit(self, plan_id, unit_ids, execution_id=None):
        raise RepositoryError("injected precommit failure")

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        fail_before_commit,
    )

    with pytest.raises(HistoryError, match="restored"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert state.old_path.exists()
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RESTORED
    assert journal.moved_units == ()


def test_apply_rejects_database_family_replacement_after_quarantine(
    workspace,
    tmp_path,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "family-replacement-after-quarantine",
    )
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(state.paths.db_path) as source:
        with sqlite3.connect(replacement) as destination:
            source.backup(destination)
    with sqlite3.connect(replacement) as connection:
        _replace_stored_plan_binding(connection, plan.id)
    real_move_all = HistoryStore.move_units_to_quarantine
    replacement_installed = False

    def replace_family_after_quarantine(self, journal):
        nonlocal replacement_installed
        moved = real_move_all(self, journal)
        os.replace(replacement, state.paths.db_path)
        replacement_installed = True
        return moved

    monkeypatch.setattr(
        SQLiteRepository,
        "_lock_database_directory",
        lambda self, descriptor: None,
    )
    monkeypatch.setattr(
        HistoryStore,
        "move_units_to_quarantine",
        replace_family_after_quarantine,
    )

    with pytest.raises(HistoryError, match="restored"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert replacement_installed is True
    assert state.old_path.exists()
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RESTORED
    with sqlite3.connect(state.paths.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM history_cleanup_plans WHERE id = ?",
            (plan.id,),
        ).fetchone() == ("planned",)
        assert connection.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE id = ?",
            (state.old_id,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("binding_change", ("canonical-units", "receipt"))
def test_apply_reasserts_exact_plan_and_receipt_inside_delete_transaction(
    workspace,
    monkeypatch,
    binding_change,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"transaction-binding-{binding_change}",
    )
    real_move_all = HistoryStore.move_units_to_quarantine

    def change_binding_after_quarantine(self, journal):
        moved = real_move_all(self, journal)
        with state.repository._connection() as connection:
            if binding_change == "canonical-units":
                _replace_stored_plan_binding(connection, plan.id)
            else:
                connection.execute(
                    """
                    UPDATE history_cleanup_plans
                    SET export_receipt_hash = ?
                    WHERE id = ?
                    """,
                    ("f" * 64, plan.id),
                )
        return moved

    monkeypatch.setattr(
        HistoryStore,
        "move_units_to_quarantine",
        change_binding_after_quarantine,
    )

    with pytest.raises(HistoryError, match="restored"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert state.old_path.exists()
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RESTORED
    assert journal.moved_units == ()


def test_apply_reclassifies_exact_binding_after_commit_before_purge(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "postcommit-binding-classification",
    )
    real_classify = SQLiteRepository.classify_history_cleanup_outcome
    classification_calls = 0

    def change_receipt_then_classify(self, *args, **kwargs):
        nonlocal classification_calls
        classification_calls += 1
        if classification_calls == 1:
            with self._connection() as connection:
                connection.execute(
                    """
                    UPDATE history_cleanup_plans
                    SET export_receipt_hash = ?
                    WHERE id = ?
                    """,
                    ("f" * 64, plan.id),
                )
        return real_classify(self, *args, **kwargs)

    monkeypatch.setattr(
        SQLiteRepository,
        "classify_history_cleanup_outcome",
        change_receipt_then_classify,
    )

    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert classification_calls == 1
    assert not state.old_path.exists()
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED
    assert len(journal.moved_units) == 1
    assert _quarantined_path(state.paths, journal, 0).exists()


def test_ambiguous_database_outcome_never_restores_purges_or_vacuums(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "ambiguous",
    )
    vacuum_calls = 0

    def raise_ambiguous(self, plan_id, unit_ids, execution_id=None):
        raise CommitOutcomeAmbiguousError("injected ambiguous outcome")

    def count_vacuum(self):
        nonlocal vacuum_calls
        vacuum_calls += 1

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        raise_ambiguous,
    )
    monkeypatch.setattr(SQLiteRepository, "vacuum", count_vacuum)

    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED
    assert not state.old_path.exists()
    assert len(journal.moved_units) == 1
    assert _quarantined_path(state.paths, journal, 0).exists()
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"
    assert vacuum_calls == 0


def test_postcommit_journal_failure_falls_back_to_reconciliation_without_purge(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "postcommit-journal",
    )
    real_write_journal = HistoryStore.write_journal
    failed_once = False
    purge_calls = 0
    vacuum_calls = 0

    def fail_committed_pending_once(self, journal, *, create=False):
        nonlocal failed_once
        if (
            journal.state is CleanupExecutionState.COMMITTED_PENDING_PURGE
            and not failed_once
        ):
            failed_once = True
            raise RuntimeError("injected postcommit journal failure")
        return real_write_journal(self, journal, create=create)

    def count_purge(self, journal):
        nonlocal purge_calls
        purge_calls += 1
        return journal

    def count_vacuum(self):
        nonlocal vacuum_calls
        vacuum_calls += 1

    monkeypatch.setattr(
        HistoryStore,
        "write_journal",
        fail_committed_pending_once,
    )
    monkeypatch.setattr(HistoryStore, "purge_quarantine", count_purge)
    monkeypatch.setattr(SQLiteRepository, "vacuum", count_vacuum)

    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED
    assert len(journal.moved_units) == 1
    assert _quarantined_path(state.paths, journal, 0).exists()
    assert state.repository.get_history_plan(plan.id)["status"] == "completed"
    assert (purge_calls, vacuum_calls) == (0, 0)


def test_history_journal_rejects_state_and_quarantine_count_mismatch(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "journal-state-mismatch",
    )

    def raise_ambiguous(self, plan_id, unit_ids, execution_id=None):
        raise CommitOutcomeAmbiguousError("injected ambiguous outcome")

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        raise_ambiguous,
    )
    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)
    journal_path = _only_journal_path(state.paths)
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["state"] = CleanupExecutionState.PURGED.value
    hash_payload = {
        key: value
        for key, value in payload.items()
        if key != "journal_hash"
    }
    payload["journal_hash"] = sha256(
        json.dumps(
            hash_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    journal_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="journal"):
        HistoryStore(state.paths).load_journal(
            journal_path.stem.removeprefix("execution-")
        )


@pytest.mark.parametrize("committed", (False, True), ids=("not-committed", "committed"))
def test_recover_ambiguous_outcome_requires_exact_database_classification(
    workspace,
    monkeypatch,
    committed,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"recover-{int(committed)}",
    )
    real_delete = SQLiteRepository.delete_planned_history_units

    def finish_then_raise_ambiguous(
        self,
        plan_id,
        unit_ids,
        execution_id=None,
    ):
        if committed:
            real_delete(self, plan_id, unit_ids, execution_id)
        raise CommitOutcomeAmbiguousError("injected ambiguous outcome")

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        finish_then_raise_ambiguous,
    )
    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)
    journal = _load_only_journal(state.paths)

    recovered = history_application.recover_history_cleanup(
        application_models.HistoryExecutionRequest(
            workspace,
            journal.execution_id,
        )
    )

    expected_state = (
        CleanupExecutionState.COMMITTED_PENDING_PURGE
        if committed
        else CleanupExecutionState.RESTORED
    )
    assert recovered.state is expected_state
    recovered_journal = HistoryStore(state.paths).load_journal(
        journal.execution_id
    )
    assert recovered_journal.state is expected_state
    if committed:
        assert not state.old_path.exists()
        assert _quarantined_path(state.paths, recovered_journal, 0).exists()
    else:
        assert state.old_path.exists()
        assert recovered_journal.moved_units == ()


@pytest.mark.parametrize("missing_family_part", ("main", "sidecars"))
def test_recover_missing_database_family_is_noncreating_and_nonmutating(
    workspace,
    monkeypatch,
    missing_family_part,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"recover-missing-{missing_family_part}",
    )

    def raise_ambiguous(self, plan_id, unit_ids, execution_id=None):
        raise CommitOutcomeAmbiguousError("injected ambiguous outcome")

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        raise_ambiguous,
    )
    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)
    journal = _load_only_journal(state.paths)
    journal_path = _only_journal_path(state.paths)
    journal_bytes = journal_path.read_bytes()
    quarantine_path = _quarantined_path(state.paths, journal, 0)
    quarantine_bytes = quarantine_path.read_bytes()
    family_paths = (
        state.paths.db_path,
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-journal"),
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-wal"),
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-shm"),
    )
    removed_paths = family_paths[:1] if missing_family_part == "main" else family_paths[1:]
    for path in removed_paths:
        path.unlink(missing_ok=True)

    with pytest.raises(
        HistoryError,
        match="recovery|repository|indeterminate|binding",
    ):
        history_application.recover_history_cleanup(
            application_models.HistoryExecutionRequest(
                workspace,
                journal.execution_id,
            )
        )

    assert all(not path.exists() for path in removed_paths)
    assert journal_path.read_bytes() == journal_bytes
    assert quarantine_path.read_bytes() == quarantine_bytes
    assert not state.old_path.exists()


def test_recover_indeterminate_mixed_rows_leaves_journal_and_files_unchanged(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "recover-indeterminate",
        candidate_count=2,
    )

    def raise_ambiguous(self, plan_id, unit_ids, execution_id=None):
        raise CommitOutcomeAmbiguousError("injected ambiguous outcome")

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        raise_ambiguous,
    )
    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)
    journal = _load_only_journal(state.paths)
    journal_path = _only_journal_path(state.paths)
    before_journal = journal_path.read_bytes()
    with state.repository._connection() as connection:
        connection.execute(
            "DELETE FROM source_snapshots WHERE id = ?",
            (int(plan.units[0].row_id),),
        )
    quarantine_bytes = tuple(
        _quarantined_path(state.paths, journal, index).read_bytes()
        for index in range(len(journal.moved_units))
    )

    with pytest.raises(HistoryError, match="indeterminate"):
        history_application.recover_history_cleanup(
            application_models.HistoryExecutionRequest(
                workspace,
                journal.execution_id,
            )
        )

    assert journal_path.read_bytes() == before_journal
    assert tuple(
        _quarantined_path(state.paths, journal, index).read_bytes()
        for index in range(len(journal.moved_units))
    ) == quarantine_bytes
    assert all(not (state.paths.root / unit.relative_path).exists() for unit in plan.units)


def test_committed_pending_purge_resume_is_idempotent_after_interruption(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "purge-interruption",
        candidate_count=2,
    )
    real_delete = SQLiteRepository.delete_planned_history_units

    def commit_then_raise(self, plan_id, unit_ids, execution_id=None):
        real_delete(self, plan_id, unit_ids, execution_id)
        raise CommitOutcomeAmbiguousError("injected post-commit ambiguity")

    monkeypatch.setattr(
        SQLiteRepository,
        "delete_planned_history_units",
        commit_then_raise,
    )
    with pytest.raises(HistoryError, match="reconciliation"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)
    journal = _load_only_journal(state.paths)
    recovered = history_application.recover_history_cleanup(
        application_models.HistoryExecutionRequest(
            workspace,
            journal.execution_id,
        )
    )
    assert recovered.state is CleanupExecutionState.COMMITTED_PENDING_PURGE

    real_purge_one = HistoryStore._purge_quarantined_unit
    purge_calls = 0

    def fail_second_purge(self, execution_id, moved_unit):
        nonlocal purge_calls
        purge_calls += 1
        if purge_calls == 2:
            raise OSError("injected purge interruption")
        return real_purge_one(self, execution_id, moved_unit)

    monkeypatch.setattr(
        HistoryStore,
        "_purge_quarantined_unit",
        fail_second_purge,
    )
    request = application_models.HistoryExecutionRequest(
        workspace,
        journal.execution_id,
    )
    with pytest.raises(HistoryError, match="purge"):
        history_application.resume_history_cleanup(request)

    interrupted = HistoryStore(state.paths).load_journal(journal.execution_id)
    assert interrupted.state is CleanupExecutionState.COMMITTED_PENDING_PURGE
    assert len(interrupted.moved_units) == 1
    assert _quarantined_path(state.paths, interrupted, 0).exists()
    monkeypatch.setattr(
        HistoryStore,
        "_purge_quarantined_unit",
        real_purge_one,
    )

    resumed = history_application.resume_history_cleanup(request)

    assert resumed.state is CleanupExecutionState.PURGED
    assert HistoryStore(state.paths).load_journal(
        journal.execution_id
    ).moved_units == ()
    assert all(not (state.paths.root / unit.relative_path).exists() for unit in plan.units)


def test_vacuum_failure_is_resumable_without_touching_original_or_quarantine(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "vacuum",
    )
    real_vacuum = SQLiteRepository.vacuum

    def fail_vacuum(self):
        raise RepositoryError("injected VACUUM failure")

    monkeypatch.setattr(SQLiteRepository, "vacuum", fail_vacuum)

    with pytest.raises(HistoryError, match="VACUUM"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.VACUUM_PENDING
    assert journal.moved_units == ()
    assert not state.old_path.exists()
    assert state.repository.get_history_plan(plan.id)["status"] == "completed"

    def unexpected_path_touch(*args, **kwargs):
        raise AssertionError("vacuum-only resume touched a managed history path")

    monkeypatch.setattr(
        HistoryStore,
        "purge_quarantine",
        unexpected_path_touch,
    )
    monkeypatch.setattr(
        HistoryStore,
        "restore_quarantine",
        unexpected_path_touch,
    )
    monkeypatch.setattr(SQLiteRepository, "vacuum", real_vacuum)

    resumed = history_application.resume_history_cleanup(
        application_models.HistoryExecutionRequest(
            workspace,
            journal.execution_id,
        )
    )

    assert resumed.state is CleanupExecutionState.PURGED
    assert HistoryStore(state.paths).load_journal(
        journal.execution_id
    ).state is CleanupExecutionState.PURGED


@pytest.mark.parametrize(
    ("status", "row_present", "expected"),
    (
        ("planned", True, "not_committed"),
        ("planned", False, "indeterminate"),
        ("completed", True, "indeterminate"),
        ("completed", False, "committed"),
        ("invalid", True, "indeterminate"),
    ),
)
def test_repository_cleanup_classification_requires_exact_plan_and_row_state(
    workspace,
    status,
    row_present,
    expected,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    with state.repository._connection() as connection:
        connection.execute(
            "UPDATE history_cleanup_plans SET status = ? WHERE id = ?",
            (status, plan.id),
        )
        if not row_present:
            connection.execute(
                "DELETE FROM source_snapshots WHERE id = ?",
                (state.old_id,),
            )

    assert state.repository.classify_history_cleanup_outcome(
        plan.id,
        plan.unit_ids(),
    ) == expected


def test_repository_cleanup_classification_maps_lookup_or_family_error_to_indeterminate(
    workspace,
    monkeypatch,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)

    @contextmanager
    def fail_read(self):
        raise DatabaseFamilyChangedError("injected database family mismatch")
        yield

    monkeypatch.setattr(
        SQLiteRepository,
        "_existing_read_connection",
        fail_read,
    )

    assert state.repository.classify_history_cleanup_outcome(
        plan.id,
        plan.unit_ids(),
    ) == "indeterminate"


def test_repository_cleanup_classification_never_creates_a_missing_database_family(
    workspace,
) -> None:
    state, plan = _seed_executable_history_plan(workspace)
    family_paths = (
        state.paths.db_path,
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-journal"),
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-wal"),
        state.paths.db_path.with_name(f"{state.paths.db_path.name}-shm"),
    )
    for path in family_paths:
        path.unlink(missing_ok=True)

    assert state.repository.classify_history_cleanup_outcome(
        plan.id,
        plan.unit_ids(),
    ) == "indeterminate"
    assert all(not path.exists() for path in family_paths)


def test_quarantine_creation_fsyncs_every_new_parent_directory(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "quarantine-directory-fsync",
    )
    real_fsync = os.fsync
    fsynced_directories: set[tuple[int, int]] = set()

    def observe_fsync(descriptor):
        stat_result = os.fstat(descriptor)
        if S_ISDIR(stat_result.st_mode):
            fsynced_directories.add((stat_result.st_dev, stat_result.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.history_store.os.fsync",
        observe_fsync,
    )

    result = _apply_exported_plan(workspace, plan, exported.receipt_hash)

    execution_root = state.paths.history_quarantine_dir / result.execution_id
    created_directories = (
        execution_root,
        execution_root / "raw",
        execution_root / "raw" / "snapshots",
        execution_root / "raw" / "snapshots" / "local",
    )
    assert {
        (path.stat().st_dev, path.stat().st_ino)
        for path in created_directories
    } <= fsynced_directories


def test_apply_sqlite_connection_finalizer_error_is_ambiguous_once(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "sqlite-finalizer",
    )
    original_connect = sqlite3.connect

    def connect_with_sqlite_close_failure(*args, **kwargs):
        connection = original_connect(
            *args,
            **kwargs,
            factory=_SyntheticFinalizerConnection,
        )
        connection.fail_close_after_commit = True
        connection.close_error_type = sqlite3.OperationalError
        return connection

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_sqlite_close_failure,
    )

    with pytest.raises(HistoryError, match="reconciliation") as captured:
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    ambiguous = captured.value.__cause__
    assert type(ambiguous) is CommitOutcomeAmbiguousError
    assert type(ambiguous.__cause__) is sqlite3.OperationalError
    assert not isinstance(ambiguous.__cause__, CommitOutcomeAmbiguousError)
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED


def test_apply_database_operation_finalizer_error_is_ambiguous_once(
    workspace,
    monkeypatch,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        "database-operation-finalizer",
    )
    original_connect = sqlite3.connect
    original_open_database_family = SQLiteRepository._open_database_family
    original_close = os.close
    finalizer_state = {
        "commit_succeeded": False,
        "directory_descriptor": None,
        "failure_raised": False,
    }

    def mark_commit_succeeded():
        finalizer_state["commit_succeeded"] = True

    def connect_with_commit_marker(*args, **kwargs):
        connection = original_connect(
            *args,
            **kwargs,
            factory=_SyntheticFinalizerConnection,
        )
        connection.commit_callback = mark_commit_succeeded
        return connection

    def record_database_directory(self, *, ensure_directory):
        descriptor, identity = original_open_database_family(
            self,
            ensure_directory=ensure_directory,
        )
        finalizer_state["directory_descriptor"] = descriptor
        return descriptor, identity

    def close_database_directory_with_failure(descriptor):
        should_fail = (
            finalizer_state["commit_succeeded"]
            and descriptor == finalizer_state["directory_descriptor"]
            and not finalizer_state["failure_raised"]
        )
        original_close(descriptor)
        if should_fail:
            finalizer_state["failure_raised"] = True
            raise OSError(
                "injected database operation descriptor close failure after commit"
            )

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_commit_marker,
    )
    monkeypatch.setattr(
        SQLiteRepository,
        "_open_database_family",
        record_database_directory,
    )
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.os.close",
        close_database_directory_with_failure,
    )

    with pytest.raises(HistoryError, match="reconciliation") as captured:
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    ambiguous = captured.value.__cause__
    assert type(ambiguous) is CommitOutcomeAmbiguousError
    assert type(ambiguous.__cause__) is OSError
    assert not isinstance(ambiguous.__cause__, CommitOutcomeAmbiguousError)
    assert finalizer_state["failure_raised"] is True
    journal = _load_only_journal(state.paths)
    assert journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED


@pytest.mark.parametrize("stale_kind", ("replacement", "symlink"))
def test_history_apply_revalidates_stale_or_symlinked_candidate_before_move(
    workspace,
    monkeypatch,
    stale_kind,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"stale-{stale_kind}",
    )
    state.old_path.unlink()
    if stale_kind == "replacement":
        state.old_path.write_text('{"replacement":true}', encoding="utf-8")
        preserved = state.old_path
    else:
        external = workspace / "external-preserved.json"
        external.write_text('{"external":true}', encoding="utf-8")
        state.old_path.symlink_to(external)
        preserved = external

    with pytest.raises(HistoryError, match="stale|unsafe"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert preserved.exists()
    assert state.repository.get_history_plan(plan.id)["status"] == "planned"
    assert list(state.paths.history_journal_dir.iterdir()) == []


@pytest.mark.parametrize(
    "binding_field",
    ("id", "manifest_hash", "manifest_relative_path"),
)
def test_history_apply_rejects_database_plan_binding_mismatch_before_move(
    workspace,
    binding_field,
) -> None:
    state, plan, exported = _seed_exported_history_plan(
        workspace,
        f"binding-{binding_field}",
    )
    replacement = {
        "id": "f" * 32,
        "manifest_hash": "f" * 64,
        "manifest_relative_path": "reviews/history-plans/other.json",
    }[binding_field]
    with state.repository._connection() as connection:
        connection.execute(
            f"UPDATE history_cleanup_plans SET {binding_field} = ? WHERE id = ?",
            (replacement, plan.id),
        )

    with pytest.raises(HistoryError, match="plan|binding|receipt"):
        _apply_exported_plan(workspace, plan, exported.receipt_hash)

    assert state.old_path.exists()
    assert list(state.paths.history_journal_dir.iterdir()) == []


def _seed_executable_history_plan(
    workspace: Path,
    *,
    candidate_count: int = 1,
) -> tuple[_SnapshotState, HistoryPlan]:
    state = _seed_old_unreferenced_snapshot(workspace)
    for index in range(1, candidate_count):
        source_uri = (workspace / f"synthetic-extra-{index}.txt").resolve().as_uri()
        source_id = state.repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri=source_uri,
                display_name=f"Synthetic extra history source {index}",
                owner=None,
                status=SourceStatus.INGESTED,
            )
        )
        old_path = state.paths.local_snapshots_dir / f"history-extra-{index}-old.json"
        old_path.write_text(json.dumps({"snapshot": f"extra-{index}-old"}), encoding="utf-8")
        state.repository.insert_source_snapshot(
            source_id,
            old_path,
            f"{index + 1:064x}",
            "text-v2",
        )
        latest_path = (
            state.paths.local_snapshots_dir / f"history-extra-{index}-latest.json"
        )
        latest_path.write_text(
            json.dumps({"snapshot": f"extra-{index}-latest"}),
            encoding="utf-8",
        )
        state.repository.insert_source_snapshot(
            source_id,
            latest_path,
            f"{index + 101:064x}",
            "text-v2",
        )
        with state.repository._connection() as connection:
            connection.execute(
                """
                UPDATE source_snapshots
                SET extracted_at = '2000-01-01T00:00:00Z'
                WHERE source_id = ?
                """,
                (source_id,),
            )
    write_sample_history_policy(state.paths)
    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))
    assert result.plan_id is not None
    assert result.candidate_count == candidate_count
    plan_files = list(state.paths.history_plans_dir.glob("plan-*.json"))
    assert len(plan_files) == 1
    payload = json.loads(plan_files[0].read_text(encoding="utf-8"))
    plan = HistoryPlan(
        id=payload["id"],
        policy=HistoryPolicy(**payload["policy"]),
        units=tuple(HistoryUnit(**unit) for unit in payload["units"]),
        manifest_hash=payload["manifest_hash"],
        confirmation_phrase=payload["confirmation_phrase"],
    )
    return state, plan


def _seed_exported_history_plan(
    workspace: Path,
    suffix: str,
    *,
    candidate_count: int = 1,
):
    state, plan = _seed_executable_history_plan(
        workspace,
        candidate_count=candidate_count,
    )
    exported = history_application.export_history_plan(
        application_models.HistoryExportRequest(
            workspace,
            plan.id,
            _outside_backup_dir(workspace, suffix),
        )
    )
    return state, plan, exported


def _apply_exported_plan(
    workspace: Path,
    plan: HistoryPlan,
    receipt_hash: str,
):
    return history_application.apply_history_plan(
        application_models.HistoryApplyRequest(
            workspace,
            plan.id,
            receipt_hash,
            plan.confirmation_phrase,
        )
    )


def _outside_backup_dir(workspace: Path, suffix: str) -> Path:
    return workspace.parent / f"{workspace.name}-{suffix}"


def _only_journal_path(paths: WorkspacePaths) -> Path:
    journal_paths = list(paths.history_journal_dir.glob("execution-*.json"))
    assert len(journal_paths) == 1
    return journal_paths[0]


def _load_only_journal(paths: WorkspacePaths):
    journal_path = _only_journal_path(paths)
    execution_id = journal_path.stem.removeprefix("execution-")
    return HistoryStore(paths).load_journal(execution_id)


def _quarantined_path(paths: WorkspacePaths, journal, index: int) -> Path:
    moved_unit = journal.moved_units[index]
    return (
        paths.history_quarantine_dir
        / journal.execution_id
        / moved_unit.quarantine_relative_path
    )


def _replace_stored_plan_binding(
    connection: sqlite3.Connection,
    plan_id: str,
) -> None:
    row = connection.execute(
        """
        SELECT canonical_policy_json, canonical_units_json
        FROM history_cleanup_plans
        WHERE id = ?
        """,
        (plan_id,),
    ).fetchone()
    assert row is not None
    policy_payload = json.loads(row[0])
    units_payload = json.loads(row[1])
    units_payload[0]["reason"] = f"{units_payload[0]['reason']}-replacement"
    canonical_units_json = json.dumps(
        units_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_hash = sha256(
        json.dumps(
            {"policy": policy_payload, "units": units_payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    connection.execute(
        """
        UPDATE history_cleanup_plans
        SET canonical_units_json = ?, manifest_hash = ?
        WHERE id = ?
        """,
        (canonical_units_json, manifest_hash, plan_id),
    )


def _seed_old_unreferenced_snapshot(
    workspace: Path,
    *,
    old_path: Path | None = None,
    old_content: bytes = b'{"snapshot":"old"}',
    create_old: bool = True,
) -> _SnapshotState:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_uri = (workspace / "synthetic-source.txt").resolve().as_uri()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_uri,
            display_name="Synthetic history source",
            owner=None,
            status=SourceStatus.INGESTED,
        )
    )
    old_path = old_path or paths.local_snapshots_dir / "history-old.json"
    if create_old:
        old_path.write_bytes(old_content)
    old_id = repository.insert_source_snapshot(
        source_id,
        old_path,
        "0" * 64,
        "text-v2",
    )
    latest_path = paths.local_snapshots_dir / "history-latest.json"
    latest_path.write_text('{"snapshot":"latest"}', encoding="utf-8")
    repository.insert_source_snapshot(
        source_id,
        latest_path,
        "1" * 64,
        "text-v2",
    )
    with repository._connection() as connection:
        connection.execute(
            "UPDATE source_snapshots SET extracted_at = '2000-01-01T00:00:00Z'"
        )
    return _SnapshotState(
        paths,
        repository,
        source_uri,
        old_path,
        latest_path,
        old_id,
    )


def _history_plan_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM history_cleanup_plans"
        ).fetchone()
    assert row is not None
    return int(row[0])


def _fail_history_plan_after_commit(monkeypatch) -> None:
    real_validate = SQLiteRepository._validate_database_family

    def fail_after_commit(self, directory_descriptor, identity, stage):
        real_validate(self, directory_descriptor, identity, stage)
        if stage == "after commit":
            raise RepositoryError(
                "injected history plan database validation failure after commit"
            )

    monkeypatch.setattr(SQLiteRepository, "initialize", lambda self: None)
    monkeypatch.setattr(
        SQLiteRepository,
        "_validate_database_family",
        fail_after_commit,
    )


def _snapshot_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        row = connection.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()
    assert row is not None
    return int(row[0])


def _history_policy_payload() -> dict[str, object]:
    return {
        "version": 1,
        "minimum_age_days": 30,
        "keep_latest_text_snapshots_per_source": 1,
        "retain_included_vision_analyses": True,
        "retain_pending_review_vision_analyses": True,
        "retain_incomplete_vision_requests": True,
    }
