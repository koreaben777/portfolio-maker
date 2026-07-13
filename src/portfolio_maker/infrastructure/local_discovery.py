from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.presentation import normalize_label


TEXT_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml"}


class DiscoveryRootError(ValueError):
    pass


@dataclass(frozen=True)
class LocalCandidate:
    path: Path
    uri: str
    display_name: str


@dataclass(frozen=True)
class SkippedPath:
    path: Path
    reason: str


def discover_local_candidates(
    home: Path,
    forbidden_paths: tuple[Path, ...] = (),
    excluded_file_patterns: tuple[str, ...] = (),
    max_candidates: int = 500,
    excluded_directories: tuple[Path, ...] = (),
) -> tuple[list[LocalCandidate], list[SkippedPath]]:
    policy = FilePolicy(
        forbidden_paths=forbidden_paths,
        excluded_file_patterns=excluded_file_patterns,
    )
    candidates: list[LocalCandidate] = []
    skipped: list[SkippedPath] = []
    seen_uris: set[str] = set()

    try:
        home_resolved = home.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise DiscoveryRootError(f"Discovery root cannot be resolved: {home}") from error
    if not home_resolved.exists() and home.is_symlink():
        raise DiscoveryRootError(f"Discovery root cannot be resolved: {home}")
    if not home_resolved.exists():
        raise DiscoveryRootError(f"Discovery root does not exist: {home}")
    if not home_resolved.is_dir():
        raise DiscoveryRootError(f"Discovery root is not a directory: {home}")

    if max_candidates <= 0:
        return candidates, skipped

    home_classification = _classify_directory(
        home_resolved, policy, excluded_directories
    )
    if home_classification != "candidate":
        return candidates, [SkippedPath(home_resolved, home_classification)]

    def record_permission_error(error: OSError) -> None:
        filename = error.filename
        if not filename:
            skipped.append(SkippedPath(home_resolved, "skipped_permission_denied"))
            return
        path = Path(filename)
        try:
            skipped_path = path.resolve(strict=False)
        except RuntimeError:
            skipped.append(SkippedPath(path.absolute(), "skipped_unresolvable"))
            return
        except OSError:
            skipped_path = path.absolute()
        skipped.append(SkippedPath(skipped_path, "skipped_permission_denied"))

    for root, dirs, files in os.walk(home_resolved, topdown=True, onerror=record_permission_error):
        dirs.sort()
        files.sort()
        for dirname in dirs[:]:
            path = Path(root) / dirname
            try:
                resolved = path.resolve()
                classification = _classify_directory(
                    resolved, policy, excluded_directories
                )
            except RuntimeError:
                skipped.append(SkippedPath(path.absolute(), "skipped_unresolvable"))
                dirs.remove(dirname)
                continue
            except OSError:
                skipped.append(SkippedPath(path.absolute(), "skipped_permission_denied"))
                dirs.remove(dirname)
                continue
            if classification != "candidate":
                skipped.append(SkippedPath(resolved, classification))
                dirs.remove(dirname)

        for filename in files:
            path = Path(root) / filename
            try:
                if path.is_symlink() and not path.exists():
                    skipped.append(SkippedPath(path.absolute(), "skipped_unresolvable"))
                    continue
                resolved = path.resolve()
                classification = (
                    "excluded_directory"
                    if _is_under_any(resolved, excluded_directories)
                    else policy.classify_path(resolved)
                )
                if classification != "candidate":
                    skipped.append(SkippedPath(resolved, classification))
                    continue
                if resolved.suffix.lower() not in TEXT_EXTENSIONS:
                    skipped.append(SkippedPath(resolved, "unsupported_extension"))
                    continue
                if resolved.stat().st_size > policy.max_file_size_bytes:
                    skipped.append(SkippedPath(resolved, "skipped_policy"))
                    continue
                uri = resolved.as_uri()
                if uri in seen_uris:
                    skipped.append(SkippedPath(resolved, "duplicate"))
                    continue
                seen_uris.add(uri)
                candidates.append(
                    LocalCandidate(resolved, uri, normalize_label(resolved.name))
                )
                if len(candidates) >= max_candidates:
                    return candidates, skipped
            except RuntimeError:
                skipped.append(SkippedPath(path.absolute(), "skipped_unresolvable"))
            except OSError:
                try:
                    skipped_path = path.resolve(strict=False)
                except (OSError, RuntimeError):
                    skipped_path = path.absolute()
                skipped.append(SkippedPath(skipped_path, "skipped_permission_denied"))

    return candidates, skipped


def _classify_directory(
    path: Path, policy: FilePolicy, excluded_directories: tuple[Path, ...]
) -> str:
    if _is_under_any(path, excluded_directories):
        return "excluded_directory"
    return policy.classify_path(path)


def _is_under_any(path: Path, directories: tuple[Path, ...]) -> bool:
    resolved = path.resolve(strict=False)
    return any(
        resolved == directory.resolve(strict=False)
        or directory.resolve(strict=False) in resolved.parents
        for directory in directories
    )
