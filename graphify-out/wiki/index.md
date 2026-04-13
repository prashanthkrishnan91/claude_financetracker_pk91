# Module Wiki

Auto-generated index of internal modules.

## `App`

- **Path**: `App.py`
- **LoC**: 21
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: _none_

## `scripts.build_code_graph`

- **Path**: `scripts/build_code_graph.py`
- **LoC**: 411
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: `repo_root`, `collect_py_files`, `module_name`, `build_short_name_map`, `resolve_import`, `resolve_relative_import`, `parse_imports`, `count_lines`, `extract_functions`, `build_graph`

## `scripts.update_docs`

- **Path**: `scripts/update_docs.py`
- **LoC**: 79
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: `get_latest_commit`, `update_progress_log`

## `v1.App`

- **Path**: `v1/App.py`
- **LoC**: 1595
- **In-degree**: 0
- **Imports**: `v1.data_engine`, `v1.drip_analytics`
- **Functions/methods**: `_init`, `_color_delta`, `_rcard`, `_color_delta_log`, `_t`

## `v1.data`

- **Path**: `v1/data/__init__.py`
- **LoC**: 1
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: _none_

## `v1.data.portfolio`

- **Path**: `v1/data/portfolio.py`
- **LoC**: 139
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: `get_positions_list`

## `v1.data_engine`

- **Path**: `v1/data_engine.py`
- **LoC**: 1355
- **In-degree**: 1
- **Imports**: `v1.holdings_manager`, `v1.main_sync`, `v1.plaid_client`, `v1.portfolio_aggregator`, `v1.price_service`
- **Functions/methods**: `_load`, `_save`, `get_system_mode`, `transition_to_live`, `_norm_decimal`, `_norm_date`, `make_tx_fingerprint`, `_bootstrap`, `strip_existing_tx_store_fingerprints`, `seed_processed_ids_from_history`

## `v1.drip_analytics`

- **Path**: `v1/drip_analytics.py`
- **LoC**: 146
- **In-degree**: 1
- **Imports**: _none_
- **Functions/methods**: `fetch_dividend_intel`, `extract_largest_positive_float`, `flatten_history`, `render_drip_dashboard`

## `v1.holdings_manager`

- **Path**: `v1/holdings_manager.py`
- **LoC**: 371
- **In-degree**: 4
- **Imports**: `v1.plaid_client`
- **Functions/methods**: `last_synced_dt`, `age_hours`, `is_stale`, `tickers`, `to_dict`, `from_dict`, `__init__`, `get_holdings`, `needs_plaid_sync`, `get_cache_status`

## `v1.main_sync`

- **Path**: `v1/main_sync.py`
- **LoC**: 378
- **In-degree**: 2
- **Imports**: _none_
- **Functions/methods**: _none_

## `v1.plaid_client`

- **Path**: `v1/plaid_client.py`
- **LoC**: 251
- **In-degree**: 3
- **Imports**: _none_
- **Functions/methods**: `normalise_ticker`, `plaid_ticker`, `__init__`, `_require_env`, `_get_client`, `get_holdings`, `refresh_investments`, `_parse_response`

## `v1.portfolio_aggregator`

- **Path**: `v1/portfolio_aggregator.py`
- **LoC**: 321
- **In-degree**: 3
- **Imports**: `v1.holdings_manager`, `v1.price_service`
- **Functions/methods**: `__init__`, `calculate_total_value`, `calculate_total_value_async`, `sync_portfolio_total`, `sync_portfolio_total_async`, `_build_position`, `_aggregate`

## `v1.price_service`

- **Path**: `v1/price_service.py`
- **LoC**: 416
- **In-degree**: 4
- **Imports**: `v1.holdings_manager`
- **Functions/methods**: `_make_session`, `is_valid`, `is_stale`, `__init__`, `fetch_prices`, `fetch_prices_async`, `_gather_aiohttp`, `_gather_aiohttp`, `_fetch_finnhub_async`, `_finnhub_bidask_async`

## `v1.test_portfolio_sync`

- **Path**: `v1/test_portfolio_sync.py`
- **LoC**: 419
- **In-degree**: 0
- **Imports**: `v1.main_sync`, `v1.plaid_client`, `v1.portfolio_aggregator`, `v1.price_service`
- **Functions/methods**: `_make_holding`, `_make_price`, `_make_portfolio`, `test_brk_b_normalised`, `test_bf_b_normalised`, `test_regular_ticker_unchanged`, `test_lowercase_input`, `test_reverse_map`, `test_valid_price`, `test_mid_price_formula`

## `v1.test_smart_sync`

- **Path**: `v1/test_smart_sync.py`
- **LoC**: 165
- **In-degree**: 0
- **Imports**: `v1.holdings_manager`, `v1.portfolio_aggregator`, `v1.price_service`
- **Functions/methods**: `_h`, `_c`, `_p`, `_agg`, `test_age_fresh`, `test_not_stale_young`, `test_stale_after_24h`, `test_tickers`, `test_round_trip`, `_path`

## `v1.tests.test_all`

- **Path**: `v1/tests/test_all.py`
- **LoC**: 406
- **In-degree**: 0
- **Imports**: `v1.utils`
- **Functions/methods**: `setup_method`, `test_transaction_count`, `test_sell_count`, `test_buy_count`, `test_amd_fully_sold`, `test_xop_fully_sold`, `test_voo_accumulated`, `test_drip_detected`, `test_drip_cost_tracked`, `test_dividends_tracked`

## `v1.utils`

- **Path**: `v1/utils/__init__.py`
- **LoC**: 1
- **In-degree**: 1
- **Imports**: _none_
- **Functions/methods**: _none_

## `v1.utils.csv_parser`

- **Path**: `v1/utils/csv_parser.py`
- **LoC**: 382
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: `_safe_float`, `_parse_lines`, `parse_robinhood_csv`, `merge_csvs`, `reconcile`

## `v1.utils.price_fetcher`

- **Path**: `v1/utils/price_fetcher.py`
- **LoC**: 155
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: `_to_yf`, `_from_yf`, `fetch_all_prices`, `force_refresh_prices`, `get_equity_summary`

## `v1.utils.rec_engine`

- **Path**: `v1/utils/rec_engine.py`
- **LoC**: 235
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: `_tax_note`, `_drip_note`, `generate_rec`

## `v2.backend.app`

- **Path**: `v2/backend/app/__init__.py`
- **LoC**: 1
- **In-degree**: 10
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.config`

- **Path**: `v2/backend/app/config.py`
- **LoC**: 74
- **In-degree**: 13
- **Imports**: _none_
- **Functions/methods**: `get_settings`

## `v2.backend.app.database`

- **Path**: `v2/backend/app/database.py`
- **LoC**: 40
- **In-degree**: 14
- **Imports**: `v2.backend.app.config`
- **Functions/methods**: `get_supabase_client`, `get_supabase_anon_client`, `get_user_client`

## `v2.backend.app.main`

- **Path**: `v2/backend/app/main.py`
- **LoC**: 90
- **In-degree**: 0
- **Imports**: `v2.backend.app.config`, `v2.backend.app.routers`
- **Functions/methods**: `lifespan`, `create_app`, `health_check`

## `v2.backend.app.middleware`

- **Path**: `v2/backend/app/middleware/__init__.py`
- **LoC**: 1
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.middleware.auth`

- **Path**: `v2/backend/app/middleware/auth.py`
- **LoC**: 102
- **In-degree**: 9
- **Imports**: `v2.backend.app.config`
- **Functions/methods**: `get_jwks_client`, `get_current_user`, `__init__`

## `v2.backend.app.models`

- **Path**: `v2/backend/app/models/__init__.py`
- **LoC**: 9
- **In-degree**: 0
- **Imports**: `v2.backend.app.models.deposit`, `v2.backend.app.models.portfolio`, `v2.backend.app.models.position`, `v2.backend.app.models.price`, `v2.backend.app.models.recommendation`, `v2.backend.app.models.transaction`, `v2.backend.app.models.user`
- **Functions/methods**: _none_

## `v2.backend.app.models.deposit`

- **Path**: `v2/backend/app/models/deposit.py`
- **LoC**: 57
- **In-degree**: 3
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.models.drip`

- **Path**: `v2/backend/app/models/drip.py`
- **LoC**: 38
- **In-degree**: 2
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.models.portfolio`

- **Path**: `v2/backend/app/models/portfolio.py`
- **LoC**: 103
- **In-degree**: 3
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.models.position`

- **Path**: `v2/backend/app/models/position.py`
- **LoC**: 82
- **In-degree**: 2
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.models.price`

- **Path**: `v2/backend/app/models/price.py`
- **LoC**: 64
- **In-degree**: 3
- **Imports**: _none_
- **Functions/methods**: `is_valid`

## `v2.backend.app.models.recommendation`

- **Path**: `v2/backend/app/models/recommendation.py`
- **LoC**: 134
- **In-degree**: 3
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.models.transaction`

- **Path**: `v2/backend/app/models/transaction.py`
- **LoC**: 56
- **In-degree**: 2
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.models.user`

- **Path**: `v2/backend/app/models/user.py`
- **LoC**: 82
- **In-degree**: 2
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.routers`

- **Path**: `v2/backend/app/routers/__init__.py`
- **LoC**: 1
- **In-degree**: 1
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.routers.ai`

- **Path**: `v2/backend/app/routers/ai.py`
- **LoC**: 55
- **In-degree**: 0
- **Imports**: `v2.backend.app.config`, `v2.backend.app.middleware.auth`, `v2.backend.app.services.ai_service`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `_make_price_service`, `ai_rebalance_latest`, `ai_rebalance`

## `v2.backend.app.routers.auth`

- **Path**: `v2/backend/app/routers/auth.py`
- **LoC**: 158
- **In-degree**: 0
- **Imports**: `v2.backend.app.database`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.user`, `v2.backend.app.services.crypto_service`
- **Functions/methods**: `signup`, `login`, `get_profile`, `update_profile`, `update_api_keys`

## `v2.backend.app.routers.deposits`

- **Path**: `v2/backend/app/routers/deposits.py`
- **LoC**: 64
- **In-degree**: 0
- **Imports**: `v2.backend.app.middleware.auth`, `v2.backend.app.models.deposit`, `v2.backend.app.services.deposit_service`
- **Functions/methods**: `get_deposit_schedule`, `get_allocation_formula`, `create_deposit_plan`, `execute_deposit`

## `v2.backend.app.routers.drip`

- **Path**: `v2/backend/app/routers/drip.py`
- **LoC**: 52
- **In-degree**: 0
- **Imports**: `v2.backend.app.config`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.drip`, `v2.backend.app.services.drip_service`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `_make_price_service`, `get_drip_summary`, `get_drip_positions`, `get_drip_history`

## `v2.backend.app.routers.portfolio`

- **Path**: `v2/backend/app/routers/portfolio.py`
- **LoC**: 179
- **In-degree**: 0
- **Imports**: `v2.backend.app.database`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.portfolio`, `v2.backend.app.services.portfolio_service`
- **Functions/methods**: `get_portfolio_summary`, `list_snapshots`, `create_snapshot`, `list_targets`, `set_targets`, `calculate_rebalance`, `get_cash_balance`, `backfill_snapshots`, `update_cash_override`

## `v2.backend.app.routers.positions`

- **Path**: `v2/backend/app/routers/positions.py`
- **LoC**: 214
- **In-degree**: 0
- **Imports**: `v2.backend.app.config`, `v2.backend.app.database`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.position`, `v2.backend.app.services.migration_service`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `_make_price_service`, `_enrich_position`, `list_positions`, `get_position`, `create_position`, `update_position`, `delete_position`, `seed_from_v1`

## `v2.backend.app.routers.prices`

- **Path**: `v2/backend/app/routers/prices.py`
- **LoC**: 216
- **In-degree**: 1
- **Imports**: `v2.backend.app.config`, `v2.backend.app.database`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.price`, `v2.backend.app.services.crypto_service`, `v2.backend.app.services.history_service`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `_get_price_service`, `get_price`, `get_batch_prices`, `get_price_history`, `price_health`

## `v2.backend.app.routers.recommendations`

- **Path**: `v2/backend/app/routers/recommendations.py`
- **LoC**: 148
- **In-degree**: 0
- **Imports**: `v2.backend.app.config`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.recommendation`, `v2.backend.app.services.agents.job_runner`, `v2.backend.app.services.price_engine`, `v2.backend.app.services.recommendation_engine`
- **Functions/methods**: `_make_price_service`, `list_active_recommendations`, `refresh_recommendations`, `get_job_status`, `get_run_insights`, `get_latest_insights`, `resolve_recommendation`, `list_decisions`, `log_decision`

## `v2.backend.app.routers.sync`

- **Path**: `v2/backend/app/routers/sync.py`
- **LoC**: 281
- **In-degree**: 0
- **Imports**: `v2.backend.app.config`, `v2.backend.app.database`, `v2.backend.app.middleware.auth`, `v2.backend.app.models.transaction`, `v2.backend.app.routers.prices`, `v2.backend.app.services.crypto_service`, `v2.backend.app.services.import_service`, `v2.backend.app.services.plaid_service`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `_parse_crypto_pdf`, `sync_plaid`, `plaid_sync_status`, `import_csv`, `refresh_prices`, `import_crypto_pdf`

## `v2.backend.app.services`

- **Path**: `v2/backend/app/services/__init__.py`
- **LoC**: 1
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.app.services.agents`

- **Path**: `v2/backend/app/services/agents/__init__.py`
- **LoC**: 26
- **In-degree**: 0
- **Imports**: `v2.backend.app.services.agents.orchestrator`, `v2.backend.app.services.agents.state`
- **Functions/methods**: _none_

## `v2.backend.app.services.agents.data_sources`

- **Path**: `v2/backend/app/services/agents/data_sources.py`
- **LoC**: 318
- **In-degree**: 4
- **Imports**: _none_
- **Functions/methods**: `_get_client`, `fetch_finnhub_news`, `fetch_yfinance_news_sync`, `fetch_yfinance_news`, `fetch_news_for_ticker`, `fetch_yfinance_history_sync`, `fetch_price_action`, `fetch_polygon_aggs`, `fetch_yfinance_fundamentals_sync`, `fetch_fundamentals`

## `v2.backend.app.services.agents.fundamental_agent`

- **Path**: `v2/backend/app/services/agents/fundamental_agent.py`
- **LoC**: 74
- **In-degree**: 1
- **Imports**: `v2.backend.app.services.agents.data_sources`, `v2.backend.app.services.agents.llm`, `v2.backend.app.services.agents.state`
- **Functions/methods**: `run_fundamental_agent`

## `v2.backend.app.services.agents.job_runner`

- **Path**: `v2/backend/app/services/agents/job_runner.py`
- **LoC**: 98
- **In-degree**: 2
- **Imports**: `v2.backend.app.config`, `v2.backend.app.database`, `v2.backend.app.services.agents.orchestrator`, `v2.backend.app.services.crypto_service`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `_user_keys`, `_make_price_service`, `build_orchestrator`, `run_agent_pipeline`

## `v2.backend.app.services.agents.llm`

- **Path**: `v2/backend/app/services/agents/llm.py`
- **LoC**: 99
- **In-degree**: 5
- **Imports**: _none_
- **Functions/methods**: `_extract_json`, `clamp`, `__init__`, `_ensure_client`, `ask_json`, `_call`

## `v2.backend.app.services.agents.orchestrator`

- **Path**: `v2/backend/app/services/agents/orchestrator.py`
- **LoC**: 403
- **In-degree**: 2
- **Imports**: `v2.backend.app.database`, `v2.backend.app.services.agents.data_sources`, `v2.backend.app.services.agents.fundamental_agent`, `v2.backend.app.services.agents.llm`, `v2.backend.app.services.agents.portfolio_manager`, `v2.backend.app.services.agents.sentiment_agent`, `v2.backend.app.services.agents.state`, `v2.backend.app.services.agents.technical_agent`
- **Functions/methods**: `_round`, `__init__`, `create_run`, `run`, `_bootstrap`, `_fanout`, `_persist`, `_update_run`, `_rationale_line`, `_urgency`

## `v2.backend.app.services.agents.portfolio_manager`

- **Path**: `v2/backend/app/services/agents/portfolio_manager.py`
- **LoC**: 230
- **In-degree**: 1
- **Imports**: `v2.backend.app.services.agents.llm`, `v2.backend.app.services.agents.state`
- **Functions/methods**: `compute_conviction`, `conviction_to_action`, `allocate_cash`, `run_portfolio_manager`, `_build_batch_context`, `_fallback_thesis`, `_fallback_summary`

## `v2.backend.app.services.agents.sentiment_agent`

- **Path**: `v2/backend/app/services/agents/sentiment_agent.py`
- **LoC**: 77
- **In-degree**: 1
- **Imports**: `v2.backend.app.services.agents.data_sources`, `v2.backend.app.services.agents.llm`, `v2.backend.app.services.agents.state`
- **Functions/methods**: `run_sentiment_agent`

## `v2.backend.app.services.agents.state`

- **Path**: `v2/backend/app/services/agents/state.py`
- **LoC**: 90
- **In-degree**: 6
- **Imports**: _none_
- **Functions/methods**: `_round`, `to_insight_row`, `cash_to_deploy`

## `v2.backend.app.services.agents.technical_agent`

- **Path**: `v2/backend/app/services/agents/technical_agent.py`
- **LoC**: 84
- **In-degree**: 1
- **Imports**: `v2.backend.app.services.agents.data_sources`, `v2.backend.app.services.agents.llm`, `v2.backend.app.services.agents.state`
- **Functions/methods**: `run_technical_agent`

## `v2.backend.app.services.ai_service`

- **Path**: `v2/backend/app/services/ai_service.py`
- **LoC**: 339
- **In-degree**: 1
- **Imports**: `v2.backend.app.config`, `v2.backend.app.database`, `v2.backend.app.services.crypto_service`
- **Functions/methods**: `_extract_json`, `_clean_ai_text`, `__init__`, `generate_rebalance`, `get_latest_analysis`

## `v2.backend.app.services.crypto_service`

- **Path**: `v2/backend/app/services/crypto_service.py`
- **LoC**: 49
- **In-degree**: 5
- **Imports**: `v2.backend.app.config`
- **Functions/methods**: `_get_key`, `encrypt_value`, `decrypt_value`

## `v2.backend.app.services.deposit_service`

- **Path**: `v2/backend/app/services/deposit_service.py`
- **LoC**: 171
- **In-degree**: 1
- **Imports**: `v2.backend.app.database`, `v2.backend.app.models.deposit`
- **Functions/methods**: `_next_biweekly_friday`, `__init__`, `get_formula`, `get_schedule`, `create_plan`, `execute_plan`, `_to_response`

## `v2.backend.app.services.drip_service`

- **Path**: `v2/backend/app/services/drip_service.py`
- **LoC**: 253
- **In-degree**: 1
- **Imports**: `v2.backend.app.database`, `v2.backend.app.models.drip`, `v2.backend.app.services.recommendation_engine`
- **Functions/methods**: `_get_dividend_dates`, `__init__`, `get_summary`, `get_positions`, `get_history`, `_to_iso`

## `v2.backend.app.services.history_service`

- **Path**: `v2/backend/app/services/history_service.py`
- **LoC**: 256
- **In-degree**: 1
- **Imports**: _none_
- **Functions/methods**: `__init__`, `to_dict`, `__init__`, `_get_http`, `close`, `get_history`, `get_batch_history`, `_fetch_yfinance`, `_read_cache`, `_write_cache`

## `v2.backend.app.services.import_service`

- **Path**: `v2/backend/app/services/import_service.py`
- **LoC**: 303
- **In-degree**: 1
- **Imports**: _none_
- **Functions/methods**: `_norm_decimal`, `_norm_date`, `make_fingerprint`, `__init__`, `import_robinhood_csv`, `reconcile_positions_from_transactions`

## `v2.backend.app.services.migration_service`

- **Path**: `v2/backend/app/services/migration_service.py`
- **LoC**: 104
- **In-degree**: 1
- **Imports**: `v2.backend.app.database`
- **Functions/methods**: `seed_v1_positions`

## `v2.backend.app.services.plaid_service`

- **Path**: `v2/backend/app/services/plaid_service.py`
- **LoC**: 384
- **In-degree**: 1
- **Imports**: _none_
- **Functions/methods**: `is_fresh`, `__init__`, `get_sync_status`, `sync_holdings`, `_call_plaid`, `_upsert_positions`, `_log_sync`

## `v2.backend.app.services.portfolio_service`

- **Path**: `v2/backend/app/services/portfolio_service.py`
- **LoC**: 659
- **In-degree**: 1
- **Imports**: `v2.backend.app.config`, `v2.backend.app.database`, `v2.backend.app.models.portfolio`, `v2.backend.app.services.price_engine`
- **Functions/methods**: `__init__`, `_get_price_service`, `get_summary`, `list_snapshots`, `create_snapshot`, `list_targets`, `set_targets`, `calculate_rebalance`, `backfill_snapshots_from_transactions`, `_get_price`

## `v2.backend.app.services.price_engine`

- **Path**: `v2/backend/app/services/price_engine.py`
- **LoC**: 580
- **In-degree**: 8
- **Imports**: _none_
- **Functions/methods**: `is_valid`, `is_stale`, `is_open`, `record_failure`, `record_success`, `__init__`, `_get_client`, `close`, `fetch_prices`, `fetch_one`

## `v2.backend.app.services.price_service`

- **Path**: `v2/backend/app/services/price_service.py`
- **LoC**: 131
- **In-degree**: 0
- **Imports**: `v2.backend.app.database`, `v2.backend.app.models.price`
- **Functions/methods**: `__init__`, `get_quote`, `get_batch_quotes`, `get_history`, `get_health_status`, `refresh_all`

## `v2.backend.app.services.recommendation_engine`

- **Path**: `v2/backend/app/services/recommendation_engine.py`
- **LoC**: 575
- **In-degree**: 2
- **Imports**: `v2.backend.app.database`, `v2.backend.app.models.recommendation`, `v2.backend.app.services.agents.job_runner`
- **Functions/methods**: `_classify_action`, `_tax_note`, `_drip_note`, `generate_rec`, `_make`, `__init__`, `get_insight_cards`, `queue_agent_run`, `get_job_status`, `get_agent_insights`

## `v2.backend.tests`

- **Path**: `v2/backend/tests/__init__.py`
- **LoC**: 1
- **In-degree**: 0
- **Imports**: _none_
- **Functions/methods**: _none_

## `v2.backend.tests.test_auth`

- **Path**: `v2/backend/tests/test_auth.py`
- **LoC**: 27
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_basic_construction`, `test_family_role`, `test_uuid_type`

## `v2.backend.tests.test_crypto_service`

- **Path**: `v2/backend/tests/test_crypto_service.py`
- **LoC**: 54
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_basic_string`, `test_empty_string`, `test_unicode_string`, `test_long_string`, `test_different_encryptions_same_input`, `test_tampered_ciphertext_fails`

## `v2.backend.tests.test_history_service`

- **Path**: `v2/backend/tests/test_history_service.py`
- **LoC**: 89
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_basic_construction`, `test_to_dict`, `test_today_is_fresh`, `test_yesterday_is_fresh`, `test_weekend_friday_is_fresh`, `test_old_data_is_stale`, `test_empty_list_is_not_fresh`, `test_uses_last_point`, `test_init_no_supabase`, `test_close_idempotent`

## `v2.backend.tests.test_import_service`

- **Path**: `v2/backend/tests/test_import_service.py`
- **LoC**: 126
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_basic_number`, `test_dollar_sign`, `test_commas`, `test_dollar_and_commas`, `test_parenthetical_negative`, `test_empty_string`, `test_none_value`, `test_zero`, `test_high_precision`, `test_different_formats_same_output`

## `v2.backend.tests.test_models`

- **Path**: `v2/backend/tests/test_models.py`
- **LoC**: 224
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_user_create_valid`, `test_user_create_invalid_email`, `test_user_create_short_password`, `test_user_update_partial`, `test_user_create_custom_settings`, `test_position_create_stock`, `test_position_create_crypto`, `test_position_create_invalid_category`, `test_position_update_partial`, `test_position_with_price`

## `v2.backend.tests.test_plaid_service`

- **Path**: `v2/backend/tests/test_plaid_service.py`
- **LoC**: 666
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `_make_plaid_response`, `_make_service`, `test_success`, `test_cached`, `test_error`, `test_fresh_status`, `test_stale_status`, `test_default_never_synced`, `test_cache_ttl_boundary`, `test_just_under_ttl`

## `v2.backend.tests.test_portfolio_service`

- **Path**: `v2/backend/tests/test_portfolio_service.py`
- **LoC**: 57
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_basic_summary`, `test_empty_portfolio`, `test_negative_pnl`, `test_category_breakdown_sums`

## `v2.backend.tests.test_price_engine`

- **Path**: `v2/backend/tests/test_price_engine.py`
- **LoC**: 275
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_valid_price`, `test_invalid_zero_price`, `test_invalid_with_error`, `test_stale_cache_source`, `test_stale_institution_source`, `test_not_stale_fresh_source`, `test_initial_state_closed`, `test_opens_after_threshold`, `test_below_threshold_stays_closed`, `test_resets_on_success`

## `v2.backend.tests.test_recommendation_engine`

- **Path**: `v2/backend/tests/test_recommendation_engine.py`
- **LoC**: 554
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `test_sell_keyword`, `test_sell_emoji`, `test_buy_keyword`, `test_accumulate_keyword`, `test_dca_keyword`, `test_buy_emoji_green`, `test_buy_emoji_fire`, `test_buy_emoji_chart`, `test_trim_keyword`, `test_trim_emoji`

## `v2.backend.tests.test_sync`

- **Path**: `v2/backend/tests/test_sync.py`
- **LoC**: 527
- **In-degree**: 0
- **Imports**: `v2.backend.app`
- **Functions/methods**: `_make_pdf_bytes`, `_parse_with_text`, `test_primary_bitcoin`, `test_primary_xrp`, `test_primary_multiple_coins`, `test_primary_case_insensitive_coin_name`, `test_primary_dogecoin`, `test_primary_avalanche`, `test_primary_ignores_zero_quantity`, `test_primary_comma_in_dollar_value`
