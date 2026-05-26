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

## Safe mode vs cost-control mode

Railway watch paths are positive glob patterns only — negation (`!path`) is not supported.
This limits how narrowly the API service can be scoped, because the API imports from nearly
all service modules. The table below describes the tradeoff:

| Mode | API Watch Paths | Cost | Risk |
|---|---|---|---|
| **Safe** (recommended) | `/v2/backend/app/**` | More deploys | Zero missed deploys |
| **Cost-control** | Specific paths (see below) | Fewer deploys | Must be maintained; new shared modules require manual update |

**Recommendation for this repo:** Use safe mode for the API service. The meaningful cost
savings come from the *worker* services (analyst, Watchtower, email delivery) not deploying
unnecessarily — those services have genuinely narrow, bounded import trees. The API imports
from nearly every service module, so narrowing its Watch Paths provides limited savings with
higher maintenance risk.

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

The API service (`app/main.py`) imports all routers, which in turn import from most service
modules including `app/services/alert/**`, `app/services/intelligence/**`, and others. There
is no practical way to exclude worker-only entrypoints without per-file enumeration, which
is fragile as the codebase grows.

**Safe mode (recommended):**

```
/v2/backend/app/**
/v2/backend/requirements*.txt
/v2/backend/pyproject.toml
/v2/backend/poetry.lock
/v2/backend/railway.toml
/v2/database/**
/supabase/**
```

This deploys the API on any backend code change, including worker-only files. It is always
correct and requires no maintenance.

**Cost-control mode (advanced — requires maintenance):**

The following paths cover what the API directly imports. Known worker-only files excluded:
`alert_email_delivery_worker_v1.py`, `alert_email_delivery_worker_entrypoint.py`,
`resend_client_v1.py`, `analyst_refresh_worker_v1.py`, `analyst_refresh_worker_entrypoint.py`.
Note: `app/services/intelligence/**` still catches Watchtower worker files because the API
imports `intel_v3_service.py` which lives in that same directory tree.

```
/v2/backend/app/main.py
/v2/backend/app/config.py
/v2/backend/app/database.py
/v2/backend/app/supabase_client.py
/v2/backend/app/routers/**
/v2/backend/app/models/**
/v2/backend/app/middleware/**
/v2/backend/app/services/intelligence/**
/v2/backend/app/services/alert/alert_candidate_service.py
/v2/backend/app/services/alert/alert_delivery_outbox_service.py
/v2/backend/app/services/alert/alert_delivery_policy_v1.py
/v2/backend/app/services/alert/alert_trigger_policy_v1.py
/v2/backend/requirements*.txt
/v2/backend/pyproject.toml
/v2/backend/poetry.lock
/v2/backend/railway.toml
/v2/database/**
/supabase/**
```

If you add a new router or service module that the API imports, add it here — otherwise the
API will not redeploy when that module changes.

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

The analyst worker runs only intelligence evidence and analyst refresh paths. It does not
need to redeploy for alert/email-only changes, frontend changes, or docs-only changes.

**Shared dependency note:** `app/services/intelligence/**` overlaps with the Watchtower
service. A change to a shared intelligence module (e.g. `intel_v3_service.py`) may deploy
both this service and Watchtower. That is correct behaviour — both depend on the shared
module.

---

### Watchtower service (`PROCESS_TYPE=watchtower`)

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

Watchtower depends on republisher, callables, and background refresh worker modules that all
live under `app/services/intelligence/**`. It does not need to redeploy for alert/email,
frontend, or docs-only changes.

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

Use this matrix to confirm Watch Paths are working after configuration. The API column
assumes **safe mode** (`/v2/backend/app/**`). Where cost-control mode differs, it is noted.

| Change type | API | Analyst worker | Watchtower | Email delivery |
|---|---|---|---|---|
| docs-only PR (`docs/**`, `*.md`) | no deploy | no deploy | no deploy | no deploy |
| frontend-only PR (`src/**`, `v2/frontend/**`) | no deploy | no deploy | no deploy | no deploy |
| email worker files only¹ | **deploys** (safe) / no deploy (cost-control) | no deploy | no deploy | **deploys** |
| Watchtower-only file (`watchtower_worker_entrypoint.py`) | **deploys** (safe) / **deploys** (cost-control)² | **deploys**² | **deploys** | no deploy |
| Analyst worker file only (`analyst_refresh_worker_v1.py`) | **deploys** (safe) / **deploys** (cost-control)² | **deploys** | **deploys**² | no deploy |
| Shared alert service file (`alert_candidate_service.py`) | **deploys** | no deploy | no deploy | **deploys** |
| Shared intelligence module (`intel_v3_service.py`) | **deploys** | **deploys** | **deploys** | no deploy |
| Shared config/dep (`config.py`, `requirements*.txt`, `railway.toml`) | **deploys** | **deploys** | **deploys** | **deploys** |
| Database migration (`v2/database/**`) | **deploys** | **deploys** | **deploys** | **deploys** |

¹ Email worker files: `alert_email_delivery_worker_v1.py`, `alert_email_delivery_worker_entrypoint.py`,
`resend_client_v1.py`. Under cost-control API mode these are excluded from API Watch Paths, so
only the email delivery service deploys.

² The analyst worker and Watchtower both watch `app/services/intelligence/**`, which includes all
v3 worker files. A change to one worker's entrypoint may deploy both. This is conservative but
safe for shared dependencies.

---

## Why watchPatterns is not used in railway.toml

Railway's config-as-code `watchPatterns` field applies **to all services that use the same
`railway.toml`**. Because all four backend services share `v2/backend/railway.toml`, any
`watchPatterns` block in that file would apply identically to all four services — making
it impossible to set service-specific deploy triggers via config-as-code.

Per-service Watch Paths in the Railway dashboard are the correct mechanism for this setup.
Do not add a `watchPatterns` block to `railway.toml` unless all four services should share
exactly the same trigger conditions and that has been verified against Railway documentation.
