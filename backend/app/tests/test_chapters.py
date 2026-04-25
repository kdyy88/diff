from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

import fitz
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import (
    ChapterValidationRequest,
    ChapterValidationRequestItem,
    DiffAnchor,
    DiffResult,
    DiffSummary,
)
from app.services.chapters import (
    _normalize_bookmark_entries,
    ChapterBookmarksUnavailable,
    ChapterExecutionPair,
    aggregate_chapter_results,
    analyze_document_plan,
    validate_chapter_match,
)
from app.services.extractor import PageInfo


def test_normalize_bookmark_entries_prefers_dest_page_and_injects_front_matter() -> None:
    entries = [
        [1, 'Chapter 1', 5, {'page': 3}],
        [1, 'Chapter 1 duplicate', 7, {'page': 3}],
        [2, 'Ignored subsection', 8, {'page': 7}],
        [1, 'Chapter 2', 10, {'page': 8}],
    ]

    candidates = _normalize_bookmark_entries(entries, total_pages=12)

    assert [candidate.text for candidate in candidates] == ['Front Matter', 'Chapter 1', 'Chapter 2']
    assert [candidate.start_page for candidate in candidates] == [0, 3, 8]
    assert candidates[1].source == 'bookmark'


def test_normalize_bookmark_entries_drops_invalid_pages_without_front_matter() -> None:
    entries = [
        [1, 'Broken', 0, {'page': -1}],
        [1, 'Too far', 99, {'page': 30}],
    ]

    candidates = _normalize_bookmark_entries(entries, total_pages=10)

    assert candidates == []


def test_validate_chapter_match_reports_duplicates_and_unmatched_suggestion() -> None:
    payload = ChapterValidationRequest(
        source_chapters=[
            ChapterValidationRequestItem(id='s-1', title='Chapter 1 Intro', start_page=0),
            ChapterValidationRequestItem(id='s-2', title='Chapter 1 Intro', start_page=3),
        ],
        modified_chapters=[
            ChapterValidationRequestItem(id='m-1', title='Chapter 1 Intro', start_page=0),
            ChapterValidationRequestItem(id='m-2', title='Chapter 2 Overview', start_page=3),
        ],
    )

    validation = validate_chapter_match(payload, source_total_pages=6, modified_total_pages=6)

    assert validation.can_continue is False
    assert any(issue.code == 'duplicate_normalized_title' for issue in validation.issues)

    unmatched_payload = ChapterValidationRequest(
        source_chapters=[
            ChapterValidationRequestItem(id='s-1', title='Chapter 1 Intro', start_page=0),
            ChapterValidationRequestItem(id='s-2', title='Appendix A', start_page=3),
        ],
        modified_chapters=[
            ChapterValidationRequestItem(id='m-1', title='Chapter 1 Intro', start_page=0),
            ChapterValidationRequestItem(id='m-2', title='Appendix Alpha', start_page=3),
        ],
    )

    unmatched_validation = validate_chapter_match(unmatched_payload, source_total_pages=6, modified_total_pages=6)
    unmatched_issue = next(issue for issue in unmatched_validation.issues if issue.code == 'unmatched_chapter')

    assert unmatched_issue.peer_raw_title == 'Appendix Alpha'
    assert unmatched_issue.suggested_peer_score is not None


def test_aggregate_chapter_results_prefixes_anchor_ids_and_keeps_zero_anchor_chapters() -> None:
    pair_one = ChapterExecutionPair(
        chapter_id='source-chapter-0',
        chapter_title='Chapter 1',
        chapter_index=0,
        source_start_page=0,
        source_end_page=1,
        modified_start_page=0,
        modified_end_page=1,
    )
    pair_two = ChapterExecutionPair(
        chapter_id='source-chapter-1',
        chapter_title='Chapter 2',
        chapter_index=1,
        source_start_page=2,
        source_end_page=3,
        modified_start_page=2,
        modified_end_page=3,
    )

    chapter_one = DiffResult(
        pages_left=[],
        pages_right=[],
        summary=DiffSummary(insertions=1, deletions=0, replacements=1, reflows=0, pages_a=2, pages_b=2),
        anchors=[
            DiffAnchor(
                id='anchor-1',
                kind='replace',
                excerpt_left='alpha',
                excerpt_right='beta',
            )
        ],
        chapters=[],
    )
    chapter_two = DiffResult(
        pages_left=[],
        pages_right=[],
        summary=DiffSummary(insertions=0, deletions=0, replacements=0, reflows=0, pages_a=2, pages_b=2),
        anchors=[],
        chapters=[],
    )

    aggregated = aggregate_chapter_results(
        [(pair_one, chapter_one), (pair_two, chapter_two)],
        source_pages=[PageInfo(page=index, width=600, height=800) for index in range(4)],
        modified_pages=[PageInfo(page=index, width=600, height=800) for index in range(4)],
    )

    assert aggregated.anchors[0].id == 'source-chapter-0:anchor-1'
    assert aggregated.anchors[0].chapter_title == 'Chapter 1'
    assert aggregated.summary.insertions == 1
    assert aggregated.summary.replacements == 1
    assert [chapter.anchor_count for chapter in aggregated.chapters] == [1, 0]


def test_analyze_document_plan_requires_bookmarks() -> None:
    with tempfile.TemporaryDirectory(prefix='chapter-plan-tests-') as temp_dir_name:
        temp_dir = Path(temp_dir_name)

        bookmark_path = temp_dir / 'bookmark.pdf'
        bookmark_doc = fitz.open()
        for _ in range(4):
            bookmark_doc.new_page()
        bookmark_doc.set_toc([[1, 'Chapter 1', 2], [1, 'Chapter 2', 4]])
        bookmark_doc.save(bookmark_path)
        bookmark_doc.close()

        bookmark_plan = asyncio.run(analyze_document_plan(bookmark_path, 'source'))
        assert bookmark_plan.chapters[0].title == 'Front Matter'
        assert bookmark_plan.chapters[1].source == 'bookmark'

        fallback_path = temp_dir / 'fallback.pdf'
        fallback_doc = fitz.open()
        fallback_page = fallback_doc.new_page()
        fallback_page.insert_text((72, 72), 'plain body text only', fontsize=11)
        fallback_doc.save(fallback_path)
        fallback_doc.close()

        try:
            asyncio.run(analyze_document_plan(fallback_path, 'source'))
        except ChapterBookmarksUnavailable as exc:
            assert 'No usable level-1 PDF bookmarks' in str(exc)
        else:
            raise AssertionError('Expected chapter analysis to require standard bookmarks.')


def test_feature_flag_off_keeps_jobs_route_available_and_hides_chapter_routes(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr('app.api.routes.PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT', False)

    source_doc = fitz.open()
    source_doc.new_page().insert_text((72, 72), 'source')
    source_bytes = source_doc.tobytes()
    source_doc.close()

    modified_doc = fitz.open()
    modified_doc.new_page().insert_text((72, 72), 'modified')
    modified_bytes = modified_doc.tobytes()
    modified_doc.close()

    job_response = client.post(
        '/api/jobs',
        files={
            'sourcePdf': ('source.pdf', source_bytes, 'application/pdf'),
            'modifiedPdf': ('modified.pdf', modified_bytes, 'application/pdf'),
        },
    )
    chapter_response = client.post(
        '/api/chapter-analyses',
        files={
            'sourcePdf': ('source.pdf', source_bytes, 'application/pdf'),
            'modifiedPdf': ('modified.pdf', modified_bytes, 'application/pdf'),
        },
    )

    assert job_response.status_code == 200
    assert chapter_response.status_code == 404


def test_jobs_route_rejects_large_uploads(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr('app.api.routes.MAX_PDF_UPLOAD_BYTES', 10)

    response = client.post(
        '/api/jobs',
        files={
            'sourcePdf': ('source.pdf', b'%PDF-1.4 large source', 'application/pdf'),
            'modifiedPdf': ('modified.pdf', b'%PDF-1.4 modified', 'application/pdf'),
        },
    )

    assert response.status_code == 413
    assert response.json()['detail'] == 'PDF uploads must be smaller than 0 MB.'