from __future__ import annotations

import os
from pathlib import Path

import pytest

from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_crawler import (
    StructuralCrawlRootError,
    crawl_local_structure,
)


def approval_for(root: Path, *, excluded: tuple[Path, ...] = ()) -> SourceApproval:
    return SourceApproval(
        approved_source_uris=(root.resolve().as_uri(),),
        legacy_forbidden_paths=(),
        excluded_directories=excluded,
        excluded_repositories=(),
        private_sources_allowed=False,
        allowed_repositories=(),
        excluded_file_patterns=(),
        approved_github_activity_urls=(),
        approved_private_github_activity_urls=(),
    )


def test_structural_crawl_has_no_global_file_count_cap(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(501):
        (root / f"file-{index:03}.md").write_text("evidence", encoding="utf-8")

    result = crawl_local_structure(root, approval_for(root))

    assert sum(entry.kind == SemanticNodeKind.FILE for entry in result.entries) == 501


def test_excluded_directory_is_pruned_without_child_names(tmp_path: Path) -> None:
    root = tmp_path / "root"
    excluded = root / "private"
    excluded.mkdir(parents=True)
    (excluded / "secret-plan.md").write_text("restricted", encoding="utf-8")
    (root / "README.md").write_text("visible", encoding="utf-8")

    result = crawl_local_structure(root, approval_for(root, excluded=(excluded,)))

    entries = {entry.relative_hierarchy: entry for entry in result.entries}
    assert entries["private"].status == AnalysisStatus.UNSUPPORTED
    assert "private/secret-plan.md" not in entries
    assert all("secret-plan" not in error.relative_hierarchy for error in result.errors)


def test_unreadable_directory_is_reported_as_partial_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    blocked = root / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "hidden.md").write_text("hidden", encoding="utf-8")
    original_scandir = os.scandir

    def denied_scandir(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]):
        if Path(path) == blocked:
            raise PermissionError("permission denied")
        return original_scandir(path)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_crawler.os.scandir", denied_scandir
    )

    result = crawl_local_structure(root, approval_for(root))

    assert "blocked/hidden.md" not in {entry.relative_hierarchy for entry in result.entries}
    assert [(error.relative_hierarchy, error.status) for error in result.errors] == [
        ("blocked", AnalysisStatus.UNREADABLE)
    ]


def test_broken_and_directory_symlinks_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = tmp_path / "target"
    target.mkdir()
    (target / "outside.md").write_text("outside", encoding="utf-8")
    root.mkdir()
    (root / "broken-link").symlink_to(root / "missing")
    (root / "directory-link").symlink_to(target, target_is_directory=True)

    result = crawl_local_structure(root, approval_for(root))

    assert "directory-link/outside.md" not in {
        entry.relative_hierarchy for entry in result.entries
    }
    assert [(error.relative_hierarchy, error.status) for error in result.errors] == [
        ("broken-link", AnalysisStatus.UNSUPPORTED),
        ("directory-link", AnalysisStatus.UNSUPPORTED),
    ]


def test_symlink_crawl_root_is_rejected_without_traversing_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "outside.md").write_text("outside", encoding="utf-8")
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)

    with pytest.raises(StructuralCrawlRootError, match="symbolic link"):
        crawl_local_structure(root_link, approval_for(root_link))


def test_structural_crawl_keeps_hard_linked_files_as_distinct_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = root / "original.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, root / "alias.md")

    result = crawl_local_structure(root, approval_for(root))
    linked_entries = [
        entry
        for entry in result.entries
        if entry.relative_hierarchy in {"original.md", "alias.md"}
    ]

    assert {entry.relative_hierarchy for entry in linked_entries} == {
        "original.md",
        "alias.md",
    }
    assert len({entry.node_id for entry in linked_entries}) == 2


def test_structural_crawl_retains_hard_link_alias_ids_after_alias_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = root / "original.md"
    alias = root / "alias.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, alias)
    first = crawl_local_structure(root, approval_for(root))
    prior_entries = {entry.relative_hierarchy: entry for entry in first.entries}

    alias.rename(root / "renamed-alias.md")
    renamed = crawl_local_structure(root, approval_for(root), prior_entries=first.entries)
    renamed_entries = {entry.relative_hierarchy: entry for entry in renamed.entries}

    assert renamed_entries["original.md"].node_id == prior_entries["original.md"].node_id
    assert renamed_entries["renamed-alias.md"].node_id == prior_entries["alias.md"].node_id


def test_structural_crawl_reserves_hard_link_keys_before_alias_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = root / "original.md"
    alias = root / "zzz-alias.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, alias)
    first = crawl_local_structure(root, approval_for(root))
    prior_entries = {entry.relative_hierarchy: entry for entry in first.entries}

    alias.rename(root / "aaa-alias.md")
    renamed = crawl_local_structure(root, approval_for(root), prior_entries=first.entries)
    renamed_entries = {entry.relative_hierarchy: entry for entry in renamed.entries}

    assert {"original.md", "aaa-alias.md"} <= renamed_entries.keys()
    assert renamed_entries["original.md"].node_id == prior_entries["original.md"].node_id
    assert (
        renamed_entries["aaa-alias.md"].node_id
        == prior_entries["zzz-alias.md"].node_id
    )
    assert (
        renamed_entries["original.md"].provider_item_key
        == prior_entries["original.md"].provider_item_key
    )
    assert (
        renamed_entries["aaa-alias.md"].provider_item_key
        == prior_entries["zzz-alias.md"].provider_item_key
    )


def test_structural_crawl_is_deterministic_and_retains_ids_after_rename(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "zeta.md").write_text("zeta", encoding="utf-8")
    (root / "alpha").mkdir()
    (root / "alpha" / "entry.md").write_text("entry", encoding="utf-8")

    first = crawl_local_structure(root, approval_for(root))
    second = crawl_local_structure(root, approval_for(root), prior_entries=first.entries)
    (root / "zeta.md").rename(root / "renamed.md")
    renamed = crawl_local_structure(root, approval_for(root), prior_entries=second.entries)

    assert [entry.relative_hierarchy for entry in first.entries] == [
        entry.relative_hierarchy for entry in second.entries
    ]
    assert [entry.relative_hierarchy for entry in first.entries] == sorted(
        entry.relative_hierarchy for entry in first.entries
    )
    first_file = next(entry for entry in first.entries if entry.relative_hierarchy == "zeta.md")
    renamed_file = next(
        entry for entry in renamed.entries if entry.relative_hierarchy == "renamed.md"
    )
    assert renamed_file.node_id == first_file.node_id
