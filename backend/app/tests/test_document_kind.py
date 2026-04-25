from __future__ import annotations

from io import BytesIO
import zipfile

import pytest

from app.services.document_kind import detect_document_kind, inspect_uploaded_document


def _build_minimal_docx_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
  <Default Extension=\"xml\" ContentType=\"application/xml\"/>
</Types>
""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
  <w:body />
</w:document>
""",
        )
    return buffer.getvalue()


def test_detect_document_kind_recognizes_pdf_magic_bytes() -> None:
    assert detect_document_kind(b"%PDF-1.7\n1 0 obj\n") == "pdf"


def test_detect_document_kind_recognizes_docx_package_shape() -> None:
    assert detect_document_kind(_build_minimal_docx_bytes()) == "docx"


def test_detect_document_kind_rejects_other_zip_packages() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("ppt/presentation.xml", "<presentation />")

    assert detect_document_kind(buffer.getvalue()) is None


def test_inspect_uploaded_document_rejects_wrong_suffix_for_detected_content() -> None:
    with pytest.raises(ValueError, match="does not match detected DOCX content"):
        inspect_uploaded_document(
            filename="contract.pdf",
            content=_build_minimal_docx_bytes(),
            max_upload_bytes=5 * 1024 * 1024,
        )


def test_inspect_uploaded_document_returns_normalized_descriptor() -> None:
    upload = inspect_uploaded_document(
        filename="nested/path/contract.DOCX",
        content=_build_minimal_docx_bytes(),
        max_upload_bytes=5 * 1024 * 1024,
    )

    assert upload.kind == "docx"
    assert upload.filename == "contract.DOCX"
    assert upload.suffix == ".docx"
    assert upload.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_inspect_uploaded_document_rejects_oversized_uploads() -> None:
    with pytest.raises(ValueError, match="smaller than 1 MB"):
        inspect_uploaded_document(
            filename="large.pdf",
            content=b"%PDF-" + (b"x" * (1024 * 1024)),
            max_upload_bytes=1024 * 1024,
        )