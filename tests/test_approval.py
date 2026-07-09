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
                "excluded_repositories": ["private-repo"],
                "private_sources_allowed": True,
            }
        ),
        encoding="utf-8",
    )

    approval = load_approval(paths)

    assert approval.approved_source_uris == ("file:///source.pdf",)
    assert approval.forbidden_paths == ("secrets/",)
    assert approval.excluded_repositories == ("private-repo",)
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
