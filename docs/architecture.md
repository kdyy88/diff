# Architecture Notes

This document explains how the current MVP turns two PDFs into review-ready diff anchors.

## Pipeline

```text
PDF A / PDF B
    -> optional chapter analysis
    -> optional chapter confirmation
    -> extraction
    -> normalization
    -> line-anchor coarse alignment
    -> local fine diff
    -> review-anchor coalescing
    -> coordinate projection
    -> API result
```

## Backend modules

### `app/services/extractor.py`

Responsibilities:

- open PDFs with `PyMuPDF`
- collect page geometry
- extract text from `rawdict`
- build character and word atoms
- detect explicit-grid tables with `Page.find_tables()`
- split table regions out of the main paragraph flow
- optionally extract only a requested page range while preserving original page numbers

Important outputs:

- `DocumentProjection.pages`
- `DocumentProjection.chars`
- `DocumentProjection.words`
- `DocumentProjection.text_segments`
- `DocumentProjection.tables`
- `DocumentProjection.raw_text`
- `DocumentProjection.normalized_text`

### `app/services/normalizer.py`

Responsibilities:

- minimal Unicode cleanup
- whitespace normalization suitable for alignment
- preserve visible source text for later review excerpts

The design goal is to normalize only what helps alignment without hiding the true source text from reviewers.

### `app/services/differ.py`

Responsibilities:

- coarse-align extracted text lines with `patiencediff`
- run local `diff-match-patch` windows only inside non-equal coarse ranges
- merge raw low-level edits into review-friendly anchors
- suppress layout-only noise through optional `reflow` anchors
- compare explicit-grid table content independently from paragraph text

Important design choices:

- text anchors are review-oriented, not raw diff opcodes
- `equal` coarse windows act as hard re-anchoring points
- nearby insert/delete events may become one `replace`
- large risky text windows are still returned but marked `confidence="low"`
- table anchors and text anchors are not merged together

### `app/services/chapters.py`

Responsibilities:

- discover first-level chapter drafts from bookmarks with `PyMuPDF`
- inject synthetic `Front Matter` chapters when needed
- normalize chapter titles for deterministic exact matching
- validate edited chapter start pages and coverage
- surface structured validation issues and nearest-peer hints
- aggregate per-chapter diff results back into one `DiffResult`

Key constraints in V1:

- only first-level chapters are execution units
- bookmark detection is the only automatic split path in production mode
- chapter mode never exports sub-PDF files; it uses extractor page ranges instead

### `app/services/projector.py`

Responsibilities:

- convert logical character ranges back to page-level highlight fragments
- merge adjacent character boxes into compact rectangles
- deduplicate visually identical fragments

### `app/services/jobs.py`

Responsibilities:

- accept uploaded files
- persist them into a temporary workspace
- run extraction and diffing in a background task
- optionally iterate through confirmed chapter pairs while keeping the same job contract
- expose job status and results through an in-memory store

Current stage progression:

- `uploaded`
- `extracting`
- `aligning`
- `projecting`
- `done`
- `failed`

In chapter mode, `stage` keeps the same state machine but includes chapter progress, for example `extracting chapter 3/8`.

## Frontend modules

### `frontend/src/pages/UploadPage.tsx`

- upload two PDFs and create a job
- discover `GET /api/features` and optionally branch into chapter analysis

### `frontend/src/pages/ProcessingPage.tsx`

- poll job progress and show status

### `frontend/src/pages/ChapterProcessingPage.tsx`

- poll chapter-analysis progress before confirmation

### `frontend/src/pages/ChapterConfirmPage.tsx`

- edit chapter titles and starting pages with 1-based UI input
- keep the chapter review layout compact and open original PDFs only on demand in a modal
- combine local page-cover validation with backend title-matching validation

### `frontend/src/pages/ReviewPage.tsx`

- render the dual-pane review workspace
- coordinate current anchor selection
- filter the result by `All Chapters` or a specific chapter

### `frontend/src/components/PdfPane.tsx`

- render only nearby pages for performance
- overlay projected highlights

### `frontend/src/components/DiffList.tsx`

- display normalized review anchors as clickable cards

## Why the diff stack is split

The project does not try to write a new general-purpose diff engine. Instead it combines two mature libraries:

- `patiencediff` provides stable line-level coarse anchoring so repeated clauses do not drift across dozens of later pages after one early mismatch.
- `diff-match-patch` provides the local word and character precision needed for numbers, abbreviations, units, and short clause edits.

The value of this codebase is the surrounding document-specific machinery:

- 2D PDF extraction
- 1D logical text reconstruction
- line segmentation for coarse anchors
- table separation
- review-anchor normalization
- low-confidence marking instead of silent suppression
- chapter discovery and confirmation flow
- 2D projection
- review UI behavior

## Known limitations

- Reading order still depends on the current extracted-line sorting strategy.
- Borderless tables and heavily merged cells are not a solved problem in this MVP.
- Jobs are ephemeral and local-only.
- The service is optimized for prototype speed, not production durability.
