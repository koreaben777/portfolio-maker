from __future__ import annotations

import json

import pytest

import portfolio_maker.infrastructure.github_connector as github_connector
from portfolio_maker.infrastructure.github_connector import (
    GitHubDiscoveryError,
    discover_github_candidates,
)


def test_private_discovery_requires_auth_and_exact_activity_approval(monkeypatch):
    auth_calls = []
    json_calls = []
    approved_url = "https://github.com/octo/private/pull/7"

    def fake_auth_status():
        auth_calls.append(True)

    def fake_run_gh_json(args):
        json_calls.append(args)
        if args[:2] == ["repo", "list"]:
            return [
                {
                    "nameWithOwner": "octo/private",
                    "url": "https://github.com/octo/private",
                    "isPrivate": True,
                },
                {
                    "nameWithOwner": "octo/excluded",
                    "url": "https://github.com/octo/excluded",
                    "isPrivate": True,
                },
            ]
        if args[:2] == ["pr", "list"]:
            return [
                {
                    "url": approved_url,
                    "title": "Approved private activity",
                    "state": "OPEN",
                    "author": {"login": "octo"},
                    "createdAt": "2026-01-01T00:00:00Z",
                },
                {
                    "url": "https://github.com/octo/private/pull/8",
                    "title": "Unapproved private activity",
                    "state": "OPEN",
                    "author": {"login": "octo"},
                    "createdAt": "2026-01-01T00:00:00Z",
                },
            ]
        return [] if args[:2] in (["issue", "list"],) else (
            {"workflow_runs": []} if args[-1:] == ["runs"] else []
        )

    monkeypatch.setattr(github_connector, "run_gh_auth_status", fake_auth_status, raising=False)
    monkeypatch.setattr(github_connector, "run_gh_json", fake_run_gh_json)

    result = discover_github_candidates(
        excluded_repositories=("octo/excluded",),
        allowed_repositories=("octo/private", "octo/excluded"),
        private_sources_allowed=True,
        approved_private_github_activity_urls=(approved_url,),
    )

    assert auth_calls == [True]
    assert [repo.name_with_owner for repo in result.repositories] == ["octo/private"]
    assert [activity.url for activity in result.activities] == [approved_url]
    assert result.activities[0].is_private is True
    assert all("octo/excluded" not in " ".join(call) for call in json_calls)


def test_private_discovery_auth_failure_is_controlled_without_credentials(monkeypatch):
    def fail_auth_status():
        raise GitHubDiscoveryError("GitHub private discovery unavailable; run gh auth status")

    monkeypatch.setattr(github_connector, "run_gh_auth_status", fail_auth_status, raising=False)

    with pytest.raises(GitHubDiscoveryError, match="private discovery unavailable") as error:
        discover_github_candidates(
            private_sources_allowed=True,
            allowed_repositories=("octo/private",),
        )

    assert "token" not in str(error.value).casefold()
    assert "Bearer" not in str(error.value)
