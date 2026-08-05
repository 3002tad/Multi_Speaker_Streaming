# Meeting Service

This compose stack owns Meeting Service runtime persistence only. It does not
expose PostgreSQL or Redis; the API is bound to `127.0.0.1:8002` for local
integration. Nginx and eCabinet join the `meeting_platform_internal` network
in the integration phase.

## Local start

From the repository root:

```powershell
docker compose --env-file meeting_service/.env -f meeting_service/docker-compose.yml up -d --build
```

Create `meeting_service/.env` locally from `.env.example`, set a non-default
`MEETING_DB_PASSWORD` and `MEETING_SERVICE_KEY`, and never commit it.

Run migrations separately when diagnosing startup:

```powershell
docker compose --env-file meeting_service/.env -f meeting_service/docker-compose.yml run --rm migration-meeting
```

## Current scope

`0001_runtime_sessions` creates only Meeting Service-owned runtime and
idempotency tables. Transcript, minutes, export and AI callback tables are
subsequent migrations.
