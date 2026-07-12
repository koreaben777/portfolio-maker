from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from portfolio_maker.infrastructure.presentation import normalize_label


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
    state_field: str | None = None


@dataclass(frozen=True)
class GitHubDiscoveryResult:
    repositories: list[GitHubRepositoryCandidate]
    activities: list[GitHubActivityCandidate]
    statuses: list[str]
    completed_endpoints: tuple[tuple[str, str], ...]
    repositories_complete: bool = True
    observed_private_repositories: tuple[str, ...] = ()


_PUBLIC_ACTIVITY_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9][A-Za-z0-9_-]*)/"
    r"(?P<repository>[A-Za-z0-9][A-Za-z0-9_.-]*)/"
    r"(?:(?P<kind>commit|issues|pull)/(?P<identifier>[A-Za-z0-9._-]+)"
    r"|actions/runs/(?P<run_id>[A-Za-z0-9._-]+))$"
)
_SAFE_REVIEW_FRAGMENT = re.compile(r"^discussion_r\d+$")
_PUBLIC_REPOSITORY_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9][A-Za-z0-9_-]*)/"
    r"(?P<repository>[A-Za-z0-9][A-Za-z0-9_.-]*)$"
)
_NUMERIC_GITHUB_IDENTIFIER = re.compile(r"^[1-9]\d*$")
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{4,40}$")
_GITHUB_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_WORKFLOW_CONCLUSION_STATES = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)
_WORKFLOW_STATUS_STATES = frozenset(
    {"completed", "in_progress", "pending", "queued", "requested", "waiting"}
)
_WORKFLOW_NON_COMPLETED_STATUS_STATES = _WORKFLOW_STATUS_STATES - {"completed"}
_ACTIVITY_STATES = {
    "pull_request": frozenset({"open", "closed", "merged"}),
    "issue": frozenset({"open", "closed"}),
    "commit": frozenset({"committed"}),
    "review_comment": frozenset({"commented"}),
}


def parse_repo_list(payload: Any) -> list[GitHubRepositoryCandidate]:
    return _deduplicate_repository_candidates(_parse_repository_candidates(payload))


def _parse_repository_candidates(payload: Any) -> list[GitHubRepositoryCandidate]:
    candidates: list[GitHubRepositoryCandidate] = []
    for item in _list_payload(payload, "repository list"):
        candidates.append(_parse_repository_candidate(item))
    return candidates


def _parse_repository_candidate(item: dict[str, Any]) -> GitHubRepositoryCandidate:
    is_private = item.get("isPrivate")
    if "isPrivate" not in item or not isinstance(is_private, bool):
        raise GitHubDiscoveryError("GitHub repository list payload is invalid")
    name_with_owner = _required_string(item, "nameWithOwner", "repository list")
    try:
        canonical_name = canonical_repository_name(name_with_owner)
    except ValueError as error:
        raise GitHubDiscoveryError("GitHub repository list payload is invalid") from error
    url = _required_string(item, "url", "repository list")
    if public_github_repository_name(url) != canonical_name:
        raise GitHubDiscoveryError("GitHub repository list payload is invalid")
    return GitHubRepositoryCandidate(
        name_with_owner=canonical_name,
        url=url,
        is_private=is_private,
    )


def _deduplicate_repository_candidates(
    candidates: list[GitHubRepositoryCandidate],
) -> list[GitHubRepositoryCandidate]:
    repos: dict[str, GitHubRepositoryCandidate] = {}
    for candidate in candidates:
        existing = repos.get(candidate.name_with_owner)
        if existing is None:
            repos[candidate.name_with_owner] = candidate
        elif candidate.is_private and not existing.is_private:
            repos[candidate.name_with_owner] = GitHubRepositoryCandidate(
                name_with_owner=existing.name_with_owner,
                url=existing.url,
                is_private=True,
            )
    return list(repos.values())


def parse_pr_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="pull_request",
            url=_required_activity_url(
                item, "url", "pull request list", repo, "pull_request"
            ),
            title=_required_normalized_title(item, "title", "pull request list"),
            state=_required_activity_state(
                item, "state", "pull request list", "pull_request"
            ),
            author=_nested_optional_string(item, "author", "login", "pull request list"),
            created_at=_required_timestamp(item, "createdAt", "pull request list"),
            merged_at=_optional_string(item, "mergedAt", "pull request list"),
        )
        for item in _list_payload(payload, "pull request list")
    ]


def parse_issue_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="issue",
            url=_required_activity_url(item, "url", "issue list", repo, "issue"),
            title=_required_normalized_title(item, "title", "issue list"),
            state=_required_activity_state(item, "state", "issue list", "issue"),
            author=_nested_optional_string(item, "author", "login", "issue list"),
            created_at=_required_timestamp(item, "createdAt", "issue list"),
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
        subject = normalize_label(message.splitlines()[0] if message else "")
        if not subject:
            raise GitHubDiscoveryError("GitHub commit list payload is invalid")
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="commit",
                url=_required_activity_url(
                    item, "html_url", "commit list", repo, "commit"
                ),
                title=subject,
                state="committed",
                author=_optional_string(author, "name", "commit list") or "",
                created_at=_required_timestamp(author, "date", "commit list"),
                merged_at=None,
            )
        )
    return activities


def parse_review_list(repo: str, payload: Any) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in _list_payload(payload, "review comment list"):
        body = _required_normalized_title(item, "body", "review comment list")
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="review_comment",
                url=_required_activity_url(
                    item,
                    "html_url",
                    "review comment list",
                    repo,
                    "review_comment",
                ),
                title=f"Review comment: {body}",
                state="commented",
                author=_nested_required_string(item, "user", "login", "review comment list"),
                created_at=_required_timestamp(item, "created_at", "review comment list"),
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
        state, state_field = _required_workflow_state(item, "workflow run list")
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="workflow_run",
                url=_required_activity_url(
                    item, "html_url", "workflow run list", repo, "workflow_run"
                ),
                title=_required_normalized_title(item, "name", "workflow run list"),
                state=state,
                author=_nested_required_string(item, "actor", "login", "workflow run list"),
                created_at=_required_timestamp(item, "created_at", "workflow run list"),
                merged_at=None,
                state_field=state_field,
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
    allowed_repositories: tuple[str, ...] = (),
    private_sources_allowed: bool = False,
) -> GitHubDiscoveryResult:
    repository_payload = run_gh_json(
        [
            "repo",
            "list",
            "--json",
            "nameWithOwner,url,isPrivate",
            "--limit",
            "100",
        ]
    )
    raw_repositories = _parse_repository_candidates(repository_payload)
    discovered_repositories = _deduplicate_repository_candidates(raw_repositories)
    repositories_complete = len(raw_repositories) < 100
    observed_private_repositories = tuple(
        sorted(
            {
                repository.name_with_owner
                for repository in raw_repositories
                if repository.is_private
            }
        )
    )
    excluded = {canonical_repository_name(name) for name in excluded_repositories}
    allowed = {canonical_repository_name(name) for name in allowed_repositories}
    repos = [
        repo
        for repo in discovered_repositories
        if canonical_repository_name(repo.name_with_owner) not in excluded
        and (not allowed or canonical_repository_name(repo.name_with_owner) in allowed)
        and (private_sources_allowed or not repo.is_private)
    ]
    activities: list[GitHubActivityCandidate] = []
    statuses: list[str] = []
    if not repositories_complete:
        statuses.append(
            "GitHub repository list discovery incomplete: result reached the 100-item limit"
        )
    completed_endpoints: list[tuple[str, str]] = []
    endpoints = (
        (
            "pull requests",
            "pull_request",
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
            100,
        ),
        ("commits", "commit", parse_commit_list, ["api", "repos/{repo}/commits"], 30),
        (
            "issues",
            "issue",
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
            100,
        ),
        (
            "review comments",
            "review_comment",
            parse_review_list,
            ["api", "repos/{repo}/pulls/comments"],
            30,
        ),
        (
            "workflow runs",
            "workflow_run",
            parse_workflow_run_list,
            ["api", "repos/{repo}/actions/runs"],
            30,
        ),
    )
    for repo in repos:
        for label, activity_type, parser, args_template, page_limit in endpoints:
            args = [part.format(repo=repo.name_with_owner) for part in args_template]
            try:
                parsed_activities = parser(repo.name_with_owner, run_gh_json(args))
                activities.extend(parsed_activities)
                if len(parsed_activities) >= page_limit:
                    statuses.append(
                        f"GitHub {label} discovery incomplete for {repo.name_with_owner}: "
                        f"result reached the {page_limit}-item limit"
                    )
                else:
                    completed_endpoints.append((repo.name_with_owner, activity_type))
            except GitHubDiscoveryError as error:
                statuses.append(
                    f"GitHub {label} discovery failed for {repo.name_with_owner}: {error}"
                )
    return GitHubDiscoveryResult(
        repos,
        activities,
        statuses,
        tuple(completed_endpoints),
        repositories_complete,
        observed_private_repositories,
    )


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


def _required_nonempty_string(item: dict[str, Any], key: str, label: str) -> str:
    value = _required_string(item, key, label)
    if not value.strip():
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _required_timestamp(item: dict[str, Any], key: str, label: str) -> str:
    value = _required_nonempty_string(item, key, label)
    if not is_valid_github_timestamp(value):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _required_activity_state(
    item: dict[str, Any], key: str, label: str, activity_type: str
) -> str:
    return _required_activity_state_value(
        _required_nonempty_string(item, key, label), label, activity_type
    )


def _required_activity_state_value(value: str, label: str, activity_type: str) -> str:
    if not is_valid_github_activity_state(activity_type, value):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _required_workflow_state(
    item: dict[str, Any], label: str
) -> tuple[str, str]:
    if "status" not in item or not isinstance(item["status"], str):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    status = item["status"]
    if _contains_unicode_control(status):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    normalized_status = normalize_label(status).casefold()
    if not status.strip() or normalized_status not in _WORKFLOW_STATUS_STATES:
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")

    if normalized_status != "completed":
        if "conclusion" not in item or item["conclusion"] is not None:
            raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
        return status, "status"

    conclusion = item.get("conclusion")
    if (
        not isinstance(conclusion, str)
        or not conclusion.strip()
        or _contains_unicode_control(conclusion)
        or normalize_label(conclusion).casefold() not in _WORKFLOW_CONCLUSION_STATES
    ):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return conclusion, "conclusion"


def _required_normalized_title(item: dict[str, Any], key: str, label: str) -> str:
    value = _required_string(item, key, label)
    if not normalize_label(value):
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")
    return value


def _required_activity_url(
    item: dict[str, Any],
    key: str,
    label: str,
    repository: str,
    activity_type: str,
) -> str:
    value = _required_nonempty_string(item, key, label)
    try:
        canonical_repository = canonical_repository_name(repository)
    except ValueError as error:
        raise GitHubDiscoveryError(f"GitHub {label} payload is invalid") from error
    if public_github_activity_identity(value) != (canonical_repository, activity_type):
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
        if isinstance(value, str) and value.strip():
            return value
    raise GitHubDiscoveryError(f"GitHub {label} payload is invalid")


def is_valid_github_timestamp(value: str) -> bool:
    if _GITHUB_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return True


def is_valid_github_activity_state(
    activity_type: str, value: str, state_field: str | None = None
) -> bool:
    if activity_type == "workflow_run" and _contains_unicode_control(value):
        return False
    normalized = normalize_label(value).casefold()
    if activity_type == "workflow_run":
        if state_field == "conclusion":
            return normalized in _WORKFLOW_CONCLUSION_STATES
        if state_field == "status":
            return normalized in _WORKFLOW_NON_COMPLETED_STATUS_STATES
        return False
    return normalized in _ACTIVITY_STATES.get(activity_type, ())


def _contains_unicode_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


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


def is_public_github_activity_url(value: str) -> bool:
    return public_github_activity_identity(value) is not None


def public_github_repository_name(value: str) -> str | None:
    parsed = _trusted_public_github_url(value)
    if parsed is None or parsed.fragment:
        return None
    match = _PUBLIC_REPOSITORY_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    try:
        return canonical_repository_name(f"{match['owner']}/{match['repository']}")
    except ValueError:
        return None


def public_github_activity_identity(value: str) -> tuple[str, str] | None:
    parsed = _trusted_public_github_url(value)
    if parsed is None:
        return None
    match = _PUBLIC_ACTIVITY_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    try:
        repository = canonical_repository_name(f"{match['owner']}/{match['repository']}")
    except ValueError:
        return None
    if match["kind"] == "commit":
        if _COMMIT_SHA.fullmatch(match["identifier"]) is None:
            return None
    elif match["kind"] in {"issues", "pull"}:
        if _NUMERIC_GITHUB_IDENTIFIER.fullmatch(match["identifier"]) is None:
            return None
    elif _NUMERIC_GITHUB_IDENTIFIER.fullmatch(match["run_id"]) is None:
        return None
    if parsed.fragment:
        if match["kind"] == "pull" and _SAFE_REVIEW_FRAGMENT.fullmatch(parsed.fragment):
            return repository, "review_comment"
        return None
    if match["kind"] is not None:
        return repository, {"commit": "commit", "issues": "issue", "pull": "pull_request"}[match["kind"]]
    return repository, "workflow_run"


def _trusted_public_github_url(value: str):
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.params
        or parsed.query
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return parsed


def public_github_activity_type(value: str) -> str | None:
    identity = public_github_activity_identity(value)
    return identity[1] if identity is not None else None


def _is_canonical_repository_component(value: str, allow_dots: bool) -> bool:
    if not value or value in {".", ".."} or not value[0].isalnum():
        return False
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if allow_dots:
        allowed += "."
    return all(character in allowed for character in value)
