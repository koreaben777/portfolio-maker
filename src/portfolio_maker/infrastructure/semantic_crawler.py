from __future__ import annotations

import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path

from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    SemanticNodeKind,
    stable_node_id,
    stable_source_id,
)
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.presentation import normalize_label


class StructuralCrawlRootError(ValueError):
    pass


@dataclass(frozen=True)
class StructuralEntry:
    node_id: str
    source_id: str
    parent_node_id: str | None
    kind: SemanticNodeKind
    display_name: str
    relative_hierarchy: str
    absolute_path: Path
    provider_item_key: str
    content_fingerprint: str | None
    device: int | None
    inode: int | None
    status: AnalysisStatus


@dataclass(frozen=True)
class StructuralCrawlError:
    relative_hierarchy: str
    status: AnalysisStatus


@dataclass(frozen=True)
class StructuralCrawl:
    entries: tuple[StructuralEntry, ...]
    errors: tuple[StructuralCrawlError, ...]


def crawl_local_structure(
    root: Path,
    approval: SourceApproval,
    prior_entries: tuple[StructuralEntry, ...] = (),
) -> StructuralCrawl:
    root_path = _resolve_root(root)
    root_stat = _lstat(root_path)
    root_key, root_device, root_inode = _provider_identity(root_stat, ".")
    source_id = stable_source_id("local", root_key)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    prior_node_ids = {
        (entry.source_id, entry.provider_item_key): entry.node_id
        for entry in prior_entries
    }
    prior_entries_by_hierarchy = {
        (entry.source_id, entry.relative_hierarchy): entry for entry in prior_entries
    }
    entries: dict[str, StructuralEntry] = {}
    used_inode_keys: set[str] = set()
    errors: list[StructuralCrawlError] = []

    def add_entry(
        path: Path,
        relative_hierarchy: str,
        parent_node_id: str | None,
        kind: SemanticNodeKind,
        status: AnalysisStatus,
        path_stat: os.stat_result,
    ) -> StructuralEntry:
        provider_item_key, device, inode = _provider_identity(path_stat, relative_hierarchy)
        if device is not None and inode is not None:
            inode_key = provider_item_key
            prior_entry = prior_entries_by_hierarchy.get((source_id, relative_hierarchy))
            if prior_entry is not None and (prior_entry.device, prior_entry.inode) == (
                device,
                inode,
            ):
                provider_item_key = prior_entry.provider_item_key
                if provider_item_key == inode_key:
                    used_inode_keys.add(inode_key)
            elif inode_key in used_inode_keys:
                provider_item_key = relative_hierarchy
            else:
                used_inode_keys.add(inode_key)
        node_id = prior_node_ids.get(
            (source_id, provider_item_key), stable_node_id(source_id, provider_item_key)
        )
        entry = StructuralEntry(
            node_id=node_id,
            source_id=source_id,
            parent_node_id=parent_node_id,
            kind=kind,
            display_name=normalize_label(path.name),
            relative_hierarchy=relative_hierarchy,
            absolute_path=path,
            provider_item_key=provider_item_key,
            content_fingerprint=None,
            device=device,
            inode=inode,
            status=status,
        )
        entries.setdefault(node_id, entry)
        return entries[node_id]

    source = add_entry(
        root_path,
        ".",
        None,
        SemanticNodeKind.SOURCE,
        AnalysisStatus.PENDING,
        root_stat,
    )

    def mark_unreadable(node_id: str, relative_hierarchy: str) -> None:
        entries[node_id] = replace(entries[node_id], status=AnalysisStatus.UNREADABLE)
        errors.append(StructuralCrawlError(relative_hierarchy, AnalysisStatus.UNREADABLE))

    def walk(directory: Path, parent_node_id: str, relative_hierarchy: str) -> None:
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda child: child.name)
        except (OSError, RuntimeError):
            mark_unreadable(parent_node_id, relative_hierarchy)
            return

        for child in children:
            child_path = Path(child.path)
            child_relative = _relative_hierarchy(root_path, child_path)
            try:
                child_stat = child.stat(follow_symlinks=False)
            except (OSError, RuntimeError):
                errors.append(
                    StructuralCrawlError(child_relative, AnalysisStatus.UNREADABLE)
                )
                continue

            if stat.S_ISLNK(child_stat.st_mode):
                add_entry(
                    child_path,
                    child_relative,
                    parent_node_id,
                    SemanticNodeKind.FILE,
                    AnalysisStatus.UNSUPPORTED,
                    child_stat,
                )
                errors.append(
                    StructuralCrawlError(child_relative, AnalysisStatus.UNSUPPORTED)
                )
                continue

            classification = policy.classify_path(child_path)
            if stat.S_ISDIR(child_stat.st_mode):
                directory_entry = add_entry(
                    child_path,
                    child_relative,
                    parent_node_id,
                    SemanticNodeKind.DIRECTORY,
                    (
                        AnalysisStatus.UNSUPPORTED
                        if classification != "candidate"
                        else AnalysisStatus.PENDING
                    ),
                    child_stat,
                )
                if classification == "candidate":
                    walk(child_path, directory_entry.node_id, child_relative)
                continue

            if stat.S_ISREG(child_stat.st_mode):
                add_entry(
                    child_path,
                    child_relative,
                    parent_node_id,
                    SemanticNodeKind.FILE,
                    (
                        AnalysisStatus.PENDING
                        if classification == "candidate"
                        else AnalysisStatus.UNSUPPORTED
                    ),
                    child_stat,
                )
                continue

            add_entry(
                child_path,
                child_relative,
                parent_node_id,
                SemanticNodeKind.FILE,
                AnalysisStatus.UNSUPPORTED,
                child_stat,
            )
            errors.append(StructuralCrawlError(child_relative, AnalysisStatus.UNSUPPORTED))

    root_classification = policy.classify_path(root_path)
    if root_classification == "candidate":
        walk(root_path, source.node_id, ".")
    else:
        entries[source.node_id] = replace(source, status=AnalysisStatus.UNSUPPORTED)

    return StructuralCrawl(
        entries=tuple(
            sorted(
                entries.values(),
                key=lambda entry: (entry.relative_hierarchy, entry.node_id),
            )
        ),
        errors=tuple(errors),
    )


def _resolve_root(root: Path) -> Path:
    root_stat = _lstat(root)
    if stat.S_ISLNK(root_stat.st_mode):
        raise StructuralCrawlRootError("Structural crawl root is a symbolic link")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise StructuralCrawlRootError("Structural crawl root cannot be resolved") from error
    if not resolved.is_dir():
        raise StructuralCrawlRootError("Structural crawl root is not a directory")
    return resolved


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as error:
        raise StructuralCrawlRootError("Structural crawl root cannot be read") from error


def _provider_identity(path_stat: os.stat_result, relative_hierarchy: str) -> tuple[str, int | None, int | None]:
    device = path_stat.st_dev if type(path_stat.st_dev) is int else None
    inode = path_stat.st_ino if type(path_stat.st_ino) is int else None
    if device is not None and inode is not None and inode > 0:
        return f"{device}:{inode}", device, inode
    return relative_hierarchy, None, None


def _relative_hierarchy(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
