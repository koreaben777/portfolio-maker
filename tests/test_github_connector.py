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
    public_github_activity_identity,
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


def test_parse_repo_list_treats_a_private_duplicate_as_private():
    repos = parse_repo_list(
        [
            {"nameWithOwner": "Octo/Demo", "url": "https://github.com/Octo/Demo", "isPrivate": False},
            {"nameWithOwner": "octo/demo", "url": "https://github.com/octo/demo", "isPrivate": True},
        ]
    )

    assert repos[0].is_private is True


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


@pytest.mark.parametrize(
    "url",
    (
        "not-a-url",
        "https://github.com/octo/other",
        "https://github.com/octo/demo/pull/1",
    ),
)
def test_parse_repo_list_rejects_url_not_bound_to_repository_name(url):
    with pytest.raises(GitHubDiscoveryError, match="repository list payload is invalid"):
        parse_repo_list(
            [{"nameWithOwner": "octo/demo", "url": url, "isPrivate": False}]
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


def test_activity_parsers_reject_invalid_normalized_states():
    with pytest.raises(GitHubDiscoveryError, match="pull request list payload is invalid"):
        parse_pr_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/pull/1", "title": "Title", "state": "Bearer synthetic.token", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="issue list payload is invalid"):
        parse_issue_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/issues/1", "title": "Title", "state": "\u0000", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list(
            "octo/demo",
            {"workflow_runs": [{"html_url": "https://github.com/octo/demo/actions/runs/1", "name": "CI", "conclusion": "Bearer synthetic.token", "actor": {"login": "octo"}, "created_at": "2026-01-01T00:00:00Z"}]},
        )


@pytest.mark.parametrize(
    ("conclusion", "status"),
    (("queued", "completed"), ("success", "queued"), ("unsupported", "completed")),
)
def test_workflow_parser_rejects_state_values_from_wrong_field(conclusion, status):
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


def test_workflow_parser_preserves_state_field_provenance():
    activities = parse_workflow_run_list(
        "octo/demo",
        {
            "workflow_runs": [
                {
                    "html_url": "https://github.com/octo/demo/actions/runs/1",
                    "name": "CI",
                    "conclusion": None,
                    "status": "queued",
                    "actor": {"login": "octo"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )

    assert activities[0].state == "queued"
    assert activities[0].state_field == "status"


def test_workflow_parser_accepts_only_compatible_nonblank_pairs():
    valid = parse_workflow_run_list(
        "octo/demo",
        {
            "workflow_runs": [
                {
                    "html_url": "https://github.com/octo/demo/actions/runs/1",
                    "name": "CI",
                    "conclusion": None,
                    "status": "queued",
                    "actor": {"login": "octo"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )
    assert valid[0].state_field == "status"

    for fields in (
        {"conclusion": "", "status": "queued"},
        {"conclusion": None, "status": "completed"},
        {"conclusion": "success", "status": None},
        {"conclusion": "success", "status": ""},
        {"conclusion": "success"},
        {"status": "queued"},
    ):
        with pytest.raises(
            GitHubDiscoveryError, match="workflow run list payload is invalid"
        ):
            parse_workflow_run_list(
                "octo/demo",
                {
                    "workflow_runs": [
                        {
                            "html_url": "https://github.com/octo/demo/actions/runs/1",
                            "name": "CI",
                            **fields,
                            "actor": {"login": "octo"},
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )


def test_workflow_parser_rejects_control_suffix_before_normalization():
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list(
            "octo/demo",
            {
                "workflow_runs": [
                    {
                        "html_url": "https://github.com/octo/demo/actions/runs/1",
                        "name": "CI",
                        "conclusion": None,
                        "status": "queued\u0000",
                        "actor": {"login": "octo"},
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    ("parser", "label", "url"),
    (
        (parse_pr_list, "pull request list", "https://github.com/octo/demo/pull/1"),
        (parse_issue_list, "issue list", "https://github.com/octo/demo/issues/1"),
    ),
)
def test_non_workflow_parsers_reject_control_suffix_before_normalization(
    parser, label, url
):
    with pytest.raises(GitHubDiscoveryError, match=f"{label} payload is invalid"):
        parser(
            "octo/demo",
            [
                {
                    "url": url,
                    "title": "Title",
                    "state": "OPEN" + chr(0),
                    "createdAt": "2026-01-01T00:00:00Z",
                    "author": None,
                }
            ],
        )


@pytest.mark.parametrize(
    ("parser", "url"),
    (
        (parse_pr_list, "https://github.com/octo/demo/pull/1"),
        (parse_issue_list, "https://github.com/octo/demo/issues/1"),
    ),
)
def test_activity_parsers_reject_control_in_title(parser, url):
    with pytest.raises(GitHubDiscoveryError, match="payload is invalid"):
        parser(
            "octo/demo",
            [
                {
                    "url": url,
                    "title": "Bearer" + chr(0) + "example-token-value",
                    "state": "OPEN",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "author": None,
                }
            ],
        )


@pytest.mark.parametrize(
    ("parser", "url"),
    (
        (parse_pr_list, "https://github.com/octo/demo/pull/1"),
        (parse_issue_list, "https://github.com/octo/demo/issues/1"),
    ),
)
def test_activity_parsers_reject_control_in_author(parser, url):
    with pytest.raises(GitHubDiscoveryError, match="payload is invalid"):
        parser(
            "octo/demo",
            [
                {
                    "url": url,
                    "title": "Title",
                    "state": "OPEN",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "author": {"login": "Bearer" + chr(0) + "author-token-value"},
                }
            ],
        )


@pytest.mark.parametrize("field", ("title", "author"))
def test_commit_parser_rejects_control_in_title_or_author(field):
    message = "Title" + chr(0) if field == "title" else "Title"
    author_name = "author" + chr(0) if field == "author" else "author"
    with pytest.raises(GitHubDiscoveryError, match="commit list payload is invalid"):
        parse_commit_list(
            "octo/demo",
            [
                {
                    "html_url": "https://github.com/octo/demo/commit/abc123",
                    "commit": {
                        "message": message,
                        "author": {
                            "name": author_name,
                            "date": "2026-01-01T00:00:00Z",
                        },
                    },
                }
            ],
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
        state_field="conclusion",
    )


def test_review_and_workflow_parsers_reject_missing_stable_fields():
    with pytest.raises(GitHubDiscoveryError, match="review comment list payload is invalid"):
        parse_review_list("octo/demo", [{}])
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list("octo/demo", {})


@pytest.mark.parametrize("timestamp", ("", "not-a-timestamp"))
def test_activity_parsers_reject_invalid_required_timestamps(timestamp):
    with pytest.raises(GitHubDiscoveryError, match="pull request list payload is invalid"):
        parse_pr_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/pull/1", "title": "Title", "state": "OPEN", "createdAt": timestamp, "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="issue list payload is invalid"):
        parse_issue_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/issues/1", "title": "Title", "state": "OPEN", "createdAt": timestamp, "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="commit list payload is invalid"):
        parse_commit_list(
            "octo/demo",
            [{"html_url": "https://github.com/octo/demo/commit/abc123", "commit": {"message": "Commit", "author": {"date": timestamp}}}],
        )
    with pytest.raises(GitHubDiscoveryError, match="review comment list payload is invalid"):
        parse_review_list(
            "octo/demo",
            [{"html_url": "https://github.com/octo/demo/pull/1#discussion_r1", "body": "Review", "user": {"login": "octo"}, "created_at": timestamp}],
        )
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list(
            "octo/demo",
            {"workflow_runs": [{"html_url": "https://github.com/octo/demo/actions/runs/1", "name": "CI", "conclusion": "success", "actor": {"login": "octo"}, "created_at": timestamp}]},
        )


def test_activity_parsers_reject_empty_normalized_titles():
    with pytest.raises(GitHubDiscoveryError, match="pull request list payload is invalid"):
        parse_pr_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/pull/1", "title": " \n", "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="issue list payload is invalid"):
        parse_issue_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/issues/1", "title": " \n", "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
        )
    with pytest.raises(GitHubDiscoveryError, match="review comment list payload is invalid"):
        parse_review_list(
            "octo/demo",
            [{"html_url": "https://github.com/octo/demo/pull/1#discussion_r1", "body": "\n", "user": {"login": "octo"}, "created_at": "2026-01-01T00:00:00Z"}],
        )
    with pytest.raises(GitHubDiscoveryError, match="workflow run list payload is invalid"):
        parse_workflow_run_list(
            "octo/demo",
            {"workflow_runs": [{"html_url": "https://github.com/octo/demo/actions/runs/1", "name": " \n", "conclusion": "success", "actor": {"login": "octo"}, "created_at": "2026-01-01T00:00:00Z"}]},
        )


@pytest.mark.parametrize(
    "url",
    ("not-a-url", "https://github.com/octo/other/pull/1"),
)
def test_activity_parsers_reject_invalid_or_cross_repository_urls(url):
    with pytest.raises(GitHubDiscoveryError, match="pull request list payload is invalid"):
        parse_pr_list(
            "octo/demo",
            [{"url": url, "title": "Title", "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://github.com/octo/demo/pull/not-a-pr",
        "https://github.com/octo/demo/issues/not-an-issue",
        "https://github.com/octo/demo/commit/not-a-sha",
        "https://github.com/octo/demo/actions/runs/not-a-run",
        "https://github.com/octo/demo/pull/not-a-pr#discussion_r1",
    ),
)
def test_public_github_activity_identity_rejects_invalid_type_specific_identifiers(url):
    assert public_github_activity_identity(url) is None


def test_parse_pr_list_rejects_invalid_numeric_identifier():
    with pytest.raises(GitHubDiscoveryError, match="pull request list payload is invalid"):
        parse_pr_list(
            "octo/demo",
            [{"url": "https://github.com/octo/demo/pull/not-a-pr", "title": "Title", "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z", "author": None}],
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
    repos = result.repositories
    activities = result.activities
    statuses = result.statuses

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
    assert set(result.completed_endpoints) == {
        ("octo/demo", "pull_request"),
        ("octo/demo", "commit"),
        ("octo/demo", "issue"),
        ("octo/demo", "review_comment"),
        ("octo/demo", "workflow_run"),
    }
    assert result.repositories_complete is True


def test_discover_github_candidates_marks_repository_cap_incomplete(monkeypatch):
    repository_list = [
        {"nameWithOwner": "Octo/Demo", "url": "https://github.com/Octo/Demo", "isPrivate": False}
    ] + [
        {
            "nameWithOwner": "octo/demo",
            "url": "https://github.com/octo/demo",
            "isPrivate": False,
        }
        for index in range(1, 100)
    ]

    def fake_run_gh_json(args):
        if args[:2] == ["repo", "list"]:
            return repository_list
        if args[:2] in (["pr", "list"], ["issue", "list"]):
            return []
        if args == ["api", "repos/octo/demo/commits"]:
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

    result = discover_github_candidates()

    assert [repo.name_with_owner for repo in result.repositories] == ["octo/demo"]
    assert result.repositories_complete is False
    assert result.statuses[0] == (
        "GitHub repository list discovery incomplete: result reached the 100-item limit"
    )


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

    result = discover_github_candidates(
        excluded_repositories=("OCTO/EXCLUDED",),
        private_sources_allowed=False,
    )
    repos = result.repositories
    activities = result.activities
    statuses = result.statuses

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
            return [
                {
                    "url": "https://github.com/octo/ok/pull/1",
                    "title": "Observed pull request",
                    "state": "OPEN",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "mergedAt": None,
                    "author": None,
                }
            ]
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
    repos = result.repositories
    activities = result.activities
    statuses = result.statuses

    assert [repo.name_with_owner for repo in repos] == ["octo/ok", "octo/flaky"]
    assert [activity.repo for activity in activities] == ["octo/ok"]
    assert statuses == [
        "GitHub pull requests discovery failed for octo/flaky: rate limited",
        "GitHub commits discovery failed for octo/flaky: rate limited",
        "GitHub issues discovery failed for octo/flaky: rate limited",
        "GitHub review comments discovery failed for octo/flaky: rate limited",
        "GitHub workflow runs discovery failed for octo/flaky: rate limited",
    ]
    assert set(result.completed_endpoints) == {
        ("octo/ok", "pull_request"),
        ("octo/ok", "commit"),
        ("octo/ok", "issue"),
        ("octo/ok", "review_comment"),
        ("octo/ok", "workflow_run"),
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

    result = discover_github_candidates()
    activities = result.activities
    statuses = result.statuses

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

    result = discover_github_candidates()
    activities = result.activities
    statuses = result.statuses

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


def test_parse_commit_list_normalizes_commit_subject():
    activities = parse_commit_list(
        "octo/demo",
        [
            {
                "html_url": "https://github.com/octo/demo/commit/abcdef1",
                "commit": {
                        "message": "  Implement subject  \n\nBody text",
                    "author": {"date": "2026-01-01T00:00:00Z"},
                },
            }
        ],
    )

    assert activities[0].title == "Implement subject"


def test_parse_commit_list_rejects_whitespace_only_subject():
    with pytest.raises(GitHubDiscoveryError, match="commit list payload is invalid"):
        parse_commit_list(
            "octo/demo",
            [
                {
                    "html_url": "https://github.com/octo/demo/commit/abcdef1",
                    "commit": {
                        "message": " \nBody text",
                        "author": {"date": "2026-01-01T00:00:00Z"},
                    },
                }
            ],
        )


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

    result = discover_github_candidates()
    repos = result.repositories
    activities = result.activities
    statuses = result.statuses

    assert [repo.name_with_owner for repo in repos] == ["octo/demo"]
    assert activities == []
    assert statuses == [
        "GitHub commits discovery failed for octo/demo: "
        "GitHub commit list payload is invalid"
    ]
