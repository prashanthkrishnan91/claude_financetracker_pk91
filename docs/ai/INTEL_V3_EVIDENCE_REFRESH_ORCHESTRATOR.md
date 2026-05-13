# Intel v3 Evidence Refresh Orchestrator — North-Star Plan

**Status:** Stage 3.0b — planning/spec only. No code in this PR.
**Date:** 2026-05-13
**Owner:** Intel v3 / Stage 3 spine.
**Predecessor:** Stage 3.0a (PR #306) — snapshot freshness/diff diagnostics live; treated here as the **measurement layer**, not the final solution.

---

## 1. Why this exists

Intel v3 today is a deterministic policy running over **persisted** evidence. The Stage 3.0a audit confirmed:

- Run Intel v3 re-executes deterministic policy correctly.
- Inputs are `recommendations` + `agent_insights` + `positions` + `target_allocations` as already written to Supabase.
- No live providers, no SEC/earnings, no valuation, no market data, no sentiment, no LLM/agent refresh runs during a v3 run.
- 10 BUY / 23 HOLD / 1 TRIM is **mechanically honest** for unchanged persisted evidence.

This is hybrid behavior: deterministic over stale-allowed data. It is unsafe to present as "current analysis" to a user about to invest.

**Non-negotiable product standard:** when the user opens the website or clicks **Run Intel v3** because they are about to invest, the visible decisions must be the latest certified analysis available *now*, not silently reused stale persisted analysis. Persisted evidence is allowed only as a **timestamped, freshness-scored artifact** inside a defined SLA. It must never be treated as current market truth without certification.

Intel v3 is therefore **partially certified**. Full S-grade certification requires the Evidence Refresh Orchestrator described below.

---

## 2. Architecture (target shape)

```
┌────────────────────────────────────────────────────────────────────────┐
│  Run Intel v3 (Orchestrator)                                           │
│                                                                        │
│   1. Evidence freshness pass  (per ticker × per source)                │
│   2. Refresh plan             (what to refresh, under budget)          │
│   3. Refresh execution        (workers, providers, agents)             │
│   4. Truth-labeling           (fresh / refreshed / stale / blocked)    │
│   5. Deterministic policy     (decide() — unchanged authority)         │
│   6. Snapshot + diagnostics   (run_mode, evidence_mode, source diff)   │
│   7. Plain-English banner     (Fresh certified / Partial / Blocked)    │
└────────────────────────────────────────────────────────────────────────┘
```

- **Workers** (existing + future) produce sourced evidence artifacts.
- **Deterministic policy** owns visible Buy/Hold/Trim/Sell. LLMs never own action authority.
- **Orchestrator** owns evidence freshness, refresh, and truth-labeling — it is a new responsibility, additive to the existing v3 service.
- **Snapshot metadata** tells the truth about source age, refresh status, and decision diff.

---

## 3. Evidence inventory (current repo)

Source-of-truth table for what we have today, owner, and current freshness behavior. "Affects Intel v3 today" = whether the value can move a deterministic decision.

| Source | Owner table / service | Refresh mechanism (today) | Timestamp field | Expected SLA (target) | Affects Intel v3 today |
|---|---|---|---|---|---|
| Active recommendations | `recommendations` | written by legacy agent pipeline; no auto-refresh during v3 | `created_at` | 24h | YES (drives `action`) |
| Agent insights (analyst verdict / drivers / risks / conviction) | `agent_insights` via `agent_runs` | written by `services/agents/orchestrator.py`; no auto-refresh during v3 | `agent_runs.finished_at` | 48h | YES (analyst evidence) |
| Portfolio positions | `positions` | user upload / Plaid sync | `updated_at` (where present) | 24h | YES (governor weights) |
| Portfolio snapshot + market values | `portfolio_snapshots.positions_data` (`market_price_usd`, `market_value_usd`, `market_value_source`, `market_value_certified_at`) | `PortfolioService.create_snapshot()` enriches when price valid/fresh | `market_value_certified_at` | 15m (intraday) / 1d (close) | INDIRECT (weights/sizing) |
| Target allocations | `target_allocations` | UI save (Stage 2.5F) | `updated_at` | 30d | YES (Deploy sizing) |
| Live price (intraday) | `services/price_service.py` → providers (yfinance/Alpaca) | request-time fetch; coalesced; Supabase cache | provider response (now) | 15m | INDIRECT |
| Latest close / price history | `price_history` (Supabase cache) | written by `price_service` on miss | row `date` | 1d | INDIRECT |
| Crypto prices | `services/crypto_service.py` | request-time fetch | provider response | 15m | INDIRECT |
| Fundamentals (EPS/PE/etc.) | `intelligence/v3/fy_eps_*`, `valuation_*` modules; SEC parsers | not auto-run during v3 today | parser output | 14d | YES (when governance gate on) |
| Valuation / price bands | `priceband_*` modules | scaffold-only (Phase 14F hidden) | scaffold artifact | 7d | NO (hidden today) |
| SEC filings | `research_workers/sec_edgar_provider.py` + `sec_companyfacts_parser.py` | research worker run; no auto-run during v3 | filing `fetched_at` | 7d (filing-aware) | INDIRECT (via Phase 11/13 governance) |
| Earnings data | `research_workers/earnings_*` | research worker run | artifact `fetched_at` | within current earnings window | INDIRECT |
| Analyst/LLM thesis | `agent_insights.analyst_verdict` (LLM/agent-produced) | run by orchestrator job; not auto-run during v3 | run `finished_at` | 48h | YES (drivers/risks/conviction) |
| Sentiment / news | `services/agents/sentiment_agent.py` | agent run; not auto-run during v3 | run `finished_at` | 4h (decay fast) | YES, but only with strong source quality |
| Research artifacts (Phase 5/6) | `research_artifacts` + `research_artifact_sources` + `research_artifact_facts` | written by research workers | `created_at` / `expires_at` | per-artifact `expires_at` | NO (truth adapter gate not crossed) |
| AI analyses (legacy) | `ai_analyses` | legacy enrichment | `created_at` | 48h | NO (read-only legacy; not consumed by v3 today) |
| Worker audit events | `worker_audit_events` | written by workers | `created_at` | n/a (audit) | NO |

**Implication:** today, fields marked YES are silently sourced from persisted evidence that has no freshness check at run time. That is the gap the orchestrator closes.

---

## 4. Freshness SLA (per source class)

Single source of truth for freshness windows. Used by both the orchestrator and the snapshot truth contract. **All thresholds are deterministic constants in a single module — no magic numbers scattered.**

| Source class | FRESH window | STALE window | HARD STALE / BLOCKED beyond | Critical for decision? |
|---|---|---|---|---|
| Live / intraday price | ≤ 15 min | 15 min – 4 h | > 4 h on market open | Critical for sizing; non-critical for action label |
| Latest close / price history | ≤ 1 trading day | 1–5 trading days | > 5 trading days | Critical for sizing; non-critical for action label |
| Crypto price | ≤ 15 min | 15 min – 4 h | > 4 h | Critical for sizing |
| Fundamentals (last filing-derived) | ≤ 14 days | 14–45 days | > 45 days | Non-critical (filing cadence) |
| Valuation / price bands | ≤ 7 days | 7–30 days | > 30 days | Non-critical (when enabled) |
| SEC filings | ≤ 7 days post-filing window | 7–30 days | > 30 days during active filing window | Critical when in active window |
| Earnings data | within current earnings window | post-window ≤ 14 days | > 14 days post-window | Critical inside window |
| Analyst / LLM thesis | ≤ 48 h | 48 h – 7 days | > 7 days | Critical for action label |
| Sentiment / news | ≤ 4 h | 4–24 h | > 24 h | Soft-only; never sole driver |
| Portfolio positions | ≤ 24 h | 24 h – 7 days | > 7 days | Critical for governor weights |
| Target allocations | ≤ 30 days | 30–90 days | > 90 days | Critical for Deploy sizing |
| Research artifacts | per artifact `expires_at` | within `expires_at` grace | past `expires_at` | Not currently consumed (Phase 5 gate) |

Stage 3.0a's blanket 72h threshold is **superseded** by this per-class SLA. The 72h fallback remains as the catch-all when a source has no explicit class.

---

## 5. Run modes

The orchestrator selects exactly one of four modes per run, embedded in the snapshot:

| Mode | Trigger | Behavior | UI label |
|---|---|---|---|
| `FAST_CERTIFIED` | every required source for every active ticker is inside FRESH window | run policy immediately; zero refresh calls | "Fresh certified — using current evidence" |
| `REFRESH_THEN_RUN` | some sources STALE; budget allows refresh; refresh providers reachable | enqueue stale sources; await up to soft timeout; run policy on refreshed evidence | "Refreshed N stale sources" |
| `PARTIAL_CERTIFIED` | some non-critical sources stale or refresh failed soft; critical sources still inside FRESH/STALE | run policy; mark affected tickers/sources stale; suppress upgrades that depend on stale evidence | "Partial: N tickers used stale analyst evidence" |
| `BLOCKED_UNCERTIFIED` | a critical source is HARD STALE / BLOCKED / provider-limited and cannot refresh | do **not** present as trusted; render banner; affected tickers' visible action remains, but card is flagged "uncertified" | "Blocked: current market data unavailable — using last certified analysis" |

`BLOCKED_UNCERTIFIED` never silently presents stale evidence as fresh. It is the truthful fail-safe.

---

## 6. Refresh orchestration

Decided **per ticker × per source class**:

1. **Freshness check** — for each ticker, every required source's age is compared against §4.
2. **Stale-source queue** — sources that are STALE (not HARD STALE) and whose refresh worker is available enter a deterministic queue ordered by: critical-for-decision first, then ticker A→Z, then source class.
3. **Provider call budget** — hard cap per run: e.g. `MAX_PROVIDER_CALLS_PER_RUN` (initial: 50). Per-provider sub-cap (e.g. `MAX_SEC_CALLS_PER_RUN=10`). Tracked in `attempted_provider_calls` / `successful_provider_calls`.
4. **LLM/agent call budget** — hard cap: e.g. `MAX_LLM_CALLS_PER_RUN` (initial: 10). Sentiment/news capped separately. Tracked in `attempted_llm_calls` / `successful_llm_calls`.
5. **Fallback** — if budget exhausted, remaining stale sources stay stale and are surfaced in snapshot diagnostics; mode degrades to `PARTIAL_CERTIFIED` (never `FAST_CERTIFIED`).
6. **Timeout** — soft refresh timeout per source class (e.g. price ≤ 3s, SEC ≤ 8s, LLM/agent ≤ 20s). Total orchestrator soft budget: 30s (configurable). On timeout: degrade.
7. **Retry** — at most one retry per source per run; exponential backoff disabled inside a single user-facing run. Multi-retry belongs in background workers, not in the user-triggered run.
8. **Provider 429 / rate limit** — treated as terminal for that source in this run; mode degrades to `PARTIAL_CERTIFIED` or `BLOCKED_UNCERTIFIED` depending on criticality.
9. **Partial success** — if some sources refresh and some fail, snapshot stores per-source success/fail counts; truth-labels each ticker accordingly.

**Hard rules:**

- No background refresh hides failures.
- No silent fallback to legacy LLM/aggregation paths.
- No fabricated freshness — if a source did not refresh, its `*_certified_at` is the original timestamp, not "now".

---

## 7. Reusing previous agents / LLM work

All existing v2 / Claude / Anthropic finance-agent / research-worker output is reused as **freshness-scored evidence**, not as authoritative current truth:

| Existing artifact | Treat as | Freshness rule | Refresh rule |
|---|---|---|---|
| `recommendations.action` | persisted decision input (analyst-derived); never the final visible action | analyst-thesis SLA | re-derive via analyst orchestrator when stale |
| `agent_insights.analyst_verdict` (drivers, risks, conviction) | analyst evidence artifact | analyst-thesis SLA | re-run analyst on stale ticker; cap by LLM budget |
| `agent_runs` (orchestrator runs) | run history; provides timestamp for analyst evidence | n/a | n/a |
| Claude / Anthropic finance-agent outputs (when stored) | analyst evidence artifact | analyst-thesis SLA | re-run agent on stale ticker |
| `ai_analyses` (legacy) | analyst evidence artifact (read-only) | analyst-thesis SLA | not auto-refreshed; superseded by orchestrator pull from canonical analyst path |
| `research_artifacts` (Phase 5/6) | research evidence; gated by `eligible_for_truth_adapter` | per-artifact `expires_at` | re-run earnings/SEC worker when expired |
| `portfolio_snapshots` market values | sizing input | price SLA | re-fetch via `price_service` |
| `target_allocations` | sizing input | target SLA | user prompt — never auto-rewritten |
| `price_history` | sizing context | close SLA | provider fetch |
| `sec_*` / earnings parsed artifacts | research evidence | filing/earnings SLA | research worker re-run |
| `priceband_*` scaffolds | hidden today; surface only if certified | priceband SLA | n/a until enabled |

**Rules to prevent old LLM claims from appearing current:**

1. Every artifact carries `produced_at`. The orchestrator stamps `evidence_age_hours` on every consumed field at run time.
2. Stale LLM claims are visible to the policy via `data_quality_label` / `analyst_used_fallback`; the policy already suppresses weak/stale paths.
3. The snapshot banner declares the source mode plainly; users see "Refreshed" / "Partial" / "Blocked" labels instead of being shown stale claims as if current.
4. No LLM artifact ever sets a field whose key is in the forbidden decision-authority list (already enforced by `artifact_truth_readiness.py`).
5. `safe_for_decision` remains DB-hard-locked false for research artifacts until Phase 6/7 gates pass (see `INTEL_V3_TRUTH_ADAPTER_READINESS_CONTRACT.md`).

---

## 8. Decision sensitivity (which refreshed fields can move Buy/Hold/Trim/Sell)

Mirrors `decide()` authority today; the orchestrator does **not** widen the policy's input surface. Refreshed fields that can affect the visible action label:

| Field | Can change action? | Can change sizing? | Source dependency |
|---|---|---|---|
| Live / close price | NO (label-stable) | YES | price service |
| Fundamentals trend (growth / profitability) | YES (with strong source) | NO | SEC/companyfacts |
| Filing / earnings change (in active window) | YES | NO | SEC/earnings worker |
| Analyst verdict confidence | YES (already an input) | NO | analyst path |
| Analyst drivers / risks | YES (already an input) | NO | analyst path |
| Evidence quality label | YES (already used) | NO | analyst path |
| Risk flag | YES (already used) | NO | analyst path |
| Portfolio weight / concentration | YES (governor) | YES | portfolio snapshot |
| Target allocation | NO (Deploy-side) | YES | target_allocations |
| Sentiment / news | NO unless source quality strong enough to clear evidence band | NO | sentiment worker (cap on influence) |
| Valuation / price band | NO (hidden until certified) | NO | priceband scaffold |

LLMs never own the final action. They only refresh evidence that the deterministic policy reads through the existing truth-aware decision input.

---

## 9. Snapshot truth contract

Every Intel v3 snapshot must expose (additive to today's payload):

```
snapshot.diagnostics = {
  evidence_mode:                "deterministic_policy_over_certified_evidence",
  run_mode:                     "FAST_CERTIFIED" | "REFRESH_THEN_RUN" | "PARTIAL_CERTIFIED" | "BLOCKED_UNCERTIFIED",
  source_freshness:             { <source_class>: { fresh: N, stale: N, hard_stale: N, missing: N } },
  per_source_oldest_timestamp:  { <source_class>: ISO | null },
  per_source_newest_timestamp:  { <source_class>: ISO | null },
  stale_count:                  N,
  missing_count:                N,
  provider_error_count:         N,
  attempted_provider_calls:     N,
  successful_provider_calls:    N,
  failed_provider_calls:        N,
  attempted_llm_calls:          N,
  successful_llm_calls:         N,
  failed_llm_calls:             N,
  successful_refresh_count:     N,
  failed_refresh_count:         N,
  previous_snapshot_id:         UUID | null,
  previous_action_counts:       { BUY: n, HOLD: n, TRIM: n, SELL: n } | null,
  current_action_counts:        { BUY: n, HOLD: n, TRIM: n, SELL: n },
  changed_decision_count:       N,
  changed_decisions:            [ { ticker, previous_action, current_action, change_reason_code } ],
  unchanged_decision_count:     N,
  confidence_status:            "trusted" | "partial_trust" | "uncertified",
}
```

Stage 3.0a already covers a subset (freshness + diff). The orchestrator adds `run_mode`, per-source breakdown, refresh counts, and the truth/trust status.

---

## 10. User-facing UX (plain English)

| Mode | Banner text |
|---|---|
| `FAST_CERTIFIED` | "Fresh certified — using current evidence." |
| `REFRESH_THEN_RUN` | "Refreshed N stale sources before running." |
| `PARTIAL_CERTIFIED` | "Partial: N tickers used stale analyst evidence. Decision shown as last certified." |
| `BLOCKED_UNCERTIFIED` | "Blocked: current market data unavailable. Showing last certified analysis only." |

Optional secondary line: "Oldest evidence: Xh. Decisions changed since last run: Y." (Stage 3.0a already renders this; orchestrator upgrades the leading label.)

**Rules:**

- No raw metrics ("max_recommendation_age_hours=…") in the banner.
- No diagnostic jargon ("evidence_mode=…") in the banner.
- No green check icon when mode ≠ `FAST_CERTIFIED`.

---

## 11. Implementation sequence — Stage 3.0b ordered slices

Each slice = one capability slice = one PR. Stop after Stage 3.0a production validation completes before starting 3.0b.1.

| # | Slice | Scope | Tests | SQL? |
|---|---|---|---|---|
| 3.0b.1 | **Freshness contract + per-source SLA module** | Pure module: `evidence_freshness_contract_v1.py` exposing SLA constants, freshness classification, run-mode selector. Wire into Intel v3 service in a read-only `diagnostics.run_mode` field; no refresh yet | Tier-2 backend pure unit tests | None |
| 3.0b.2 | **Evidence inventory adapter** | `evidence_inventory_adapter_v1.py`: collect timestamps for every source class listed in §3 (extends `ReadOnlyEvidenceAdapter`). Produces per-source freshness map (no refresh). Snapshot exposes per-source freshness counts | Tier-2 pure + read-only DB | None |
| 3.0b.3 | **Market price refresh path** | Orchestrator can call `price_service.fetch_prices()` for stale tickers under budget; portfolio snapshot market values refreshed on demand; sizing inputs re-certified | Tier-2 backend + provider stub | None |
| 3.0b.4 | **Valuation / priceband refresh path** | Optional refresh of valuation context behind existing governance gate; only activates when `intel_v3_valuation_context_adapter_v1_enabled` is on. No visible decision change unless gate already on | Tier-2 backend; Phase 13 path-aware | None |
| 3.0b.5 | **Research artifact freshness path** | Orchestrator reads `research_artifacts.expires_at`, surfaces stale/expired artifacts in diagnostics; **no consumption** (truth adapter gate still closed) | Tier-2 backend | None |
| 3.0b.6 | **Agent / LLM refresh worker path** | Per-ticker analyst refresh: orchestrator invokes existing analyst orchestrator for stale tickers; capped by LLM budget; result written via existing `agent_insights` / `agent_runs` path; freshness re-read | Tier-3 backend + recorded LLM stubs | None |
| 3.0b.7 | **Policy sensitivity tests** | Adversarial tests: refreshed analyst confidence flips an action only when evidence quality and source policy match `decide()` invariants; raw metrics never bypass the policy | Tier-2 / Tier-3 adversarial | None |
| 3.0b.8 | **UI trust labels** | `IntelV3Cockpit.tsx` `SnapshotBanner` renders the four mode labels from §10; preserves existing freshness sub-line; plain-English; mobile-safe | Frontend contract tests | None |
| 3.0b.9 | **Production validation** | Real-account runs across each run mode (forced via deterministic-time fixture in staging); confirms log line `intel_v3_run_mode_summary`, snapshot diagnostics, and banner alignment | Manual + runtime evidence | None |

**Sequencing rationale:**

- 3.0b.1–3.0b.2 are **diagnostic-only**: they upgrade the measurement layer (Stage 3.0a) into the SLA contract layer without changing visible behavior.
- 3.0b.3–3.0b.6 introduce refresh paths *one source class at a time*, smallest blast radius first (price → valuation → research → analyst/LLM).
- 3.0b.7 is the adversarial gate that proves refreshed evidence cannot bypass the deterministic policy.
- 3.0b.8 is the only user-visible slice; it must come after 3.0b.7 so labels never lie about trust.
- 3.0b.9 closes the certification loop in production.

**Constraints honored:**

- No huge rewrite — orchestrator wraps the existing `IntelV3Service.run_v3()` flow; `decide()` is untouched.
- No final decision authority for LLMs — already enforced; orchestrator only refreshes inputs.
- No hidden stale evidence — every stale read is surfaced in diagnostics.
- No fake freshness — `*_certified_at` is never rewritten unless a real refresh succeeded.
- No new providers unless documented later — current scope is yfinance/Alpaca (price) + SEC EDGAR (research) + existing analyst/LLM path.
- No Deploy changes — Deploy v3 already consumes the snapshot; it gets richer truth metadata for free.
- No Watchtower work — gated separately (§12).
- Frontend stays plain-English (§10).

---

## 12. Watchtower gate (Stage 3 entry)

Watchtower must not be built on stale or uncertified Intel. **Before** any Watchtower trigger slice starts, the following must be certified in production:

1. Stage 3.0a production validation complete (`intel_v3_freshness_summary` log + banner).
2. Stage 3.0b slices 1–8 merged and production-validated (slice 9).
3. At least one run in each of the four run modes observed in production logs.
4. `BLOCKED_UNCERTIFIED` mode confirmed to suppress trust labels in UI; user can see the block.
5. Adversarial sensitivity test (3.0b.7) passing in CI on the policy boundary.
6. `safe_for_decision` for research artifacts remains DB-hard-locked false (Phase 5 invariant preserved).
7. No raw-metric jargon in Intel banner copy (plain-English check).
8. AI usage budget under §6 caps for typical production runs (recorded in the usage snapshot).

Until **all eight** are green, Watchtower remains parked in `BUILD_QUEUE.md → Next`. Watchtower triggers may not consume Intel v3 evidence in any mode other than `FAST_CERTIFIED` for their initial slice.

---

## 13. What Stage 3.0b does *not* do

- Does not add new providers beyond what is already in the repo.
- Does not enable Phase 6+ artifact consumption (truth adapter remains gated).
- Does not move Watchtower into Now.
- Does not change Deploy v3 readiness, sizing, or Step 1/2/3 surface.
- Does not change the v3 visible action algorithm — `decide()` keeps current authority.
- Does not add background refresh jobs; refresh is request-time and capped.
- Does not change SQL schemas. Diagnostics ride in the existing snapshot JSONB payload.

---

## 14. Open questions parked for later

- Should orchestrator support a "force refresh" user gesture (button) once `REFRESH_THEN_RUN` is proven? — Defer to a post-3.0b decision; do not assume.
- Should freshness SLAs be configurable per user? — No, keep deterministic at product level until evidence demands otherwise.
- How do we age-out `agent_insights` rows so the analyst path naturally produces fresh ones? — Belongs to a separate retention-policy slice; orchestrator only reads.
