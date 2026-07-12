import json
from pathlib import Path

import pytest

from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
    GitHubDiscoveryError,
    GitHubRepositoryCandidate,
    discover_github_candidates,
    parse_commit_list,
    parse_issue_list,
    parse_pr_list,
    parse_repo_list,
    parse_review_list,
    parse_workflow_run_list,
)


def load_fixture(name: str):
    path = Path("tests/fixtures/github") / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_parse_repo_list():
    repos = parse_repo_list(load_fixture("gh_repo_list.json"))

    assert repos == [
        GitHubRepositoryCandidate(
            name_with_owner="octo/demo",
            url="https://github.com/octo/demo",
            is_private=False,
        )
    ]


def test_parse_repo_list_requires_boolean_privacy_field():
    with pytest.raises(GitHubDiscoveryError, match="repository list payload is invalid"):
        parse_repo_list(
            [{"nameWithOwner": "octo/demo", "url": "https://github.com/octo/demo"}]
        )


def test_parse_repo_list_canonicalizes_and_deduplicates_case_equivalent_repositories():
    repos = parse_repo_list(
        [
            {"nameWithOwner": "Octo/Demo", "url": "https://github.com/Octo/Demo", "isPrivate": False},
            {"nameWithOwner": "octo/demo", "url": "https://github.com/octo/demo", "isPrivate": False},
        ]
    )

    assert repos == [
        GitHubRepositoryCandidate(
            name_with_owner="octo/demo",
            url="https://github.com/Octo/Demo",
            is_private=False,
        )
    ]


@pytest.mark.parametrize("repository", ("../repo", "owner/..", "_owner/repo", "-owner/repo"))
def test_parse_repo_list_rejects_noncanonical_repository_name(repository):
    with pytest.raises(GitHubDiscoveryError, match="repository list payload is invalid"):
        parse_repo_list(
            [
                {
                    "nameWithOwner": repository,
                    "url": "https://github.com/octo/demo",
                    "isPrivate": False,
                }
            ]
        )


def test_github_repository_candidate_keeps_only_discovery_fields():
    assert set(GitHubRepositoryCandidate.__dataclass_fields__) == {
        "name_with_owner",
        "url",
        "is_private",
    }


def test_parse_pr_and_issue_lists():
    prs = parse_pr_list("octo/demo", load_fixture("gh_pr_list.json"))
    issues = parse_issue_list("octo/demo", load_fixture("gh_issue_list.json"))

    assert prs[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Add RAG ingestion",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    assert issues[0].activity_type == "issue"


@pytest.mark.parametrize("parser", (parse_pr_list, parse_issue_list))
def test_activity_parsers_reject_empty_state(parser):
    with pytest.raises(GitHubDiscoveryError, match="payload is invalid"):
        parser(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/pull/1", "title": "Title", "state": "", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
        )


def test_parse_commit_review_and_workflow_run_lists():
    commits = parse_commit_list("octo/demo", load_fixture("gh_commit_list.json"))
    reviews = parse_review_list("octo/demo", load_fixture("gh_review_list.json"))
    runs = parse_workflow_run_list("octo/demo", load_fixture("gh_workflow_run_list.json"))

    assert commits[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="commit",
        url="https://github.com/octo/demo/commit/abc123",
        title="Implement ingestion pipeline",
        state="committed",
        author="octo",
        created_at="2026-01-04T00:00:00Z",
        merged_at=None,
    )
    assert reviews[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="review_comment",
        url="https://github.com/octo/demo/pull/1#discussion_r1",
        title="Review comment: Please tighten approval validation.",
        state="commented",
        author="octo",
        created_at="2026-01-06T00:00:00Z",
        merged_at=None,
    )
    assert runs[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="workflow_run",
        url="https://github.com/octo/demo/actions/runs/10",
        title="CI",
        state="success",
        author="octo",
        created_at="2026-01-05T00:00:00Z",
        merged_at=None,
    )


def test_review_and_workflow_parsers_reject_missing_stable_fields():
    with pytest.raises(GitHubDiscoveryError, match="review comment list payload is invalid"):
        parse_review_list("octo/demo", [{}])
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list("octo/demo", {})


def test_activity_parsers_reject_blank_required_timestamps():
    with pytest.raises(GitHubDiscoveryError, match="pull request list payload is invalid"):
        parse_pr_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/pull/1", "title": "Title", "state": "OPEN", "createdAt": "", "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="issue list payload is invalid"):
        parse_issue_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/issues/1", "title": "Title", "state": "OPEN", "createdAt": "", "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="review comment list payload is invalid"):
        parse_review_list(
            "octo/demo",
            [{"html_url": "https://github.com/octo/demo/pull/1#discussion_r1", "body": "Review", "user": {"login": "octo"}, "created_at": ""}],
        )
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list(
            "octo/demo",
            {"workflow_runs": [{"html_url": "https://github.com/octo/demo/actions/runs/1", "name": "CI", "conclusion": "success", "actor": {"login": "octo"}, "created_at": ""}]},
        )


@pytest.mark.parametrize(
    "conclusion,status",
    (("", None), (None, ""), ("", ""), ("   ", "\t")),
)
def test_workflow_parser_rejects_blank_conclusion_and_status(conclusion, status):
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list(
            "octo/demo",
            {
                "workflow_runs": [
                    {
                        "html_url": "https://github.com/octo/demo/actions/runs/1",
                        "name": "CI",
                        "conclusion": conclusion,
                        "status": status,
                        "actor": {"login": "octo"},
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )


def test_discover_github_candidates_collects_repo_activities(monkeypatch):
    calls = []

    def fake_run_gh_json(args):
        calls.append(args)
        if args[:2] == ["repo", "list"]:
            return load_fixture("gh_repo_list.json")
        if args[:2] == ["pr", "list"]:
            return load_fixture("gh_pr_list.json")
        if args[:2] == ["issue", "list"]:
            return load_fixture("gh_issue_list.json")
        if args == ["api", "repos/octo/demo/commits"]:
            return load_fixture("gh_commit_list.json")
        if args == ["api", "repos/octo/demo/pulls/comments"]:
            return load_fixture("gh_review_list.json")
        if args == ["api", "repos/octo/demo/actions/runs"]:
            return load_fixture("gh_workflow_run_list.json")
        raise AssertionError(args)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        fake_run_gh_json,
    )

    result = discover_github_candidates()
    repos, activities, statuses = result

    assert len(repos) == 1
    assert len(activities) == 5
    assert statuses == []
    assert [activity.activity_type for activity in activities] == [
        "pull_request",
        "commit",
        "issue",
        "review_comment",
        "workflow_run",
    ]
    assert {(outcome.repository, outcome.activity_type, outcome.succeeded) for outcome in result.endpoint_outcomes} == {
        ("octo/demo", "pull_request", True),
        ("octo/demo", "commit", True),
        ("octo/demo", "issue", True),
        ("octo/demo", "review_comment", True),
        ("octo/demo", "workflow_run", True),
    }


def test_discover_github_candidates_filters_repos_before_activity_calls(monkeypatch):
    calls = []

    def fake_run_gh_json(args):
        calls.append(args)
        if args[:2] == ["repo", "list"]:
            return [
                {
                    "nameWithOwner": "octo/public",
                    "url": "https://github.com/octo/public",
                    "isPrivate": False,
                },
                {
                    "nameWithOwner": "octo/private",
                    "url": "https://github.com/octo/private",
                    "isPrivate": True,
                },
                {
                    "nameWithOwner": "octo/excluded",
                    "url": "https://github.com/octo/excluded",
                    "isPrivate": False,
                },
            ]
        assert "octo/private" not in " ".join(args)
        assert "octo/excluded" not in " ".join(args).casefold()
        if args[:2] in (["pr", "list"], ["issue", "list"]):
            return []
        if args == ["api", "repos/octo/public/commits"]:
            return []
        if args == ["api", "repos/octo/public/pulls/comments"]:
            return []
        if args == ["api", "repos/octo/public/actions/runs"]:
            return {"workflow_runs": []}
        raise AssertionError(args)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        fake_run_gh_json,
    )

    repos, activities, statuses = discover_github_candidates(
        excluded_repositories=("OCTO/EXCLUDED",),
        private_sources_allowed=False,
    )

    assert [repo.name_with_owner for repo in repos] == ["octo/public"]
    assert activities == []
    assert statuses == []
    assert all("octo/private" not in " ".join(call) for call in calls)
    assert all("octo/excluded" not in " ".join(call).casefold() for call in calls)


def test_discover_github_candidates_keeps_partial_results_when_repo_activity_fails(monkeypatch):
    def fake_run_gh_json(args):
        if args[:2] == ["repo", "list"]:
            return [
                {
                    "nameWithOwner": "octo/ok",
                    "url": "https://github.com/octo/ok",
                    "isPrivate": False,
                },
                {
                    "nameWithOwner": "octo/flaky",
                    "url": "https://github.com/octo/flaky",
                    "isPrivate": False,
                },
            ]
        if args[:2] == ["pr", "list"] and args[3] == "octo/ok":
            return load_fixture("gh_pr_list.json")
        if "octo/flaky" in " ".join(args):
            raise GitHubDiscoveryError("rate limited")
        if args[:2] in (["pr", "list"], ["issue", "list"]):
            return []
        if args[0] == "api" and args[1].endswith("/commits"):
            return []
        if args[0] == "api" and args[1].endswith("/pulls/comments"):
            return []
        if args[0] == "api" and args[1].endswith("/actions/runs"):
            return {"workflow_runs": []}
        raise AssertionError(args)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        fake_run_gh_json,
    )

    result = discover_github_candidates()
    repos, activities, statuses = result

    assert [repo.name_with_owner for repo in repos] == ["octo/ok", "octo/flaky"]
    assert [activity.repo for activity in activities] == ["octo/ok"]
    assert statuses == [
        "GitHub pull requests discovery failed for octo/flaky: rate limited",
        "GitHub commits discovery failed for octo/flaky: rate limited",
        "GitHub issues discovery failed for octo/flaky: rate limited",
        "GitHub review comments discovery failed for octo/flaky: rate limited",
        "GitHub workflow runs discovery failed for octo/flaky: rate limited",
    ]
    assert {(outcome.repository, outcome.activity_type, outcome.succeeded) for outcome in result.endpoint_outcomes} == {
        ("octo/ok", "pull_request", True),
        ("octo/ok", "commit", True),
        ("octo/ok", "issue", True),
        ("octo/ok", "review_comment", True),
        ("octo/ok", "workflow_run", True),
        ("octo/flaky", "pull_request", False),
        ("octo/flaky", "commit", False),
        ("octo/flaky", "issue", False),
        ("octo/flaky", "review_comment", False),
        ("octo/flaky", "workflow_run", False),
    }


def test_discover_github_candidates_keeps_early_endpoint_success_on_late_failure(monkeypatch):
    def fake_run_gh_json(args):
        if args[:2] == ["repo", "list"]:
            return [
                {
                    "nameWithOwner": "octo/demo",
                    "url": "https://github.com/octo/demo",
                    "isPrivate": False,
                }
            ]
        if args[:2] == ["pr", "list"]:
            return load_fixture("gh_pr_list.json")
        if args[:2] == ["issue", "list"]:
            return []
        if args == ["api", "repos/octo/demo/commits"]:
            return []
        if args == ["api", "repos/octo/demo/pulls/comments"]:
            return []
        if args == ["api", "repos/octo/demo/actions/runs"]:
            raise GitHubDiscoveryError("synthetic endpoint failure")
        raise AssertionError(args)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        fake_run_gh_json,
    )

    _, activities, statuses = discover_github_candidates()

    assert [activity.activity_type for activity in activities] == ["pull_request"]
    assert statuses == [
        "GitHub workflow runs discovery failed for octo/demo: synthetic endpoint failure"
    ]


def test_discover_github_candidates_isolates_blank_workflow_state(monkeypatch):
    def fake_run_gh_json(args):
        if args[:2] == ["repo", "list"]:
            return [
                {
                    "nameWithOwner": "octo/demo",
                    "url": "https://github.com/octo/demo",
                    "isPrivate": False,
                }
            ]
        if args[:2] in (["pr", "list"], ["issue", "list"]):
            return []
        if args == ["api", "repos/octo/demo/commits"]:
            return []
        if args == ["api", "repos/octo/demo/pulls/comments"]:
            return []
        if args == ["api", "repos/octo/demo/actions/runs"]:
            return {
                "workflow_runs": [
                    {
                        "html_url": "https://github.com/octo/demo/actions/runs/1",
                        "name": "CI",
                        "conclusion": "",
                        "status": "",
                        "actor": {"login": "octo"},
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            }
        raise AssertionError(args)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        fake_run_gh_json,
    )

    _, activities, statuses = discover_github_candidates()

    assert activities == []
    assert statuses == [
        "GitHub workflow runs discovery failed for octo/demo: "
        "GitHub workflow run list payload is invalid"
    ]


def test_discover_github_candidates_rejects_malformed_repository_payload(monkeypatch):
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        lambda args: {"unexpected": "object"},
    )

    with pytest.raises(GitHubDiscoveryError, match="repository list payload is invalid"):
        discover_github_candidates()


def test_parse_commit_list_handles_empty_commit_message():
    activities = parse_commit_list(
        "octo/demo",
        [
            {
                "html_url": "https://github.com/octo/demo/commit/synthetic",
                "commit": {"message": "", "author": {}},
            }
        ],
    )

    assert activities[0].title == ""


@pytest.mark.parametrize("html_url", (None, ""))
def test_parse_commit_list_requires_nonempty_stable_url(html_url):
    item = {
        "commit": {"message": "Synthetic commit", "author": {}},
    }
    if html_url is not None:
        item["html_url"] = html_url

    with pytest.raises(GitHubDiscoveryError, match="commit list payload is invalid"):
        parse_commit_list("octo/demo", [item])


def test_discover_github_candidates_isolates_commit_without_stable_url(monkeypatch):
    def fake_run_gh_json(args):
        if args[:2] == ["repo", "list"]:
            return [
                {
                    "nameWithOwner": "octo/demo",
                    "url": "https://github.com/octo/demo",
                    "isPrivate": False,
                }
            ]
        if args == ["api", "repos/octo/demo/commits"]:
            return [{"commit": {"message": "Synthetic commit", "author": {}}}]
        if args[:2] in (["pr", "list"], ["issue", "list"]):
            return []
        if args == ["api", "repos/octo/demo/pulls/comments"]:
            return []
        if args == ["api", "repos/octo/demo/actions/runs"]:
            return {"workflow_runs": []}
        raise AssertionError(args)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.github_connector.run_gh_json",
        fake_run_gh_json,
    )

    repos, activities, statuses = discover_github_candidates()

    assert [repo.name_with_owner for repo in repos] == ["octo/demo"]
    assert activities == []
    assert statuses == [
        "GitHub commits discovery failed for octo/demo: "
        "GitHub commit list payload is invalid"
    ]
