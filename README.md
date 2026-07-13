# Season27

Premier League 2026/27 prediction game, built incrementally with FastHTML.

## Run locally

Install Python 3.12 dependencies and start the application:

```bash
uv sync
uv run season27
```

Open <http://127.0.0.1:5001>.

The development seed creates five generic local-only accounts and imports the confirmed 20-club
2026/27 roster. The administrator can review and approve it from `/admin`; approval is permanent
and unavailable once the game opens. No real player identities or login codes belong in the
repository; configure those securely outside version control.

To preview another game phase, provide a London local time or an ISO timestamp with an offset:

```bash
SEASON27_DEV_NOW=2026-08-21T00:00:00 uv run season27
```

The page displays a warning whenever this development-only override is active.

## Verify

```bash
uv run ruff check .
uv run mypy
uv run pytest
npm install
npm run typecheck
npm run build
```

The app creates and seeds its local SQLite database on first start. Alembic is configured for
versioned production schema changes; run `uv run alembic upgrade head` against a fresh database.
