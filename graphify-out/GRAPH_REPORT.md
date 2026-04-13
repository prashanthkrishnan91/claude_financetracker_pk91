# Code Graph Report

_Generated: 2026-04-13 19:39_  |  _Root: /home/user/claude_financetracker_pk91_

## Summary

| Metric | Value |
|--------|-------|
| Python modules | 123 |
| Internal edges | 104 |
| God-node threshold (degree) | ≥ 3 |
| God nodes | 27 |
| Communities | 53 |

## God Nodes

Modules with the highest combined in+out degree — highest blast radius for changes.

| Module | In | Out | LoC | Path |
|--------|-----|-----|-----|------|
| `database` | 14 | 0 | ? | `—` |
| `config` | 13 | 0 | ? | `—` |
| `v2.backend.app.routers.sync` | 0 | 9 | 281 | `v2/backend/app/routers/sync.py` |
| `middleware.auth` | 9 | 0 | ? | `—` |
| `v2.backend.app.services.agents.orchestrator` | 0 | 8 | 403 | `v2/backend/app/services/agents/orchestrator.py` |
| `v2.backend.app.models` | 0 | 7 | 9 | `v2/backend/app/models/__init__.py` |
| `v2.backend.app.routers.prices` | 0 | 7 | 216 | `v2/backend/app/routers/prices.py` |
| `services.price_engine` | 7 | 0 | ? | `—` |
| `v2.backend.app.routers.positions` | 0 | 6 | 214 | `v2/backend/app/routers/positions.py` |
| `v2.backend.app.routers.recommendations` | 0 | 6 | 148 | `v2/backend/app/routers/recommendations.py` |
| `state` | 6 | 0 | ? | `—` |
| `v2.backend.app.routers.drip` | 0 | 5 | 52 | `v2/backend/app/routers/drip.py` |
| `v2.backend.app.services.agents.job_runner` | 0 | 5 | 98 | `v2/backend/app/services/agents/job_runner.py` |
| `llm` | 5 | 0 | ? | `—` |
| `v2.backend.app.routers.ai` | 0 | 4 | 55 | `v2/backend/app/routers/ai.py` |
| `v2.backend.app.routers.auth` | 0 | 4 | 158 | `v2/backend/app/routers/auth.py` |
| `v2.backend.app.routers.portfolio` | 0 | 4 | 179 | `v2/backend/app/routers/portfolio.py` |
| `v2.backend.app.services.portfolio_service` | 0 | 4 | 659 | `v2/backend/app/services/portfolio_service.py` |
| `data_sources` | 4 | 0 | ? | `—` |
| `v2.backend.app.routers.deposits` | 0 | 3 | 64 | `v2/backend/app/routers/deposits.py` |

## Communities

Clusters of tightly coupled modules (union-find on import graph).

### Community 0 (64 modules)

- `agents.job_runner` (? LoC, 0 deps)
- `config` (? LoC, 0 deps)
- `crypto_service` (? LoC, 0 deps)
- `data_sources` (? LoC, 0 deps)
- `database` (? LoC, 0 deps)
- `fundamental_agent` (? LoC, 0 deps)
- `llm` (? LoC, 0 deps)
- `middleware.auth` (? LoC, 0 deps)
- `models.deposit` (? LoC, 0 deps)
- `models.drip` (? LoC, 0 deps)
- `models.portfolio` (? LoC, 0 deps)
- `models.position` (? LoC, 0 deps)
- `models.price` (? LoC, 0 deps)
- `models.recommendation` (? LoC, 0 deps)
- `models.transaction` (? LoC, 0 deps)
- … and 49 more

### Community 1 (8 modules)

- `deposit` (? LoC, 0 deps)
- `portfolio` (? LoC, 0 deps)
- `position` (? LoC, 0 deps)
- `price` (? LoC, 0 deps)
- `recommendation` (? LoC, 0 deps)
- `transaction` (? LoC, 0 deps)
- `user` (? LoC, 0 deps)
- `v2.backend.app.models` (9 LoC, 7 deps)

### Community 2 (1 modules)

- `v2.backend.app.models.user` (82 LoC, 0 deps)

### Community 3 (1 modules)

- `v2.backend.app.models.position` (82 LoC, 0 deps)

### Community 4 (1 modules)

- `v2.backend.app.models.price` (64 LoC, 0 deps)

### Community 5 (1 modules)

- `v2.backend.app.middleware` (1 LoC, 0 deps)

### Community 6 (1 modules)

- `v1.tests.test_all` (406 LoC, 0 deps)

### Community 7 (1 modules)

- `v2.backend.tests` (1 LoC, 0 deps)

### Community 8 (1 modules)

- `v2.backend.app.models.transaction` (56 LoC, 0 deps)

### Community 9 (1 modules)

- `v2.backend.app.services.import_service` (303 LoC, 0 deps)

## Edge List (internal imports)

```
v2.backend.app.database → config
v2.backend.app.main → config
v2.backend.app.main → routers
v2.backend.app.middleware.auth → config
v2.backend.app.models → deposit
v2.backend.app.models → portfolio
v2.backend.app.models → position
v2.backend.app.models → price
v2.backend.app.models → recommendation
v2.backend.app.models → transaction
v2.backend.app.models → user
v2.backend.app.routers.ai → config
v2.backend.app.routers.ai → middleware.auth
v2.backend.app.routers.ai → services.ai_service
v2.backend.app.routers.ai → services.price_engine
v2.backend.app.routers.auth → database
v2.backend.app.routers.auth → middleware.auth
v2.backend.app.routers.auth → models.user
v2.backend.app.routers.auth → services.crypto_service
v2.backend.app.routers.deposits → middleware.auth
v2.backend.app.routers.deposits → models.deposit
v2.backend.app.routers.deposits → services.deposit_service
v2.backend.app.routers.drip → config
v2.backend.app.routers.drip → middleware.auth
v2.backend.app.routers.drip → models.drip
v2.backend.app.routers.drip → services.drip_service
v2.backend.app.routers.drip → services.price_engine
v2.backend.app.routers.portfolio → database
v2.backend.app.routers.portfolio → middleware.auth
v2.backend.app.routers.portfolio → models.portfolio
v2.backend.app.routers.portfolio → services.portfolio_service
v2.backend.app.routers.positions → config
v2.backend.app.routers.positions → database
v2.backend.app.routers.positions → middleware.auth
v2.backend.app.routers.positions → models.position
v2.backend.app.routers.positions → services.migration_service
v2.backend.app.routers.positions → services.price_engine
v2.backend.app.routers.prices → config
v2.backend.app.routers.prices → database
v2.backend.app.routers.prices → middleware.auth
v2.backend.app.routers.prices → models.price
v2.backend.app.routers.prices → services.crypto_service
v2.backend.app.routers.prices → services.history_service
v2.backend.app.routers.prices → services.price_engine
v2.backend.app.routers.recommendations → config
v2.backend.app.routers.recommendations → middleware.auth
v2.backend.app.routers.recommendations → models.recommendation
v2.backend.app.routers.recommendations → services.agents.job_runner
v2.backend.app.routers.recommendations → services.price_engine
v2.backend.app.routers.recommendations → services.recommendation_engine
v2.backend.app.routers.sync → config
v2.backend.app.routers.sync → database
v2.backend.app.routers.sync → middleware.auth
v2.backend.app.routers.sync → models.transaction
v2.backend.app.routers.sync → prices
v2.backend.app.routers.sync → services.crypto_service
v2.backend.app.routers.sync → services.import_service
v2.backend.app.routers.sync → services.plaid_service
v2.backend.app.routers.sync → services.price_engine
v2.backend.app.services.agents → orchestrator
v2.backend.app.services.agents → state
v2.backend.app.services.agents.fundamental_agent → data_sources
v2.backend.app.services.agents.fundamental_agent → llm
v2.backend.app.services.agents.fundamental_agent → state
v2.backend.app.services.agents.job_runner → config
v2.backend.app.services.agents.job_runner → crypto_service
v2.backend.app.services.agents.job_runner → database
v2.backend.app.services.agents.job_runner → orchestrator
v2.backend.app.services.agents.job_runner → price_engine
v2.backend.app.services.agents.orchestrator → data_sources
v2.backend.app.services.agents.orchestrator → database
v2.backend.app.services.agents.orchestrator → fundamental_agent
v2.backend.app.services.agents.orchestrator → llm
v2.backend.app.services.agents.orchestrator → portfolio_manager
v2.backend.app.services.agents.orchestrator → sentiment_agent
v2.backend.app.services.agents.orchestrator → state
v2.backend.app.services.agents.orchestrator → technical_agent
v2.backend.app.services.agents.portfolio_manager → llm
v2.backend.app.services.agents.portfolio_manager → state
v2.backend.app.services.agents.sentiment_agent → data_sources
v2.backend.app.services.agents.sentiment_agent → llm
v2.backend.app.services.agents.sentiment_agent → state
v2.backend.app.services.agents.technical_agent → data_sources
v2.backend.app.services.agents.technical_agent → llm
v2.backend.app.services.agents.technical_agent → state
v2.backend.app.services.ai_service → config
v2.backend.app.services.ai_service → crypto_service
v2.backend.app.services.ai_service → database
v2.backend.app.services.crypto_service → config
v2.backend.app.services.deposit_service → database
v2.backend.app.services.deposit_service → models.deposit
v2.backend.app.services.drip_service → database
v2.backend.app.services.drip_service → models.drip
v2.backend.app.services.drip_service → recommendation_engine
v2.backend.app.services.migration_service → database
v2.backend.app.services.portfolio_service → config
v2.backend.app.services.portfolio_service → database
v2.backend.app.services.portfolio_service → models.portfolio
v2.backend.app.services.portfolio_service → services.price_engine
v2.backend.app.services.price_service → database
v2.backend.app.services.price_service → models.price
v2.backend.app.services.recommendation_engine → agents.job_runner
v2.backend.app.services.recommendation_engine → database
v2.backend.app.services.recommendation_engine → models.recommendation
```
