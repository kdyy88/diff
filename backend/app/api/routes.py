from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import DEFAULT_FOOTER_MARGIN, DEFAULT_HEADER_MARGIN, MAX_PDF_UPLOAD_BYTES, PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT
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
from app.services.jobs import job_store


router = APIRouter(prefix="/api")


def _require_chapter_split_enabled() -> None:
    if not PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT:
        raise HTTPException(status_code=404, detail="Chapter split feature is disabled.")


def _validate_pdf_upload(upload: UploadFile) -> None:
    if upload.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Both uploads must be PDF files.")

    current_position = upload.file.tell()
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(current_position)
    if size > MAX_PDF_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF uploads must be smaller than {MAX_PDF_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )


@router.get("/features", response_model=FeatureFlags)
def get_features() -> FeatureFlags:
    return FeatureFlags(chapterSplit=PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT)


@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(
    source_pdf: UploadFile = File(..., alias="sourcePdf"),
    modified_pdf: UploadFile = File(..., alias="modifiedPdf"),
    header_margin: float = Form(DEFAULT_HEADER_MARGIN, alias="headerMargin"),
    footer_margin: float = Form(DEFAULT_FOOTER_MARGIN, alias="footerMargin"),
    show_reflow: bool = Form(False, alias="showReflow"),
) -> CreateJobResponse:
    _validate_pdf_upload(source_pdf)
    _validate_pdf_upload(modified_pdf)

    return await job_store.create_job(
        source_pdf,
        modified_pdf,
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
        path = job_store.get_file(job_id, side)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path=path, media_type="application/pdf", filename=path.name)


@router.post("/chapter-analyses", response_model=ChapterAnalysisStatus)
async def create_chapter_analysis(
    source_pdf: UploadFile = File(..., alias="sourcePdf"),
    modified_pdf: UploadFile = File(..., alias="modifiedPdf"),
    header_margin: float = Form(DEFAULT_HEADER_MARGIN, alias="headerMargin"),
    footer_margin: float = Form(DEFAULT_FOOTER_MARGIN, alias="footerMargin"),
    show_reflow: bool = Form(False, alias="showReflow"),
) -> ChapterAnalysisStatus:
    _require_chapter_split_enabled()
    _validate_pdf_upload(source_pdf)
    _validate_pdf_upload(modified_pdf)
    return await chapter_analysis_store.create_analysis(
        source_pdf,
        modified_pdf,
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
        path = chapter_analysis_store.get_file(analysis_id, side)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chapter analysis not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path=path, media_type="application/pdf", filename=path.name)


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