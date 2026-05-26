# Railway Watch Paths — Per-Service Deploy Cost Control

## Why this document exists

All four Railway backend services share one config file (`v2/backend/railway.toml`) and one
root directory (`v2/backend`). Railway's `PROCESS_TYPE` env var selects the runtime process
at start-up — it does **not** control whether a deployment is created. Every push to a
watched branch triggers a new deployment on every service unless Watch Paths are configured.

Watch Paths are set **per service** in the Railway dashboard under
**Settings → Source → Watch Paths**. With one shared `railway.toml` that cannot distinguish
which service it is building, per-service watch rules must live in the Railway dashboard, not
in the TOML file.

---

## Manual Railway setup checklist

For each service listed below:

1. Open the Railway project → select the service.
2. Go to **Settings → Source**.
3. Find the **Watch Paths** field.
4. Paste only that service's path list (one path per line, no trailing spaces).
5. Save. Railway applies the change immediately — no redeploy required.
6. Leave **Root Directory** as `v2/backend`.
7. Leave **Config File** as `v2/backend/railway.toml`.
8. Leave **PROCESS_TYPE** env var as currently configured for the service.

> **Note:** All paths below use the repository-root-relative form that Railway expects
> (leading `/` anchors to repo root, `**` matches any depth).

---

## Service Watch Paths

### API service (`PROCESS_TYPE` = _not set_ or `api`)

```
/v2/backend/app/**
/v2/backend/requirements*.txt
/v2/backend/pyproject.toml
/v2/backend/poetry.lock
/v2/backend/railway.toml
/v2/database/**
/supabase/**
```

The API service needs to redeploy when application code, dependencies, Railway config, or
database schema changes. It does not need to redeploy for docs, frontend, or worker-only
changes.

---

### Analyst refresh worker service (`PROCESS_TYPE=worker`)

```
/v2/backend/app/services/intelligence/**
/v2/backend/app/config.py
/v2/backend/app/database.py
/v2/backend/app/supabase_client.py
/v2/backend/requirements*.txt
/v2/backend/pyproject.toml
/v2/backend/poetry.lock
/v2/backend/railway.toml
/v2/database/**
```

The analyst worker only runs intelligence evidence and analyst refresh paths. It does not
need to redeploy for alert/email changes, frontend changes, or docs-only changes.

---

### Watchtower service (`PROCESS_TYPE=watchtower`)

```
/v2/backend/app/services/intelligence/v3/watchtower_worker_entrypoint.py
/v2/backend/app/services/intelligence/v3/**
/v2/backend/app/services/intelligence/**
/v2/backend/app/config.py
/v2/backend/app/database.py
/v2/backend/app/supabase_client.py
/v2/backend/requirements*.txt
/v2/backend/pyproject.toml
/v2/backend/poetry.lock
/v2/backend/railway.toml
/v2/database/**
```

Watchtower overlaps with the analyst worker in the intelligence module tree because it
depends on republisher and callables modules within that tree. It does not need to redeploy
for alert/email, frontend, or docs-only changes.

---

### Alert email delivery service (`PROCESS_TYPE=email_delivery` or `alert_email_delivery`)

```
/v2/backend/app/services/alert/**
/v2/backend/app/config.py
/v2/backend/app/database.py
/v2/backend/app/supabase_client.py
/v2/backend/requirements*.txt
/v2/backend/pyproject.toml
/v2/backend/poetry.lock
/v2/backend/railway.toml
/v2/database/**
```

The email delivery worker runs only the alert outbox processing path. It does not need to
redeploy for intelligence/analyst/Watchtower changes, frontend changes, or docs-only changes.

---

## Verification matrix

Use this matrix to confirm Watch Paths are working after configuration.

| Change type | API | Analyst worker | Watchtower | Email delivery |
|---|---|---|---|---|
| docs-only PR (e.g. `docs/**`, `*.md`) | no deploy | no deploy | no deploy | no deploy |
| frontend-only PR (`src/**`, `v2/frontend/**`) | no deploy | no deploy | no deploy | no deploy |
| alert email worker change only (`app/services/alert/**`) | no deploy | no deploy | no deploy | **deploys** |
| Watchtower-only change (`app/services/intelligence/v3/watchtower_*`) | no deploy | may deploy¹ | **deploys** | no deploy |
| Analyst worker-only change (`app/services/intelligence/v3/analyst_*`) | no deploy | **deploys** | may deploy¹ | no deploy |
| Shared config/dep change (`config.py`, `requirements*.txt`, `railway.toml`) | **deploys** | **deploys** | **deploys** | **deploys** |
| Database migration (`v2/database/**`) | **deploys** | **deploys** | **deploys** | **deploys** |

¹ The analyst worker and Watchtower share the `app/services/intelligence/**` watch path because
Watchtower depends on republisher and callables modules that live under that tree. A change
touching only `analyst_refresh_worker_v1.py` may therefore also trigger a Watchtower deploy.
That is a deliberate conservative choice — over-deploying is safer than under-deploying for
shared dependencies.

---

## Why watchPatterns is not used in railway.toml

Railway's config-as-code `watchPatterns` field applies **to all services that use the same
`railway.toml`**. Because all four backend services share `v2/backend/railway.toml`, any
`watchPatterns` block in that file would apply identically to all four services — making
it impossible to set service-specific deploy triggers via config-as-code.

Per-service Watch Paths in the Railway dashboard are the correct mechanism for this setup.
Do not add a `watchPatterns` block to `railway.toml` unless all four services should share
exactly the same trigger conditions and that has been verified against Railway documentation.
