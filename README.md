# PDF Flow Diff

`PDF Flow Diff` is a local web MVP for reviewing long-form PDF revisions when pagination reflow makes page-by-page comparison unusable.

It is designed for workflows where a tiny text change matters, but a single inserted sentence can push the next 100 pages into different physical positions. The project collapses both PDFs into a continuous logical text flow, computes diffs with `diff-match-patch`, then projects review anchors back onto the original PDF coordinates for side-by-side visual review.

## What it solves

- Ignores false positives caused only by pagination reflow or layout overflow.
- Tracks real edits at word and character granularity.
- Projects every review anchor back to page coordinates for visual highlighting.
- Supports a review-friendly dual-pane UI with anchor jumping instead of fragile scroll lock.
- Separates explicit-grid table regions from body text so table edits can be reviewed at cell level.

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
3. Run `diff-match-patch` on the normalized 1D streams.
4. Coalesce raw edit events into review-friendly anchors.
5. Project anchor ranges back to PDF page coordinates.
6. Render side-by-side PDFs with anchor-linked highlights in the React UI.

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

- Backend: FastAPI, PyMuPDF, diff-match-patch, Pydantic
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
uv run uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

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

Primary anchor kinds:

- `insert`
- `delete`
- `replace`
- `reflow`

Primary source types:

- `text`
- `table`

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

## How table diffs currently work

For PDFs with explicit ruling lines, the extractor uses `PyMuPDF` table detection to split those regions out of the main text flow. Table cells are then diffed independently and returned as `source_type="table"` anchors so the UI can highlight a single cell instead of collapsing the whole table into one paragraph-like change.

If a table cannot be matched reliably, the system intentionally falls back to a weaker structural signal instead of returning misleading cell-level results.

## Design choices

- The project does use Google's open-source `diff-match-patch`; it does not reimplement the core text diff algorithm.
- Custom logic focuses on:
  - text flow reconstruction across pages
  - minimal normalization before alignment
  - review-anchor coalescing
  - reflow detection
  - coordinate projection
  - explicit-grid table extraction

## Documentation index

- [API reference](docs/api.md)
- [Backend integration guide](docs/backend-integration.md)
- [Architecture notes](docs/architecture.md)
- [GitHub release prep](docs/github-release.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Roadmap

- Better table matching for inserted/deleted sections across multiple pages
- Optional audit-mode output that preserves raw low-level edit events
- Exportable machine-readable results
- Broader browser compatibility validation
- Better handling for merged cells and borderless tables
