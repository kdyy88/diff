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
    SectionStatus,
)
from app.services.docx_outline import is_heading_one_block, load_docx_blocks
from app.services.document_kind import UploadedDocument
from app.services.extractor import PageInfo


CHAPTER_NUMBERING_RE = re.compile(
    r"^(?:chapter\s+\d+[\w.-]*|第[一二三四五六七八九十百千万0-9]+章|[ivxlcdm]+|[a-z]|\d+(?:\.\d+)*)(?:[\s:：\-.、]+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _ChapterCandidate:
    text: str
    start_page: int
    start_y: float | None
    level: int
    path: list[str]
    raw_order: int
    source: str
    confidence: str


@dataclass(slots=True)
class ChapterExecutionPair:
    chapter_id: str
    chapter_title: str
    chapter_index: int
    chapter_level: int
    chapter_path: list[str]
    parent_id: str | None
    source_start_page: int | None
    source_end_page: int | None
    modified_start_page: int | None
    modified_end_page: int | None
    source_start_y: float | None = None
    source_end_y: float | None = None
    modified_start_y: float | None = None
    modified_end_y: float | None = None
    status: SectionStatus = "equal"
    confidence_reason: str | None = None


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


def _normalize_path(path: list[str]) -> list[str]:
    return [normalize_chapter_title(item) for item in path if normalize_chapter_title(item)]


def _make_chapter_id(side: str, index: int) -> str:
    return f"{side}-chapter-{index}"


def _build_plan_from_candidates(
    side: str,
    total_pages: int,
    candidates: list[_ChapterCandidate],
) -> DocumentChapterPlan:
    chapters: list[ChapterDraft] = []
    ordered = sorted(candidates, key=lambda item: (item.start_page, item.start_y or 0.0, item.raw_order))
    latest_id_by_level: dict[int, str] = {}
    for index, candidate in enumerate(ordered):
        next_candidate = ordered[index + 1] if index + 1 < len(ordered) else None
        if next_candidate is None:
            end_page = total_pages - 1
            end_y = None
        elif next_candidate.start_page == candidate.start_page:
            end_page = candidate.start_page
            end_y = next_candidate.start_y
        else:
            end_page = next_candidate.start_page - 1
            end_y = None
        title = candidate.text.strip() or f"Chapter {index + 1}"
        chapter_id = _make_chapter_id(side, index)
        parent_id = None
        if candidate.level > 1:
            for parent_level in range(candidate.level - 1, 0, -1):
                parent_id = latest_id_by_level.get(parent_level)
                if parent_id is not None:
                    break
        chapters.append(
            ChapterDraft(
                id=chapter_id,
                title=title,
                normalized_title=normalize_chapter_title(title),
                normalized_path=_normalize_path(candidate.path or [title]),
                start_page=candidate.start_page,
                end_page=end_page,
                start_y=candidate.start_y,
                end_y=end_y,
                level=candidate.level,
                parent_id=parent_id,
                path=candidate.path or [title],
                source=candidate.source,
                confidence=candidate.confidence,
            )
        )
        latest_id_by_level[candidate.level] = chapter_id
        for stale_level in [level for level in latest_id_by_level if level > candidate.level]:
            latest_id_by_level.pop(stale_level, None)
    return DocumentChapterPlan(side=side, total_pages=total_pages, chapters=chapters)


def _normalize_bookmark_entries(raw_toc: list[list[object]], total_pages: int) -> list[_ChapterCandidate]:
    candidates: list[_ChapterCandidate] = []
    current_path: list[str] = []
    for raw_order, entry in enumerate(raw_toc):
        if len(entry) < 3:
            continue
        level = entry[0]
        title = str(entry[1]).strip()
        page = entry[2]
        dest = entry[3] if len(entry) > 3 else None
        if not isinstance(level, int) or level < 1 or not title:
            continue
        if not normalize_chapter_title(title):
            continue

        start_page: int | None = None
        start_y: float | None = None
        if isinstance(dest, dict) and isinstance(dest.get("page"), (int, str)):
            try:
                start_page = int(dest["page"])
            except (TypeError, ValueError):
                start_page = None
            point = dest.get("to")
            if point is not None and hasattr(point, "y"):
                start_y = float(point.y)
        elif isinstance(page, int):
            start_page = page - 1
        elif isinstance(page, float):
            start_page = int(page) - 1

        if start_page is None or start_page < 0 or start_page >= total_pages:
            continue

        if level <= len(current_path):
            current_path = current_path[: level - 1]
        while len(current_path) < level - 1:
            current_path.append("")
        current_path.append(title)
        path = [part for part in current_path if part]

        candidates.append(
            _ChapterCandidate(
                text=title,
                start_page=start_page,
                start_y=start_y,
                level=level,
                path=path,
                raw_order=raw_order,
                source="bookmark",
                confidence="high",
            )
        )

    deduped: list[_ChapterCandidate] = []
    seen_positions: set[tuple[int, int, str]] = set()
    for candidate in sorted(candidates, key=lambda item: (item.start_page, item.start_y or 0.0, item.raw_order)):
        dedupe_key = (
            candidate.start_page,
            round(candidate.start_y or 0.0),
            "/".join(_normalize_path(candidate.path or [candidate.text])),
        )
        if dedupe_key in seen_positions:
            continue
        seen_positions.add(dedupe_key)
        deduped.append(candidate)

    if deduped and 0 < deduped[0].start_page < total_pages:
        deduped.insert(
            0,
            _ChapterCandidate(
                text="Front Matter",
                start_page=0,
                start_y=None,
                level=1,
                path=["Front Matter"],
                raw_order=-1,
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
            start_y=None,
            level=1,
            path=[block.text],
            raw_order=block.index,
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
                start_y=None,
                level=1,
                path=["Front Matter"],
                raw_order=-1,
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
    seed_plan: DocumentChapterPlan | None = None,
) -> tuple[DocumentChapterPlan, list[ChapterValidationIssue]]:
    issues: list[ChapterValidationIssue] = []
    chapters: list[ChapterDraft] = []
    previous_start: int | None = None
    previous_y: float | None = None
    seed_by_id = {chapter.id: chapter for chapter in (seed_plan.chapters if seed_plan else [])}

    for index, item in enumerate(request_items):
        title = item.title.strip() or f"Chapter {index + 1}"
        seed = seed_by_id.get(item.id)
        seed_title_changed = seed is not None and title != seed.title
        path = item.path or (seed.path if seed else []) or [title]
        if seed_title_changed and path:
            path = [*path[:-1], title]
        level = item.level or (seed.level if seed else 1)
        start_y = item.start_y if item.start_y is not None else (seed.start_y if seed else None)
        parent_id = item.parent_id if item.parent_id is not None else (seed.parent_id if seed else None)
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
        same_page_with_valid_position = (
            previous_start is not None
            and start_page == previous_start
            and (
                previous_y is None
                or start_y is None
                or start_y > previous_y
            )
        )
        if previous_start is not None and (start_page < previous_start or (start_page == previous_start and not same_page_with_valid_position)):
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
                normalized_path=_normalize_path(path),
                start_page=start_page,
                end_page=start_page,
                start_y=start_y,
                end_y=None,
                level=level,
                parent_id=parent_id,
                path=path,
                source="manual",
                confidence=seed.confidence if seed and not seed_title_changed and start_page == seed.start_page else "low",
            )
        )
        previous_start = start_page
        previous_y = start_y

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
            next_chapter = chapters[index + 1] if index + 1 < len(chapters) else None
            if next_chapter and next_chapter.start_page == chapter.start_page:
                chapter.end_page = chapter.start_page
                chapter.end_y = next_chapter.start_y
            else:
                chapter.end_page = next_start - 1
                chapter.end_y = None
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
        key = "/".join(chapter.normalized_path) if chapter.normalized_path else chapter.normalized_title
        titles.setdefault(key, []).append(chapter)
    for normalized_title, chapters in titles.items():
        if not normalized_title or len(chapters) < 2:
            continue
        positions = {(chapter.start_page, chapter.start_y) for chapter in chapters}
        severity = "error" if len(positions) < len(chapters) else "warning"
        for chapter in chapters:
            issues.append(
                ChapterValidationIssue(
                    code="duplicate_normalized_title",
                    severity=severity,
                    side=plan.side,
                    chapter_id=chapter.id,
                    message=(
                        "Duplicate chapter titles share the same location and must be disambiguated."
                        if severity == "error"
                        else "Duplicate chapter titles were disambiguated by outline order and page position."
                    ),
                    raw_title=chapter.title,
                    normalized_title=normalized_title,
                )
            )


def _match_key(chapter: ChapterDraft) -> str:
    return "/".join(chapter.normalized_path) if chapter.normalized_path else chapter.normalized_title


def _chapter_has_children(chapter: ChapterDraft, chapters: list[ChapterDraft]) -> bool:
    return any(candidate.parent_id == chapter.id for candidate in chapters)


def _group_chapters_by_match_key(chapters: list[ChapterDraft]) -> dict[str, list[ChapterDraft]]:
    grouped: dict[str, list[ChapterDraft]] = {}
    for chapter in sorted(chapters, key=lambda item: (item.start_page, item.start_y or 0.0, item.level, item.id)):
        grouped.setdefault(_match_key(chapter), []).append(chapter)
    return grouped


def _blocking_issues(issues: list[ChapterValidationIssue]) -> list[ChapterValidationIssue]:
    return [issue for issue in issues if issue.severity == "error"]


def _closest_peer(chapter: ChapterDraft, peers: list[ChapterDraft]) -> tuple[ChapterDraft | None, float | None]:
    if not peers:
        return None, None
    best_peer: ChapterDraft | None = None
    best_score = -1.0
    chapter_key = _match_key(chapter)
    for peer in peers:
        score = SequenceMatcher(None, chapter_key, _match_key(peer)).ratio()
        if score > best_score:
            best_score = score
            best_peer = peer
    return best_peer, round(best_score, 3) if best_peer is not None else None


def validate_chapter_match(
    payload: ChapterValidationRequest,
    *,
    source_total_pages: int,
    modified_total_pages: int,
    source_seed_plan: DocumentChapterPlan | None = None,
    modified_seed_plan: DocumentChapterPlan | None = None,
) -> ChapterValidationResult:
    source_plan, issues = _hydrate_confirmed_plan(
        side="source",
        total_pages=source_total_pages,
        request_items=payload.source_chapters,
        seed_plan=source_seed_plan,
    )
    modified_plan, modified_issues = _hydrate_confirmed_plan(
        side="modified",
        total_pages=modified_total_pages,
        request_items=payload.modified_chapters,
        seed_plan=modified_seed_plan,
    )
    issues.extend(modified_issues)

    _add_duplicate_title_issues(source_plan, issues)
    _add_duplicate_title_issues(modified_plan, issues)

    if not _blocking_issues(issues):
        source_groups = _group_chapters_by_match_key(source_plan.chapters)
        modified_groups = _group_chapters_by_match_key(modified_plan.chapters)
        matched_source_ids: set[str] = set()
        matched_modified_ids: set[str] = set()

        for key, source_group in source_groups.items():
            modified_group = modified_groups.get(key, [])
            for source_chapter, modified_chapter in zip(source_group, modified_group):
                matched_source_ids.add(source_chapter.id)
                matched_modified_ids.add(modified_chapter.id)

        for chapter in source_plan.chapters:
            if chapter.id in matched_source_ids:
                continue
            peer, score = _closest_peer(chapter, modified_plan.chapters)
            issues.append(
                ChapterValidationIssue(
                    code="unmatched_chapter",
                    severity="warning",
                    side="source",
                    chapter_id=chapter.id,
                    message="This section exists only on the source side and will be treated as deleted.",
                    raw_title=chapter.title,
                    normalized_title=chapter.normalized_title,
                    peer_chapter_id=peer.id if peer else None,
                    peer_raw_title=peer.title if peer else None,
                    peer_normalized_title=peer.normalized_title if peer else None,
                    suggested_peer_score=score,
                )
            )
        for chapter in modified_plan.chapters:
            if chapter.id in matched_modified_ids:
                continue
            peer, score = _closest_peer(chapter, source_plan.chapters)
            issues.append(
                ChapterValidationIssue(
                    code="unmatched_chapter",
                    severity="warning",
                    side="modified",
                    chapter_id=chapter.id,
                    message="This section exists only on the modified side and will be treated as inserted.",
                    raw_title=chapter.title,
                    normalized_title=chapter.normalized_title,
                    peer_chapter_id=peer.id if peer else None,
                    peer_raw_title=peer.title if peer else None,
                    peer_normalized_title=peer.normalized_title if peer else None,
                    suggested_peer_score=score,
                )
            )

    return ChapterValidationResult(
        can_continue=not _blocking_issues(issues),
        issues=issues,
        source_plan=source_plan,
        modified_plan=modified_plan,
    )


def build_execution_pairs(validation: ChapterValidationResult) -> list[ChapterExecutionPair]:
    modified_groups = _group_chapters_by_match_key(validation.modified_plan.chapters)
    consumed_modified_ids: set[str] = set()
    pairs: list[ChapterExecutionPair] = []

    for chapter in validation.source_plan.chapters:
        modified_candidates = [candidate for candidate in modified_groups.get(_match_key(chapter), []) if candidate.id not in consumed_modified_ids]
        modified_chapter = modified_candidates[0] if modified_candidates else None
        if modified_chapter is not None:
            consumed_modified_ids.add(modified_chapter.id)

        is_container = _chapter_has_children(chapter, validation.source_plan.chapters)
        pairs.append(
            ChapterExecutionPair(
                chapter_id=chapter.id,
                chapter_title=chapter.title,
                chapter_index=len(pairs),
                chapter_level=chapter.level,
                chapter_path=chapter.path,
                parent_id=chapter.parent_id,
                source_start_page=chapter.start_page,
                source_end_page=chapter.end_page,
                modified_start_page=modified_chapter.start_page if modified_chapter else None,
                modified_end_page=modified_chapter.end_page if modified_chapter else None,
                source_start_y=chapter.start_y,
                source_end_y=chapter.end_y,
                modified_start_y=modified_chapter.start_y if modified_chapter else None,
                modified_end_y=modified_chapter.end_y if modified_chapter else None,
                status="container" if is_container else "equal" if modified_chapter else "deleted",
            )
        )

    for chapter in validation.modified_plan.chapters:
        if chapter.id in consumed_modified_ids:
            continue
        pairs.append(
            ChapterExecutionPair(
                chapter_id=chapter.id,
                chapter_title=chapter.title,
                chapter_index=len(pairs),
                chapter_level=chapter.level,
                chapter_path=chapter.path,
                parent_id=chapter.parent_id,
                source_start_page=None,
                source_end_page=None,
                modified_start_page=chapter.start_page,
                modified_end_page=chapter.end_page,
                modified_start_y=chapter.start_y,
                modified_end_y=chapter.end_y,
                status="inserted",
            )
        )

    return pairs


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
                        "chapter_level": pair.chapter_level,
                        "chapter_path": pair.chapter_path,
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
                "status": pair.status,
                "level": pair.chapter_level,
                "parent_id": pair.parent_id,
                "path": pair.chapter_path,
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
            source_seed_plan=result.source_plan,
            modified_seed_plan=result.modified_plan,
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
