from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from stat import S_ISDIR, S_ISREG
import tarfile

from portfolio_maker.domain.history_models import (
    CleanupExecutionState,
    HistoryCleanupJournal,
    HistoryPlan,
    HistoryPolicy,
    HistoryUnit,
    QuarantinedHistoryUnit,
)
from portfolio_maker.infrastructure.managed_files import (
    open_managed_directory,
    read_managed_relative_file,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.workspace import WorkspacePaths


_KIND_ROOTS = {
    "source_snapshot": ("raw", "snapshots", "local"),
    "vision_analysis": ("derived", "vision"),
    "vision_request": ("reviews", "vision-requests"),
}
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
_PLAN_FIELDS = frozenset(
    {
        "version",
        "id",
        "policy",
        "units",
        "manifest_hash",
        "confirmation_phrase",
    }
)
_POLICY_FIELDS = frozenset(
    {
        "minimum_age_days",
        "keep_latest_text_snapshots_per_source",
        "retain_included_vision_analyses",
        "retain_pending_review_vision_analyses",
        "retain_incomplete_vision_requests",
    }
)
_UNIT_FIELDS = frozenset(
    {
        "kind",
        "row_id",
        "relative_path",
        "content_hash",
        "size",
        "device",
        "inode",
        "reason",
    }
)
_JOURNAL_FIELDS = frozenset(
    {
        "version",
        "execution_id",
        "plan_id",
        "manifest_hash",
        "state",
        "units",
        "moved_units",
        "journal_hash",
    }
)
_MOVED_UNIT_FIELDS = frozenset({"unit", "quarantine_relative_path"})


class _DuplicateFieldError(ValueError):
    pass


class HistoryStore:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def write_plan(self, plan: HistoryPlan) -> Path:
        self.revalidate_units(plan.units)
        _validate_plan(plan)
        payload = {
            "version": 1,
            "id": plan.id,
            "policy": asdict(plan.policy),
            "units": [asdict(unit) for unit in plan.units],
            "manifest_hash": plan.manifest_hash,
            "confirmation_phrase": plan.confirmation_phrase,
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        plan_path = self.paths.history_plans_dir / (
            f"plan-{plan.id}-{plan.manifest_hash}.json"
        )
        return write_managed_text(plan_path, content, overwrite=False)

    def remove_plan(self, plan_path: Path) -> None:
        if plan_path.parent != self.paths.history_plans_dir:
            raise OSError("managed history plan path is invalid")
        remove_managed_file(plan_path)

    def load_verified_plan(self, plan_id: str) -> HistoryPlan:
        _validate_hex_identifier(plan_id, 32, "managed history plan id")
        directory_descriptor = open_managed_directory(
            self.paths.history_plans_dir
        )
        try:
            prefix = f"plan-{plan_id}-"
            names = [
                name
                for name in os.listdir(directory_descriptor)
                if name.startswith(prefix) and name.endswith(".json")
            ]
        finally:
            os.close(directory_descriptor)
        if len(names) != 1:
            raise OSError("managed history plan is missing or ambiguous")
        snapshot = read_managed_relative_file(
            self.paths.history_plans_dir,
            Path(names[0]),
        )
        try:
            payload = json.loads(
                snapshot.content.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_fields,
            )
            if (
                type(payload) is not dict
                or frozenset(payload) != _PLAN_FIELDS
                or type(payload["version"]) is not int
                or payload["version"] != 1
                or type(payload["policy"]) is not dict
                or frozenset(payload["policy"]) != _POLICY_FIELDS
                or type(payload["units"]) is not list
            ):
                raise ValueError("managed history plan shape is invalid")
            policy = HistoryPolicy(**payload["policy"])
            units = tuple(
                _history_unit_from_payload(unit_payload)
                for unit_payload in payload["units"]
            )
            plan = HistoryPlan(
                id=payload["id"],
                policy=policy,
                units=units,
                manifest_hash=payload["manifest_hash"],
                confirmation_phrase=payload["confirmation_phrase"],
            )
            _validate_plan(plan)
        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            _DuplicateFieldError,
        ) as error:
            raise OSError("managed history plan is invalid") from error
        expected_name = f"plan-{plan.id}-{plan.manifest_hash}.json"
        if (
            plan.id != plan_id
            or names[0] != expected_name
            or snapshot.content != _plan_content(plan)
        ):
            raise OSError("managed history plan identity is invalid")
        return plan

    def create_export_archive(
        self,
        plan: HistoryPlan,
        backup_dir: Path,
        database_backup: bytes,
    ) -> tuple[Path, str]:
        _validate_plan(plan)
        if not isinstance(database_backup, bytes) or not database_backup:
            raise OSError("history export database backup is invalid")
        self.revalidate_units(plan.units)
        plan_snapshot = read_managed_relative_file(
            self.paths.history_plans_dir,
            Path(f"plan-{plan.id}-{plan.manifest_hash}.json"),
        )
        if plan_snapshot.content != _plan_content(plan):
            raise OSError("managed history plan identity changed")
        candidate_contents: list[tuple[HistoryUnit, bytes]] = []
        for unit in plan.units:
            root, relative_path = self._unit_root_and_path(unit)
            snapshot = read_managed_relative_file(root, relative_path)
            _assert_snapshot_matches_unit(snapshot, unit)
            candidate_contents.append((unit, snapshot.content))

        absolute_backup_dir, directory_descriptor = self._open_export_directory(
            backup_dir
        )
        archive_name = f"history-plan-{plan.id}.tar"
        archive_descriptor: int | None = None
        archive_created = False
        try:
            archive_descriptor = os.open(
                archive_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            archive_created = True
            with os.fdopen(archive_descriptor, "w+b") as archive_file:
                archive_descriptor = None
                with tarfile.open(fileobj=archive_file, mode="w") as archive:
                    _add_tar_bytes(
                        archive,
                        "history-plan.json",
                        plan_snapshot.content,
                    )
                    for unit, content in candidate_contents:
                        _add_tar_bytes(
                            archive,
                            f"managed/{unit.relative_path}",
                            content,
                        )
                    _add_tar_bytes(archive, "portfolio.db", database_backup)
                archive_file.flush()
                os.fsync(archive_file.fileno())
                archive_file.seek(0)
                digest = sha256()
                while chunk := archive_file.read(65_536):
                    digest.update(chunk)
                stat_result = os.fstat(archive_file.fileno())
                if (
                    not S_ISREG(stat_result.st_mode)
                    or stat_result.st_nlink != 1
                ):
                    raise OSError("history export archive is unsafe")
            os.fsync(directory_descriptor)
            return absolute_backup_dir / archive_name, digest.hexdigest()
        except Exception:
            if archive_created:
                try:
                    os.unlink(archive_name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
                else:
                    os.fsync(directory_descriptor)
            raise
        finally:
            if archive_descriptor is not None:
                os.close(archive_descriptor)
            os.close(directory_descriptor)

    def prepare_export_directory(self, backup_dir: Path) -> Path:
        absolute, directory_descriptor = self._open_export_directory(backup_dir)
        os.close(directory_descriptor)
        return absolute

    def write_journal(
        self,
        journal: HistoryCleanupJournal,
        *,
        create: bool = False,
    ) -> Path:
        _validate_journal(journal)
        payload = _journal_payload(journal)
        payload["journal_hash"] = _journal_hash(payload)
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return write_managed_text(
            self._journal_path(journal.execution_id),
            content,
            overwrite=not create,
        )

    def load_journal(self, execution_id: str) -> HistoryCleanupJournal:
        _validate_hex_identifier(
            execution_id,
            32,
            "history cleanup execution id",
        )
        try:
            snapshot = read_managed_relative_file(
                self.paths.history_journal_dir,
                Path(f"execution-{execution_id}.json"),
            )
            payload = json.loads(
                snapshot.content.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_fields,
            )
            if type(payload) is not dict or frozenset(payload) != _JOURNAL_FIELDS:
                raise ValueError("history cleanup journal shape is invalid")
            recorded_hash = payload["journal_hash"]
            if (
                not isinstance(recorded_hash, str)
                or recorded_hash != _journal_hash(payload)
                or type(payload["version"]) is not int
                or payload["version"] != 1
                or type(payload["units"]) is not list
                or type(payload["moved_units"]) is not list
            ):
                raise ValueError("history cleanup journal hash is invalid")
            units = tuple(
                _history_unit_from_payload(unit_payload)
                for unit_payload in payload["units"]
            )
            moved_units = tuple(
                _quarantined_unit_from_payload(moved_payload)
                for moved_payload in payload["moved_units"]
            )
            journal = HistoryCleanupJournal(
                execution_id=payload["execution_id"],
                plan_id=payload["plan_id"],
                manifest_hash=payload["manifest_hash"],
                state=CleanupExecutionState(payload["state"]),
                units=units,
                moved_units=moved_units,
            )
            _validate_journal(journal)
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            _DuplicateFieldError,
        ) as error:
            raise OSError("history cleanup journal is invalid") from error
        if journal.execution_id != execution_id:
            raise OSError("history cleanup journal identity is invalid")
        return journal

    def move_units_to_quarantine(
        self,
        journal: HistoryCleanupJournal,
    ) -> HistoryCleanupJournal:
        current = journal
        for unit in journal.units[len(journal.moved_units) :]:
            moved_unit = self._move_unit_to_quarantine(
                journal.execution_id,
                unit,
            )
            next_journal = replace(
                current,
                state=CleanupExecutionState.QUARANTINED,
                moved_units=(*current.moved_units, moved_unit),
            )
            try:
                self.write_journal(next_journal)
            except Exception:
                self._restore_quarantined_unit(
                    journal.execution_id,
                    moved_unit,
                )
                raise
            current = next_journal
        return current

    def restore_quarantine(
        self,
        journal: HistoryCleanupJournal,
    ) -> HistoryCleanupJournal:
        current = journal
        while current.moved_units:
            moved_unit = current.moved_units[-1]
            self._restore_quarantined_unit(
                current.execution_id,
                moved_unit,
            )
            remaining = current.moved_units[:-1]
            current = replace(
                current,
                state=(
                    CleanupExecutionState.RESTORED
                    if not remaining
                    else CleanupExecutionState.QUARANTINED
                ),
                moved_units=remaining,
            )
            self.write_journal(current)
        if current.state is not CleanupExecutionState.RESTORED:
            current = replace(current, state=CleanupExecutionState.RESTORED)
            self.write_journal(current)
        return current

    def purge_quarantine(
        self,
        journal: HistoryCleanupJournal,
    ) -> HistoryCleanupJournal:
        current = journal
        while current.moved_units:
            moved_unit = current.moved_units[0]
            self._purge_quarantined_unit(
                current.execution_id,
                moved_unit,
            )
            current = replace(
                current,
                state=CleanupExecutionState.COMMITTED_PENDING_PURGE,
                moved_units=current.moved_units[1:],
            )
            self.write_journal(current)
        return current

    def revalidate_units(
        self,
        units: tuple[HistoryUnit, ...] | list[HistoryUnit],
    ) -> None:
        for unit in units:
            root, relative_path = self._unit_root_and_path(unit)
            snapshot = read_managed_relative_file(root, relative_path)
            if (
                snapshot.size != unit.size
                or snapshot.device != unit.device
                or snapshot.inode != unit.inode
                or snapshot.content_hash != unit.content_hash
            ):
                raise OSError("managed history candidate identity changed")

    def _unit_root_and_path(self, unit: HistoryUnit) -> tuple[Path, Path]:
        expected_root = _KIND_ROOTS.get(unit.kind)
        if expected_root is None:
            raise OSError("managed history candidate kind is invalid")
        relative_path = Path(unit.relative_path)
        if (
            relative_path.is_absolute()
            or relative_path.as_posix() != unit.relative_path
            or len(relative_path.parts) <= len(expected_root)
            or relative_path.parts[: len(expected_root)] != expected_root
        ):
            raise OSError("managed history candidate root is invalid")
        managed_root = self.paths.root.joinpath(*expected_root)
        child_path = Path(*relative_path.parts[len(expected_root) :])
        return managed_root, child_path

    def _journal_path(self, execution_id: str) -> Path:
        _validate_hex_identifier(
            execution_id,
            32,
            "history cleanup execution id",
        )
        return self.paths.history_journal_dir / f"execution-{execution_id}.json"

    def _open_export_directory(self, backup_dir: Path) -> tuple[Path, int]:
        if not isinstance(backup_dir, Path):
            raise OSError("history export directory is invalid")
        absolute = backup_dir if backup_dir.is_absolute() else backup_dir.absolute()
        if (
            not absolute.name
            or absolute.name in {".", ".."}
            or any(part in {"", ".", ".."} for part in absolute.parts[1:])
        ):
            raise OSError("history export directory is invalid")
        try:
            absolute.relative_to(self.paths.workspace)
        except ValueError:
            pass
        else:
            raise OSError("history export directory must be outside the workspace")

        parent_descriptor = open_managed_directory(absolute.parent)
        directory_descriptor: int | None = None
        created = False
        try:
            try:
                directory_descriptor = os.open(
                    absolute.name,
                    _DIRECTORY_FLAGS,
                    dir_fd=parent_descriptor,
                )
            except FileNotFoundError:
                os.mkdir(
                    absolute.name,
                    0o700,
                    dir_fd=parent_descriptor,
                )
                created = True
                directory_descriptor = os.open(
                    absolute.name,
                    _DIRECTORY_FLAGS,
                    dir_fd=parent_descriptor,
                )
                os.fsync(parent_descriptor)
            stat_result = os.fstat(directory_descriptor)
            if not S_ISDIR(stat_result.st_mode):
                raise OSError("history export target must be a directory")
            if os.listdir(directory_descriptor):
                raise OSError("history export directory must be empty")
            os.fchmod(directory_descriptor, 0o700)
            return absolute, directory_descriptor
        except Exception:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            if created:
                try:
                    os.rmdir(absolute.name, dir_fd=parent_descriptor)
                except OSError:
                    pass
            raise
        finally:
            os.close(parent_descriptor)

    def _move_unit_to_quarantine(
        self,
        execution_id: str,
        unit: HistoryUnit,
    ) -> QuarantinedHistoryUnit:
        _validate_hex_identifier(
            execution_id,
            32,
            "history cleanup execution id",
        )
        original_root, original_relative = self._unit_root_and_path(unit)
        source_snapshot = read_managed_relative_file(
            original_root,
            original_relative,
        )
        _assert_snapshot_matches_unit(source_snapshot, unit)

        quarantine_relative = Path(unit.relative_path)
        quarantine_root = self.paths.history_quarantine_dir / execution_id
        destination_parent = quarantine_root / quarantine_relative.parent
        source_parent_descriptor = open_managed_directory(
            original_root / original_relative.parent
        )
        destination_parent_descriptor = _open_durable_directory(
            destination_parent
        )
        renamed = False
        try:
            source_stat = os.stat(
                original_relative.name,
                dir_fd=source_parent_descriptor,
                follow_symlinks=False,
            )
            _assert_stat_matches_unit(source_stat, unit)
            try:
                os.stat(
                    quarantine_relative.name,
                    dir_fd=destination_parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise OSError("history quarantine target already exists")
            if source_stat.st_dev != os.fstat(destination_parent_descriptor).st_dev:
                raise OSError("history quarantine must use the same filesystem")
            os.rename(
                original_relative.name,
                quarantine_relative.name,
                src_dir_fd=source_parent_descriptor,
                dst_dir_fd=destination_parent_descriptor,
            )
            renamed = True
            os.fsync(source_parent_descriptor)
            os.fsync(destination_parent_descriptor)
            moved_snapshot = read_managed_relative_file(
                quarantine_root,
                quarantine_relative,
            )
            _assert_snapshot_matches_unit(moved_snapshot, unit)
        except Exception:
            if renamed:
                try:
                    os.rename(
                        quarantine_relative.name,
                        original_relative.name,
                        src_dir_fd=destination_parent_descriptor,
                        dst_dir_fd=source_parent_descriptor,
                    )
                    os.fsync(destination_parent_descriptor)
                    os.fsync(source_parent_descriptor)
                except OSError:
                    pass
            raise
        finally:
            os.close(destination_parent_descriptor)
            os.close(source_parent_descriptor)
        return QuarantinedHistoryUnit(unit, quarantine_relative.as_posix())

    def _restore_quarantined_unit(
        self,
        execution_id: str,
        moved_unit: QuarantinedHistoryUnit,
    ) -> None:
        unit = moved_unit.unit
        original_root, original_relative = self._unit_root_and_path(unit)
        quarantine_root = self.paths.history_quarantine_dir / execution_id
        quarantine_relative = Path(moved_unit.quarantine_relative_path)
        try:
            quarantine_snapshot = read_managed_relative_file(
                quarantine_root,
                quarantine_relative,
            )
        except FileNotFoundError:
            original_snapshot = read_managed_relative_file(
                original_root,
                original_relative,
            )
            _assert_snapshot_matches_unit(original_snapshot, unit)
            return
        _assert_snapshot_matches_unit(quarantine_snapshot, unit)

        quarantine_parent_descriptor = open_managed_directory(
            quarantine_root / quarantine_relative.parent
        )
        original_parent_descriptor = open_managed_directory(
            original_root / original_relative.parent
        )
        renamed = False
        try:
            quarantine_stat = os.stat(
                quarantine_relative.name,
                dir_fd=quarantine_parent_descriptor,
                follow_symlinks=False,
            )
            _assert_stat_matches_unit(quarantine_stat, unit)
            try:
                os.stat(
                    original_relative.name,
                    dir_fd=original_parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise OSError("managed history restore target already exists")
            if quarantine_stat.st_dev != os.fstat(original_parent_descriptor).st_dev:
                raise OSError("managed history restore crossed filesystems")
            os.rename(
                quarantine_relative.name,
                original_relative.name,
                src_dir_fd=quarantine_parent_descriptor,
                dst_dir_fd=original_parent_descriptor,
            )
            renamed = True
            os.fsync(quarantine_parent_descriptor)
            os.fsync(original_parent_descriptor)
            restored_snapshot = read_managed_relative_file(
                original_root,
                original_relative,
            )
            _assert_snapshot_matches_unit(restored_snapshot, unit)
        except Exception:
            if renamed:
                try:
                    os.rename(
                        original_relative.name,
                        quarantine_relative.name,
                        src_dir_fd=original_parent_descriptor,
                        dst_dir_fd=quarantine_parent_descriptor,
                    )
                    os.fsync(original_parent_descriptor)
                    os.fsync(quarantine_parent_descriptor)
                except OSError:
                    pass
            raise
        finally:
            os.close(original_parent_descriptor)
            os.close(quarantine_parent_descriptor)

    def _purge_quarantined_unit(
        self,
        execution_id: str,
        moved_unit: QuarantinedHistoryUnit,
    ) -> None:
        unit = moved_unit.unit
        original_root, original_relative = self._unit_root_and_path(unit)
        quarantine_root = self.paths.history_quarantine_dir / execution_id
        quarantine_relative = Path(moved_unit.quarantine_relative_path)
        try:
            snapshot = read_managed_relative_file(
                quarantine_root,
                quarantine_relative,
            )
        except FileNotFoundError:
            try:
                read_managed_relative_file(original_root, original_relative)
            except FileNotFoundError:
                return
            raise OSError("managed history unit was restored before purge")
        _assert_snapshot_matches_unit(snapshot, unit)
        parent_descriptor = open_managed_directory(
            quarantine_root / quarantine_relative.parent
        )
        try:
            stat_result = os.stat(
                quarantine_relative.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            _assert_stat_matches_unit(stat_result, unit)
            os.unlink(quarantine_relative.name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)


def _validate_plan(plan: HistoryPlan) -> None:
    if (
        not isinstance(plan, HistoryPlan)
        or not isinstance(plan.id, str)
        or len(plan.id) != 32
        or any(character not in "0123456789abcdef" for character in plan.id)
        or not isinstance(plan.manifest_hash, str)
        or len(plan.manifest_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in plan.manifest_hash
        )
        or not isinstance(plan.policy, HistoryPolicy)
        or not isinstance(plan.units, tuple)
        or plan.confirmation_phrase != "DELETE MANAGED HISTORY"
        or not plan.units
    ):
        raise OSError("managed history plan is invalid")
    for unit in plan.units:
        _validate_history_unit(unit)
    if len(plan.unit_ids()) != len(set(plan.unit_ids())):
        raise OSError("managed history plan units are duplicated")
    manifest_payload = {
        "policy": asdict(plan.policy),
        "units": [asdict(unit) for unit in plan.units],
    }
    canonical_manifest = json.dumps(
        manifest_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if sha256(canonical_manifest).hexdigest() != plan.manifest_hash:
        raise OSError("managed history plan hash is invalid")


def _validate_history_unit(unit: HistoryUnit) -> None:
    if (
        not isinstance(unit, HistoryUnit)
        or unit.kind not in _KIND_ROOTS
        or not isinstance(unit.row_id, str)
        or not unit.row_id
        or not isinstance(unit.relative_path, str)
        or not unit.relative_path
        or not isinstance(unit.content_hash, str)
        or len(unit.content_hash) != 64
        or any(character not in "0123456789abcdef" for character in unit.content_hash)
        or type(unit.size) is not int
        or unit.size < 0
        or type(unit.device) is not int
        or unit.device < 0
        or type(unit.inode) is not int
        or unit.inode < 0
        or not isinstance(unit.reason, str)
        or not unit.reason
    ):
        raise OSError("managed history unit is invalid")
    relative_path = Path(unit.relative_path)
    expected_root = _KIND_ROOTS[unit.kind]
    if (
        relative_path.is_absolute()
        or relative_path.as_posix() != unit.relative_path
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or len(relative_path.parts) <= len(expected_root)
        or relative_path.parts[: len(expected_root)] != expected_root
    ):
        raise OSError("managed history unit path is invalid")


def _validate_journal(journal: HistoryCleanupJournal) -> None:
    if not isinstance(journal, HistoryCleanupJournal):
        raise OSError("history cleanup journal is invalid")
    _validate_hex_identifier(
        journal.execution_id,
        32,
        "history cleanup execution id",
    )
    _validate_hex_identifier(journal.plan_id, 32, "managed history plan id")
    _validate_hex_identifier(
        journal.manifest_hash,
        64,
        "managed history manifest hash",
    )
    if (
        not isinstance(journal.state, CleanupExecutionState)
        or not isinstance(journal.units, tuple)
        or not journal.units
        or not isinstance(journal.moved_units, tuple)
    ):
        raise OSError("history cleanup journal is invalid")
    unit_by_id: dict[tuple[str, str], HistoryUnit] = {}
    for unit in journal.units:
        _validate_history_unit(unit)
        unit_id = (unit.kind, unit.row_id)
        if unit_id in unit_by_id:
            raise OSError("history cleanup journal units are duplicated")
        unit_by_id[unit_id] = unit
    seen_moved: set[tuple[str, str]] = set()
    for moved_unit in journal.moved_units:
        if not isinstance(moved_unit, QuarantinedHistoryUnit):
            raise OSError("history cleanup journal move is invalid")
        _validate_history_unit(moved_unit.unit)
        unit_id = (moved_unit.unit.kind, moved_unit.unit.row_id)
        if (
            unit_by_id.get(unit_id) != moved_unit.unit
            or unit_id in seen_moved
            or moved_unit.quarantine_relative_path != moved_unit.unit.relative_path
        ):
            raise OSError("history cleanup journal move is invalid")
        seen_moved.add(unit_id)
    observed_moved = tuple(
        (moved.unit.kind, moved.unit.row_id)
        for moved in journal.moved_units
    )
    no_moved_states = {
        CleanupExecutionState.EXPORTED,
        CleanupExecutionState.PURGED,
        CleanupExecutionState.RESTORED,
        CleanupExecutionState.VACUUM_PENDING,
    }
    if (
        journal.state is CleanupExecutionState.PLANNED
        or (journal.state in no_moved_states and journal.moved_units)
        or (
            journal.state is CleanupExecutionState.QUARANTINED
            and not journal.moved_units
        )
        or (
            journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED
            and len(journal.moved_units) != len(journal.units)
        )
    ):
        raise OSError("history cleanup journal state is inconsistent")
    if journal.state is CleanupExecutionState.COMMITTED_PENDING_PURGE:
        expected_moved = tuple(
            (unit.kind, unit.row_id)
            for unit in journal.units[len(journal.units) - len(journal.moved_units) :]
        )
    else:
        expected_moved = tuple(
            (unit.kind, unit.row_id)
            for unit in journal.units[: len(journal.moved_units)]
        )
    if observed_moved != expected_moved:
        raise OSError("history cleanup journal move order is invalid")


def _history_unit_from_payload(payload: object) -> HistoryUnit:
    if type(payload) is not dict or frozenset(payload) != _UNIT_FIELDS:
        raise ValueError("managed history unit shape is invalid")
    unit = HistoryUnit(**payload)
    _validate_history_unit(unit)
    return unit


def _quarantined_unit_from_payload(payload: object) -> QuarantinedHistoryUnit:
    if type(payload) is not dict or frozenset(payload) != _MOVED_UNIT_FIELDS:
        raise ValueError("history cleanup moved unit shape is invalid")
    unit = _history_unit_from_payload(payload["unit"])
    quarantine_relative_path = payload["quarantine_relative_path"]
    if not isinstance(quarantine_relative_path, str):
        raise ValueError("history cleanup moved path is invalid")
    return QuarantinedHistoryUnit(unit, quarantine_relative_path)


def _plan_payload(plan: HistoryPlan) -> dict[str, object]:
    return {
        "version": 1,
        "id": plan.id,
        "policy": asdict(plan.policy),
        "units": [asdict(unit) for unit in plan.units],
        "manifest_hash": plan.manifest_hash,
        "confirmation_phrase": plan.confirmation_phrase,
    }


def _plan_content(plan: HistoryPlan) -> bytes:
    return json.dumps(
        _plan_payload(plan),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _journal_payload(journal: HistoryCleanupJournal) -> dict[str, object]:
    return {
        "version": 1,
        "execution_id": journal.execution_id,
        "plan_id": journal.plan_id,
        "manifest_hash": journal.manifest_hash,
        "state": journal.state.value,
        "units": [asdict(unit) for unit in journal.units],
        "moved_units": [
            {
                "unit": asdict(moved_unit.unit),
                "quarantine_relative_path": moved_unit.quarantine_relative_path,
            }
            for moved_unit in journal.moved_units
        ],
    }


def _journal_hash(payload: dict[str, object]) -> str:
    hash_payload = {
        key: value
        for key, value in payload.items()
        if key != "journal_hash"
    }
    return sha256(
        json.dumps(
            hash_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateFieldError
        payload[key] = value
    return payload


def _validate_hex_identifier(value: object, length: int, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise OSError(f"{label} is invalid")


def _assert_snapshot_matches_unit(snapshot: object, unit: HistoryUnit) -> None:
    if (
        snapshot.size != unit.size
        or snapshot.device != unit.device
        or snapshot.inode != unit.inode
        or snapshot.content_hash != unit.content_hash
    ):
        raise OSError("managed history candidate identity changed")


def _assert_stat_matches_unit(stat_result: os.stat_result, unit: HistoryUnit) -> None:
    if (
        not S_ISREG(stat_result.st_mode)
        or stat_result.st_nlink != 1
        or stat_result.st_size != unit.size
        or stat_result.st_dev != unit.device
        or stat_result.st_ino != unit.inode
    ):
        raise OSError("managed history candidate identity changed")


def _open_durable_directory(path: Path) -> int:
    absolute = path if path.is_absolute() else path.absolute()
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            if component in {"", ".", ".."}:
                raise OSError("managed directory path is invalid")
            try:
                next_descriptor = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                next_descriptor = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=descriptor,
                )
            try:
                if not S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise OSError("managed path component must be a directory")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _add_tar_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o600
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, BytesIO(content))
