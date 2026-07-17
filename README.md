# Season27

Premier League 2026/27 prediction game, built incrementally with FastHTML.

## Run locally

Install Python 3.12 dependencies and start the application:

```bash
uv sync
uv run season27
```

Open <http://127.0.0.1:5001>.

The development seed creates five generic local-only accounts and loads the fixed 20-club 2026/27
team list. No real player identities or login codes belong in the repository; configure those
securely outside version control.

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
npm run e2e
```

Checkpoint 10 adds production packaging, CI, browser lifecycle/accessibility coverage, guarded
staging rehearsal controls, and launch operations. See [deployment](docs/DEPLOYMENT.md),
[staging rehearsal](docs/REHEARSAL.md), and the [launch checklist](docs/LAUNCH_CHECKLIST.md).

The app creates and seeds its local SQLite database on first start. Alembic is configured for
versioned production schema changes; run `uv run alembic upgrade head` against a fresh database.

Production uses the BBC Premier League table as its standings source. Ordinary leaderboard visits
reuse standings checked within the previous 15 minutes; the authenticated refresh control requests
a throttled cache bypass. If the source is unavailable or invalid, Season27 retains the last valid
snapshot and displays a stale-data warning. Source URL, cache duration, refresh throttle, and network
timeouts can be configured through `SEASON27_STANDINGS_*` environment variables.

The administrator uses the normal player login and can manage player display names, rotate login
codes, revoke device sessions, adjust pre-lock dates, perform reasoned exceptional corrections,
reinstate excluded players, inspect audit history, and export season data. Newly generated login
codes are shown once, are never written to logs or exports, and revoke that player's existing
sessions immediately.
