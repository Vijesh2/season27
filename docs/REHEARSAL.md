# Staging rehearsal

Staging must use a separate service and volume. Set `SEASON27_ENVIRONMENT=staging`; every page then
shows a red **STAGING ENVIRONMENT — NOT LIVE** banner. Generic development accounts are appropriate
there, but real names and login codes must never be copied into its database or repository.

Reset only the isolated SQLite staging database:

```bash
SEASON27_ENVIRONMENT=staging SEASON27_DATABASE_URL=sqlite:////data/staging.db \
  uv run season27-reset-staging --confirm RESET-STAGING
```

Use `uv run season27-rehearsal <phase>` to print the time override for `prediction-open`, `deadline`,
`swap-1` through `swap-4`, or `final`. Apply that value as `SEASON27_DEV_NOW` and redeploy staging.

For each phase, have all five participants sign in on their intended phone/browser. Rehearse initial
ordering and submission, the deadline lock, each swap window, leaderboard updates, a stale standings
failure, admin correction/audit/export, session revocation, and recovery from a redeploy. Record
browser/device, outcome, and follow-up owner. Delete the time override before any production deploy.
