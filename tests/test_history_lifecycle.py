from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from stat import S_IMODE, S_ISDIR, S_ISREG

import pytest

from portfolio_maker.application.history import (
    HistoryError,
    load_history_policy,
    plan_history_cleanup,
    write_sample_history_policy,
)
from portfolio_maker.application.models import HistoryPlanRequest
from portfolio_maker.domain.history_models import HistoryPlan, HistoryPolicy
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
            raise RuntimeError(
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
    replaced = False

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

    with pytest.raises(HistoryError, match="planning failed safely") as captured:
        plan_history_cleanup(HistoryPlanRequest(workspace=workspace))

    assert type(captured.value.__cause__) is DatabaseFamilyChangedError
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
    assert (len(plan_files), len(report_files)) == (1, 0)
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
