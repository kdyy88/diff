from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import DEFAULT_FOOTER_MARGIN, DEFAULT_HEADER_MARGIN
from app.models.schemas import CreateJobResponse, DiffResult, JobStatus
from app.services.jobs import job_store


router = APIRouter(prefix="/api")


@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(
    source_pdf: UploadFile = File(..., alias="sourcePdf"),
    modified_pdf: UploadFile = File(..., alias="modifiedPdf"),
    header_margin: float = Form(DEFAULT_HEADER_MARGIN, alias="headerMargin"),
    footer_margin: float = Form(DEFAULT_FOOTER_MARGIN, alias="footerMargin"),
    show_reflow: bool = Form(False, alias="showReflow"),
) -> CreateJobResponse:
    if source_pdf.content_type != "application/pdf" or modified_pdf.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Both uploads must be PDF files.")

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
