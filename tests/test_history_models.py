from pathlib import Path

import pytest

from portfolio_maker.application.models import (
    HistoryApplyRequest,
    HistoryExportRequest,
    HistoryPlanRequest,
)
from portfolio_maker.domain.history_models import (
    CleanupExecutionState,
    HistoryPlan,
    HistoryPolicy,
    HistoryUnit,
)


def test_default_history_policy_retains_protected_units() -> None:
    policy = HistoryPolicy.default()

    assert policy.minimum_age_days == 30
    assert policy.keep_latest_text_snapshots_per_source == 1
    assert policy.retain_included_vision_analyses is True
    assert policy.retain_pending_review_vision_analyses is True
    assert policy.retain_incomplete_vision_requests is True


def test_history_plan_canonicalizes_units_and_has_confirmation_phrase() -> None:
    policy = HistoryPolicy.default()
    units = [
        HistoryUnit("vision_analysis", "2", "derived/vision/2.json", "b" * 64, 2, 1, 2, "old"),
        HistoryUnit("snapshot", "1", "raw/snapshots/1.txt", "a" * 64, 1, 1, 1, "old"),
    ]

    plan = HistoryPlan.create(policy, units)

    assert plan.units == (units[1], units[0])
    assert plan.unit_ids() == (("snapshot", "1"), ("vision_analysis", "2"))
    assert len(plan.manifest_hash) == 64
    assert plan.confirmation_phrase == "DELETE MANAGED HISTORY"


def test_history_plan_canonicalizes_reversed_units_with_same_identity() -> None:
    policy = HistoryPolicy.default()
    first = HistoryUnit("snapshot", "1", "raw/snapshots/1-a.txt", "a" * 64, 1, 1, 1, "old")
    second = HistoryUnit("snapshot", "1", "raw/snapshots/1-b.txt", "b" * 64, 2, 1, 2, "new")

    forward = HistoryPlan.create(policy, [first, second])
    reverse = HistoryPlan.create(policy, [second, first])

    assert forward.units == reverse.units
    assert forward.manifest_hash == reverse.manifest_hash


def test_history_contracts_are_immutable_and_expose_execution_states() -> None:
    policy = HistoryPolicy.default()

    with pytest.raises(Exception):
        policy.minimum_age_days = 14

    assert CleanupExecutionState.COMMITTED_PENDING_PURGE.value == "committed_pending_purge"
    assert CleanupExecutionState.VACUUM_PENDING.value == "vacuum_pending"


def test_history_requests_keep_workspace_and_backup_paths() -> None:
    workspace = Path("/tmp/workspace")
    backup_dir = Path("/tmp/backup")

    assert HistoryPlanRequest(workspace).workspace == workspace
    assert HistoryExportRequest(workspace, "plan-1", backup_dir).backup_dir == backup_dir
    assert HistoryApplyRequest(workspace, "plan-1", "receipt", "DELETE MANAGED HISTORY").confirmation == "DELETE MANAGED HISTORY"
