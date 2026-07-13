from __future__ import annotations

import json

import pytest

from portfolio_maker.application.approval import ApprovalFormatError
from portfolio_maker.application.artifact_approval import (
    load_artifact_policy,
    write_sample_artifact_policy,
)
from portfolio_maker.workspace import WorkspacePaths


def _write_policy(workspace, payload):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.artifact_approval_path.write_text(json.dumps(payload), encoding="utf-8")
    return paths


def test_new_default_html_policy_is_restricted_and_allows_approved_origins(tmp_path):
    policy = load_artifact_policy(WorkspacePaths.from_root(tmp_path))
    html = policy.for_kind("portfolio_html")

    assert html.delivery_scope == "restricted"
    assert html.include_local is True
    assert html.include_public_github is True
    assert html.include_private_github is True


def test_sample_artifact_policy_persists_all_restricted_artifacts(tmp_path):
    paths = WorkspacePaths.from_root(tmp_path)

    policy_path = write_sample_artifact_policy(paths)

    assert policy_path == paths.artifact_approval_path
    policy = load_artifact_policy(paths)
    assert policy.explicit is True
    assert all(
        policy.for_kind(kind).delivery_scope == "restricted"
        for kind in policy.artifact_kinds
    )


def test_open_public_policy_rejects_local_or_private_include_flags(tmp_path):
    paths = _write_policy(
        tmp_path,
        {
            "version": 1,
            "artifacts": {
                "portfolio_html": {
                    "delivery_scope": "open_public",
                    "include_local": True,
                    "include_private_github": False,
                }
            },
        },
    )

    with pytest.raises(ApprovalFormatError, match="open_public"):
        load_artifact_policy(paths)


@pytest.mark.parametrize("scope", ("share", "", None))
def test_artifact_policy_rejects_malformed_delivery_scope(tmp_path, scope):
    paths = _write_policy(
        tmp_path,
        {
            "version": 1,
            "artifacts": {
                "portfolio_html": {"delivery_scope": scope},
            },
        },
    )

    with pytest.raises(ApprovalFormatError, match="delivery_scope"):
        load_artifact_policy(paths)


def test_missing_artifact_policy_marks_legacy_compatibility(tmp_path):
    policy = load_artifact_policy(WorkspacePaths.from_root(tmp_path))

    assert policy.explicit is False
    assert policy.legacy_compatibility is True
