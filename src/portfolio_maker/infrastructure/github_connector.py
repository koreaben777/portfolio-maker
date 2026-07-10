from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


class GitHubDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRepositoryCandidate:
    name_with_owner: str
    url: str
    is_private: bool


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
        repos.append(
            GitHubRepositoryCandidate(
                name_with_owner=item["nameWithOwner"],
                url=item["url"],
                is_private=bool(item.get("isPrivate", False)),
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
        user = item.get("user") or {}
        body = str(item.get("body") or "pull request").splitlines()[0]
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="review_comment",
                url=item.get("html_url") or "",
                title=f"Review comment: {body}",
                state="commented",
                author=user.get("login", ""),
                created_at=item.get("created_at") or "",
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
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise GitHubDiscoveryError("GitHub discovery failed; check gh auth or use --no-github.") from error


def discover_github_candidates(
    excluded_repositories: tuple[str, ...] = (),
    private_sources_allowed: bool = False,
) -> tuple[list[GitHubRepositoryCandidate], list[GitHubActivityCandidate], list[str]]:
    repos = parse_repo_list(
        run_gh_json(
            [
                "repo",
                "list",
                "--json",
                "nameWithOwner,url,isPrivate",
                "--limit",
                "100",
            ]
        )
    )
    excluded = set(excluded_repositories)
    repos = [
        repo
        for repo in repos
        if repo.name_with_owner not in excluded
        and (private_sources_allowed or not repo.is_private)
    ]
    activities: list[GitHubActivityCandidate] = []
    statuses: list[str] = []
    for repo in repos:
        try:
            repo_activities: list[GitHubActivityCandidate] = []
            repo_activities.extend(
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
            repo_activities.extend(
                parse_commit_list(
                    repo.name_with_owner,
                    run_gh_json(["api", f"repos/{repo.name_with_owner}/commits"]),
                )
            )
            repo_activities.extend(
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
            repo_activities.extend(
                parse_review_list(
                    repo.name_with_owner,
                    run_gh_json(["api", f"repos/{repo.name_with_owner}/pulls/comments"]),
                )
            )
            repo_activities.extend(
                parse_workflow_run_list(
                    repo.name_with_owner,
                    run_gh_json(["api", f"repos/{repo.name_with_owner}/actions/runs"]),
                )
            )
            activities.extend(repo_activities)
        except GitHubDiscoveryError as error:
            statuses.append(f"GitHub activity discovery failed for {repo.name_with_owner}: {error}")
    return repos, activities, statuses
