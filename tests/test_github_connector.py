import json
from pathlib import Path

from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
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
            description="Demo portfolio project",
            primary_language="Python",
        )
    ]


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
        activity_type="review",
        url="https://github.com/octo/demo/pull/1",
        title="Review: Add RAG ingestion",
        state="APPROVED",
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


def test_discover_github_candidates_does_not_paginate_api_json(monkeypatch):
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

    repos, activities = discover_github_candidates()

    assert len(repos) == 1
    assert len(activities) == 5
    assert all("--paginate" not in call for call in calls)
