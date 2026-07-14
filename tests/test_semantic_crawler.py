from __future__ import annotations

import os
from pathlib import Path

import pytest

from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_crawler import crawl_local_structure


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
