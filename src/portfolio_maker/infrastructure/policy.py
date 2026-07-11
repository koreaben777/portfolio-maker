from __future__ import annotations

import re
from fnmatch import fnmatchcase
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
    ".portfolio-maker",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "passwords.csv",
    "passwords.json",
    "password-export.csv",
    "password-export.json",
    "password_export.csv",
    "password_export.json",
    "lastpass.csv",
    "lastpass_export.csv",
    "lastpass-export.csv",
    "bitwarden.csv",
    "bitwarden_export.csv",
    "bitwarden-export.csv",
    "1password.csv",
    "1password_export.csv",
    "1password-export.csv",
    "chrome_passwords.csv",
    "chrome-passwords.csv",
    "firefox_logins.csv",
    "firefox-logins.csv",
    "firefox_logins.json",
}

SENSITIVE_KEY = r"[A-Za-z0-9_-]*(?:password|api[_-]?key|token|secret(?:[_-]?access)?[_-]?key|secret)[A-Za-z0-9_-]*"

SECRET_PATTERNS = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "literal",
    ),
    (re.compile(r"(?im)(Authorization\s*:\s*Bearer\s+)[^\s,;]+"), "bare_key_value"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "literal"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "literal"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "literal"),
    (
        re.compile(r'(?i)(["\']%s["\']\s*:\s*)(["\'])(.*?)\2' % SENSITIVE_KEY),
        "quoted_key_value",
    ),
    (
        re.compile(r"(?i)(\b%s\b\s*[:=]\s*)(['\"])(.*?)\2" % SENSITIVE_KEY),
        "quoted_key_value",
    ),
    (
        re.compile(r'(?i)(["\']%s["\']\s*:\s*)([^"\'}\n]+?)(?=,\s*["\']|})' % SENSITIVE_KEY),
        "bare_key_value",
    ),
    (
        re.compile(r"(?i)(?<![\"'])(\b%s\b\s*[:=](?![^\S\r\n]*[\"'])[^\S\r\n]*)([^\n]+)" % SENSITIVE_KEY),
        "bare_key_value",
    ),
]
DEFAULT_EXCLUDED_NAMES_CASEFOLD = {name.casefold() for name in DEFAULT_EXCLUDED_NAMES}
SENSITIVE_FILE_NAMES_CASEFOLD = {name.casefold() for name in SENSITIVE_FILE_NAMES}
PASSWORD_MANAGER_EXPORT_FILENAME = re.compile(
    r"(?i)^(?:(?:bitwarden|lastpass|1password|chrome|firefox)[_-]export"
    r"|chrome[_-]passwords|firefox[_-]logins)"
    r"(?:[_-](?:\d{8}|\d{4}-\d{2}-\d{2})(?:[_-]?\d{6})?)?\.(?:csv|json)$"
)
SECRET_SHAPED_FILENAME = re.compile(
    r"(?i)(?:\bsk-|\bgithub_pat_|\bgh[pousr]_)[A-Za-z0-9_-]{8,}"
)


class SourcePathPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class FilePolicy:
    forbidden_paths: tuple[Path, ...] = ()
    excluded_file_patterns: tuple[str, ...] = ()
    max_file_size_bytes: int = 2_000_000

    def is_forbidden(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        for forbidden in self.forbidden_paths:
            # Normalize both paths through resolve(strict=False) so relative roots,
            # cwd-dependent paths, and differently normalized inputs compare
            # against the same absolute path representation.
            forbidden_resolved = forbidden.resolve(strict=False)
            if resolved == forbidden_resolved or forbidden_resolved in resolved.parents:
                return True
        return False

    def classify_path(self, path: Path) -> str:
        if self.is_forbidden(path):
            return "forbidden"
        if is_sensitive_filename(path):
            return "skipped_policy"
        if any(
            fnmatchcase(path.name.casefold(), pattern.casefold())
            for pattern in self.excluded_file_patterns
        ):
            return "skipped_policy"
        if any(part.casefold() in DEFAULT_EXCLUDED_NAMES_CASEFOLD for part in path.parts):
            return "skipped_policy"
        return "candidate"


def mask_secrets(text: str) -> str:
    masked = text
    for pattern, replacement_type in SECRET_PATTERNS:
        if replacement_type == "quoted_key_value":
            masked = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}", masked)
        elif replacement_type == "bare_key_value":
            masked = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", masked)
        else:
            masked = pattern.sub("[REDACTED]", masked)
    return masked


def is_sensitive_filename(path: Path | str) -> bool:
    name = Path(path).name
    return (
        name.casefold() in SENSITIVE_FILE_NAMES_CASEFOLD
        or PASSWORD_MANAGER_EXPORT_FILENAME.fullmatch(name) is not None
        or SECRET_SHAPED_FILENAME.search(name) is not None
    )


def mask_public_value(value: str) -> str:
    if is_sensitive_filename(value):
        return "[REDACTED]"
    return mask_secrets(value)
