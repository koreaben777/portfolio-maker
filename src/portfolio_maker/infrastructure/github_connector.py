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


def parse_repo_list(payload: Any) -> list[GitHubRepositoryCandidate]:
    repos: list[GitHubRepositoryCandidate] = []
    for item in _list_payload(payload, "repository list"):
        is_private = item.get("isPrivate")
        if "isPrivate" not in item or not isinstance(is_private, bool):
            raise GitHubDiscoveryError("GitHub repository list payload is invalid")
        repos.append(
            GitHubRepositoryCandidate(
                name_with_owner=_required_string(item, "nameWithOwner", "repository list"),
                url=_required_string(item, "url", "repository list"),
                is_private=is_private,
            )
        )
    return repos


def parse_pr_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="pull_request",
            url=_required_string(item, "url", "pull request list"),
            title=_required_string(item, "title", "pull request list"),
            state=_required_string(item, "state", "pull request list"),
            author=_nested_optional_string(item, "author", "login", "pull request list"),
            created_at=_required_string(item, "createdAt", "pull request list"),
            merged_at=_optional_string(item, "mergedAt", "pull request list"),
        )
        for item in _list_payload(payload, "pull request list")
    ]


def parse_issue_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="issue",
            url=_required_string(item, "url", "issue list"),
            title=_required_string(item, "title", "issue list"),
            state=_required_string(item, "state", "issue list"),
            author=_nested_optional_string(item, "author", "login", "issue list"),
            created_at=_required_string(item, "createdAt", "issue list"),
            merged_at=None,
        )
        for item in _list_payload(payload, "issue list")
    ]


def parse_commit_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in _list_payload(payload, "commit list"):
        commit = _object(item.get("commit") or {}, "commit list")
        author = _object(commit.get("author") or {}, "commit list")
        message = _required_string(commit, "message", "commit list")
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="commit",
                url=_optional_string(item, "html_url", "commit list") or "",
                title=message.splitlines()[0] if message else "",
                state="committed",
                author=_optional_string(author, "name", "commit list") or "",
                created_at=_optional_string(author, "date", "commit list") or "",
                merged_at=None,
            )
        )
    return activities


def parse_review_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in _list_payload(payload, "review comment list"):
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="review_comment",
                url=_required_string(item, "html_url", "review comment list"),
                title=f"Review comment: {_required_string(item, 'body', 'review comment list')}".splitlines()[0],
                state="commented",
                author=_nested_required_string(item, "user", "login", "review comment list"),
                created_at=_required_string(item, "created_at", "review comment list"),
                merged_at=None,
            )
        )
    return activities


def parse_workflow_run_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    body = _object(payload, "workflow run list")
    runs = body.get("workflow_runs")
    if not isinstance(runs, list):
        raise GitHubDiscoveryError("GitHub workflow run list payload is invalid")
    activities: list[GitHubActivityCandidate] = []
    for item in _list_payload(runs, "workflow run list"):
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="workflow_run",
                url=_required_string(item, "html_url", "workflow run list"),
                title=_required_string(item, "name", "workflow run list"),
                state=_required_one_of_strings(item, ("conclusion", "status"), "workflow run list"),
                author=_nested_required_string(item, "actor", "login", "workflow run list"),
                created_at=_required_string(item, "created_at", "workflow run list"),
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
    excluded = {canonical_repository_name(name) for name in excluded_repositories}
    repos = [
        repo
        for repo in repos
        if canonical_repository_name(repo.name_with_owner) not in excluded
        and (private_sources_allowed or not repo.is_private)
    ]
    activities: list[GitHubActivityCandidate] = []
    statuses: list[str] = []
    endpoints = (
        (
            "pull requests",
            parse_pr_list,
            [
                "pr",
                "list",
                "--repo",
                "{repo}",
                "--state",
                "all",
                "--json",
                "title,url,state,createdAt,mergedAt,author",
                "--limit",
                "100",
            ],
        ),
        ("commits", parse_commit_list, ["api", "repos/{repo}/commits"]),
        (
            "issues",
            parse_issue_list,
            [
                "issue",
                "list",
                "--repo",
                "{repo}",
                "--state",
                "all",
                "--json",
                "title,url,state,createdAt,author",
                "--limit",
                "100",
            ],
        ),
        ("review comments", parse_review_list, ["api", "repos/{repo}/pulls/comments"]),
        ("workflow runs", parse_workflow_run_list, ["api", "repos/{repo}/actions/runs"]),
    )
    for repo in repos:
        for label, parser, args_template in endpoints:
            args = [part.format(repo=repo.name_with_owner) for part in args_template]
            try:
                activities.extend(parser(repo.name_with_owner, run_gh_json(args)))
            except GitHubDiscoveryError as error:
                statuses.append(
                    f"GitHub {label} discovery failed for {repo.name_with_owner}: {error}"
                )
    return repos, activities, statuses


def _list_payload(payload: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return [_object(item, label) for item in payload]


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _required_string(item: dict[str, Any], key: str, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _optional_string(item: dict[str, Any], key: str, label: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _nested_optional_string(
    item: dict[str, Any], key: str, nested_key: str, label: str
) -> str:
    value = item.get(key)
    if value is None:
        return ""
    nested = _object(value, label).get(nested_key)
    if nested is None:
        return ""
    if not isinstance(nested, str):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return nested


def _nested_required_string(
    item: dict[str, Any], key: str, nested_key: str, label: str
) -> str:
    value = _object(item.get(key), label)
    return _required_string(value, nested_key, label)


def _required_one_of_strings(
    item: dict[str, Any], keys: tuple[str, ...], label: str
) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            return value
    raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")


def canonical_repository_name(name: str) -> str:
    owner, separator, repository = name.strip().partition("/")
    if (
        separator != "/"
        or "/" in repository
        or not _is_canonical_repository_component(owner, allow_dots=False)
        or not _is_canonical_repository_component(repository, allow_dots=True)
    ):
        raise ValueError("repository name must use canonical owner/repo form")
    return f"{owner.casefold()}/{repository.casefold()}"


def _is_canonical_repository_component(value: str, allow_dots: bool) -> bool:
    if not value or value in {".", ".."} or not value[0].isalnum():
        return False
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if allow_dots:
        allowed += "."
    return all(character in allowed for character in value)
