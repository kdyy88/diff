# PDF Flow Diff

`PDF Flow Diff` is a local web MVP for reviewing long-form PDF revisions when pagination reflow makes page-by-page comparison unusable.

It is designed for workflows where a tiny text change matters, but a single inserted sentence can push the next 100 pages into different physical positions. The project reconstructs a continuous logical text flow, coarse-aligns extracted lines with `patiencediff`, refines only changed windows with `diff-match-patch`, then projects review anchors back onto the original PDF coordinates for side-by-side visual review.

## What it solves

- Ignores false positives caused only by pagination reflow or layout overflow.
- Tracks real edits at word and character granularity.
- Projects every review anchor back to page coordinates for visual highlighting.
- Supports a review-friendly dual-pane UI with anchor jumping instead of fragile scroll lock.
- Separates explicit-grid table regions from body text so table edits can be reviewed at cell level.
- Optionally splits the review flow into chapter analysis, confirmation, per-chapter diff, and chapter-filtered review.

## Current scope

Supported:

- Text-based PDFs exported from Word or similar tools
- Mixed body text plus explicit-grid tables
- Local single-user review workflow
- Browser-based review on current Chromium-class browsers

Not supported in this MVP:

- Scanned PDFs or OCR
- Handwritten annotations
- Image diffs
- Multi-user jobs or persistent queues
- Audit export files such as JSON download or marked-up PDFs

## Architecture at a glance

The core pipeline is:

1. Extract page geometry, text atoms, and table regions from each PDF with `PyMuPDF`.
2. Reconstruct a cross-page logical text flow and normalize only what is needed for alignment.
3. Coarse-align extracted text lines with `patiencediff` so hard `equal` windows can re-anchor the flow.
4. Run local `diff-match-patch` only inside non-equal windows for word and character precision.
5. Coalesce raw edit events into review-friendly anchors and mark risky large windows as low confidence.
6. Project anchor ranges back to PDF page coordinates.
7. Render side-by-side PDFs with anchor-linked highlights in the React UI.

When `PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT=true`, the upload flow can branch into:

1. `upload`
2. `chapter analysis`
3. `chapter confirmation`
4. `chapter diff job`
5. `review`

The chapter mode never exports physical sub-PDFs. It reuses the main diff stack with page-range extraction and then aggregates the per-chapter results back into one review result.

See [docs/architecture.md](docs/architecture.md) for the full module breakdown.

## Repository layout

```text
.
├── backend/               FastAPI service and diff engine
├── frontend/              React review UI
├── docs/                  API, integration, and architecture docs
└── .github/workflows/     CI pipeline
```

## Tech stack

- Backend: FastAPI, PyMuPDF, patiencediff, diff-match-patch, Pydantic
- Frontend: React, Vite, TypeScript, Tailwind, react-pdf
- Tooling: `uv` for Python dependency management, `pnpm` for frontend dependency management

## Quick start

Requirements:

- Python `3.11+`
- Node.js `20+`
- `uv`
- `pnpm`

### 1. Start the backend

```bash
cd backend
uv sync --extra dev
export PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT=true
uv run uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

Completed jobs also save LLM-ready markdown shards under `artifacts/markdown-jobs/<job_id>/` at the repository root.

Useful endpoints while developing:

- `GET /health`
- `GET /docs` for Swagger UI
- `GET /redoc` for ReDoc

### 2. Start the frontend

```bash
cd frontend
pnpm install
pnpm dev
```

The Vite dev server runs at `http://localhost:5173` and expects the backend at `http://localhost:8000`.

## Backend API quick overview

The backend exposes an async job workflow:

1. `POST /api/jobs` uploads `sourcePdf` and `modifiedPdf`.
2. `GET /api/jobs/{id}` polls job progress.
3. `GET /api/jobs/{id}/result` fetches the final diff anchors.
4. `GET /api/jobs/{id}/files/{side}` streams the original uploaded PDF back for rendering.

After a job reaches `done`, the backend also writes markdown bundle files such as `00-overview.md` and `10-part-01.md` into `artifacts/markdown-jobs/{id}/`.

When chapter mode is enabled, the backend also exposes a feature-discovery and chapter-analysis workflow:

1. `GET /api/features` returns `chapterSplit`.
2. `POST /api/chapter-analyses` uploads the PDFs and starts bookmark-based chapter analysis.
3. `GET /api/chapter-analyses/{id}` polls chapter-analysis progress.
4. `GET /api/chapter-analyses/{id}/result` returns the editable chapter plans.
5. `POST /api/chapter-analyses/{id}/validate` performs authoritative page-cover and exact-title matching checks.
6. `POST /api/chapter-analyses/{id}/confirm` returns the existing `CreateJobResponse`, after which the frontend goes back to the normal `processing -> review` flow.

If chapter analysis finds that either PDF does not contain usable built-in bookmarks, the app automatically falls back to the normal full-document diff flow.

Primary anchor kinds:

- `insert`
- `delete`
- `replace`
- `reflow`

Primary source types:

- `text`
- `table`

Anchor confidence values:

- `high`
- `low`

Full request and response examples live in [docs/api.md](docs/api.md), and backend integration guidance lives in [docs/backend-integration.md](docs/backend-integration.md).

## Local development

Backend checks:

```bash
cd backend
uv run pytest
```

Frontend build:

```bash
cd frontend
pnpm build
```

Feature-flagged chapter flow validation:

```bash
cd backend
uv run pytest app/tests/test_chapters.py
```

## How table diffs currently work

For PDFs with explicit ruling lines, the extractor uses `PyMuPDF` table detection to split those regions out of the main text flow. Table cells are then diffed independently and returned as `source_type="table"` anchors so the UI can highlight a single cell instead of collapsing the whole table into one paragraph-like change.

If a table cannot be matched reliably, the system intentionally falls back to a weaker structural signal instead of returning misleading cell-level results.

## Design choices

- The project does use mature open-source diff libraries; it does not reimplement the core text diff algorithms.
- Custom logic focuses on:
  - text flow reconstruction across pages
  - line-level coarse anchoring with `patiencediff`
  - windowed local refinement with `diff-match-patch`
  - minimal normalization before alignment
  - review-anchor coalescing
  - low-confidence marking for risky large replace windows
  - reflow detection
  - coordinate projection
  - explicit-grid table extraction

## License

This repository is released under `GPL-2.0-only` to stay compatible with the current `patiencediff` coarse-alignment dependency used by the backend. See [LICENSE](LICENSE).

## Documentation index

- [API reference](docs/api.md)
- [Backend integration guide](docs/backend-integration.md)
- [Architecture notes](docs/architecture.md)
- [GitHub release prep](docs/github-release.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Roadmap

- Better table matching for inserted/deleted sections across multiple pages
- Smarter bookmark-poor document onboarding beyond the current manual fallback
- Optional audit-mode output that preserves raw low-level edit events
- Exportable machine-readable results
- Broader browser compatibility validation
- Better handling for merged cells and borderless tables
