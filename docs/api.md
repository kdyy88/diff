# API Reference

This document describes the current HTTP contract exposed by the local FastAPI backend.

Base URL during development:

```text
http://localhost:8000
```

Interactive OpenAPI docs are also available at:

- `/docs`
- `/redoc`

## Overview

The backend uses a job-based workflow:

1. Upload two PDFs with `POST /api/jobs`.
2. Poll the job with `GET /api/jobs/{job_id}`.
3. Fetch review anchors with `GET /api/jobs/{job_id}/result`.
4. Render the uploaded PDF files with `GET /api/jobs/{job_id}/files/{side}`.

## `GET /health`

Simple health check.

Response:

```json
{
  "status": "ok"
}
```

## `POST /api/jobs`

Create a new diff job.

Content type:

```text
multipart/form-data
```

Form fields:

- `sourcePdf`: required, PDF file
- `modifiedPdf`: required, PDF file
- `headerMargin`: optional, float, default `50`
- `footerMargin`: optional, float, default `50`
- `showReflow`: optional, boolean, default `false`

Notes:

- Both uploaded files must report `application/pdf`.
- The current MVP stores files in a temporary directory and tracks jobs in process memory.

Example:

```bash
curl -X POST http://localhost:8000/api/jobs \
  -F sourcePdf=@./source.pdf \
  -F modifiedPdf=@./modified.pdf \
  -F headerMargin=50 \
  -F footerMargin=50 \
  -F showReflow=false
```

Success response:

```json
{
  "id": "job-7a6b8f9e10c24fd08b7fd5c9d7aa1f5f",
  "status": "uploaded"
}
```

Error response:

```json
{
  "detail": "Both uploads must be PDF files."
}
```

## `GET /api/jobs/{job_id}`

Poll job state and summary progress.

Response shape:

```json
{
  "id": "job-7a6b8f9e10c24fd08b7fd5c9d7aa1f5f",
  "status": "aligning",
  "stage": "aligning",
  "progress": 55,
  "error": null,
  "summary": null
}
```

`status` values:

- `uploaded`
- `extracting`
- `aligning`
- `projecting`
- `done`
- `failed`

Behavior notes:

- `summary` is `null` until the job finishes successfully.
- On failure, `error` contains the exception message captured from the pipeline.

Example error:

```json
{
  "detail": "Job not found."
}
```

## `GET /api/jobs/{job_id}/result`

Fetch the completed diff result.

Response shape:

```json
{
  "pages_left": [
    { "page": 0, "width": 595.28, "height": 841.89 }
  ],
  "pages_right": [
    { "page": 0, "width": 595.28, "height": 841.89 }
  ],
  "summary": {
    "insertions": 1,
    "deletions": 0,
    "replacements": 2,
    "reflows": 0,
    "pages_a": 12,
    "pages_b": 12
  },
  "anchors": [
    {
      "id": "anchor-text-0",
      "kind": "replace",
      "source_type": "text",
      "excerpt_left": "Dose 0.5 mg",
      "excerpt_right": "Dose 0.05 mg",
      "left_fragments": [
        {
          "page": 0,
          "bbox": [72.0, 88.1, 142.4, 102.3],
          "viewport_ref": "page-0-fragment-0"
        }
      ],
      "right_fragments": [
        {
          "page": 0,
          "bbox": [72.0, 88.1, 146.0, 102.3],
          "viewport_ref": "page-0-fragment-0"
        }
      ],
      "left_range": { "start": 120, "end": 131 },
      "right_range": { "start": 120, "end": 132 },
      "raw_event_count": 1,
      "group_key": "text-group-0",
      "table_context": null
    }
  ]
}
```

Special cases:

- If the job exists but is not finished yet, the endpoint returns `409` with:

```json
{
  "detail": "Result is not ready yet."
}
```

## `GET /api/jobs/{job_id}/files/{side}`

Return the original uploaded file for frontend rendering.

Path params:

- `job_id`: job identifier returned by `POST /api/jobs`
- `side`: either `source` or `modified`

The endpoint streams the stored PDF with `application/pdf`.

Possible errors:

- `404` if the job does not exist
- `400` if `side` is not one of the supported values

## Core response types

### `DiffSummary`

- `insertions`: count of normalized review anchors with `kind="insert"`
- `deletions`: count of normalized review anchors with `kind="delete"`
- `replacements`: count of normalized review anchors with `kind="replace"`
- `reflows`: count of `reflow` anchors when `showReflow=true`
- `pages_a`: page count of the source PDF
- `pages_b`: page count of the modified PDF

### `DiffAnchor`

- `id`: stable per-result anchor identifier
- `kind`: `insert | delete | replace | reflow`
- `source_type`: `text | table`
- `excerpt_left`: review excerpt from the source side
- `excerpt_right`: review excerpt from the modified side
- `left_fragments`: projected highlight fragments for the source PDF
- `right_fragments`: projected highlight fragments for the modified PDF
- `left_range`: character range in the source logical text stream, or `null` for table anchors
- `right_range`: character range in the modified logical text stream, or `null` for table anchors
- `raw_event_count`: how many raw low-level edit events were merged into this review anchor
- `group_key`: normalization/debug group identifier
- `table_context`: populated only for `source_type="table"`

### `HighlightFragment`

- `page`: zero-based page index
- `bbox`: `[x0, y0, x1, y1]` in PDF points
- `viewport_ref`: frontend-friendly fragment reference

### `TableContext`

- `table_id`: internal table identifier
- `row`: zero-based row index, or `-1` for structure-level anchors
- `col`: zero-based column index, or `-1` for structure-level anchors
- `row_label`: optional row label inferred from the first cell
- `col_label`: optional column label inferred from the detected header

## Semantics that matter to integrators

- The backend uses `diff-match-patch` for text diffing. It does not expose raw diff opcodes directly.
- `anchors` are review-oriented, normalized results, not raw edit events.
- `replace` anchors may merge several nearby low-level insert/delete events into a single review card.
- `reflow` anchors represent unchanged content that moved enough to be useful as a position anchor.
- Table anchors are isolated from paragraph-style anchor coalescing.

## Coordinate model

- All `bbox` values are in original PDF point coordinates.
- Frontends should scale using:

```text
render_scale = rendered_page_width / original_pdf_page_width
```

- Highlight placement then becomes:

```text
left = x0 * render_scale
top = y0 * render_scale
width = (x1 - x0) * render_scale
height = (y1 - y0) * render_scale
```

## Error model

Common status codes:

- `200` success
- `400` invalid request or invalid `side`
- `404` missing job
- `409` result requested before completion
- `500` unexpected processing failure surfaced through job state
