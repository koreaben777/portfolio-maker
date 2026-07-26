from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
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
    HistoryDatabaseFamilyIdentity,
    HistoryDatabaseSidecarIdentity,
    HistoryExportArchiveBinding,
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
        "export_receipt_hash",
        "database_family",
        "pending_move",
        "journal_hash",
    }
)
_MOVED_UNIT_FIELDS = frozenset({"unit", "quarantine_relative_path"})
_DATABASE_FAMILY_FIELDS = frozenset(
    {
        "directory_device",
        "directory_inode",
        "main_device",
        "main_inode",
        "sidecars",
    }
)
_DATABASE_SIDECAR_FIELDS = frozenset({"suffix", "device", "inode"})
_DATABASE_SIDECARS = ("-journal", "-wal", "-shm")


class _DuplicateFieldError(ValueError):
    pass


@dataclass(frozen=True)
class _ExportDirectoryIdentity:
    absolute_path: Path
    parent_device: int
    parent_inode: int
    target_device: int
    target_inode: int


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
        expected_directory: _ExportDirectoryIdentity,
    ) -> tuple[HistoryExportArchiveBinding, str]:
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

        (
            export_directory,
            parent_descriptor,
            directory_descriptor,
        ) = self._open_export_directory(
            backup_dir,
            expected=expected_directory,
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
            self._assert_export_directory_binding(
                export_directory,
                parent_descriptor,
                directory_descriptor,
            )
            named_archive = os.stat(
                archive_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not S_ISREG(named_archive.st_mode)
                or named_archive.st_nlink != 1
                or (named_archive.st_dev, named_archive.st_ino)
                != (stat_result.st_dev, stat_result.st_ino)
                or named_archive.st_size != stat_result.st_size
            ):
                raise OSError("history export archive identity changed")
            parent_stat = os.fstat(parent_descriptor)
            directory_stat = os.fstat(directory_descriptor)
            return (
                HistoryExportArchiveBinding(
                    archive_path=(
                        export_directory.absolute_path / archive_name
                    ).as_posix(),
                    parent_device=parent_stat.st_dev,
                    parent_inode=parent_stat.st_ino,
                    export_directory_device=directory_stat.st_dev,
                    export_directory_inode=directory_stat.st_ino,
                    archive_device=stat_result.st_dev,
                    archive_inode=stat_result.st_ino,
                    size=stat_result.st_size,
                    sha256=digest.hexdigest(),
                ),
                digest.hexdigest(),
            )
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
            os.close(parent_descriptor)

    def prepare_export_directory(
        self,
        backup_dir: Path,
    ) -> _ExportDirectoryIdentity:
        identity, parent_descriptor, directory_descriptor = (
            self._open_export_directory(backup_dir)
        )
        os.close(directory_descriptor)
        os.close(parent_descriptor)
        return identity

    def revalidate_export_directory(
        self,
        identity: _ExportDirectoryIdentity,
    ) -> None:
        if not isinstance(identity, _ExportDirectoryIdentity):
            raise OSError("history export directory identity is invalid")
        parent_descriptor = open_managed_directory(
            identity.absolute_path.parent
        )
        directory_descriptor: int | None = None
        try:
            directory_descriptor = os.open(
                identity.absolute_path.name,
                _DIRECTORY_FLAGS,
                dir_fd=parent_descriptor,
            )
            self._assert_export_directory_binding(
                identity,
                parent_descriptor,
                directory_descriptor,
            )
        finally:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            os.close(parent_descriptor)

    @contextmanager
    def open_verified_export_archive(
        self,
        binding: HistoryExportArchiveBinding,
    ) -> Iterator[None]:
        archive_path = _archive_path_from_binding(binding)
        export_path = archive_path.parent
        parent_path = export_path.parent
        parent_descriptor = open_managed_directory(parent_path)
        directory_descriptor: int | None = None
        archive_descriptor: int | None = None
        try:
            if _descriptor_identity(parent_descriptor) != (
                binding.parent_device,
                binding.parent_inode,
            ):
                raise OSError("history export archive parent identity changed")
            directory_stat = os.stat(
                export_path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not S_ISDIR(directory_stat.st_mode)
                or (directory_stat.st_dev, directory_stat.st_ino)
                != (
                    binding.export_directory_device,
                    binding.export_directory_inode,
                )
            ):
                raise OSError("history export archive directory identity changed")
            directory_descriptor = os.open(
                export_path.name,
                _DIRECTORY_FLAGS,
                dir_fd=parent_descriptor,
            )
            if _descriptor_identity(directory_descriptor) != (
                binding.export_directory_device,
                binding.export_directory_inode,
            ):
                raise OSError("history export archive directory identity changed")
            self._assert_directory_outside_workspace(directory_descriptor)
            named_archive = os.stat(
                archive_path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not S_ISREG(named_archive.st_mode)
                or named_archive.st_nlink != 1
                or (named_archive.st_dev, named_archive.st_ino)
                != (binding.archive_device, binding.archive_inode)
                or named_archive.st_size != binding.size
            ):
                raise OSError("history export archive identity changed")
            archive_descriptor = os.open(
                archive_path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_descriptor,
            )
            archive_stat = os.fstat(archive_descriptor)
            if (
                not S_ISREG(archive_stat.st_mode)
                or archive_stat.st_nlink != 1
                or (archive_stat.st_dev, archive_stat.st_ino)
                != (binding.archive_device, binding.archive_inode)
                or archive_stat.st_size != binding.size
            ):
                raise OSError("history export archive identity changed")
            digest = _descriptor_sha256(archive_descriptor)
            refreshed_stat = os.fstat(archive_descriptor)
            if (
                digest != binding.sha256
                or not S_ISREG(refreshed_stat.st_mode)
                or refreshed_stat.st_nlink != 1
                or (refreshed_stat.st_dev, refreshed_stat.st_ino)
                != (binding.archive_device, binding.archive_inode)
                or refreshed_stat.st_size != binding.size
            ):
                raise OSError("history export archive content changed")
            yield
        finally:
            if archive_descriptor is not None:
                os.close(archive_descriptor)
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            os.close(parent_descriptor)

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
                or type(payload["database_family"]) is not dict
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
            pending_payload = payload["pending_move"]
            pending_move = (
                None
                if pending_payload is None
                else _quarantined_unit_from_payload(pending_payload)
            )
            database_family = _database_family_from_payload(
                payload["database_family"]
            )
            journal = HistoryCleanupJournal(
                execution_id=payload["execution_id"],
                plan_id=payload["plan_id"],
                manifest_hash=payload["manifest_hash"],
                state=CleanupExecutionState(payload["state"]),
                units=units,
                moved_units=moved_units,
                export_receipt_hash=payload["export_receipt_hash"],
                database_family=database_family,
                pending_move=pending_move,
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
            pending_move = QuarantinedHistoryUnit(
                unit,
                unit.relative_path,
            )
            intent = replace(
                current,
                state=CleanupExecutionState.QUARANTINED,
                pending_move=pending_move,
            )
            self.write_journal(intent)
            moved_unit = self._move_unit_to_quarantine(
                journal.execution_id,
                unit,
            )
            next_journal = replace(
                intent,
                state=CleanupExecutionState.QUARANTINED,
                moved_units=(*current.moved_units, moved_unit),
                pending_move=None,
            )
            self.write_journal(next_journal)
            current = next_journal
        return current

    def restore_quarantine(
        self,
        journal: HistoryCleanupJournal,
    ) -> HistoryCleanupJournal:
        current = self._resolve_pending_move(journal)
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

    def _resolve_pending_move(
        self,
        journal: HistoryCleanupJournal,
    ) -> HistoryCleanupJournal:
        pending_move = journal.pending_move
        if pending_move is None:
            return journal
        unit = pending_move.unit
        original_root, original_relative = self._unit_root_and_path(unit)
        quarantine_root = (
            self.paths.history_quarantine_dir / journal.execution_id
        )
        quarantine_relative = Path(pending_move.quarantine_relative_path)
        original_exists = _exact_unit_exists(
            original_root,
            original_relative,
            unit,
        )
        quarantine_exists = _exact_unit_exists(
            quarantine_root,
            quarantine_relative,
            unit,
        )
        if original_exists == quarantine_exists:
            raise OSError(
                "history cleanup pending move location is indeterminate"
            )
        if quarantine_exists:
            self._restore_quarantined_unit(
                journal.execution_id,
                pending_move,
            )
        resolved = replace(
            journal,
            state=(
                CleanupExecutionState.QUARANTINED
                if journal.moved_units
                else CleanupExecutionState.RESTORED
            ),
            pending_move=None,
        )
        self.write_journal(resolved)
        return resolved

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

    def _open_export_directory(
        self,
        backup_dir: Path,
        *,
        expected: _ExportDirectoryIdentity | None = None,
    ) -> tuple[_ExportDirectoryIdentity, int, int]:
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
            self._assert_directory_outside_workspace(parent_descriptor)
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
            self._assert_directory_outside_workspace(directory_descriptor)
            if os.listdir(directory_descriptor):
                raise OSError("history export directory must be empty")
            os.fchmod(directory_descriptor, 0o700)
            parent_stat = os.fstat(parent_descriptor)
            identity = _ExportDirectoryIdentity(
                absolute_path=absolute,
                parent_device=parent_stat.st_dev,
                parent_inode=parent_stat.st_ino,
                target_device=stat_result.st_dev,
                target_inode=stat_result.st_ino,
            )
            if expected is not None and identity != expected:
                raise OSError("history export directory identity changed")
            self._assert_export_directory_binding(
                identity,
                parent_descriptor,
                directory_descriptor,
            )
            return identity, parent_descriptor, directory_descriptor
        except Exception:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            if created:
                try:
                    os.rmdir(absolute.name, dir_fd=parent_descriptor)
                except OSError:
                    pass
            os.close(parent_descriptor)
            raise

    def _assert_directory_outside_workspace(
        self,
        directory_descriptor: int,
    ) -> None:
        workspace_descriptor = open_managed_directory(self.paths.workspace)
        try:
            workspace_identity = _descriptor_identity(workspace_descriptor)
        finally:
            os.close(workspace_descriptor)
        if _descriptor_has_ancestor(
            directory_descriptor,
            workspace_identity,
        ):
            raise OSError("history export directory must be outside the workspace")

    def _assert_export_directory_binding(
        self,
        identity: _ExportDirectoryIdentity,
        parent_descriptor: int,
        directory_descriptor: int,
    ) -> None:
        if (
            _descriptor_identity(parent_descriptor)
            != (identity.parent_device, identity.parent_inode)
            or _descriptor_identity(directory_descriptor)
            != (identity.target_device, identity.target_inode)
        ):
            raise OSError("history export directory identity changed")
        named_target = os.stat(
            identity.absolute_path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not S_ISDIR(named_target.st_mode)
            or (named_target.st_dev, named_target.st_ino)
            != (identity.target_device, identity.target_inode)
        ):
            raise OSError("history export directory identity changed")
        self._assert_directory_outside_workspace(directory_descriptor)

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
            source_stat = _optional_stat(
                source_parent_descriptor,
                original_relative.name,
            )
            destination_stat = _optional_stat(
                destination_parent_descriptor,
                quarantine_relative.name,
            )
            if source_stat is None:
                if destination_stat is None:
                    raise OSError(
                        "managed history move source and target are missing"
                    )
                _assert_stat_matches_unit(destination_stat, unit)
                moved_snapshot = read_managed_relative_file(
                    quarantine_root,
                    quarantine_relative,
                )
                _assert_snapshot_matches_unit(moved_snapshot, unit)
                return QuarantinedHistoryUnit(
                    unit,
                    quarantine_relative.as_posix(),
                )
            if destination_stat is not None:
                raise OSError("history quarantine target already exists")
            source_snapshot = read_managed_relative_file(
                original_root,
                original_relative,
            )
            _assert_snapshot_matches_unit(source_snapshot, unit)
            _assert_stat_matches_unit(source_stat, unit)
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
    _validate_hex_identifier(
        journal.export_receipt_hash,
        64,
        "history export receipt hash",
    )
    _validate_database_family_identity(journal.database_family)
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
    pending_move = journal.pending_move
    if pending_move is not None:
        if not isinstance(pending_move, QuarantinedHistoryUnit):
            raise OSError("history cleanup pending move is invalid")
        _validate_history_unit(pending_move.unit)
        next_index = len(journal.moved_units)
        if (
            journal.state is not CleanupExecutionState.QUARANTINED
            or next_index >= len(journal.units)
            or pending_move.unit != journal.units[next_index]
            or pending_move.quarantine_relative_path
            != pending_move.unit.relative_path
        ):
            raise OSError("history cleanup pending move is invalid")
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
        or (journal.state in no_moved_states and pending_move is not None)
        or (journal.state in no_moved_states and journal.moved_units)
        or (
            journal.state is CleanupExecutionState.QUARANTINED
            and not journal.moved_units
            and pending_move is None
        )
        or (
            journal.state is CleanupExecutionState.RECONCILIATION_REQUIRED
            and (
                len(journal.moved_units) != len(journal.units)
                or pending_move is not None
            )
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


def _database_family_from_payload(
    payload: object,
) -> HistoryDatabaseFamilyIdentity:
    if (
        type(payload) is not dict
        or frozenset(payload) != _DATABASE_FAMILY_FIELDS
        or type(payload["sidecars"]) is not list
    ):
        raise ValueError("history database family shape is invalid")
    sidecars: list[HistoryDatabaseSidecarIdentity] = []
    for sidecar_payload in payload["sidecars"]:
        if (
            type(sidecar_payload) is not dict
            or frozenset(sidecar_payload) != _DATABASE_SIDECAR_FIELDS
        ):
            raise ValueError("history database sidecar shape is invalid")
        sidecars.append(HistoryDatabaseSidecarIdentity(**sidecar_payload))
    identity = HistoryDatabaseFamilyIdentity(
        directory_device=payload["directory_device"],
        directory_inode=payload["directory_inode"],
        main_device=payload["main_device"],
        main_inode=payload["main_inode"],
        sidecars=tuple(sidecars),
    )
    _validate_database_family_identity(identity)
    return identity


def _validate_database_family_identity(
    identity: HistoryDatabaseFamilyIdentity,
) -> None:
    if (
        not isinstance(identity, HistoryDatabaseFamilyIdentity)
        or type(identity.directory_device) is not int
        or identity.directory_device < 0
        or type(identity.directory_inode) is not int
        or identity.directory_inode < 0
        or type(identity.main_device) is not int
        or identity.main_device < 0
        or type(identity.main_inode) is not int
        or identity.main_inode < 0
        or not isinstance(identity.sidecars, tuple)
        or len(identity.sidecars) != len(_DATABASE_SIDECARS)
    ):
        raise OSError("history database family identity is invalid")
    for expected_suffix, sidecar in zip(
        _DATABASE_SIDECARS,
        identity.sidecars,
        strict=True,
    ):
        if (
            not isinstance(sidecar, HistoryDatabaseSidecarIdentity)
            or sidecar.suffix != expected_suffix
            or type(sidecar.device) is not int
            or sidecar.device < 0
            or type(sidecar.inode) is not int
            or sidecar.inode < 0
        ):
            raise OSError("history database family identity is invalid")


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
        "export_receipt_hash": journal.export_receipt_hash,
        "database_family": {
            "directory_device": journal.database_family.directory_device,
            "directory_inode": journal.database_family.directory_inode,
            "main_device": journal.database_family.main_device,
            "main_inode": journal.database_family.main_inode,
            "sidecars": [
                asdict(sidecar)
                for sidecar in journal.database_family.sidecars
            ],
        },
        "pending_move": (
            None
            if journal.pending_move is None
            else {
                "unit": asdict(journal.pending_move.unit),
                "quarantine_relative_path": (
                    journal.pending_move.quarantine_relative_path
                ),
            }
        ),
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


def _optional_stat(
    directory_descriptor: int,
    name: str,
) -> os.stat_result | None:
    try:
        return os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _exact_unit_exists(
    root: Path,
    relative_path: Path,
    unit: HistoryUnit,
) -> bool:
    try:
        snapshot = read_managed_relative_file(root, relative_path)
    except FileNotFoundError:
        return False
    _assert_snapshot_matches_unit(snapshot, unit)
    return True


def _descriptor_identity(descriptor: int) -> tuple[int, int]:
    stat_result = os.fstat(descriptor)
    if not S_ISDIR(stat_result.st_mode):
        raise OSError("managed directory descriptor is invalid")
    return stat_result.st_dev, stat_result.st_ino


def _archive_path_from_binding(binding: HistoryExportArchiveBinding) -> Path:
    if (
        not isinstance(binding, HistoryExportArchiveBinding)
        or not isinstance(binding.archive_path, str)
        or "\x00" in binding.archive_path
    ):
        raise OSError("history export archive binding is invalid")
    archive_path = Path(binding.archive_path)
    if (
        not archive_path.is_absolute()
        or archive_path.parent == archive_path
        or any(part in {"", ".", ".."} for part in archive_path.parts)
        or archive_path.as_posix() != binding.archive_path
    ):
        raise OSError("history export archive binding is invalid")
    for value in (
        binding.parent_device,
        binding.parent_inode,
        binding.export_directory_device,
        binding.export_directory_inode,
        binding.archive_device,
        binding.archive_inode,
        binding.size,
    ):
        if type(value) is not int or value < 0:
            raise OSError("history export archive binding is invalid")
    _validate_hex_identifier(binding.sha256, 64, "history export archive hash")
    return archive_path


def _descriptor_sha256(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = sha256()
    while chunk := os.read(descriptor, 65_536):
        digest.update(chunk)
    return digest.hexdigest()


def _descriptor_has_ancestor(
    descriptor: int,
    expected_identity: tuple[int, int],
) -> bool:
    current_descriptor = os.dup(descriptor)
    try:
        while True:
            current_identity = _descriptor_identity(current_descriptor)
            if current_identity == expected_identity:
                return True
            parent_descriptor = os.open(
                "..",
                _DIRECTORY_FLAGS,
                dir_fd=current_descriptor,
            )
            parent_identity = _descriptor_identity(parent_descriptor)
            if parent_identity == current_identity:
                os.close(parent_descriptor)
                return False
            os.close(current_descriptor)
            current_descriptor = parent_descriptor
    finally:
        os.close(current_descriptor)


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
