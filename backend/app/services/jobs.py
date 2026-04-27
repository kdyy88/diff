from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4

from app.core.config import TERMINAL_RECORD_TTL_SECONDS, ensure_default_temp_dir, ensure_markdown_output_dir
from app.models.schemas import CreateJobResponse, DiffResult, JobStatus
from app.services.chapters import ChapterExecutionPair, aggregate_chapter_results
from app.services.document_kind import UploadedDocument
from app.services.differ import compare_documents
from app.services.extractor import SectionWindow, extract_document, extract_page_infos, slice_document_projection
from app.services.markdown_bundle import build_markdown_bundle


@dataclass
class _JobRecord:
    id: str
    status: str
    stage: str
    progress: int
    document_kind: str
    header_margin: float
    footer_margin: float
    include_reflow: bool
    source_path: Path
    modified_path: Path
    source_filename: str
    modified_filename: str
    source_media_type: str
    modified_media_type: str
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
        source_document: UploadedDocument,
        modified_document: UploadedDocument,
        *,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
    ) -> CreateJobResponse:
        job_id, source_path, modified_path = self._prepare_job_paths(source_document, modified_document)
        self._write_upload(source_document.content, source_path)
        self._write_upload(modified_document.content, modified_path)
        return self._register_job(
            job_id,
            source_path,
            modified_path,
            document_kind=source_document.kind,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=include_reflow,
            source_filename=source_document.filename,
            modified_filename=modified_document.filename,
            source_media_type=source_document.media_type,
            modified_media_type=modified_document.media_type,
            chapter_pairs=None,
        )

    async def create_job_from_paths(
        self,
        source_path: str | Path,
        modified_path: str | Path,
        *,
        document_kind: str,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
        source_filename: str,
        modified_filename: str,
        source_media_type: str,
        modified_media_type: str,
        chapter_pairs: list[ChapterExecutionPair],
    ) -> CreateJobResponse:
        source_input_path = Path(source_path)
        modified_input_path = Path(modified_path)
        job_id, next_source_path, next_modified_path = self._prepare_job_paths_from_suffixes(
            source_input_path.suffix,
            modified_input_path.suffix,
        )
        shutil.copyfile(source_path, next_source_path)
        shutil.copyfile(modified_path, next_modified_path)
        return self._register_job(
            job_id,
            next_source_path,
            next_modified_path,
            document_kind=document_kind,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=include_reflow,
            source_filename=source_filename,
            modified_filename=modified_filename,
            source_media_type=source_media_type,
            modified_media_type=modified_media_type,
            chapter_pairs=chapter_pairs,
        )

    def _prepare_job_paths(
        self,
        source_document: UploadedDocument,
        modified_document: UploadedDocument,
    ) -> tuple[str, Path, Path]:
        return self._prepare_job_paths_from_suffixes(source_document.suffix, modified_document.suffix)

    def _prepare_job_paths_from_suffixes(self, source_suffix: str, modified_suffix: str) -> tuple[str, Path, Path]:
        job_id = f"job-{uuid4().hex}"
        job_dir = Path(mkdtemp(prefix=f"{job_id}-", dir=ensure_default_temp_dir()))
        return job_id, job_dir / f"source{source_suffix}", job_dir / f"modified{modified_suffix}"

    def _register_job(
        self,
        job_id: str,
        source_path: Path,
        modified_path: Path,
        *,
        document_kind: str,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
        source_filename: str,
        modified_filename: str,
        source_media_type: str,
        modified_media_type: str,
        chapter_pairs: list[ChapterExecutionPair] | None,
    ) -> CreateJobResponse:
        self._cleanup_expired_jobs()
        timestamp = monotonic()
        effective_reflow = include_reflow if document_kind == "pdf" else False
        job = _JobRecord(
            id=job_id,
            status="uploaded",
            stage="uploaded",
            progress=0,
            document_kind=document_kind,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=effective_reflow,
            source_path=source_path,
            modified_path=modified_path,
            source_filename=source_filename,
            modified_filename=modified_filename,
            source_media_type=source_media_type,
            modified_media_type=modified_media_type,
            chapter_pairs=chapter_pairs,
            created_at=timestamp,
            last_accessed_at=timestamp,
        )
        self._jobs[job_id] = job
        asyncio.create_task(self._run_job(job_id))
        return CreateJobResponse(id=job_id, document_kind=document_kind, status="uploaded")

    def _write_upload(self, content: bytes, path: Path) -> None:
        path.write_bytes(content)

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

        job = self._jobs.get(job_id)
        bundle = build_markdown_bundle(
            job_id,
            result,
            source_document_path=job.source_path if job else None,
            modified_document_path=job.modified_path if job else None,
        )
        for bundle_file in bundle.files:
            (output_dir / bundle_file.name).write_text(bundle_file.content, encoding="utf-8")

        return output_dir

    def _run_chapter_pipeline(self, job: _JobRecord) -> DiffResult:
        assert job.chapter_pairs is not None

        source_pages = extract_page_infos(job.source_path)
        modified_pages = extract_page_infos(job.modified_path)
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
        chapter_results: list[tuple[ChapterExecutionPair, DiffResult]] = []
        total_pairs = len(job.chapter_pairs)

        for index, pair in enumerate(job.chapter_pairs, start=1):
            job.status = "extracting"
            job.stage = f"extracting chapter {index}/{total_pairs}"
            job.progress = 10 + int(((index - 1) / total_pairs) * 70)
            source_window = slice_document_projection(
                source_projection,
                SectionWindow(
                    start_page=pair.source_start_page,
                    end_page=pair.source_end_page,
                    start_y=pair.source_start_y,
                    end_y=pair.source_end_y,
                ),
            )
            modified_window = slice_document_projection(
                modified_projection,
                SectionWindow(
                    start_page=pair.modified_start_page,
                    end_page=pair.modified_end_page,
                    start_y=pair.modified_start_y,
                    end_y=pair.modified_end_y,
                ),
            )

            job.status = "aligning"
            job.stage = f"aligning chapter {index}/{total_pairs}"
            job.progress = 10 + int(((index - 0.35) / total_pairs) * 70)
            diff_result = compare_documents(
                source_window,
                modified_window,
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
            document_kind=job.document_kind,
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

    def get_file_metadata(self, job_id: str, side: str) -> tuple[Path, str, str]:
        self._cleanup_expired_jobs()
        job = self._jobs[job_id]
        self._touch_job(job)
        if side == "source":
            return job.source_path, job.source_media_type, job.source_filename
        if side == "modified":
            return job.modified_path, job.modified_media_type, job.modified_filename
        raise ValueError(f"Unsupported file side: {side}")

    def get_review_html(self, job_id: str, side: str) -> str:
        self._cleanup_expired_jobs()
        job = self._jobs[job_id]
        self._touch_job(job)
        if job.document_kind != "docx":
            raise ValueError("Review HTML is only available for DOCX jobs.")
        path = job.source_path if side == "source" else job.modified_path if side == "modified" else None
        if path is None:
            raise ValueError(f"Unsupported file side: {side}")
        projection = extract_document(path, header_margin=job.header_margin, footer_margin=job.footer_margin)
        return projection.review_html or ""


job_store = JobStore()
