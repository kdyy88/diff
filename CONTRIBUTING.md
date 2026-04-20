# Contributing

Thanks for considering a contribution.

## Development setup

Backend:

```bash
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
pnpm install
pnpm dev
```

## Before opening a pull request

Please run:

```bash
cd backend
uv run pytest
```

```bash
cd frontend
pnpm build
```

## Contribution guidelines

- Keep the backend contract backward compatible unless the change explicitly updates the docs.
- Prefer review-friendly output over exposing raw low-level diff noise.
- For table changes, make the failure path explicit rather than silently guessing.
- Update `README.md` and relevant files under `docs/` when API or behavior changes.
- Add or update tests for backend diff behavior whenever possible.

## Pull request checklist

- The change is scoped and explained clearly.
- Tests or build checks pass locally.
- Documentation is updated when behavior changes.
- New config or runtime assumptions are documented.
