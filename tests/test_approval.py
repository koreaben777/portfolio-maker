import json

import pytest

import portfolio_maker.infrastructure.managed_files as managed_files
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
        "allowed_repositories": [],
        "excluded_file_patterns": [],
    }


def test_write_sample_approval_exclusively_preserves_concurrent_file(workspace, monkeypatch):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    competing_payload = '{"approved_source_uris": ["file:///kept.txt"]}'
    original_link = managed_files.os.link
    injected = False

    def create_competing_file_before_link(source, destination, *args, **kwargs):
        nonlocal injected
        if destination == paths.approval_path.name and not injected:
            injected = True
            paths.approval_path.write_text(competing_payload, encoding="utf-8")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(managed_files.os, "link", create_competing_file_before_link)

    with pytest.raises(ApprovalFormatError, match="already exists"):
        write_sample_approval(paths)

    assert paths.approval_path.read_text(encoding="utf-8") == competing_payload


def test_force_write_sample_approval_rejects_symlink_and_preserves_external_file(
    workspace,
    tmp_path,
):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    external = tmp_path / "external-approval.json"
    external.write_text('{"preserve": true}', encoding="utf-8")
    paths.approval_path.symlink_to(external)

    with pytest.raises(OSError, match="regular file"):
        write_sample_approval(paths, force=True)

    assert external.read_text(encoding="utf-8") == '{"preserve": true}'


def test_load_approval_missing_file_fails_closed(workspace):
    paths = WorkspacePaths.from_root(workspace)

    with pytest.raises(ApprovalMissingError):
        load_approval(paths)


def test_load_approval_maps_invalid_utf8_to_format_error_without_modifying_file(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    damaged = b"\xff\xfe"
    paths.approval_path.write_bytes(damaged)

    with pytest.raises(ApprovalFormatError, match="invalid UTF-8"):
        load_approval(paths)

    assert paths.approval_path.read_bytes() == damaged


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


def test_load_approval_reads_repository_allowlist_and_filename_patterns(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps(
            {
                "allowed_repositories": ["octo/demo"],
                "excluded_file_patterns": ["*.secret", "PRIVATE*"],
            }
        ),
        encoding="utf-8",
    )

    approval = load_approval(paths)

    assert approval.allowed_repositories == ("octo/demo",)
    assert approval.excluded_file_patterns == ("*.secret", "PRIVATE*")


@pytest.mark.parametrize("pattern", ("", "nested/file.md", r"nested\\file.md", "bad\npattern"))
def test_load_approval_rejects_unsafe_filename_pattern(workspace, pattern):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps({"excluded_file_patterns": [pattern]}),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError, match="excluded_file_patterns"):
        load_approval(paths)


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
