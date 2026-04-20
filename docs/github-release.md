# GitHub Release Prep

This file collects the metadata and copy you can use when publishing the repository.

## Suggested repository name

`pdf-flow-diff`

## Suggested short description

High-precision PDF diff review for long documents with pagination reflow, coordinate-projected highlights, and table-aware anchors.

## Suggested GitHub topics

- `pdf`
- `diff`
- `fastapi`
- `react`
- `pymupdf`
- `document-review`
- `document-comparison`
- `compliance`
- `legal-tech`
- `biotech`

## Suggested About URL

If you later publish docs or a demo, link that here. Until then, leaving it empty is fine.

## Suggested initial release title

`v0.1.0 - Local MVP`

## Suggested initial release notes

```markdown
## Highlights

- Async FastAPI backend for PDF diff jobs
- Cross-page text-flow reconstruction to suppress pagination-only false positives
- `diff-match-patch` based text diffing with coordinate projection back to PDF space
- Review-friendly `insert/delete/replace/reflow` anchors
- Table-aware extraction for explicit-grid PDFs with cell-level highlights
- Local React review UI with dual-pane PDF navigation

## Backend API

- `POST /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/result`
- `GET /api/jobs/{job_id}/files/{side}`
- `GET /health`

## Current limitations

- Text-based PDFs only
- No OCR or scanned-PDF support
- No persistent job queue
- No exportable audit package yet
```

## Suggested pinned sections for the repo homepage

Good first things to show on GitHub:

1. Problem statement from `README.md`
2. Architecture diagram or pipeline summary
3. Backend API docs link
4. Known limitations
5. Roadmap

## Publish checklist

- Create the remote GitHub repository
- Add repository description and topics
- Push the `main` branch
- Enable Actions so CI runs on push and pull requests
- Optionally mark the first release as `v0.1.0`
- Add screenshots or a short demo GIF to the README later

## Suggested first screenshots

- Upload screen with both PDFs selected
- Processing screen showing async stages
- Review workspace with one text replacement anchor selected
- Review workspace with one table cell anchor selected
