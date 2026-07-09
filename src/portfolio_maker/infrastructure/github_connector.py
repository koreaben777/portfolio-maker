from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GitHubRepositoryCandidate:
    name_with_owner: str
    url: str
    is_private: bool
    description: str
    primary_language: str | None


@dataclass(frozen=True)
class GitHubActivityCandidate:
    repo: str
    activity_type: str
    url: str
    title: str
    state: str
    author: str
    created_at: str
    merged_at: str | None


def parse_repo_list(payload: list[dict]) -> list[GitHubRepositoryCandidate]:
    repos: list[GitHubRepositoryCandidate] = []
    for item in payload:
        language = item.get("primaryLanguage") or {}
        repos.append(
            GitHubRepositoryCandidate(
                name_with_owner=item["nameWithOwner"],
                url=item["url"],
                is_private=bool(item.get("isPrivate", False)),
                description=item.get("description") or "",
                primary_language=language.get("name"),
            )
        )
    return repos


def parse_pr_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="pull_request",
            url=item["url"],
            title=item["title"],
            state=item["state"],
            author=(item.get("author") or {}).get("login", ""),
            created_at=item["createdAt"],
            merged_at=item.get("mergedAt"),
        )
        for item in payload
    ]


def parse_issue_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="issue",
            url=item["url"],
            title=item["title"],
            state=item["state"],
            author=(item.get("author") or {}).get("login", ""),
            created_at=item["createdAt"],
            merged_at=None,
        )
        for item in payload
    ]


def parse_commit_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in payload:
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        message = str(commit.get("message") or "").splitlines()[0]
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="commit",
                url=item.get("html_url") or "",
                title=message,
                state="committed",
                author=author.get("name") or "",
                created_at=author.get("date") or "",
                merged_at=None,
            )
        )
    return activities


def parse_review_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in payload:
        pull_request = item.get("pullRequest") or {}
        user = item.get("user") or {}
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="review",
                url=pull_request.get("url") or item.get("html_url") or "",
                title=f"Review: {pull_request.get('title') or item.get('body') or 'pull request'}",
                state=item.get("state") or "",
                author=(item.get("author") or user).get("login", ""),
                created_at=item.get("submittedAt") or item.get("created_at") or "",
                merged_at=None,
            )
        )
    return activities


def parse_workflow_run_list(repo: str, payload: dict) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in payload.get("workflow_runs", []):
        actor = item.get("actor") or {}
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="workflow_run",
                url=item.get("html_url") or "",
                title=item.get("name") or "workflow",
                state=item.get("conclusion") or item.get("status") or "",
                author=actor.get("login") or "",
                created_at=item.get("created_at") or "",
                merged_at=None,
            )
        )
    return activities


def run_gh_json(args: list[str]) -> Any:
    completed = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def discover_github_candidates() -> tuple[list[GitHubRepositoryCandidate], list[GitHubActivityCandidate]]:
    repos = parse_repo_list(
        run_gh_json(
            [
                "repo",
                "list",
                "--json",
                "nameWithOwner,url,isPrivate,description,primaryLanguage",
                "--limit",
                "100",
            ]
        )
    )
    activities: list[GitHubActivityCandidate] = []
    for repo in repos:
        activities.extend(
            parse_pr_list(
                repo.name_with_owner,
                run_gh_json(
                    [
                        "pr",
                        "list",
                        "--repo",
                        repo.name_with_owner,
                        "--state",
                        "all",
                        "--json",
                        "title,url,state,createdAt,mergedAt,author",
                        "--limit",
                        "100",
                    ]
                ),
            )
        )
        activities.extend(
            parse_commit_list(
                repo.name_with_owner,
                run_gh_json(["api", f"repos/{repo.name_with_owner}/commits"]),
            )
        )
        activities.extend(
            parse_issue_list(
                repo.name_with_owner,
                run_gh_json(
                    [
                        "issue",
                        "list",
                        "--repo",
                        repo.name_with_owner,
                        "--state",
                        "all",
                        "--json",
                        "title,url,state,createdAt,author",
                        "--limit",
                        "100",
                    ]
                ),
            )
        )
        activities.extend(
            parse_review_list(
                repo.name_with_owner,
                run_gh_json(["api", f"repos/{repo.name_with_owner}/pulls/comments"]),
            )
        )
        activities.extend(
            parse_workflow_run_list(
                repo.name_with_owner,
                run_gh_json(["api", f"repos/{repo.name_with_owner}/actions/runs"]),
            )
        )
    return repos, activities
