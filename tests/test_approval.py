import json

import pytest

from portfolio_maker.application.approval import (
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
