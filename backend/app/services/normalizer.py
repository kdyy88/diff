from __future__ import annotations

import unicodedata


def normalize_char(char: str) -> str:
    if not char:
        return char
    if char.isspace():
        return " "
    normalized = unicodedata.normalize("NFKC", char)
    if len(normalized) == 1:
        return " " if normalized.isspace() else normalized
    return char


def normalize_text(text: str) -> str:
    return "".join(normalize_char(char) for char in text)
