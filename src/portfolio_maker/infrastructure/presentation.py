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


def markdown_text(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", normalize_label(value))
