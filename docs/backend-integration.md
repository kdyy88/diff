# Backend Integration Guide

This guide is for teams that want to integrate the backend into another review UI, workflow engine, or compliance system without reusing the bundled React frontend.

## Integration model

Treat the backend as an asynchronous document comparison service:

1. Upload a source PDF and a modified PDF.
2. Poll until the job reaches `done` or `failed`.
3. Fetch the normalized review anchors.
4. Render or transform those anchors in your own client.

The backend is intentionally opinionated:

- text diffing uses `diff-match-patch`
- output is normalized for human review
- the main unit of work is the `DiffAnchor`, not the raw diff opcode

## Recommended polling flow

Suggested client flow:

1. `POST /api/jobs`
2. Cache the returned `job_id`
3. Poll `GET /api/jobs/{job_id}` every `1s` to `2s`
4. Stop polling when `status` becomes `done` or `failed`
5. If `done`, fetch `GET /api/jobs/{job_id}/result`
6. If `failed`, surface the `error` field to the operator

Practical polling rule:

- poll every second while `progress < 90`
- back off to every two seconds once the job is near completion if you expect very large files

## What the backend guarantees

### 1. Cross-page logical alignment

The backend does not compare PDFs page-by-page. It reconstructs a single logical text stream across the whole document so that pagination shifts do not become wall-to-wall false diffs.

### 2. Character-aware replacement tracking

Text diffs are computed on normalized character streams, and projected back to original coordinates. This makes changes such as:

- `0.5 -> 0.05`
- `Ala -> Val`
- `mg -> mcg`

visible as precise review anchors instead of coarse paragraph noise.

### 3. Review-oriented anchor coalescing

The backend intentionally merges nearby low-level edits into a smaller number of review-friendly anchors. This is why `summary.replacements` should be interpreted as the number of review anchors, not the number of raw diff events.

## Understanding `DiffAnchor`

### Common fields

- `kind`: `insert | delete | replace | reflow`
- `source_type`: `text | table`
- `left_fragments` / `right_fragments`: PDF highlight boxes
- `excerpt_left` / `excerpt_right`: short review excerpts
- `raw_event_count`: number of merged low-level edits

### Text anchors

Text anchors additionally expose:

- `left_range`
- `right_range`

These ranges refer to the backend's internal logical character stream. They are useful for debugging or for advanced integrations, but most UIs can ignore them and render only fragments plus excerpts.

### Table anchors

Table anchors have:

- `source_type = "table"`
- `table_context != null`
- `left_range = null`
- `right_range = null`

Use `table_context.row` and `table_context.col` as logical cell coordinates. When available, `row_label` and `col_label` provide display-friendly labels for cards, logs, or audit systems.

## Handling `reflow`

`reflow` anchors are opt-in through `showReflow=true`.

Use them when:

- your UI needs cross-page jump anchors
- operators want to inspect large location shifts
- you are tuning layout-sensitive workflows

Hide them by default if the user only wants actual content edits.

## Coordinate usage

Each fragment `bbox` is expressed in original PDF points, not browser pixels.

To overlay highlights:

1. obtain the source page width from `pages_left` or `pages_right`
2. compute a scale based on the rendered page width
3. apply that scale to the returned bbox

If your renderer supports zoom, keep the original point coordinates and only recompute the display scale.

## Table-specific behavior

The current implementation only promises structured table handling for explicit-grid tables that `PyMuPDF` can detect reliably.

Behavior by case:

- Reliable table detection and row alignment: emit cell-level anchors
- Missing opposite-side table: emit structure-level insert/delete anchors
- Column mismatch: emit structure-level replace anchors
- Unreliable table semantics: fall back to a coarser table signal rather than silently pretending cell alignment is trustworthy

This means downstream systems should avoid assuming every table anchor corresponds to a single clean cell replacement.

## Backend-only embedding patterns

Common ways to integrate the service:

- Internal QA portal that renders the two PDFs and a custom anchor list
- Regulated workflow service that stores only excerpts plus bbox metadata
- Review assistant that converts anchors into approval tasks per section or per cell

## Operational assumptions

Current MVP assumptions:

- in-memory job store
- temporary file storage under the system temp directory
- no recovery after service restart
- no authentication or authorization
- no concurrency isolation for multi-tenant deployment

For productionization, the usual next steps would be:

- persistent job metadata store
- object storage for uploads
- worker queue
- authentication and tenant isolation
- retention and cleanup policy

## API contract stability

For external consumers, these are the safest fields to depend on:

- `JobStatus.id`
- `JobStatus.status`
- `JobStatus.progress`
- `JobStatus.error`
- `DiffResult.pages_left`
- `DiffResult.pages_right`
- `DiffResult.summary`
- `DiffAnchor.kind`
- `DiffAnchor.source_type`
- `DiffAnchor.left_fragments`
- `DiffAnchor.right_fragments`
- `DiffAnchor.excerpt_left`
- `DiffAnchor.excerpt_right`
- `DiffAnchor.table_context`

Fields such as `group_key` should be treated as helpful but not foundational.
