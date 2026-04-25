from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import TERMINAL_RECORD_TTL_SECONDS, ensure_default_temp_dir, ensure_markdown_output_dir
from app.models.schemas import CreateJobResponse, DiffResult, JobStatus
from app.services.chapters import ChapterExecutionPair, aggregate_chapter_results
from app.services.differ import compare_documents
from app.services.extractor import extract_document, extract_page_infos
from app.services.markdown_bundle import build_markdown_bundle


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
    chapter_pairs: list[ChapterExecutionPair] | None = None
    error: str | None = None
    result: DiffResult | None = None
    markdown_dir: Path | None = None
    created_at: float = 0.0
    last_accessed_at: float = 0.0


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, _JobRecord] = {}

    def _cleanup_expired_jobs(self) -> None:
        now = monotonic()
        expired_job_ids = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in {"done", "failed"} and now - job.last_accessed_at > TERMINAL_RECORD_TTL_SECONDS
        ]
        for job_id in expired_job_ids:
            job = self._jobs.pop(job_id)
            shutil.rmtree(job.source_path.parent, ignore_errors=True)

    def _touch_job(self, job: _JobRecord) -> None:
        job.last_accessed_at = monotonic()

    async def create_job(
        self,
        source_pdf: UploadFile,
        modified_pdf: UploadFile,
        *,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
    ) -> CreateJobResponse:
        job_id, source_path, modified_path = self._prepare_job_paths()
        await self._write_upload(source_pdf, source_path)
        await self._write_upload(modified_pdf, modified_path)
        return self._register_job(
            job_id,
            source_path,
            modified_path,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=include_reflow,
            chapter_pairs=None,
        )

    async def create_job_from_paths(
        self,
        source_path: str | Path,
        modified_path: str | Path,
        *,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
        chapter_pairs: list[ChapterExecutionPair],
    ) -> CreateJobResponse:
        job_id, next_source_path, next_modified_path = self._prepare_job_paths()
        shutil.copyfile(source_path, next_source_path)
        shutil.copyfile(modified_path, next_modified_path)
        return self._register_job(
            job_id,
            next_source_path,
            next_modified_path,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=include_reflow,
            chapter_pairs=chapter_pairs,
        )

    def _prepare_job_paths(self) -> tuple[str, Path, Path]:
        job_id = f"job-{uuid4().hex}"
        job_dir = Path(mkdtemp(prefix=f"{job_id}-", dir=ensure_default_temp_dir()))
        return job_id, job_dir / "source.pdf", job_dir / "modified.pdf"

    def _register_job(
        self,
        job_id: str,
        source_path: Path,
        modified_path: Path,
        *,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
        chapter_pairs: list[ChapterExecutionPair] | None,
    ) -> CreateJobResponse:
        self._cleanup_expired_jobs()
        timestamp = monotonic()
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
            chapter_pairs=chapter_pairs,
            created_at=timestamp,
            last_accessed_at=timestamp,
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
                if job.chapter_pairs:
                    return self._run_chapter_pipeline(job)

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
            job.markdown_dir = await asyncio.to_thread(self._persist_markdown_bundle, job.id, result)
            job.result = result
            job.progress = 100
            job.status = "done"
            job.stage = "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.stage = "failed"
            job.progress = 100
            job.error = str(exc)

    def _persist_markdown_bundle(self, job_id: str, result: DiffResult) -> Path:
        output_dir = ensure_markdown_output_dir() / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        bundle = build_markdown_bundle(job_id, result)
        for bundle_file in bundle.files:
            (output_dir / bundle_file.name).write_text(bundle_file.content, encoding="utf-8")

        return output_dir

    def _run_chapter_pipeline(self, job: _JobRecord) -> DiffResult:
        assert job.chapter_pairs is not None

        source_pages = extract_page_infos(job.source_path)
        modified_pages = extract_page_infos(job.modified_path)
        chapter_results: list[tuple[ChapterExecutionPair, DiffResult]] = []
        total_pairs = len(job.chapter_pairs)

        for index, pair in enumerate(job.chapter_pairs, start=1):
            job.status = "extracting"
            job.stage = f"extracting chapter {index}/{total_pairs}"
            job.progress = 10 + int(((index - 1) / total_pairs) * 70)
            source_projection = extract_document(
                job.source_path,
                header_margin=job.header_margin,
                footer_margin=job.footer_margin,
                page_range=(pair.source_start_page, pair.source_end_page),
            )
            modified_projection = extract_document(
                job.modified_path,
                header_margin=job.header_margin,
                footer_margin=job.footer_margin,
                page_range=(pair.modified_start_page, pair.modified_end_page),
            )

            job.status = "aligning"
            job.stage = f"aligning chapter {index}/{total_pairs}"
            job.progress = 10 + int(((index - 0.35) / total_pairs) * 70)
            diff_result = compare_documents(
                source_projection,
                modified_projection,
                include_reflow=job.include_reflow,
            )

            job.status = "projecting"
            job.stage = f"projecting chapter {index}/{total_pairs}"
            job.progress = 10 + int(((index - 0.1) / total_pairs) * 70)
            chapter_results.append((pair, diff_result))

        return aggregate_chapter_results(
            chapter_results,
            source_pages=source_pages,
            modified_pages=modified_pages,
        )

    def get_status(self, job_id: str) -> JobStatus:
        self._cleanup_expired_jobs()
        job = self._jobs[job_id]
        self._touch_job(job)
        return JobStatus(
            id=job.id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            error=job.error,
            summary=job.result.summary if job.result else None,
        )

    def get_result(self, job_id: str) -> DiffResult:
        self._cleanup_expired_jobs()
        job = self._jobs[job_id]
        self._touch_job(job)
        if not job.result:
            raise ValueError("Result is not ready yet.")
        return job.result

    def get_file(self, job_id: str, side: str) -> Path:
        self._cleanup_expired_jobs()
        job = self._jobs[job_id]
        self._touch_job(job)
        if side == "source":
            return job.source_path
        if side == "modified":
            return job.modified_path
        raise ValueError(f"Unsupported file side: {side}")


job_store = JobStore()