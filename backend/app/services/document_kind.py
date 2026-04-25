from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
from typing import Literal
import zipfile


DocumentKind = Literal["pdf", "docx"]

PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_SUPPORTED_SUFFIXES: dict[DocumentKind, set[str]] = {
    "pdf": {".pdf"},
    "docx": {".docx"},
}

_MEDIA_TYPES: dict[DocumentKind, str] = {
    "pdf": PDF_MEDIA_TYPE,
    "docx": DOCX_MEDIA_TYPE,
}


@dataclass(frozen=True, slots=True)
class UploadedDocument:
    filename: str
    content: bytes
    kind: DocumentKind
    suffix: str
    media_type: str


def detect_document_kind(content: bytes) -> DocumentKind | None:
    if content.startswith(b"%PDF-"):
        return "pdf"

    if not content:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return None

    if "[Content_Types].xml" in names and "word/document.xml" in names:
        return "docx"
    return None


def media_type_for_document(kind: DocumentKind) -> str:
    return _MEDIA_TYPES[kind]


def suffix_for_document(kind: DocumentKind) -> str:
    return next(iter(_SUPPORTED_SUFFIXES[kind]))


def inspect_uploaded_document(
    *,
    filename: str | None,
    content: bytes,
    max_upload_bytes: int,
) -> UploadedDocument:
    if len(content) > max_upload_bytes:
        raise ValueError(
            f"Document uploads must be smaller than {max_upload_bytes // (1024 * 1024)} MB."
        )

    kind = detect_document_kind(content)
    if kind is None:
        raise ValueError("Only PDF and DOCX uploads are supported.")

    raw_filename = (filename or "").strip()
    if not raw_filename:
        raise ValueError("Each upload must include a filename.")

    suffix = Path(raw_filename).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES[kind]:
        expected = ", ".join(sorted(_SUPPORTED_SUFFIXES[kind]))
        raise ValueError(
            f"Filename extension {suffix or '(missing)'} does not match detected {kind.upper()} content. Expected {expected}."
        )

    return UploadedDocument(
        filename=Path(raw_filename).name,
        content=content,
        kind=kind,
        suffix=suffix,
        media_type=media_type_for_document(kind),
    )