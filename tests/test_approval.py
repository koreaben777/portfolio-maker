import json

import pytest

from portfolio_maker.application.approval import (
    ApprovalFormatError,
    ApprovalMissingError,
    load_approval,
    write_sample_approval,
)
from portfolio_maker.workspace import WorkspacePaths


def test_write_sample_approval_creates_empty_versioned_json(workspace):
    paths = WorkspacePaths.from_root(workspace)

    approval_path = write_sample_approval(paths)

    assert approval_path == paths.approval_path
    assert json.loads(approval_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "approved_source_uris": [],
        "forbidden_paths": [],
        "excluded_repositories": [],
        "private_sources_allowed": False,
    }


def test_write_sample_approval_exclusively_preserves_concurrent_file(workspace, monkeypatch):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    competing_payload = '{"approved_source_uris": ["file:///kept.txt"]}'
    original_open = type(paths.approval_path).open
    injected = False

    def create_competing_file_before_open(path, mode="r", *args, **kwargs):
        nonlocal injected
        if path == paths.approval_path and mode in {"w", "x"} and not injected:
            injected = True
            with original_open(path, "w", encoding="utf-8") as competing:
                competing.write(competing_payload)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(type(paths.approval_path), "open", create_competing_file_before_open)

    with pytest.raises(ApprovalFormatError, match="already exists"):
        write_sample_approval(paths)

    assert paths.approval_path.read_text(encoding="utf-8") == competing_payload


def test_load_approval_missing_file_fails_closed(workspace):
    paths = WorkspacePaths.from_root(workspace)

    with pytest.raises(ApprovalMissingError):
        load_approval(paths)


def test_load_approval_reads_valid_payload(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps(
            {
                "version": 1,
                "approved_source_uris": ["file:///source.pdf"],
                "forbidden_paths": ["secrets/"],
                "excluded_repositories": ["private-org/private-repo"],
                "private_sources_allowed": True,
            }
        ),
        encoding="utf-8",
    )

    approval = load_approval(paths)

    assert approval.approved_source_uris == ("file:///source.pdf",)
    assert approval.forbidden_paths == ((workspace / "secrets").resolve(),)
    assert approval.excluded_repositories == ("private-org/private-repo",)
    assert approval.private_sources_allowed is True


def test_load_approval_rejects_source_uri_string(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"approved_source_uris": "file:///source.pdf"}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError):
        load_approval(paths)


def test_load_approval_rejects_private_sources_allowed_string(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"private_sources_allowed": "false"}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError):
        load_approval(paths)


def test_load_approval_rejects_unsupported_version(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"version": 2}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError, match="version must be 1"):
        load_approval(paths)


def test_load_approval_rejects_unknown_tilde_user_as_format_error(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"forbidden_paths": ["~portfolio_maker_missing_user/private"]}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError, match="invalid forbidden path"):
        load_approval(paths)


def test_load_approval_rejects_short_repository_exclusion(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"excluded_repositories": ["demo"]}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError, match="owner/repo"):
        load_approval(paths)


@pytest.mark.parametrize(
    "repository",
    ("../repo", "owner/..", "./repo", "owner/.", "_owner/repo", "-owner/repo"),
)
def test_load_approval_rejects_noncanonical_repository_exclusion(workspace, repository):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"excluded_repositories": [repository]}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError, match="owner/repo"):
        load_approval(paths)
