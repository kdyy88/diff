from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.core.config import DEFAULT_FOOTER_MARGIN, DEFAULT_HEADER_MARGIN, MAX_DOCUMENT_UPLOAD_BYTES, PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT
from app.models.schemas import (
    ChapterAnalysisResult,
    ChapterAnalysisStatus,
    ChapterValidationRequest,
    ChapterValidationResult,
    CreateJobResponse,
    DiffResult,
    FeatureFlags,
    JobStatus,
)
from app.services.chapters import ChapterValidationConflict, chapter_analysis_store
from app.services.document_kind import UploadedDocument, inspect_uploaded_document
from app.services.jobs import job_store


router = APIRouter(prefix="/api")
MAX_PDF_UPLOAD_BYTES = MAX_DOCUMENT_UPLOAD_BYTES


def _require_chapter_split_enabled() -> None:
    if not PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT:
        raise HTTPException(status_code=404, detail="Chapter split feature is disabled.")


def _resolve_upload(primary: UploadFile | None, legacy: UploadFile | None, label: str) -> UploadFile:
    if primary is not None and legacy is not None:
        raise HTTPException(status_code=400, detail=f"Provide either {label} or its legacy alias, not both.")
    upload = primary or legacy
    if upload is None:
        raise HTTPException(status_code=422, detail=f"Missing required upload: {label}.")
    return upload


async def _read_validated_upload(upload: UploadFile) -> UploadedDocument:
    try:
        content = await upload.read()
    finally:
        await upload.close()

    try:
        return inspect_uploaded_document(
            filename=upload.filename,
            content=content,
            max_upload_bytes=MAX_PDF_UPLOAD_BYTES,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Document uploads must be smaller than"):
            detail = detail.replace("Document uploads", "PDF uploads", 1)
        status_code = 413 if "smaller than" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


async def _read_document_pair(
    source_upload: UploadFile,
    modified_upload: UploadFile,
    *,
    allow_docx: bool,
    docx_message: str,
) -> tuple[UploadedDocument, UploadedDocument]:
    source_document = await _read_validated_upload(source_upload)
    modified_document = await _read_validated_upload(modified_upload)

    if source_document.kind != modified_document.kind:
        raise HTTPException(
            status_code=400,
            detail="Both uploads must use the same document format. Supported pairs are PDF-PDF and DOCX-DOCX.",
        )

    if source_document.kind == "docx" and not allow_docx:
        raise HTTPException(status_code=501, detail=docx_message)

    return source_document, modified_document


@router.get("/features", response_model=FeatureFlags)
def get_features() -> FeatureFlags:
    return FeatureFlags(chapterSplit=PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT)


@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(
    source_file: UploadFile | None = File(None, alias="sourceFile"),
    modified_file: UploadFile | None = File(None, alias="modifiedFile"),
    source_pdf: UploadFile | None = File(None, alias="sourcePdf"),
    modified_pdf: UploadFile | None = File(None, alias="modifiedPdf"),
    header_margin: float = Form(DEFAULT_HEADER_MARGIN, alias="headerMargin"),
    footer_margin: float = Form(DEFAULT_FOOTER_MARGIN, alias="footerMargin"),
    show_reflow: bool = Form(False, alias="showReflow"),
) -> CreateJobResponse:
    source_document, modified_document = await _read_document_pair(
        _resolve_upload(source_file, source_pdf, "sourceFile"),
        _resolve_upload(modified_file, modified_pdf, "modifiedFile"),
        allow_docx=True,
        docx_message="DOCX comparison is not implemented yet.",
    )

    return await job_store.create_job(
        source_document,
        modified_document,
        header_margin=header_margin,
        footer_margin=footer_margin,
        include_reflow=show_reflow,
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
def get_job_status(job_id: str) -> JobStatus:
    try:
        return job_store.get_status(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc


@router.get("/jobs/{job_id}/result", response_model=DiffResult)
def get_job_result(job_id: str) -> DiffResult:
    try:
        return job_store.get_result(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/files/{side}")
def get_job_pdf(job_id: str, side: str) -> FileResponse:
    try:
        path, media_type, filename = job_store.get_file_metadata(job_id, side)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path=path, media_type=media_type, filename=filename)


@router.get("/jobs/{job_id}/review/{side}", response_class=HTMLResponse)
def get_job_review(job_id: str, side: str) -> HTMLResponse:
    try:
        html = job_store.get_review_html(job_id, side)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse(content=html)


@router.post("/chapter-analyses", response_model=ChapterAnalysisStatus)
async def create_chapter_analysis(
    source_file: UploadFile | None = File(None, alias="sourceFile"),
    modified_file: UploadFile | None = File(None, alias="modifiedFile"),
    source_pdf: UploadFile | None = File(None, alias="sourcePdf"),
    modified_pdf: UploadFile | None = File(None, alias="modifiedPdf"),
    header_margin: float = Form(DEFAULT_HEADER_MARGIN, alias="headerMargin"),
    footer_margin: float = Form(DEFAULT_FOOTER_MARGIN, alias="footerMargin"),
    show_reflow: bool = Form(False, alias="showReflow"),
) -> ChapterAnalysisStatus:
    _require_chapter_split_enabled()
    source_document, modified_document = await _read_document_pair(
        _resolve_upload(source_file, source_pdf, "sourceFile"),
        _resolve_upload(modified_file, modified_pdf, "modifiedFile"),
        allow_docx=True,
        docx_message="DOCX chapter analysis is not implemented yet.",
    )
    return await chapter_analysis_store.create_analysis(
        source_document,
        modified_document,
        header_margin=header_margin,
        footer_margin=footer_margin,
        include_reflow=show_reflow,
    )


@router.get("/chapter-analyses/{analysis_id}", response_model=ChapterAnalysisStatus)
def get_chapter_analysis_status(analysis_id: str) -> ChapterAnalysisStatus:
    _require_chapter_split_enabled()
    try:
        return chapter_analysis_store.get_status(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc


@router.get("/chapter-analyses/{analysis_id}/result", response_model=ChapterAnalysisResult)
def get_chapter_analysis_result(analysis_id: str) -> ChapterAnalysisResult:
    _require_chapter_split_enabled()
    try:
        return chapter_analysis_store.get_result(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/chapter-analyses/{analysis_id}/files/{side}")
def get_chapter_analysis_pdf(analysis_id: str, side: str) -> FileResponse:
    _require_chapter_split_enabled()
    try:
        path, media_type, filename = chapter_analysis_store.get_file_metadata(analysis_id, side)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path=path, media_type=media_type, filename=filename)


@router.get("/chapter-analyses/{analysis_id}/review/{side}", response_class=HTMLResponse)
def get_chapter_analysis_review(analysis_id: str, side: str) -> HTMLResponse:
    _require_chapter_split_enabled()
    try:
        html = chapter_analysis_store.get_review_html(analysis_id, side)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse(content=html)


@router.post("/chapter-analyses/{analysis_id}/validate", response_model=ChapterValidationResult)
def validate_chapter_analysis(analysis_id: str, payload: ChapterValidationRequest) -> ChapterValidationResult:
    _require_chapter_split_enabled()
    try:
        return chapter_analysis_store.validate(analysis_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/chapter-analyses/{analysis_id}/confirm", response_model=CreateJobResponse)
async def confirm_chapter_analysis(analysis_id: str, payload: ChapterValidationRequest) -> CreateJobResponse:
    _require_chapter_split_enabled()
    try:
        return await chapter_analysis_store.confirm(analysis_id, payload, job_store.create_job_from_paths)
    except ChapterValidationConflict as exc:
        raise HTTPException(status_code=409, detail=exc.validation.model_dump()) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc