from __future__ import annotations

import re
import unicodedata


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|])")


def normalize_label(value: str) -> str:
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    return " ".join(without_controls.split())


def is_path_like_public_label(value: str) -> bool:
    normalized = normalize_label(value)
    lowered = normalized.casefold()
    return (
        not normalized
        or lowered.startswith("file:")
        or normalized.startswith(("/", "~/", "~\\", "\\\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", normalized))
        or "/" in normalized
        or "\\" in normalized
    )


def safe_local_public_label(value: str) -> str:
    normalized = normalize_label(value)
    if is_path_like_public_label(normalized):
        return "Approved local evidence"
    return normalized


def markdown_text(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", normalize_label(value))
