from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.infrastructure.policy import FilePolicy


TEXT_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml"}


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
    max_candidates: int = 500,
) -> tuple[list[LocalCandidate], list[SkippedPath]]:
    policy = FilePolicy(forbidden_paths=forbidden_paths)
    candidates: list[LocalCandidate] = []
    skipped: list[SkippedPath] = []

    if max_candidates <= 0:
        return candidates, skipped

    def record_permission_error(error: OSError) -> None:
        filename = error.filename
        skipped_path = Path(filename).resolve(strict=False) if filename else home.resolve(strict=False)
        skipped.append(SkippedPath(skipped_path, "skipped_permission_denied"))

    for root, dirs, files in os.walk(home, topdown=True, onerror=record_permission_error):
        for dirname in dirs[:]:
            path = Path(root) / dirname
            try:
                resolved = path.resolve()
                classification = policy.classify_path(resolved)
            except OSError:
                skipped.append(SkippedPath(path.resolve(strict=False), "skipped_permission_denied"))
                dirs.remove(dirname)
                continue
            if classification != "candidate":
                skipped.append(SkippedPath(resolved, classification))
                dirs.remove(dirname)

        for filename in files:
            path = Path(root) / filename
            try:
                resolved = path.resolve()
                classification = policy.classify_path(resolved)
                if classification != "candidate":
                    skipped.append(SkippedPath(resolved, classification))
                    continue
                if resolved.suffix.lower() not in TEXT_EXTENSIONS:
                    skipped.append(SkippedPath(resolved, "unsupported_extension"))
                    continue
                if resolved.stat().st_size > policy.max_file_size_bytes:
                    skipped.append(SkippedPath(resolved, "skipped_policy"))
                    continue
                candidates.append(LocalCandidate(resolved, resolved.as_uri(), resolved.name))
                if len(candidates) >= max_candidates:
                    return candidates, skipped
            except OSError:
                try:
                    skipped_path = path.resolve(strict=False)
                except OSError:
                    skipped_path = path.absolute()
                skipped.append(SkippedPath(skipped_path, "skipped_permission_denied"))

    return candidates, skipped
