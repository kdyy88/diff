from __future__ import annotations

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp
from time import monotonic
import unicodedata
from uuid import uuid4

import fitz

from app.core.config import TERMINAL_RECORD_TTL_SECONDS, ensure_default_temp_dir
from app.models.schemas import (
    ChapterAnalysisResult,
    ChapterAnalysisStatus,
    ChapterDraft,
    ChapterValidationIssue,
    ChapterValidationRequest,
    ChapterValidationRequestItem,
    ChapterValidationResult,
    CreateJobResponse,
    DiffAnchor,
    DiffResult,
    DiffSummary,
    DocumentChapterPlan,
)
from app.services.docx_outline import is_heading_one_block, load_docx_blocks
from app.services.document_kind import UploadedDocument
from app.services.extractor import PageInfo


CHAPTER_NUMBERING_RE = re.compile(
    r"^(?:chapter\s+\d+[\w.-]*|第[一二三四五六七八九十百千万0-9]+章|\d+(?:\.\d+)*)(?:[\s:：\-.、]+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _ChapterCandidate:
    text: str
    start_page: int
    source: str
    confidence: str


@dataclass(slots=True)
class ChapterExecutionPair:
    chapter_id: str
    chapter_title: str
    chapter_index: int
    source_start_page: int
    source_end_page: int
    modified_start_page: int
    modified_end_page: int


@dataclass(slots=True)
class _AnalysisRecord:
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
    error: str | None = None
    result: ChapterAnalysisResult | None = None
    created_at: float = 0.0
    last_accessed_at: float = 0.0


class ChapterValidationConflict(Exception):
    def __init__(self, validation: ChapterValidationResult) -> None:
        super().__init__("Chapter validation failed.")
        self.validation = validation


class ChapterBookmarksUnavailable(Exception):
    pass


def normalize_chapter_title(title: str) -> str:
    lowered = CHAPTER_NUMBERING_RE.sub("", title.strip().lower())
    compact = " ".join(lowered.split())
    stripped = "".join(
        character
        for character in compact
        if not unicodedata.category(character).startswith("P")
    )
    return " ".join(stripped.split())


def _make_chapter_id(side: str, index: int) -> str:
    return f"{side}-chapter-{index}"


def _build_plan_from_candidates(
    side: str,
    total_pages: int,
    candidates: list[_ChapterCandidate],
) -> DocumentChapterPlan:
    chapters: list[ChapterDraft] = []
    ordered = sorted(candidates, key=lambda item: (item.start_page, item.text.lower()))
    for index, candidate in enumerate(ordered):
        end_page = ordered[index + 1].start_page - 1 if index + 1 < len(ordered) else total_pages - 1
        title = candidate.text.strip() or f"Chapter {index + 1}"
        chapters.append(
            ChapterDraft(
                id=_make_chapter_id(side, index),
                title=title,
                normalized_title=normalize_chapter_title(title),
                start_page=candidate.start_page,
                end_page=end_page,
                source=candidate.source,
                confidence=candidate.confidence,
            )
        )
    return DocumentChapterPlan(side=side, total_pages=total_pages, chapters=chapters)


def _normalize_bookmark_entries(raw_toc: list[list[object]], total_pages: int) -> list[_ChapterCandidate]:
    candidates: list[_ChapterCandidate] = []
    for entry in raw_toc:
        if len(entry) < 3:
            continue
        level = entry[0]
        title = str(entry[1]).strip()
        page = entry[2]
        dest = entry[3] if len(entry) > 3 else None
        if level != 1 or not title:
            continue

        start_page: int | None = None
        if isinstance(dest, dict) and isinstance(dest.get("page"), int):
            start_page = int(dest["page"])
        elif isinstance(page, int):
            start_page = page - 1
        elif isinstance(page, float):
            start_page = int(page) - 1

        if start_page is None or start_page < 0 or start_page >= total_pages:
            continue
        candidates.append(
            _ChapterCandidate(
                text=title,
                start_page=start_page,
                source="bookmark",
                confidence="high",
            )
        )

    deduped: list[_ChapterCandidate] = []
    seen_pages: set[int] = set()
    for candidate in sorted(candidates, key=lambda item: (item.start_page, item.text.lower())):
        if candidate.start_page in seen_pages:
            continue
        seen_pages.add(candidate.start_page)
        deduped.append(candidate)

    if deduped and 0 < deduped[0].start_page < total_pages:
        deduped.insert(
            0,
            _ChapterCandidate(
                text="Front Matter",
                start_page=0,
                source="synthetic",
                confidence="medium",
            ),
        )
    return deduped


def analyze_bookmark_plan(pdf_path: str | Path, side: str) -> DocumentChapterPlan | None:
    path = Path(pdf_path)
    with fitz.open(path) as document:
        total_pages = len(document)
        candidates = _normalize_bookmark_entries(document.get_toc(simple=False), total_pages)
    if not candidates:
        return None
    return _build_plan_from_candidates(side, total_pages, candidates)


def analyze_docx_heading_plan(docx_path: str | Path, side: str) -> DocumentChapterPlan | None:
    blocks = load_docx_blocks(docx_path)
    total_blocks = len(blocks)
    if total_blocks == 0:
        return None

    candidates = [
        _ChapterCandidate(
            text=block.text,
            start_page=block.index,
            source="heading",
            confidence="high",
        )
        for block in blocks
        if is_heading_one_block(block) and block.text
    ]

    if not candidates:
        return None

    if candidates[0].start_page > 0:
        candidates.insert(
            0,
            _ChapterCandidate(
                text="Front Matter",
                start_page=0,
                source="synthetic",
                confidence="medium",
            ),
        )
    return _build_plan_from_candidates(side, total_blocks, candidates)


async def analyze_document_plan(
    document_path: str | Path,
    side: str,
    *,
    document_kind: str = "pdf",
) -> DocumentChapterPlan:
    if document_kind == "pdf":
        bookmark_plan = analyze_bookmark_plan(document_path, side)
        if bookmark_plan is not None:
            return bookmark_plan
        raise ChapterBookmarksUnavailable(
            f"No usable level-1 PDF bookmarks were found in the {side} document. "
            "Continuing with the regular full-document diff instead."
        )

    if document_kind == "docx":
        heading_plan = analyze_docx_heading_plan(document_path, side)
        if heading_plan is not None:
            return heading_plan
        raise ChapterBookmarksUnavailable(
            f"No usable Heading 1 outline was found in the {side} DOCX document. "
            "Continuing with the regular full-document diff instead."
        )

    raise ValueError(f"Unsupported document kind for chapter analysis: {document_kind}")


def _hydrate_confirmed_plan(
    *,
    side: str,
    total_pages: int,
    request_items: list[ChapterValidationRequestItem],
) -> tuple[DocumentChapterPlan, list[ChapterValidationIssue]]:
    issues: list[ChapterValidationIssue] = []
    chapters: list[ChapterDraft] = []
    previous_start: int | None = None

    for index, item in enumerate(request_items):
        title = item.title.strip() or f"Chapter {index + 1}"
        normalized_title = normalize_chapter_title(title)
        start_page = item.start_page
        if start_page < 0 or start_page >= total_pages:
            issues.append(
                ChapterValidationIssue(
                    code="invalid_page",
                    side=side,
                    chapter_id=item.id,
                    message=f"Start page must be within 1 and {total_pages}.",
                    raw_title=title,
                    normalized_title=normalized_title,
                )
            )
        if previous_start is not None and start_page <= previous_start:
            issues.append(
                ChapterValidationIssue(
                    code="non_increasing_start",
                    side=side,
                    chapter_id=item.id,
                    message="Start pages must be strictly increasing.",
                    raw_title=title,
                    normalized_title=normalized_title,
                )
            )
        chapters.append(
            ChapterDraft(
                id=item.id,
                title=title,
                normalized_title=normalized_title,
                start_page=start_page,
                end_page=start_page,
                source="manual",
                confidence="low",
            )
        )
        previous_start = start_page

    if chapters:
        if chapters[0].start_page != 0:
            issues.append(
                ChapterValidationIssue(
                    code="coverage_gap",
                    side=side,
                    chapter_id=chapters[0].id,
                    message="Chapters must start on page 1 and cover the full document without gaps.",
                    raw_title=chapters[0].title,
                    normalized_title=chapters[0].normalized_title,
                )
            )
        for index, chapter in enumerate(chapters):
            next_start = chapters[index + 1].start_page if index + 1 < len(chapters) else total_pages
            chapter.end_page = next_start - 1
            if chapter.end_page < chapter.start_page:
                issues.append(
                    ChapterValidationIssue(
                        code="coverage_gap",
                        side=side,
                        chapter_id=chapter.id,
                        message="Chapters must cover the full document without gaps or overlaps.",
                        raw_title=chapter.title,
                        normalized_title=chapter.normalized_title,
                    )
                )
        if chapters[-1].end_page != total_pages - 1:
            issues.append(
                ChapterValidationIssue(
                    code="coverage_gap",
                    side=side,
                    chapter_id=chapters[-1].id,
                    message="Chapters must end on the final page of the document.",
                    raw_title=chapters[-1].title,
                    normalized_title=chapters[-1].normalized_title,
                )
            )

    return DocumentChapterPlan(side=side, total_pages=total_pages, chapters=chapters), issues


def _add_duplicate_title_issues(plan: DocumentChapterPlan, issues: list[ChapterValidationIssue]) -> None:
    titles: dict[str, list[ChapterDraft]] = {}
    for chapter in plan.chapters:
        titles.setdefault(chapter.normalized_title, []).append(chapter)
    for normalized_title, chapters in titles.items():
        if not normalized_title or len(chapters) < 2:
            continue
        for chapter in chapters:
            issues.append(
                ChapterValidationIssue(
                    code="duplicate_normalized_title",
                    side=plan.side,
                    chapter_id=chapter.id,
                    message="Normalized chapter titles must be unique on each side.",
                    raw_title=chapter.title,
                    normalized_title=normalized_title,
                )
            )


def _closest_peer(chapter: ChapterDraft, peers: list[ChapterDraft]) -> tuple[ChapterDraft | None, float | None]:
    if not peers:
        return None, None
    best_peer: ChapterDraft | None = None
    best_score = -1.0
    for peer in peers:
        score = SequenceMatcher(None, chapter.normalized_title, peer.normalized_title).ratio()
        if score > best_score:
            best_score = score
            best_peer = peer
    return best_peer, round(best_score, 3) if best_peer is not None else None


def validate_chapter_match(
    payload: ChapterValidationRequest,
    *,
    source_total_pages: int,
    modified_total_pages: int,
) -> ChapterValidationResult:
    source_plan, issues = _hydrate_confirmed_plan(
        side="source",
        total_pages=source_total_pages,
        request_items=payload.source_chapters,
    )
    modified_plan, modified_issues = _hydrate_confirmed_plan(
        side="modified",
        total_pages=modified_total_pages,
        request_items=payload.modified_chapters,
    )
    issues.extend(modified_issues)

    _add_duplicate_title_issues(source_plan, issues)
    _add_duplicate_title_issues(modified_plan, issues)

    if not issues:
        source_titles = {chapter.normalized_title: chapter for chapter in source_plan.chapters}
        modified_titles = {chapter.normalized_title: chapter for chapter in modified_plan.chapters}

        for chapter in source_plan.chapters:
            if chapter.normalized_title in modified_titles:
                continue
            peer, score = _closest_peer(chapter, modified_plan.chapters)
            issues.append(
                ChapterValidationIssue(
                    code="unmatched_chapter",
                    side="source",
                    chapter_id=chapter.id,
                    message="No exact chapter title match was found on the modified side.",
                    raw_title=chapter.title,
                    normalized_title=chapter.normalized_title,
                    peer_chapter_id=peer.id if peer else None,
                    peer_raw_title=peer.title if peer else None,
                    peer_normalized_title=peer.normalized_title if peer else None,
                    suggested_peer_score=score,
                )
            )
        for chapter in modified_plan.chapters:
            if chapter.normalized_title in source_titles:
                continue
            peer, score = _closest_peer(chapter, source_plan.chapters)
            issues.append(
                ChapterValidationIssue(
                    code="unmatched_chapter",
                    side="modified",
                    chapter_id=chapter.id,
                    message="No exact chapter title match was found on the source side.",
                    raw_title=chapter.title,
                    normalized_title=chapter.normalized_title,
                    peer_chapter_id=peer.id if peer else None,
                    peer_raw_title=peer.title if peer else None,
                    peer_normalized_title=peer.normalized_title if peer else None,
                    suggested_peer_score=score,
                )
            )

    return ChapterValidationResult(
        can_continue=not issues,
        issues=issues,
        source_plan=source_plan,
        modified_plan=modified_plan,
    )


def build_execution_pairs(validation: ChapterValidationResult) -> list[ChapterExecutionPair]:
    modified_by_title = {chapter.normalized_title: chapter for chapter in validation.modified_plan.chapters}
    return [
        ChapterExecutionPair(
            chapter_id=chapter.id,
            chapter_title=chapter.title,
            chapter_index=index,
            source_start_page=chapter.start_page,
            source_end_page=chapter.end_page,
            modified_start_page=modified_by_title[chapter.normalized_title].start_page,
            modified_end_page=modified_by_title[chapter.normalized_title].end_page,
        )
        for index, chapter in enumerate(validation.source_plan.chapters)
    ]


def aggregate_chapter_results(
    chapter_results: list[tuple[ChapterExecutionPair, DiffResult]],
    *,
    source_pages: list[PageInfo],
    modified_pages: list[PageInfo],
) -> DiffResult:
    anchors: list[DiffAnchor] = []
    chapters: list[dict[str, object]] = []
    summary = DiffSummary(pages_a=len(source_pages), pages_b=len(modified_pages))

    for pair, result in chapter_results:
        first_anchor_id: str | None = None
        for anchor in result.anchors:
            next_id = f"{pair.chapter_id}:{anchor.id}"
            if first_anchor_id is None:
                first_anchor_id = next_id
            anchors.append(
                anchor.model_copy(
                    update={
                        "id": next_id,
                        "chapter_id": pair.chapter_id,
                        "chapter_title": pair.chapter_title,
                        "chapter_index": pair.chapter_index,
                    }
                )
            )

        summary.insertions += result.summary.insertions
        summary.deletions += result.summary.deletions
        summary.replacements += result.summary.replacements
        summary.reflows += result.summary.reflows
        chapters.append(
            {
                "id": pair.chapter_id,
                "title": pair.chapter_title,
                "index": pair.chapter_index,
                "anchor_count": len(result.anchors),
                "first_anchor_id": first_anchor_id,
                "summary": result.summary,
            }
        )

    return DiffResult(
        pages_left=[
            {"page": page.page, "width": page.width, "height": page.height}
            for page in source_pages
        ],
        pages_right=[
            {"page": page.page, "width": page.width, "height": page.height}
            for page in modified_pages
        ],
        summary=summary,
        anchors=anchors,
        chapters=chapters,
    )


class ChapterAnalysisStore:
    def __init__(self) -> None:
        self._analyses: dict[str, _AnalysisRecord] = {}

    def _cleanup_expired_analyses(self) -> None:
        now = monotonic()
        expired_analysis_ids = [
            analysis_id
            for analysis_id, record in self._analyses.items()
            if record.status in {"done", "failed", "fallback"}
            and now - record.last_accessed_at > TERMINAL_RECORD_TTL_SECONDS
        ]
        for analysis_id in expired_analysis_ids:
            record = self._analyses.pop(analysis_id)
            shutil.rmtree(record.source_path.parent, ignore_errors=True)

    def _touch_analysis(self, record: _AnalysisRecord) -> None:
        record.last_accessed_at = monotonic()

    async def create_analysis(
        self,
        source_document: UploadedDocument,
        modified_document: UploadedDocument,
        *,
        header_margin: float,
        footer_margin: float,
        include_reflow: bool,
    ) -> ChapterAnalysisStatus:
        self._cleanup_expired_analyses()
        analysis_id, source_path, modified_path = self._prepare_analysis_paths(source_document, modified_document)
        self._write_upload(source_document.content, source_path)
        self._write_upload(modified_document.content, modified_path)
        timestamp = monotonic()

        record = _AnalysisRecord(
            id=analysis_id,
            status="uploaded",
            stage="uploaded",
            progress=0,
            document_kind=source_document.kind,
            header_margin=header_margin,
            footer_margin=footer_margin,
            include_reflow=include_reflow,
            source_path=source_path,
            modified_path=modified_path,
            source_filename=source_document.filename,
            modified_filename=modified_document.filename,
            source_media_type=source_document.media_type,
            modified_media_type=modified_document.media_type,
            created_at=timestamp,
            last_accessed_at=timestamp,
        )
        self._analyses[analysis_id] = record
        asyncio.create_task(self._run_analysis(analysis_id))
        return ChapterAnalysisStatus(
            id=analysis_id,
            document_kind=source_document.kind,
            status="uploaded",
            stage="uploaded",
            progress=0,
            error=None,
        )

    def _prepare_analysis_paths(
        self,
        source_document: UploadedDocument,
        modified_document: UploadedDocument,
    ) -> tuple[str, Path, Path]:
        analysis_id = f"analysis-{uuid4().hex}"
        analysis_dir = Path(mkdtemp(prefix=f"{analysis_id}-", dir=ensure_default_temp_dir()))
        return (
            analysis_id,
            analysis_dir / f"source{source_document.suffix}",
            analysis_dir / f"modified{modified_document.suffix}",
        )

    def _write_upload(self, content: bytes, path: Path) -> None:
        path.write_bytes(content)

    async def _run_analysis(self, analysis_id: str) -> None:
        record = self._analyses[analysis_id]
        try:
            record.status = "analyzing"
            record.stage = "analyzing source chapters"
            record.progress = 10
            source_plan = await analyze_document_plan(
                record.source_path,
                "source",
                document_kind=record.document_kind,
            )

            record.stage = "analyzing modified chapters"
            record.progress = 55
            modified_plan = await analyze_document_plan(
                record.modified_path,
                "modified",
                document_kind=record.document_kind,
            )

            record.result = ChapterAnalysisResult(
                id=record.id,
                document_kind=record.document_kind,
                status="done",
                source_plan=source_plan,
                modified_plan=modified_plan,
            )
            record.status = "done"
            record.stage = "done"
            record.progress = 100
        except ChapterBookmarksUnavailable as exc:
            record.status = "fallback"
            record.stage = "falling back to full-document diff"
            record.progress = 100
            record.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.stage = "failed"
            record.progress = 100
            record.error = str(exc)

    def get_status(self, analysis_id: str) -> ChapterAnalysisStatus:
        self._cleanup_expired_analyses()
        record = self._analyses[analysis_id]
        self._touch_analysis(record)
        return ChapterAnalysisStatus(
            id=record.id,
            document_kind=record.document_kind,
            status=record.status,
            stage=record.stage,
            progress=record.progress,
            error=record.error,
        )

    def get_result(self, analysis_id: str) -> ChapterAnalysisResult:
        self._cleanup_expired_analyses()
        record = self._analyses[analysis_id]
        self._touch_analysis(record)
        if record.result is None:
            raise ValueError("Chapter analysis result is not ready yet.")
        return record.result

    def get_file(self, analysis_id: str, side: str) -> Path:
        self._cleanup_expired_analyses()
        record = self._analyses[analysis_id]
        self._touch_analysis(record)
        if side == "source":
            return record.source_path
        if side == "modified":
            return record.modified_path
        raise ValueError(f"Unsupported file side: {side}")

    def get_file_metadata(self, analysis_id: str, side: str) -> tuple[Path, str, str]:
        self._cleanup_expired_analyses()
        record = self._analyses[analysis_id]
        self._touch_analysis(record)
        if side == "source":
            return record.source_path, record.source_media_type, record.source_filename
        if side == "modified":
            return record.modified_path, record.modified_media_type, record.modified_filename
        raise ValueError(f"Unsupported file side: {side}")

    def get_review_html(self, analysis_id: str, side: str) -> str:
        self._cleanup_expired_analyses()
        record = self._analyses[analysis_id]
        self._touch_analysis(record)
        if record.document_kind != "docx":
            raise ValueError("Review HTML is only available for DOCX chapter analyses.")
        path = record.source_path if side == "source" else record.modified_path if side == "modified" else None
        if path is None:
            raise ValueError(f"Unsupported file side: {side}")
        projection = extract_document(path, header_margin=record.header_margin, footer_margin=record.footer_margin)
        return projection.review_html or ""

    def validate(self, analysis_id: str, payload: ChapterValidationRequest) -> ChapterValidationResult:
        result = self.get_result(analysis_id)
        return validate_chapter_match(
            payload,
            source_total_pages=result.source_plan.total_pages,
            modified_total_pages=result.modified_plan.total_pages,
        )

    async def confirm(self, analysis_id: str, payload: ChapterValidationRequest, create_job_from_paths) -> CreateJobResponse:
        validation = self.validate(analysis_id, payload)
        if not validation.can_continue:
            raise ChapterValidationConflict(validation)

        record = self._analyses[analysis_id]
        return await create_job_from_paths(
            record.source_path,
            record.modified_path,
            document_kind=record.document_kind,
            header_margin=record.header_margin,
            footer_margin=record.footer_margin,
            include_reflow=record.include_reflow,
            source_filename=record.source_filename,
            modified_filename=record.modified_filename,
            source_media_type=record.source_media_type,
            modified_media_type=record.modified_media_type,
            chapter_pairs=build_execution_pairs(validation),
        )


chapter_analysis_store = ChapterAnalysisStore()