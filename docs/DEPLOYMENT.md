# Deployment

Season27 runs as one container and one process. Its SQLite database must live on a persistent
Railway volume; horizontal replicas are intentionally unsupported because SQLite is a single-writer
store.

## Railway setup

1. Create a service from this repository and attach a persistent volume mounted at `/data`.
2. Set `SEASON27_ENVIRONMENT=production`, `SEASON27_SECRET_KEY` to a long random secret, and set
   `SEASON27_BOOTSTRAP_ADMIN_NAME` and `SEASON27_BOOTSTRAP_ADMIN_CODE` as Railway secret variables.
   The code must contain exactly four letters or digits and is only used if the player table is empty.
3. Set `RAILWAY_VOLUME_MOUNT_PATH=/data` if Railway has not supplied it automatically. The app then
   uses `/data/season27.db`. Alternatively set an explicit `SEASON27_DATABASE_URL`.
4. Deploy. Startup applies all Alembic migrations before accepting traffic. Railway checks `/ready`;
   `/live` is the process liveness endpoint.
5. Keep the service at one replica and verify login, static assets, database persistence after a
   redeploy, standings refresh, and an export before inviting players.

Never put player identities or codes in repository files, build arguments, command output, or logs.
Rotate the bootstrap code through the application after first login, then remove the bootstrap code
variable from Railway. Back up the volume before migrations and before administrative corrections.

## Rollback

Redeploy the previous image. If a schema migration must be reversed, restore the pre-deploy database
backup rather than improvising a downgrade against live data. The health endpoint returning 503
prevents an unhealthy release from being treated as ready.
