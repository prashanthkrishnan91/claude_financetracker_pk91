# Consolidated Three-View App — Runtime Proof: API Response Captures

Captured 2026-07-18 from the REAL application code running locally over HTTP:

- Backend: `v2/backend` FastAPI (`app.main:app`) served by uvicorn on `127.0.0.1:8000`.
- Frontend: `v2/frontend` Next 14 **production build** (`next build` + `next start`) on `127.0.0.1:3000`.
- Auth: a local fake GoTrue server on `127.0.0.1:54321` issued a **real HS256 JWT**
  (aud=`authenticated`, sub=the fixed proof user UUID) and served a JWKS; the backend's
  real JWT middleware (`app/middleware/auth.py`, PyJWKClient + `jwt.decode`) validated
  every request. Tokens are omitted from all captures.

## What ran real vs. fixture-staged

**Real code paths (unmodified):** all FastAPI routers, JWT validation, the
deterministic allocation policy (`allocation_policy_v1.run_next_buy_policy_diagnostic`),
the tax-lot engine (`tax_lot_engine`), the Intel v3 snapshot read path
(`IntelV3Service.get_latest_snapshot` incl. evidence-freshness embedding), the
Run-Intel enqueue path (`enqueue_run_v3` incl. real `analyst_refresh_jobs` row writes via
`enqueue_refresh_jobs`), the watchlist router (incl. the 409 duplicate policy), the
paycheck-plan preview mapping, the Next.js API route
`/api/advisor/paycheck-plan/preview` (server-side cert-secret injection), and the
Next.js production frontend with React Query.

**Fixture-injected at the outermost boundaries only:**

1. **Supabase DB client** — every module-level `get_supabase_client` binding was
   replaced with a factory returning an in-memory postgrest-like client seeded with a
   coherent 6-position portfolio for the proof user (pattern-matched from the repo's
   own test fakes in `tests/test_watchlist_router.py` / `tests/test_allocation_policy_v1.py`).
   The `intel_v3_snapshots.payload` fixture was produced by calling the REAL
   `snapshot_builder.build_snapshot()` with real `DecisionOutputV3` objects, then adding
   the worker-certification fields (`snapshot_source=worker_certified`, counts,
   `certification_summary`) exactly as the analyst-refresh worker does.
2. **Supabase auth server** — fake GoTrue on :54321 (see above).
3. **`PriceService.fetch_prices`** — returns fixture `PriceResult`s with fresh
   timestamps; no live provider calls. Tickers without a fixture price (e.g. PLTR)
   return no result, exercising the app's honest unknown-price handling.
4. **Run-Intel scenarios only** (`run_partial` / `run_complete`): `run_on_demand_drain`
   was replaced with a deterministic result and the fast freshness gate was made
   unavailable so the endpoint exercised its real full-ticker fallback. This is
   fixture-driven **staging of the REAL `/intel/v3/run` response contract** — the
   enqueue, job-store writes, snapshot lookup, and `next_required_action` derivation
   are the real code. The drain itself normally performs LLM analyst work, which is
   impossible without provider credentials.

Scenarios are selected per backend process via `FIXTURE_SCENARIO`
(baseline / degraded / run_partial / run_complete / no_snapshot).

---

## 1. `GET /api/v1/positions` — scenario: baseline (real router + real JWT middleware; DB + prices fixture)

6 rows returned (VTI, SPY, SCHD, NVDA, AAPL, BTC). Trimmed to first + last row;
`current_price` / `market_value` / `unrealised_pnl` were computed by the real
`_enrich_position` from the fixture PriceResults.

```json
[
  {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "category": "Core",
    "shares": "10.0",
    "avg_cost": "150.0",
    "drip_shares": "0",
    "drip_cost": "0",
    "divs_received": "0",
    "target_price": null,
    "bear_price": null,
    "bull_price": null,
    "lt_eligible": false,
    "lt_date": null,
    "coingecko_id": null,
    "id": "066c3930-23ba-5e03-95ba-dcac4fe1625d",
    "user_id": "9d5b1f0a-3c77-4e0b-9c2e-1f2a3b4c5d6e",
    "source": "csv_import",
    "last_synced_at": "2026-07-18T12:26:35.503378Z",
    "created_at": "2025-06-13T15:26:35.503378Z",
    "updated_at": "2026-07-18T12:26:35.503378Z",
    "current_price": 211.85,
    "market_value": 2118.5,
    "unrealised_pnl": 618.5,
    "unrealised_pnl_pct": 41.2333,
    "price_source": null,
    "day_change": null,
    "day_change_pct": null
  },
  {
    "ticker": "VTI",
    "name": "Vanguard Total Stock Market ETF",
    "category": "ETF",
    "shares": "20.0",
    "avg_cost": "210.0",
    "drip_shares": "0",
    "drip_cost": "0",
    "divs_received": "0",
    "target_price": null,
    "bear_price": null,
    "bull_price": null,
    "lt_eligible": false,
    "lt_date": null,
    "coingecko_id": null,
    "id": "92f0535b-d8e6-5b87-b848-68f6064cbf75",
    "user_id": "9d5b1f0a-3c77-4e0b-9c2e-1f2a3b4c5d6e",
    "source": "csv_import",
    "last_synced_at": "2026-07-18T12:26:35.503378Z",
    "created_at": "2025-06-13T15:26:35.503378Z",
    "updated_at": "2026-07-18T12:26:35.503378Z",
    "current_price": 285.12,
    "market_value": 5702.4,
    "unrealised_pnl": 1502.4,
    "unrealised_pnl_pct": 35.7714,
    "price_source": null,
    "day_change": null,
    "day_change_pct": null
  }
]
```

## 2. `GET /api/v1/positions/tax-lots` — scenario: baseline (real tax_lot_engine over fixture transactions)

The fixture transactions reconcile exactly for VTI/SPY/SCHD/NVDA/BTC and are
deliberately incomplete for AAPL (6 of 10 held shares have buys) → real
`quantity_mismatch` gating. VTI shows one long-term (2024-11-10) and one
short-term (2026-03-05) FIFO lot. Trimmed to VTI + AAPL.

```json
{
  "engine_version": "tax_lot_engine_v1",
  "jurisdiction_note": "Holding periods use the US federal convention (long-term begins the day after the one-year calendar anniversary). Estimates only \u2014 not tax advice.",
  "disclaimer": "All tax-lot figures are estimates derived from imported transactions. They are not tax advice.",
  "holdings (trimmed to VTI + AAPL of 6)": [
    {
      "ticker": "VTI",
      "reconciliation": {
        "position_shares": 20.0,
        "lot_shares": 20.0,
        "share_difference": 0.0,
        "quantity_tolerance": 0.02,
        "position_cost_basis": 4200.0,
        "lot_cost_basis": 4200.0,
        "basis_difference_pct": 0.0,
        "basis_tolerance_pct": 2.0,
        "status": "reconciled"
      },
      "authoritative": true,
      "message": null,
      "lots": [
        {
          "acquired_date": "2024-11-10",
          "source_tx_type": "Buy",
          "remaining_shares": 12.0,
          "cost_per_share": 205.0,
          "cost_basis": 2460.0,
          "estimated_holding_classification": "long_term",
          "estimated_long_term_start_date": "2025-11-11",
          "days_until_long_term": 0,
          "current_value": 3421.44,
          "unrealized_gain": 961.44,
          "unrealized_gain_pct": 39.0829
        },
        {
          "acquired_date": "2026-03-05",
          "source_tx_type": "Buy",
          "remaining_shares": 8.0,
          "cost_per_share": 217.5,
          "cost_basis": 1740.0,
          "estimated_holding_classification": "short_term",
          "estimated_long_term_start_date": "2027-03-06",
          "days_until_long_term": 231,
          "current_value": 2280.96,
          "unrealized_gain": 540.96,
          "unrealized_gain_pct": 31.0897
        }
      ],
      "unsupported_events": [],
      "event_counts": {
        "share_increasing": 2
      }
    },
    {
      "ticker": "AAPL",
      "reconciliation": {
        "position_shares": 10.0,
        "lot_shares": 6.0,
        "share_difference": -4.0,
        "quantity_tolerance": 0.01,
        "position_cost_basis": 1500.0,
        "lot_cost_basis": 888.0,
        "basis_difference_pct": null,
        "basis_tolerance_pct": 2.0,
        "status": "quantity_mismatch"
      },
      "authoritative": false,
      "message": "Tax-lot details need reconciliation before they can be relied on.",
      "lots": null,
      "unsupported_events": [],
      "event_counts": {
        "share_increasing": 1
      }
    }
  ]
}
```

## 3. `GET /api/v1/intel/v3/snapshot` — scenario: baseline (real read path; payload built by the real `snapshot_builder.build_snapshot`)

Top level (card arrays collapsed), then the NVDA card (drawer payload trimmed).
`evidence_freshness_state` was computed at response time by the real
`get_evidence_freshness_state` against the fixture `portfolio_snapshots` row.

```json
{
  "schema_version": "v3.1",
  "snapshot_id": "39bf707d-b84b-4d11-92c1-1e66e989be0e",
  "run_id": "d6a5c88b-f7ee-49d6-9166-22ae8670d6ea",
  "generated_at": "2026-07-18T15:26:35.504123+00:00",
  "is_stale": false,
  "source_health": {
    "status": "ok"
  },
  "portfolio_command_center": {
    "total_holdings": 6,
    "buy_count": 1,
    "hold_count": 5,
    "trim_count": 0,
    "sell_count": 0,
    "high_conviction": 1,
    "thin_evidence": 2,
    "source_health": {
      "status": "ok"
    }
  },
  "action_counts": {
    "BUY": 1,
    "HOLD": 5
  },
  "evidence_band_counts": {
    "STRONG": 1,
    "PARTIAL": 3,
    "THIN": 2
  },
  "source_pack_validated_count": 4,
  "source_pack_pending_count": 2,
  "conviction_counts": {
    "HIGH": 1,
    "MEDIUM": 3,
    "LOW": 2
  },
  "opportunity_radar_preview": {
    "status": "deferred",
    "reason": "Opportunity Radar launches after held-position spine is stable."
  },
  "what_changed": [],
  "warnings": [],
  "legacy_path_used": false,
  "evidence_mapping_version": "analyst_verdict_synthesis_v1",
  "stage7_explanation_contract_version": "stage7_explanation_v2",
  "stage8e_catalyst_explanation_contract_version": "stage8e_catalyst_explanation_v1",
  "stage8f_filing_type_contract_version": "stage8f_filing_type_v1",
  "snapshot_source": "worker_certified",
  "agents_ran_via_worker": true,
  "this_click_used_llm": false,
  "certified_holding_count": 6,
  "total_holding_count": 6,
  "failed_tickers_in_certification": [],
  "certification_summary": {
    "certified": true,
    "certified_holding_count": 6,
    "total_holding_count": 6,
    "failed_holding_count": 0,
    "latest_agent_run_at": "2026-07-18T14:46:35.503378+00:00",
    "latest_recommendation_at": "2026-07-18T14:46:35.503378+00:00",
    "agent_run_ids_used": [
      "72be08d7-a422-4307-a8d6-ca2d5970f24f"
    ],
    "certification_errors": []
  },
  "evidence_freshness_state": "certified_current",
  "current_holdings": "[ 6 cards \u2014 one shown below ]"
}
```

NVDA card (action BUY, evidence STRONG, updated now — this is what gates NVDA
into the cash plan's stock sleeve):

```json
{
  "ticker": "NVDA",
  "name": "NVIDIA Corporation",
  "asset_type": "stock",
  "action": "BUY",
  "conviction": "HIGH",
  "evidence_band": "STRONG",
  "portfolio_fit": "Room to add",
  "risk_level": "MEDIUM",
  "thesis_state": "intact",
  "why_text": "Fundamental and technical evidence both support adding while the position is under target.",
  "risk_text": "A sharp change in demand signals or concentration above cap would change this view.",
  "action_text": "Add to this position if Deploy has room.",
  "what_would_change_view": "A sharp change in demand signals or concentration above cap would change this view.",
  "fit_text": "Room to add",
  "evidence_text": "Multiple independent signals confirm this view.",
  "flags": [],
  "source_snapshot_id": "39bf707d-b84b-4d11-92c1-1e66e989be0e",
  "source_run_id": "d6a5c88b-f7ee-49d6-9166-22ae8670d6ea",
  "updated_at": "2026-07-18T15:26:35.504179+00:00",
  "detail_drawer_payload": {
    "rationale": "Fundamental and technical evidence both support adding while the position is under target.",
    "evidence_band": "STRONG",
    "evidence_quality": "STRONG",
    "committee": {
      "status": "source_validated"
    },
    "price_context": "SUPPRESSED",
    "portfolio_fit_raw": "UNDERWEIGHT",
    "risk_band": "MEDIUM",
    "schema_version": "v3.1",
    "( \u2026 evidence_explanation / asset_intelligence_context trimmed \u2026 )": "present"
  }
}
```

## 4. `POST http://127.0.0.1:3000/api/advisor/paycheck-plan/preview` — scenario: baseline (through the REAL Next.js route; cert secret injected server-side; real allocation policy)

Request body `{"cash_to_deploy": 2500}` with only the user's Authorization header —
the browser never sees `X-Finance-Runtime-Cert-Secret`. `trusted: true` because
reconciliation passes (snapshot equity == position-derived value) and all prices are
fresh. Full response:

```json
{
  "preview_version": "paycheck_plan_preview_v1",
  "cash_to_deploy": 2500,
  "generated_at": "2026-07-18T15:26:36.362797+00:00",
  "trusted": true,
  "status": "ready",
  "planned_buys": [
    {
      "ticker": "NVDA",
      "amount": 2500,
      "reason": "This asset group is underweight versus its target; Passed evidence freshness, confidence, and concentration checks",
      "reason_codes": [
        "individual_stock_group_underweight",
        "evidence_fresh_and_constructive"
      ]
    }
  ],
  "explanations": {
    "selected": [
      {
        "ticker": "NVDA",
        "asset_type": "equity",
        "amount": 2500,
        "percent_of_deployable_cash": 100,
        "reasons": [
          "This asset group is underweight versus its target",
          "Passed evidence freshness, confidence, and concentration checks"
        ],
        "evidence": {
          "action": "BUY",
          "evidence_band": "STRONG"
        },
        "policy_role": null,
        "raw_codes": [
          "individual_stock_group_underweight",
          "evidence_fresh_and_constructive"
        ]
      }
    ],
    "not_selected": [
      {
        "ticker": "AAPL",
        "bucket": "evidence_blocked",
        "plain_english": "AAPL is not eligible: Its Intel action is HOLD \u2014 only BUY evidence makes a stock eligible for new cash.",
        "raw_codes": [
          "evidence_signal_not_constructive"
        ]
      },
      {
        "ticker": "BTC",
        "bucket": "concentration_blocked",
        "plain_english": "BTC: This position is already at or above its concentration cap.",
        "raw_codes": [
          "at_or_above_crypto_cap_5.0pct"
        ]
      },
      {
        "ticker": "SCHD",
        "bucket": "group_cap_blocked",
        "plain_english": "SCHD: Its ETF group is already above its target allocation.",
        "raw_codes": [
          "etf_group_dividend_etf_already_above_target"
        ]
      },
      {
        "ticker": "SPY",
        "bucket": "group_cap_blocked",
        "plain_english": "SPY: Its ETF group is already above its target allocation.",
        "raw_codes": [
          "etf_group_broad_index_etf_already_above_target"
        ]
      },
      {
        "ticker": "VTI",
        "bucket": "group_cap_blocked",
        "plain_english": "VTI: Its ETF group is already above its target allocation.",
        "raw_codes": [
          "etf_group_broad_index_etf_already_above_target"
        ]
      }
    ],
    "plan_notes": []
  },
  "allocation_summary": {
    "allocated_cash": 2500,
    "unallocated_cash": 0,
    "allocation_count": 1
  },
  "data_freshness_status": "ok",
  "caveats": [
    "This is deterministic allocation guidance, not personalized investment advice."
  ],
  "next_required_fix": null,
  "recommendations_trusted": false,
  "source_diagnostic_version": "allocation_policy_v1"
}
```

## 5. Watchlist CRUD cycle — scenario: baseline (real router; DB fixture; PLTR has no fixture price)

`POST /api/v1/watchlist` (VTI price_below 300) → 201:

```json
{
  "id": "8872d9c9-8e96-40ac-aa7d-00aedf22956e",
  "ticker": "VTI",
  "criteria_type": "price_below",
  "threshold": 300.0,
  "notes": "Add if it dips",
  "created_at": "2026-07-18T15:26:36.396832Z",
  "updated_at": null,
  "current_price": null,
  "price_as_of": null,
  "criteria_met": null
}
```

Duplicate `POST` (VTI price_below again) → **HTTP 409** (real duplicate policy):

```json
{
  "detail": {
    "error": "duplicate_watchlist_entry",
    "message": "VTI already has a price below entry. Edit that entry instead of adding a duplicate."
  }
}
```

`GET /api/v1/watchlist` — VTI enriched with the fixture price (criteria_met=true);
PLTR honestly unknown (`current_price: null`, `criteria_met: null`):

```json
[
  {
    "id": "8872d9c9-8e96-40ac-aa7d-00aedf22956e",
    "ticker": "VTI",
    "criteria_type": "price_below",
    "threshold": 300.0,
    "notes": "Add if it dips",
    "created_at": "2026-07-18T15:26:36.396832Z",
    "updated_at": null,
    "current_price": 285.12,
    "price_as_of": "2026-07-18T15:26:36.500839Z",
    "criteria_met": true
  },
  {
    "id": "e49e4ab1-5e74-4de1-9d30-32f447af16a8",
    "ticker": "PLTR",
    "criteria_type": "price_below",
    "threshold": 100.0,
    "notes": null,
    "created_at": "2026-07-18T15:26:36.489822Z",
    "updated_at": null,
    "current_price": null,
    "price_as_of": null,
    "criteria_met": null
  }
]
```

`PATCH /api/v1/watchlist/{id}` (threshold 290) → 200:

```json
{
  "id": "8872d9c9-8e96-40ac-aa7d-00aedf22956e",
  "ticker": "VTI",
  "criteria_type": "price_below",
  "threshold": 290.0,
  "notes": "Add if it dips",
  "created_at": "2026-07-18T15:26:36.396832Z",
  "updated_at": "2026-07-18T15:26:36.565026Z",
  "current_price": null,
  "price_as_of": null,
  "criteria_met": null
}
```

`DELETE /api/v1/watchlist/{id}` → **HTTP 204** (no body).

## 6. `POST /api/v1/intel/v3/run` — scenario: run_partial (real enqueue + job-store writes; drain result fixture-staged)

The real `enqueue_run_v3` wrote 6 `analyst_refresh_jobs` rows (full-ticker fallback,
gate unavailable), then the staged bounded drain reported 4/6 processed with
resumable=true. `next_required_action` and `snapshot_available_after_run` are derived
by the real router code. The UI renders this as "Continue Intel run".

```json
{
  "status": "refresh_requested",
  "queued_ticker_count": 6,
  "stale_analyst_ticker_count": 6,
  "total_holding_count": 6,
  "existing_certified_snapshot_id": "338022a2-90ac-475b-8ff7-085fdfc02a6e",
  "existing_certified_snapshot": false,
  "run_click_response_ms": 3,
  "certified_snapshot_available_on_click": false,
  "refresh_jobs_pending_count": 6,
  "refresh_jobs_remaining_count": 6,
  "freshness_gate": {},
  "urgent_refresh_triggered": false,
  "message": "Analyst refresh enqueued for 6/6 holdings. Background worker will run LLM analysis and publish a certified snapshot.",
  "on_demand_processing_enabled": true,
  "on_demand_jobs_attempted": 4,
  "on_demand_jobs_succeeded": 4,
  "on_demand_jobs_failed": 0,
  "snapshot_available_after_run": false,
  "next_required_action": "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining"
}
```

## 6b. `POST /api/v1/intel/v3/run` — scenario: run_complete (real enqueue; drain result fixture-staged; certified snapshot in fixture DB)

All 6 jobs drained; the real `get_latest_snapshot` found the worker-certified fixture
snapshot → `snapshot_available_after_run: true`,
`next_required_action: none_certified_snapshot_current`.

```json
{
  "status": "refresh_requested",
  "queued_ticker_count": 6,
  "stale_analyst_ticker_count": 6,
  "total_holding_count": 6,
  "existing_certified_snapshot_id": "dab54ca4-1eca-4908-b0e3-303317b7e6cf",
  "existing_certified_snapshot": true,
  "run_click_response_ms": 3,
  "certified_snapshot_available_on_click": true,
  "refresh_jobs_pending_count": 6,
  "refresh_jobs_remaining_count": 6,
  "freshness_gate": {},
  "urgent_refresh_triggered": false,
  "message": "Analyst refresh enqueued for 6/6 holdings. Background worker will run LLM analysis and publish a certified snapshot.",
  "on_demand_processing_enabled": true,
  "on_demand_jobs_attempted": 6,
  "on_demand_jobs_succeeded": 6,
  "on_demand_jobs_failed": 0,
  "snapshot_available_after_run": true,
  "next_required_action": "none_certified_snapshot_current"
}
```

## 7. `POST /api/advisor/paycheck-plan/preview` — scenario: degraded (real policy; fixture has stale SCHD price_history + 3-day-old snapshot ~2.4% off)

`trusted: false`, `status: "degraded"`, no planned buys, honest caveats and
`next_required_fix`. Trimmed not_selected list.

```json
{
  "preview_version": "paycheck_plan_preview_v1",
  "cash_to_deploy": 2500,
  "generated_at": "2026-07-18T15:26:39.275429+00:00",
  "trusted": false,
  "status": "degraded",
  "planned_buys": [],
  "explanations": {
    "selected": [],
    "not_selected": [
      {
        "ticker": "AAPL",
        "bucket": "evidence_blocked",
        "plain_english": "AAPL is not eligible: Its Intel action is HOLD \u2014 only BUY evidence makes a stock eligible for new cash.",
        "raw_codes": [
          "evidence_signal_not_constructive"
        ]
      },
      {
        "ticker": "BTC",
        "bucket": "concentration_blocked",
        "plain_english": "BTC: This position is already at or above its concentration cap.",
        "raw_codes": [
          "at_or_above_crypto_cap_5.0pct"
        ]
      },
      {
        "ticker": "NVDA",
        "bucket": "below_minimum_trade",
        "plain_english": "NVDA is eligible but the remaining cash is below the $25 minimum trade size.",
        "raw_codes": []
      }
    ],
    "( \u2026 remaining not_selected trimmed \u2026 )": 4,
    "plan_notes": []
  },
  "allocation_summary": {
    "allocated_cash": 2500,
    "unallocated_cash": 0,
    "allocation_count": 1
  },
  "data_freshness_status": "stale",
  "caveats": [
    "This is deterministic allocation guidance, not personalized investment advice.",
    "The numeric plan is not yet fully trusted \u2014 treat these figures as directional only.",
    "No investable buy plan is confirmed until underlying portfolio data is fully refreshed.",
    "Some holdings have stale price data."
  ],
  "next_required_fix": "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
  "recommendations_trusted": false,
  "source_diagnostic_version": "allocation_policy_v1"
}
```

## 8. `GET /api/v1/intel/v3/snapshot` — scenario: no_snapshot (real 404 path)

**HTTP 404**:

```json
{
  "detail": {
    "code": "no_snapshot",
    "message": "No Intel v3 snapshot exists yet. Run Intel v3 first."
  }
}
```

---

## Screenshot index

All PNGs in this directory were taken with Playwright (Chromium) against the real
production frontend at 1440x900 (desktop) / 390x844 (mobile), after logging in through
the real `/login` page. See the proof report for the per-file description.

## Route redirect verification (real production frontend)

- `/dashboard` → `/dashboard/positions` (after real login)
- `/dashboard/deposits` → `/dashboard/advisor`
- `/dashboard/paycheck-plan` → `/dashboard/advisor?section=cash-plan` (cash-plan section focused/scrolled)
- Duplicate watchlist add shows the inline 409 message
  "VTI already has a price below entry. Edit that entry instead of adding a duplicate."

---

## Same-PR semantic patch — Advisor readiness proxy captures (local contract proof)

Captured through the frontend server route `GET /api/advisor/readiness` (server-only cert
secret; maps the cert-gated financial-truth-baseline diagnostic). Same harness provenance as
above: real backend + real Next production build; fixture Supabase client.

### baseline scenario (all truth dimensions healthy)
```json
{"portfolio_truth":"certified","price_truth":"ok","reconciliation":"pass","snapshot_value":20633.85,"position_derived_value":20633.85,"snapshot_stale":false,"next_required_repair":"No immediate fix required — financial truth is certified","as_of":"2026-07-18T19:31:47.013269+00:00"}
```

### degraded scenario (honest degraded truth with disagreeing values and repair action)
```json
{"portfolio_truth":"degraded","price_truth":"stale","reconciliation":"degraded","snapshot_value":21129.06,"position_derived_value":20633.85,"snapshot_stale":true,"next_required_repair":"Trigger a portfolio snapshot refresh to reduce snapshot age below 24h","as_of":"2026-07-18T19:31:59.766107+00:00"}
```

Vercel preview note: the preview deployment for this branch reports Ready (Vercel bot comment on
PR #473), but this sandbox's egress proxy returns 403 CONNECT for vercel.app, so in-environment
HTTP verification of the preview was not possible — preview checks are limited to the Vercel
build/deploy status until verified from an unrestricted network. The readiness proxy targets
pre-existing production diagnostics, so no backend deploy is required for it to function in
preview once verified.
