from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXCLUDED_NAMES = {
    ".Trash",
    "Library",
    "Applications",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(password\s*=\s*)([^\s]+)"),
    re.compile(r"(?i)(api[_-]?key\s*=\s*)([^\s]+)"),
    re.compile(r"(?i)(token\s*=\s*)([^\s]+)"),
]


@dataclass(frozen=True)
class FilePolicy:
    forbidden_paths: tuple[Path, ...] = ()
    max_file_size_bytes: int = 2_000_000

    def is_forbidden(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        for forbidden in self.forbidden_paths:
            forbidden_resolved = forbidden.resolve(strict=False)
            if resolved == forbidden_resolved or forbidden_resolved in resolved.parents:
                return True
        return False

    def classify_path(self, path: Path) -> str:
        if self.is_forbidden(path):
            return "forbidden"
        if path.name in SENSITIVE_FILE_NAMES:
            return "skipped_policy"
        if any(part in DEFAULT_EXCLUDED_NAMES for part in path.parts):
            return "skipped_policy"
        return "candidate"


def mask_secrets(text: str) -> str:
    masked = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            masked = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", masked)
        else:
            masked = pattern.sub("[REDACTED]", masked)
    return masked
