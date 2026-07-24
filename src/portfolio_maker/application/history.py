from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from portfolio_maker.application.models import (
    HistoryApplyRequest,
    HistoryApplyResult,
    HistoryExecutionRequest,
    HistoryExportRequest,
    HistoryExportResult,
    HistoryPlanRequest,
    HistoryPlanResult,
)
from portfolio_maker.domain.history_models import (
    CleanupExecutionState,
    HistoryCleanupJournal,
    HistoryPlan,
    HistoryPolicy,
    HistoryUnit,
)
from portfolio_maker.infrastructure.history_store import HistoryStore
from portfolio_maker.infrastructure.managed_files import (
    read_managed_relative_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.sqlite_repository import (
    CommitOutcomeAmbiguousError,
    RepositoryError,
    SQLiteRepository,
)
from portfolio_maker.workspace import WorkspacePaths


_POLICY_FIELDS = frozenset(
    {
        "version",
        "minimum_age_days",
        "keep_latest_text_snapshots_per_source",
        "retain_included_vision_analyses",
        "retain_pending_review_vision_analyses",
        "retain_incomplete_vision_requests",
    }
)
_MAX_MINIMUM_AGE_DAYS = timedelta.max.days
_MAX_SQLITE_INTEGER = (1 << 63) - 1


class HistoryError(RuntimeError):
    pass


class _DuplicatePolicyFieldError(ValueError):
    pass


def write_sample_history_policy(
    paths: WorkspacePaths,
    *,
    force: bool = False,
) -> Path:
    payload = {
        "version": 1,
        **_policy_payload(HistoryPolicy.default()),
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        paths.ensure()
        return write_managed_text(
            paths.history_policy_path,
            content,
            overwrite=force,
        )
    except FileExistsError as error:
        raise HistoryError("history policy already exists") from error
    except OSError as error:
        raise HistoryError("history policy path is unsafe") from error


def load_history_policy(paths: WorkspacePaths) -> HistoryPolicy | None:
    try:
        snapshot = read_managed_relative_file(
            paths.reviews_dir,
            Path(paths.history_policy_path.name),
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise HistoryError("history policy path is unsafe") from error
    try:
        payload = json.loads(
            snapshot.content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_fields,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicatePolicyFieldError,
    ) as error:
        raise HistoryError("history policy is malformed") from error
    return _parse_history_policy(payload)


def plan_history_cleanup(request: HistoryPlanRequest) -> HistoryPlanResult:
    paths = WorkspacePaths.from_root(request.workspace)
    try:
        paths.ensure()
    except OSError as error:
        raise HistoryError("managed history workspace is unsafe") from error
    policy = load_history_policy(paths)
    if policy is None:
        return _write_summary(
            paths,
            plan_id=None,
            units=(),
            fallback_reason="history_policy_missing_retain_all",
        )

    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    try:
        with repository.exclusive_workspace_operation():
            repository.initialize()
            units = _select_history_candidates(repository, policy)
            if not units:
                return _write_summary(
                    paths,
                    plan_id=None,
                    units=(),
                    fallback_reason="no_eligible_candidates",
                )
            plan = HistoryPlan.create(policy, list(units))
            plan_path = store.write_plan(plan)
            try:
                repository.create_history_plan(
                    plan,
                    plan_path.relative_to(paths.root),
                )
            except CommitOutcomeAmbiguousError as error:
                raise HistoryError(
                    "history cleanup planning failed safely"
                ) from error
            except RepositoryError as error:
                try:
                    stored_plan = repository.find_history_plan(plan.id)
                except RepositoryError as lookup_error:
                    raise HistoryError(
                        "history cleanup planning failed safely"
                    ) from lookup_error
                expected_relative_path = plan_path.relative_to(
                    paths.root
                ).as_posix()
                if stored_plan is not None:
                    if (
                        stored_plan["id"] != plan.id
                        or stored_plan["manifest_hash"] != plan.manifest_hash
                        or stored_plan["manifest_relative_path"]
                        != expected_relative_path
                    ):
                        raise HistoryError(
                            "history cleanup plan binding is inconsistent"
                        ) from error
                    raise HistoryError(
                        "history cleanup planning failed safely"
                    ) from error
                try:
                    store.remove_plan(plan_path)
                except OSError as cleanup_error:
                    raise HistoryError(
                        "history cleanup plan binding failed and cleanup was incomplete"
                    ) from cleanup_error
                raise HistoryError("history cleanup planning failed safely") from error
            try:
                return _write_summary(
                    paths,
                    plan_id=plan.id,
                    units=plan.units,
                )
            except OSError as error:
                raise HistoryError(
                    "history cleanup report publication failed safely"
                ) from error
    except RepositoryError as error:
        raise HistoryError("history cleanup planning failed safely") from error
    except (OSError, TypeError, ValueError) as error:
        raise HistoryError("managed history candidate or plan is unsafe") from error


def export_history_plan(request: HistoryExportRequest) -> HistoryExportResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    try:
        store.prepare_export_directory(request.backup_dir)
        with repository.exclusive_workspace_operation():
            plan = _load_bound_plan(
                store,
                repository,
                request.plan_id,
                allowed_statuses=("planned",),
            )
            store.revalidate_units(plan.units)
            database_backup = repository.backup_bytes()
            try:
                repository.assert_history_plan_binding(
                    plan,
                    allowed_statuses=("planned",),
                )
            except RepositoryError as error:
                raise HistoryError(
                    "history cleanup plan binding changed during export"
                ) from error
            archive_path, receipt_hash = store.create_export_archive(
                plan,
                request.backup_dir,
                database_backup,
            )
            try:
                repository.record_history_export_receipt(
                    plan.id,
                    plan.manifest_hash,
                    receipt_hash,
                )
            except (CommitOutcomeAmbiguousError, RepositoryError) as error:
                try:
                    receipt_confirmed = repository.export_receipt_matches(
                        plan.id,
                        plan.manifest_hash,
                        receipt_hash,
                    )
                except Exception:
                    receipt_confirmed = False
                if not receipt_confirmed:
                    raise HistoryError(
                        "history export receipt could not be confirmed"
                    ) from error
            return HistoryExportResult(plan.id, receipt_hash, archive_path)
    except HistoryError:
        raise
    except (OSError, RepositoryError, TypeError, ValueError) as error:
        raise HistoryError("history export failed safely") from error


def apply_history_plan(request: HistoryApplyRequest) -> HistoryApplyResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    try:
        with repository.exclusive_workspace_operation():
            plan = _load_bound_plan(
                store,
                repository,
                request.plan_id,
                allowed_statuses=("planned",),
            )
            try:
                repository.assert_export_receipt(
                    plan.id,
                    request.export_receipt_hash,
                )
            except RepositoryError as error:
                raise HistoryError(str(error)) from error
            _require_exact_confirmation(
                request.confirmation,
                plan.confirmation_phrase,
            )
            try:
                store.revalidate_units(plan.units)
            except OSError as error:
                raise HistoryError(
                    "managed history plan is stale or unsafe"
                ) from error

            execution_id = uuid4().hex
            journal = HistoryCleanupJournal(
                execution_id=execution_id,
                plan_id=plan.id,
                manifest_hash=plan.manifest_hash,
                state=CleanupExecutionState.EXPORTED,
                units=plan.units,
                moved_units=(),
            )
            journal_created = False
            try:
                store.write_journal(journal, create=True)
                journal_created = True
                journal = store.move_units_to_quarantine(journal)
            except Exception as error:
                if not journal_created:
                    raise HistoryError(
                        "history cleanup journal creation failed before file move"
                    ) from error
                try:
                    current = store.load_journal(execution_id)
                    store.restore_quarantine(current)
                except Exception as restore_error:
                    raise HistoryError(
                        "history quarantine move failed; recovery is required"
                    ) from restore_error
                raise HistoryError(
                    "history quarantine move failed; managed files were restored"
                ) from error

            try:
                repository.delete_planned_history_units(
                    plan.id,
                    plan.unit_ids(),
                    execution_id,
                )
            except CommitOutcomeAmbiguousError as error:
                journal = replace(
                    journal,
                    state=CleanupExecutionState.RECONCILIATION_REQUIRED,
                )
                try:
                    store.write_journal(journal)
                except Exception as journal_error:
                    raise HistoryError(
                        "database outcome is ambiguous; recovery journal update failed"
                    ) from journal_error
                raise HistoryError(
                    "database outcome is ambiguous; reconciliation is required"
                ) from error
            except Exception as error:
                try:
                    store.restore_quarantine(journal)
                except OSError as restore_error:
                    raise HistoryError(
                        "database update failed; managed file recovery is required"
                    ) from restore_error
                raise HistoryError(
                    "database update failed; managed files were restored"
                ) from error

            journal = replace(
                journal,
                state=CleanupExecutionState.COMMITTED_PENDING_PURGE,
            )
            try:
                store.write_journal(journal)
            except Exception as error:
                reconciliation_required = replace(
                    journal,
                    state=CleanupExecutionState.RECONCILIATION_REQUIRED,
                )
                try:
                    store.write_journal(reconciliation_required)
                except Exception as journal_error:
                    raise HistoryError(
                        "database cleanup committed; recovery journal update failed"
                    ) from journal_error
                raise HistoryError(
                    "database cleanup committed; reconciliation is required"
                ) from error
            return _finish_committed_cleanup(repository, store, journal)
    except HistoryError:
        raise
    except RepositoryError as error:
        raise HistoryError("history cleanup repository is unavailable") from error
    except (OSError, TypeError, ValueError) as error:
        raise HistoryError("history cleanup plan or journal is unsafe") from error


def recover_history_cleanup(
    request: HistoryExecutionRequest,
) -> HistoryApplyResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    try:
        with repository.exclusive_workspace_operation():
            journal = store.load_journal(request.execution_id)
            if journal.state not in {
                CleanupExecutionState.QUARANTINED,
                CleanupExecutionState.RECONCILIATION_REQUIRED,
            }:
                raise HistoryError(
                    "history cleanup journal cannot be recovered from its current state"
                )
            _assert_journal_plan_binding(store, repository, journal)
            outcome = repository.classify_history_cleanup_outcome(
                journal.plan_id,
                tuple((unit.kind, unit.row_id) for unit in journal.units),
            )
            if outcome == "indeterminate":
                raise HistoryError(
                    "history cleanup outcome is indeterminate; no changes were made"
                )
            if outcome == "not_committed":
                try:
                    restored = store.restore_quarantine(journal)
                except OSError as error:
                    raise HistoryError(
                        "history cleanup restore was interrupted; recovery is required"
                    ) from error
                return _execution_result(restored)
            if journal.state is not CleanupExecutionState.RECONCILIATION_REQUIRED:
                raise HistoryError(
                    "committed cleanup requires explicit reconciliation state"
                )
            pending = replace(
                journal,
                state=CleanupExecutionState.COMMITTED_PENDING_PURGE,
            )
            store.write_journal(pending)
            return _execution_result(pending)
    except HistoryError:
        raise
    except (OSError, RepositoryError, TypeError, ValueError) as error:
        raise HistoryError("history cleanup recovery failed safely") from error


def resume_history_cleanup(
    request: HistoryExecutionRequest,
) -> HistoryApplyResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    try:
        with repository.exclusive_workspace_operation():
            journal = store.load_journal(request.execution_id)
            if journal.state is CleanupExecutionState.VACUUM_PENDING:
                try:
                    repository.vacuum()
                    completed = replace(
                        journal,
                        state=CleanupExecutionState.PURGED,
                    )
                    store.write_journal(completed)
                except (OSError, RepositoryError) as error:
                    raise HistoryError(
                        "history cleanup VACUUM retry failed"
                    ) from error
                return _execution_result(completed)
            if journal.state is not CleanupExecutionState.COMMITTED_PENDING_PURGE:
                raise HistoryError(
                    "history cleanup journal cannot be resumed from its current state"
                )
            _assert_journal_plan_binding(store, repository, journal)
            outcome = repository.classify_history_cleanup_outcome(
                journal.plan_id,
                tuple((unit.kind, unit.row_id) for unit in journal.units),
            )
            if outcome != "committed":
                raise HistoryError(
                    "history cleanup commit cannot be confirmed; no changes were made"
                )
            return _finish_committed_cleanup(repository, store, journal)
    except HistoryError:
        raise
    except (OSError, RepositoryError, TypeError, ValueError) as error:
        raise HistoryError("history cleanup resume failed safely") from error


def _finish_committed_cleanup(
    repository: SQLiteRepository,
    store: HistoryStore,
    journal: HistoryCleanupJournal,
) -> HistoryApplyResult:
    try:
        journal = store.purge_quarantine(journal)
    except OSError as error:
        raise HistoryError(
            "history quarantine purge was interrupted; resume is required"
        ) from error
    try:
        repository.vacuum()
    except Exception as error:
        vacuum_pending = replace(
            journal,
            state=CleanupExecutionState.VACUUM_PENDING,
        )
        try:
            store.write_journal(vacuum_pending)
        except OSError as journal_error:
            raise HistoryError(
                "history cleanup committed; VACUUM and journal recovery are required"
            ) from journal_error
        raise HistoryError(
            "history cleanup committed; VACUUM retry is required"
        ) from error
    completed = replace(journal, state=CleanupExecutionState.PURGED)
    try:
        store.write_journal(completed)
    except OSError as error:
        raise HistoryError(
            "history cleanup completed but journal finalization failed"
        ) from error
    return _execution_result(completed)


def _load_bound_plan(
    store: HistoryStore,
    repository: SQLiteRepository,
    plan_id: str,
    *,
    allowed_statuses: tuple[str, ...],
) -> HistoryPlan:
    try:
        plan = store.load_verified_plan(plan_id)
        repository.assert_history_plan_binding(
            plan,
            allowed_statuses=allowed_statuses,
        )
        return plan
    except (OSError, RepositoryError, TypeError, ValueError) as error:
        raise HistoryError(
            "history cleanup plan binding is invalid"
        ) from error


def _assert_journal_plan_binding(
    store: HistoryStore,
    repository: SQLiteRepository,
    journal: HistoryCleanupJournal,
) -> None:
    plan = _load_bound_plan(
        store,
        repository,
        journal.plan_id,
        allowed_statuses=("planned", "completed"),
    )
    if (
        plan.manifest_hash != journal.manifest_hash
        or plan.units != journal.units
    ):
        raise HistoryError("history cleanup journal plan binding is invalid")


def _require_exact_confirmation(actual: str, expected: str) -> None:
    if actual != expected:
        raise HistoryError("exact history cleanup confirmation is required")


def _execution_result(journal: HistoryCleanupJournal) -> HistoryApplyResult:
    return HistoryApplyResult(
        execution_id=journal.execution_id,
        state=journal.state,
        affected_count=len(journal.units),
    )


def _parse_history_policy(payload: Any) -> HistoryPolicy:
    if not isinstance(payload, dict) or set(payload) != _POLICY_FIELDS:
        raise HistoryError("history policy must use the fixed v1 schema")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise HistoryError("history policy must use version 1")
    minimum_age_days = payload["minimum_age_days"]
    keep_latest = payload["keep_latest_text_snapshots_per_source"]
    retained_states = (
        payload["retain_included_vision_analyses"],
        payload["retain_pending_review_vision_analyses"],
        payload["retain_incomplete_vision_requests"],
    )
    if (
        type(minimum_age_days) is not int
        or minimum_age_days < 0
        or minimum_age_days > _MAX_MINIMUM_AGE_DAYS
        or type(keep_latest) is not int
        or keep_latest < 1
        or keep_latest > _MAX_SQLITE_INTEGER
        or any(type(value) is not bool for value in retained_states)
    ):
        raise HistoryError("history policy contains invalid values")
    if not all(retained_states):
        raise HistoryError("history policy must retain mandatory workflow states")
    return HistoryPolicy(
        minimum_age_days=minimum_age_days,
        keep_latest_text_snapshots_per_source=keep_latest,
        retain_included_vision_analyses=retained_states[0],
        retain_pending_review_vision_analyses=retained_states[1],
        retain_incomplete_vision_requests=retained_states[2],
    )


def _select_history_candidates(
    repository: SQLiteRepository,
    policy: HistoryPolicy,
) -> tuple[HistoryUnit, ...]:
    oldest_datetime = datetime.min.replace(tzinfo=timezone.utc)
    maximum_elapsed_days = (datetime.now(timezone.utc) - oldest_datetime).days
    if policy.minimum_age_days > maximum_elapsed_days:
        return ()
    return tuple(repository.history_candidates(policy))


def _write_summary(
    paths: WorkspacePaths,
    *,
    plan_id: str | None,
    units: tuple[HistoryUnit, ...],
    fallback_reason: str | None = None,
) -> HistoryPlanResult:
    kind_counts = Counter(unit.kind for unit in units)
    reason_counts = Counter(unit.reason for unit in units)
    if fallback_reason is not None:
        reason_counts[fallback_reason] += 1
    kinds = tuple(sorted(kind_counts))
    reasons = tuple(sorted(reason_counts))
    candidate_count = len(units)
    total_bytes = sum(unit.size for unit in units)
    lines = [
        "# Managed History Cleanup Plan",
        "",
        f"Plan ID: {plan_id or 'none'}",
        f"Candidates: {candidate_count}",
        f"Bytes: {total_bytes}",
        "Kinds:",
    ]
    lines.extend(
        [f"- {kind}: {kind_counts[kind]}" for kind in kinds]
        or ["- none: 0"]
    )
    lines.append("Reasons:")
    lines.extend(
        [f"- {reason}: {reason_counts[reason]}" for reason in reasons]
        or ["- none: 0"]
    )
    report_name = (
        f"history-plan-{plan_id}-report.md"
        if plan_id is not None
        else f"history-plan-{fallback_reason}-report.md"
    )
    report_path = write_managed_text(
        paths.reviews_dir / report_name,
        "\n".join(lines) + "\n",
        overwrite=plan_id is None,
    )
    return HistoryPlanResult(
        plan_id=plan_id,
        candidate_count=candidate_count,
        total_bytes=total_bytes,
        kinds=kinds,
        reasons=reasons,
        report_path=report_path,
    )


def _policy_payload(policy: HistoryPolicy) -> dict[str, object]:
    return {
        "minimum_age_days": policy.minimum_age_days,
        "keep_latest_text_snapshots_per_source": (
            policy.keep_latest_text_snapshots_per_source
        ),
        "retain_included_vision_analyses": (
            policy.retain_included_vision_analyses
        ),
        "retain_pending_review_vision_analyses": (
            policy.retain_pending_review_vision_analyses
        ),
        "retain_incomplete_vision_requests": (
            policy.retain_incomplete_vision_requests
        ),
    }


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicatePolicyFieldError
        payload[key] = value
    return payload
