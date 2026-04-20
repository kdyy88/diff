# Architecture Notes

This document explains how the current MVP turns two PDFs into review-ready diff anchors.

## Pipeline

```text
PDF A / PDF B
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
- expose job status and results through an in-memory store

Current stage progression:

- `uploaded`
- `extracting`
- `aligning`
- `projecting`
- `done`
- `failed`

## Frontend modules

### `frontend/src/pages/UploadPage.tsx`

- upload two PDFs and create a job

### `frontend/src/pages/ProcessingPage.tsx`

- poll job progress and show status

### `frontend/src/pages/ReviewPage.tsx`

- render the dual-pane review workspace
- coordinate current anchor selection

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
- 2D projection
- review UI behavior

## Known limitations

- Reading order still depends on heuristic ordering of extracted lines.
- Borderless tables and heavily merged cells are not a solved problem in this MVP.
- Jobs are ephemeral and local-only.
- The service is optimized for prototype speed, not production durability.
