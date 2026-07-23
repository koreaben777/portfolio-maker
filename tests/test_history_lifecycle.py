from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from stat import S_IMODE

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
from portfolio_maker.infrastructure.history_store import HistoryStore
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


@dataclass(frozen=True)
class _SnapshotState:
    paths: WorkspacePaths
    repository: SQLiteRepository
    source_uri: str
    old_path: Path
    latest_path: Path
    old_id: int


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


def _snapshot_count(repository: SQLiteRepository) -> int:
    with repository._read_connection() as connection:
        row = connection.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()
    assert row is not None
    return int(row[0])
