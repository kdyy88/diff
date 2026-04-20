from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import shutil
from tempfile import mkdtemp
from typing import BinaryIO
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import DEFAULT_TEMP_DIR
from app.models.schemas import CreateJobResponse, DiffResult, JobStatus
from app.services.differ import compare_documents
from app.services.extractor import extract_document


@dataclass
class _JobRecord:
    id: str
    status: str
    stage: str
    progress: int
    header_margin: float
    footer_margin: float
    include_reflow: bool
    source_path: Path
    modified_path: Path
    error: str | None = None
    result: DiffResult | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, _JobRecord] = {}

    async def create_job(
        self,
        source_pdf: UploadFile,
        modified_pdf: UploadFile,
        *,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
    ) -> CreateJobResponse:
        job_id = f"job-{uuid4().hex}"
        job_dir = Path(mkdtemp(prefix=f"{job_id}-", dir=DEFAULT_TEMP_DIR))
        source_path = job_dir / "source.pdf"
        modified_path = job_dir / "modified.pdf"
        await self._write_upload(source_pdf, source_path)
        await self._write_upload(modified_pdf, modified_path)

        job = _JobRecord(
            id=job_id,
            status="uploaded",
            stage="uploaded",
            progress=0,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=include_reflow,
            source_path=source_path,
            modified_path=modified_path,
        )
        self._jobs[job_id] = job
        asyncio.create_task(self._run_job(job_id))
        return CreateJobResponse(id=job_id, status="uploaded")

    async def _write_upload(self, upload: UploadFile, path: Path) -> None:
        content = await upload.read()
        path.write_bytes(content)
        await upload.close()

    async def _run_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        try:
            job.status = "extracting"
            job.stage = "extracting"
            job.progress = 10

            def run_pipeline() -> DiffResult:
                source_projection = extract_document(
                    job.source_path,
                    header_margin=job.header_margin,
                    footer_margin=job.footer_margin,
                )
                modified_projection = extract_document(
                    job.modified_path,
                    header_margin=job.header_margin,
                    footer_margin=job.footer_margin,
                )
                job.progress = 55
                job.status = "aligning"
                job.stage = "aligning"
                diff_result = compare_documents(
                    source_projection,
                    modified_projection,
                    include_reflow=job.include_reflow,
                )
                job.progress = 90
                job.status = "projecting"
                job.stage = "projecting"
                return diff_result

            result = await asyncio.to_thread(run_pipeline)
            job.result = result
            job.progress = 100
            job.status = "done"
            job.stage = "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.stage = "failed"
            job.progress = 100
            job.error = str(exc)

    def get_status(self, job_id: str) -> JobStatus:
        job = self._jobs[job_id]
        return JobStatus(
            id=job.id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            error=job.error,
            summary=job.result.summary if job.result else None,
        )

    def get_result(self, job_id: str) -> DiffResult:
        job = self._jobs[job_id]
        if not job.result:
            raise ValueError("Result is not ready yet.")
        return job.result

    def get_file(self, job_id: str, side: str) -> Path:
        job = self._jobs[job_id]
        if side == "source":
            return job.source_path
        if side == "modified":
            return job.modified_path
        raise ValueError(f"Unsupported file side: {side}")


job_store = JobStore()
