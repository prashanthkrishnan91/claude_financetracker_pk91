# Code Graph Report

_Generated: 2026-04-13 19:46_  |  _Root: /home/user/claude_financetracker_pk91_

## Summary

| Metric | Value |
|--------|-------|
| Python modules | 79 |
| Internal edges | 133 |
| God-node threshold (degree) | ≥ 5 |
| God nodes | 22 |
| Communities | 15 |

## God Nodes

Modules with the highest combined in+out degree — highest blast radius for changes.

| Module | In | Out | LoC | Path |
|--------|-----|-----|-----|------|
| `v2.backend.app.database` | 14 | 1 | 40 | `v2/backend/app/database.py` |
| `v2.backend.app.config` | 13 | 0 | 74 | `v2/backend/app/config.py` |
| `v2.backend.app.middleware.auth` | 9 | 1 | 102 | `v2/backend/app/middleware/auth.py` |
| `v2.backend.app.services.agents.orchestrator` | 2 | 8 | 403 | `v2/backend/app/services/agents/orchestrator.py` |
| `v2.backend.app` | 10 | 0 | 1 | `v2/backend/app/__init__.py` |
| `v2.backend.app.routers.sync` | 0 | 9 | 281 | `v2/backend/app/routers/sync.py` |
| `v2.backend.app.routers.prices` | 1 | 7 | 216 | `v2/backend/app/routers/prices.py` |
| `v2.backend.app.services.price_engine` | 8 | 0 | 580 | `v2/backend/app/services/price_engine.py` |
| `v2.backend.app.models` | 0 | 7 | 9 | `v2/backend/app/models/__init__.py` |
| `v2.backend.app.services.agents.job_runner` | 2 | 5 | 98 | `v2/backend/app/services/agents/job_runner.py` |
| `v1.data_engine` | 1 | 5 | 1355 | `v1/data_engine.py` |
| `v2.backend.app.routers.positions` | 0 | 6 | 214 | `v2/backend/app/routers/positions.py` |
| `v2.backend.app.routers.recommendations` | 0 | 6 | 148 | `v2/backend/app/routers/recommendations.py` |
| `v2.backend.app.services.crypto_service` | 5 | 1 | 49 | `v2/backend/app/services/crypto_service.py` |
| `v2.backend.app.services.agents.state` | 6 | 0 | 90 | `v2/backend/app/services/agents/state.py` |
| `v1.holdings_manager` | 4 | 1 | 371 | `v1/holdings_manager.py` |
| `v1.portfolio_aggregator` | 3 | 2 | 321 | `v1/portfolio_aggregator.py` |
| `v1.price_service` | 4 | 1 | 416 | `v1/price_service.py` |
| `v2.backend.app.routers.drip` | 0 | 5 | 52 | `v2/backend/app/routers/drip.py` |
| `v2.backend.app.services.portfolio_service` | 1 | 4 | 659 | `v2/backend/app/services/portfolio_service.py` |

## Communities

Clusters of tightly coupled modules (union-find on import graph).

### Community 2 (45 modules)

- `v2.backend.app.config` (74 LoC, 0 deps)
- `v2.backend.app.database` (40 LoC, 1 deps)
- `v2.backend.app.main` (90 LoC, 2 deps)
- `v2.backend.app.middleware.auth` (102 LoC, 1 deps)
- `v2.backend.app.models` (9 LoC, 7 deps)
- `v2.backend.app.models.deposit` (57 LoC, 0 deps)
- `v2.backend.app.models.drip` (38 LoC, 0 deps)
- `v2.backend.app.models.portfolio` (103 LoC, 0 deps)
- `v2.backend.app.models.position` (82 LoC, 0 deps)
- `v2.backend.app.models.price` (64 LoC, 0 deps)
- `v2.backend.app.models.recommendation` (134 LoC, 0 deps)
- `v2.backend.app.models.transaction` (56 LoC, 0 deps)
- `v2.backend.app.models.user` (82 LoC, 0 deps)
- `v2.backend.app.routers` (1 LoC, 0 deps)
- `v2.backend.app.routers.ai` (55 LoC, 4 deps)
- … and 30 more

### Community 3 (11 modules)

- `v2.backend.app` (1 LoC, 0 deps)
- `v2.backend.tests.test_auth` (27 LoC, 1 deps)
- `v2.backend.tests.test_crypto_service` (54 LoC, 1 deps)
- `v2.backend.tests.test_history_service` (89 LoC, 1 deps)
- `v2.backend.tests.test_import_service` (126 LoC, 1 deps)
- `v2.backend.tests.test_models` (224 LoC, 1 deps)
- `v2.backend.tests.test_plaid_service` (666 LoC, 1 deps)
- `v2.backend.tests.test_portfolio_service` (57 LoC, 1 deps)
- `v2.backend.tests.test_price_engine` (275 LoC, 1 deps)
- `v2.backend.tests.test_recommendation_engine` (554 LoC, 1 deps)
- `v2.backend.tests.test_sync` (527 LoC, 1 deps)

### Community 0 (10 modules)

- `v1.App` (1595 LoC, 2 deps)
- `v1.data_engine` (1355 LoC, 5 deps)
- `v1.drip_analytics` (146 LoC, 0 deps)
- `v1.holdings_manager` (371 LoC, 1 deps)
- `v1.main_sync` (378 LoC, 0 deps)
- `v1.plaid_client` (251 LoC, 0 deps)
- `v1.portfolio_aggregator` (321 LoC, 2 deps)
- `v1.price_service` (416 LoC, 1 deps)
- `v1.test_portfolio_sync` (419 LoC, 4 deps)
- `v1.test_smart_sync` (165 LoC, 3 deps)

### Community 1 (2 modules)

- `v1.tests.test_all` (406 LoC, 1 deps)
- `v1.utils` (1 LoC, 0 deps)

### Community 4 (1 modules)

- `App` (21 LoC, 0 deps)

### Community 5 (1 modules)

- `v2.backend.app.services` (1 LoC, 0 deps)

### Community 6 (1 modules)

- `v1.data` (1 LoC, 0 deps)

### Community 7 (1 modules)

- `scripts.build_code_graph` (411 LoC, 0 deps)

### Community 8 (1 modules)

- `v2.backend.app.middleware` (1 LoC, 0 deps)

### Community 9 (1 modules)

- `v1.data.portfolio` (139 LoC, 0 deps)

## Edge List (internal imports)

```
v1.App → v1.data_engine
v1.App → v1.drip_analytics
v1.data_engine → v1.holdings_manager
v1.data_engine → v1.main_sync
v1.data_engine → v1.plaid_client
v1.data_engine → v1.portfolio_aggregator
v1.data_engine → v1.price_service
v1.holdings_manager → v1.plaid_client
v1.portfolio_aggregator → v1.holdings_manager
v1.portfolio_aggregator → v1.price_service
v1.price_service → v1.holdings_manager
v1.test_portfolio_sync → v1.main_sync
v1.test_portfolio_sync → v1.plaid_client
v1.test_portfolio_sync → v1.portfolio_aggregator
v1.test_portfolio_sync → v1.price_service
v1.test_smart_sync → v1.holdings_manager
v1.test_smart_sync → v1.portfolio_aggregator
v1.test_smart_sync → v1.price_service
v1.tests.test_all → v1.utils
v2.backend.app.database → v2.backend.app.config
v2.backend.app.main → v2.backend.app.config
v2.backend.app.main → v2.backend.app.routers
v2.backend.app.middleware.auth → v2.backend.app.config
v2.backend.app.models → v2.backend.app.models.deposit
v2.backend.app.models → v2.backend.app.models.portfolio
v2.backend.app.models → v2.backend.app.models.position
v2.backend.app.models → v2.backend.app.models.price
v2.backend.app.models → v2.backend.app.models.recommendation
v2.backend.app.models → v2.backend.app.models.transaction
v2.backend.app.models → v2.backend.app.models.user
v2.backend.app.routers.ai → v2.backend.app.config
v2.backend.app.routers.ai → v2.backend.app.middleware.auth
v2.backend.app.routers.ai → v2.backend.app.services.ai_service
v2.backend.app.routers.ai → v2.backend.app.services.price_engine
v2.backend.app.routers.auth → v2.backend.app.database
v2.backend.app.routers.auth → v2.backend.app.middleware.auth
v2.backend.app.routers.auth → v2.backend.app.models.user
v2.backend.app.routers.auth → v2.backend.app.services.crypto_service
v2.backend.app.routers.deposits → v2.backend.app.middleware.auth
v2.backend.app.routers.deposits → v2.backend.app.models.deposit
v2.backend.app.routers.deposits → v2.backend.app.services.deposit_service
v2.backend.app.routers.drip → v2.backend.app.config
v2.backend.app.routers.drip → v2.backend.app.middleware.auth
v2.backend.app.routers.drip → v2.backend.app.models.drip
v2.backend.app.routers.drip → v2.backend.app.services.drip_service
v2.backend.app.routers.drip → v2.backend.app.services.price_engine
v2.backend.app.routers.portfolio → v2.backend.app.database
v2.backend.app.routers.portfolio → v2.backend.app.middleware.auth
v2.backend.app.routers.portfolio → v2.backend.app.models.portfolio
v2.backend.app.routers.portfolio → v2.backend.app.services.portfolio_service
v2.backend.app.routers.positions → v2.backend.app.config
v2.backend.app.routers.positions → v2.backend.app.database
v2.backend.app.routers.positions → v2.backend.app.middleware.auth
v2.backend.app.routers.positions → v2.backend.app.models.position
v2.backend.app.routers.positions → v2.backend.app.services.migration_service
v2.backend.app.routers.positions → v2.backend.app.services.price_engine
v2.backend.app.routers.prices → v2.backend.app.config
v2.backend.app.routers.prices → v2.backend.app.database
v2.backend.app.routers.prices → v2.backend.app.middleware.auth
v2.backend.app.routers.prices → v2.backend.app.models.price
v2.backend.app.routers.prices → v2.backend.app.services.crypto_service
v2.backend.app.routers.prices → v2.backend.app.services.history_service
v2.backend.app.routers.prices → v2.backend.app.services.price_engine
v2.backend.app.routers.recommendations → v2.backend.app.config
v2.backend.app.routers.recommendations → v2.backend.app.middleware.auth
v2.backend.app.routers.recommendations → v2.backend.app.models.recommendation
v2.backend.app.routers.recommendations → v2.backend.app.services.agents.job_runner
v2.backend.app.routers.recommendations → v2.backend.app.services.price_engine
v2.backend.app.routers.recommendations → v2.backend.app.services.recommendation_engine
v2.backend.app.routers.sync → v2.backend.app.config
v2.backend.app.routers.sync → v2.backend.app.database
v2.backend.app.routers.sync → v2.backend.app.middleware.auth
v2.backend.app.routers.sync → v2.backend.app.models.transaction
v2.backend.app.routers.sync → v2.backend.app.routers.prices
v2.backend.app.routers.sync → v2.backend.app.services.crypto_service
v2.backend.app.routers.sync → v2.backend.app.services.import_service
v2.backend.app.routers.sync → v2.backend.app.services.plaid_service
v2.backend.app.routers.sync → v2.backend.app.services.price_engine
v2.backend.app.services.agents → v2.backend.app.services.agents.orchestrator
v2.backend.app.services.agents → v2.backend.app.services.agents.state
v2.backend.app.services.agents.fundamental_agent → v2.backend.app.services.agents.data_sources
v2.backend.app.services.agents.fundamental_agent → v2.backend.app.services.agents.llm
v2.backend.app.services.agents.fundamental_agent → v2.backend.app.services.agents.state
v2.backend.app.services.agents.job_runner → v2.backend.app.config
v2.backend.app.services.agents.job_runner → v2.backend.app.database
v2.backend.app.services.agents.job_runner → v2.backend.app.services.agents.orchestrator
v2.backend.app.services.agents.job_runner → v2.backend.app.services.crypto_service
v2.backend.app.services.agents.job_runner → v2.backend.app.services.price_engine
v2.backend.app.services.agents.orchestrator → v2.backend.app.database
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.data_sources
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.fundamental_agent
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.llm
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.portfolio_manager
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.sentiment_agent
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.state
v2.backend.app.services.agents.orchestrator → v2.backend.app.services.agents.technical_agent
v2.backend.app.services.agents.portfolio_manager → v2.backend.app.services.agents.llm
v2.backend.app.services.agents.portfolio_manager → v2.backend.app.services.agents.state
v2.backend.app.services.agents.sentiment_agent → v2.backend.app.services.agents.data_sources
v2.backend.app.services.agents.sentiment_agent → v2.backend.app.services.agents.llm
v2.backend.app.services.agents.sentiment_agent → v2.backend.app.services.agents.state
v2.backend.app.services.agents.technical_agent → v2.backend.app.services.agents.data_sources
v2.backend.app.services.agents.technical_agent → v2.backend.app.services.agents.llm
v2.backend.app.services.agents.technical_agent → v2.backend.app.services.agents.state
v2.backend.app.services.ai_service → v2.backend.app.config
v2.backend.app.services.ai_service → v2.backend.app.database
v2.backend.app.services.ai_service → v2.backend.app.services.crypto_service
v2.backend.app.services.crypto_service → v2.backend.app.config
v2.backend.app.services.deposit_service → v2.backend.app.database
v2.backend.app.services.deposit_service → v2.backend.app.models.deposit
v2.backend.app.services.drip_service → v2.backend.app.database
v2.backend.app.services.drip_service → v2.backend.app.models.drip
v2.backend.app.services.drip_service → v2.backend.app.services.recommendation_engine
v2.backend.app.services.migration_service → v2.backend.app.database
v2.backend.app.services.portfolio_service → v2.backend.app.config
v2.backend.app.services.portfolio_service → v2.backend.app.database
v2.backend.app.services.portfolio_service → v2.backend.app.models.portfolio
v2.backend.app.services.portfolio_service → v2.backend.app.services.price_engine
v2.backend.app.services.price_service → v2.backend.app.database
v2.backend.app.services.price_service → v2.backend.app.models.price
v2.backend.app.services.recommendation_engine → v2.backend.app.database
v2.backend.app.services.recommendation_engine → v2.backend.app.models.recommendation
v2.backend.app.services.recommendation_engine → v2.backend.app.services.agents.job_runner
v2.backend.tests.test_auth → v2.backend.app
v2.backend.tests.test_crypto_service → v2.backend.app
v2.backend.tests.test_history_service → v2.backend.app
v2.backend.tests.test_import_service → v2.backend.app
v2.backend.tests.test_models → v2.backend.app
v2.backend.tests.test_plaid_service → v2.backend.app
v2.backend.tests.test_portfolio_service → v2.backend.app
v2.backend.tests.test_price_engine → v2.backend.app
v2.backend.tests.test_recommendation_engine → v2.backend.app
v2.backend.tests.test_sync → v2.backend.app
```
