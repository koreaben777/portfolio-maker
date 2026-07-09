from __future__ import annotations

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

    for path in home.rglob("*"):
        try:
            resolved = path.resolve()
            classification = policy.classify_path(resolved)
            if classification != "candidate":
                skipped.append(SkippedPath(resolved, classification))
                if path.is_dir():
                    continue
                continue
            if path.is_dir():
                continue
            if resolved.suffix.lower() not in TEXT_EXTENSIONS:
                skipped.append(SkippedPath(resolved, "unsupported_extension"))
                continue
            if resolved.stat().st_size > policy.max_file_size_bytes:
                skipped.append(SkippedPath(resolved, "oversized_file"))
                continue
            candidates.append(LocalCandidate(resolved, resolved.as_uri(), resolved.name))
            if len(candidates) >= max_candidates:
                break
        except PermissionError:
            skipped.append(SkippedPath(path.resolve(strict=False), "permission_denied"))

    return candidates, skipped
