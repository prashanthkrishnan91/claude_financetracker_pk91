# Investment Intelligence Platform — North-Star Architecture

**Status:** Stage 3.0b architecture addendum (planning + foundational primitives landing in the Stage 3.0b v1 PR; full implementation spans Stage 3.0b → Stage 4 over multiple slices).
**Date:** 2026-05-13
**Owner:** Intel v3 / Stage 3 spine.
**Supersedes nothing.** Extends `INTEL_V3_EVIDENCE_REFRESH_ORCHESTRATOR.md` — the orchestrator described there is the v1 implementation of §4 (Refresh Tiers) and §9 (Runtime Behavior) below.

---

## Why this doc exists

Stage 3.0a confirmed Intel v3 was deterministic policy over **persisted** evidence with `attempted_llm_calls=0`, `live_provider_calls=0`, recommendations 191.8h old, agent_insights 286.1h old, 68 stale signals. Stage 3.0b v1 (this PR) closes the gap for price/market-value evidence and surfaces analyst staleness honestly as `BLOCKED_UNCERTIFIED`. But "refresh old rows" is not a durable architecture for an Investment Intelligence Platform. This doc is the durable north-star — the contract every future Intel slice answers to.

The implementation arrives one capability slice at a time. The doc itself lands now so future PRs reference §N rather than re-inventing.

---

## 1. Market Data Plane

A scalable, deterministic, no-LLM market data plane. Not a 34-ticker-only loop.

- **Owned tickers** (today): refreshed on Run Intel v3 under Tier 0.
- **Watchlist tickers** (next): refreshed cheaply on user open, same path as owned.
- **Opportunity universe** (later): thousands of tickers, batched factor screen — see §8.

Required evidence types per ticker (per source class):

| Evidence type | Source class | Freshness SLA target |
|---|---|---|
| current/latest price | market_price | 15 min intraday / 1d close |
| latest close | market_close | 1 trading day |
| market cap | market_meta | 1 day |
| volume / liquidity | market_meta | 1 day |
| 1d/5d/1m/3m/1y returns | market_history | 1 trading day |
| volatility / drawdown | market_history | 7 days |
| price vs 52-week range | market_history | 1 trading day |
| market value / portfolio weight | portfolio_snapshot | 24 h |
| source/provider label | (every artifact) | n/a |
| fetched_at / certified_at | (every artifact) | n/a |
| provider error / rate-limit status | provider_health | n/a |

**Rules:**

- Batched fetch wherever the provider supports it; per-ticker fan-out is the fallback, not the norm.
- Deterministic, no LLM, no agent.
- Failure modes are first-class — `provider_error`, `rate_limited`, `circuit_open` are all carried in the evidence artifact, never swallowed.
- Refresh budgets caps total provider calls per run (see `evidence_refresh_orchestrator_v1.MAX_PROVIDER_CALLS_PER_RUN`).

Stage 3.0b v1 covers price/market-value only via `price_engine.PriceService.fetch_prices()`. Future slices extend to market_history / market_meta under the same contract.

---

## 2. Evidence Graph / Artifact Contract

Every piece of evidence — regardless of source — conforms to a single canonical shape. The Python contract lives at `evidence_artifact_contract_v1.py`. The fields:

| Field | Type | Purpose |
|---|---|---|
| `ticker` | str | upper-cased symbol; non-empty (or `None` for portfolio-level facts) |
| `asset_type` | enum | `stock` / `etf` / `crypto` / `bond` / `index` / `macro` / `other` |
| `source_class` | enum | see `provider_registry_v1.SOURCE_CLASSES` |
| `source_name` | str | concrete provider/source name (e.g. `alpaca`, `sec_edgar`, `agent_orchestrator`) |
| `evidence_type` | str | e.g. `latest_price`, `eps_ttm`, `analyst_verdict`, `news_sentiment_24h` |
| `value` | Any | scalar/dict — the actual payload |
| `produced_at` | datetime | when the underlying fact was created (provider response time) |
| `fetched_at` | datetime | when we fetched/observed it |
| `certified_at` | datetime | when our pipeline last validated and stamped it |
| `expires_at` | datetime / None | hard expiry where applicable (artifact-specific) |
| `freshness_sla_hours` | float / None | soft SLA window for this artifact class |
| `source_quality` | enum | `HIGH` / `MEDIUM` / `LOW` / `UNKNOWN` |
| `confidence` | float / None | provider/agent-reported confidence in [0,1] |
| `trust_status` | enum | `trusted` / `partial_trust` / `uncertified` |
| `allowed_policy_axis` | list[str] | which `decide()` axes this artifact may influence (e.g. `["action","sizing"]`); never includes axes the artifact is forbidden from |
| `evidence_id` | str | stable id (UUID or content-hash) |
| `source_id` | str / None | foreign id back to the originating row (`recommendations.id`, `agent_runs.id`, etc.) |
| `provider_error` | str / None | last error captured during fetch |
| `rate_limit_status` | enum | `ok` / `near_limit` / `limited` / `unknown` |
| `error_reason` | str / None | parser-level reason for downgrade (`"thesis_status_changed"`, `"input_changed"`, …) |
| `policy_version` | str | which deterministic policy version is allowed to consume this artifact |

**Rules:**

- Every existing repo output must be **mappable** into this contract — not rewritten. Mappers in `evidence_artifact_contract_v1` translate `recommendations` / `agent_insights` / `agent_runs` / `research_artifacts*` / SEC facts / valuation scaffolds / sentiment / Claude/Anthropic outputs / portfolio positions / target allocations into `EvidenceArtifact` instances.
- Persisted evidence is **allowed only inside SLA**; outside SLA the artifact is degraded to `partial_trust` / `uncertified` at read time. The mapper never silently re-stamps `certified_at`.
- `allowed_policy_axis` is the enforcement spine: research artifacts may not influence the `action` axis until Phase 6/7 truth-adapter gates pass.
- `policy_version` lets `decide()` reject artifacts produced under an incompatible policy version.

Stage 3.0b v1 ships the contract + mapper stubs for the most-trafficked existing tables (`recommendations`, `agent_insights`, portfolio price). Other mappers land per slice as their consumers come online.

---

## 3. Provider Registry / Source Router

A central provider registry at `provider_registry_v1.py`. The registry classifies every source/provider the platform may eventually use and stores per-provider metadata used by the orchestrator, the refresh budget, and diagnostics.

**Source classes (enum):**

- `market_price` — real-time / intraday / close prices
- `market_history` — historical OHLCV, returns, volatility
- `market_meta` — market cap, volume, liquidity, 52-week range
- `fundamentals` — financial statements, ratios, growth/profitability
- `filings` — SEC EDGAR submissions / XBRL facts / 8-K / 10-Q / 10-K
- `earnings_calendar` — upcoming earnings, estimates, surprises, transcripts
- `news_sentiment` — news headlines, sentiment scores
- `analyst_estimates` — sell-side estimates, target prices, recommendations
- `etf_holdings` — ETF / fund constituent and weight data
- `crypto` — crypto prices / on-chain stats
- `macro` — rates, yield curves, inflation, FX, GDP
- `universe_screener` — bulk universe lists for the Opportunity Scout (§8)
- `analyst_thesis` — LLM/agent-produced analyst evidence (existing AgentOrchestrator / Claude / Anthropic)
- `portfolio_state` — internal portfolio positions / snapshots / target allocations

**Per-provider metadata (registry row fields):**

- `provider_id`, `display_name`
- `source_classes` (one or more)
- `enabled` (bool) and `env_var_name` (e.g. `ALPACA_API_KEY`)
- `freshness_sla_hours` (per source class)
- `rate_limit_per_minute` / `rate_limit_per_day`
- `batch_supported` (bool) + `max_batch_size`
- `fallback_priority` (lower = first try)
- `failure_mode` (one of `degrade` / `error` / `circuit_break`)
- `cost_tier` (`free` / `paid` / `personal-use-only`)
- `commercial_caveat` (str / None) — captures personal-use restrictions where relevant
- `test_strategy` (`stub` / `recorded` / `live_safe`)

**Known providers seeded in the registry (today's repo):**

| provider_id | source classes | enabled today | notes |
|---|---|---|---|
| `yfinance` | market_price, market_history, market_meta | yes (keyless) | rate-limit prone; circuit breaker present |
| `alpaca` | market_price, market_history | env-gated | preferred for stocks |
| `finnhub` | market_price | env-gated | bid/ask midpoint |
| `polygon` | market_price, market_history | env-gated | snapshot fallback |
| `coingecko` | crypto | yes (keyless) | tight TTL + stale fallback |
| `sec_edgar` | filings, fundamentals | yes (free) | already used by research_workers |
| `sec_companyfacts` | fundamentals | yes (free) | XBRL parser present |
| `agent_orchestrator` | analyst_thesis | env-gated (Anthropic key) | existing `services/agents/orchestrator.py` |
| `claude_analyst` | analyst_thesis | env-gated | reuse via AgentOrchestrator |
| `research_workers` | filings, earnings_calendar | yes (free) | existing `research_workers/` package |
| `portfolio_service` | portfolio_state | always | internal |

**Future providers** (NOT added in this PR; documented for the registry to accommodate without surface churn):

- FMP-style aggregators (statements, estimates, transcripts, screeners, 13F, insider/congressional, ETFs/funds)
- Alpha Vantage-style news/sentiment and intraday
- Nasdaq Data Link-style economic / alternative / bulk datasets
- Premium fundamentals providers

**Hard rule:** new providers must be added through the registry. No screen/service may know which provider produced a value — only the source class.

---

## 4. Refresh Tiers

Implemented incrementally. The orchestrator's budget caps and refresh-target selection map onto these tiers.

| Tier | What | Trigger | Budget |
|---|---|---|---|
| **Tier 0** | prices, latest close, portfolio weights, market values | every Run Intel v3; cheap GETs may also do this on page open | 0 LLM, capped provider |
| **Tier 1** | SEC filings, earnings, fundamentals, valuation bands | event-driven (new filing / earnings open) or SLA-expired | 0 LLM, capped provider |
| **Tier 2** | analyst thesis, risk red-team, sentiment/news summary | only when Tier 0/1 indicate stale or conflicting analyst evidence | capped LLM, capped provider |
| **Tier 3** | deep research on shortlisted opportunities | explicit user gesture or candidate-promotion | larger LLM budget, audited |
| **Tier 4** | broad universe / hidden-gem scan (Opportunity Scout) | scheduled background or explicit user gesture | factor-screen across thousands; LLM only on shortlist |

Stage 3.0b v1 wires Tier 0 (price refresh) and exposes the injection points for Tier 2 (`analyst_refresh`). Tier 1, 3, 4 are future slices.

---

## 5. Event-Driven Invalidation

Freshness is not only time-based. Evidence is invalidated or downgraded when any of the following events fire:

- price moves materially (>2σ or > N%) after a thesis was produced
- earnings window opens / closes for a ticker
- a new SEC filing appears
- a major news / sentiment shock occurs (severity-gated)
- provider error / rate-limit occurs
- portfolio weight / concentration crosses a guardrail
- user enters new deploy cash → Deploy v3 inputs invalidate
- analyst evidence conflicts with deterministic market / valuation facts
- evidence source's hard `expires_at` is reached
- a ticker changes corporate status / symbol / liquidity (delisting, ticker change, halts)

Implementation pattern: an `EventBus` emits `EvidenceInvalidatedEvent` carrying `(ticker, source_class, reason, severity)`. The orchestrator subscribes; on the next user-triggered run, invalidated artifacts are forced into the refresh queue ahead of plain time-stale ones. Stage 3.0b v1 implements the time-based half; the event half lands in a later slice.

---

## 6. Decision Replay / Auditability

Every Intel snapshot must be replayable end-to-end:

- `policy_version`
- `run_mode` (FAST_CERTIFIED / REFRESH_THEN_RUN / PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED)
- `source_freshness` summary by source class
- `evidence_ids` used per ticker (per artifact class)
- per-source timestamps
- `attempted_provider_calls` / `successful_provider_calls` / `failed_provider_calls`
- `attempted_llm_calls` / `successful_llm_calls` / `failed_llm_calls`
- `refresh_targets` and `refresh_decisions` (which sources we tried to refresh and why)
- prior-vs-current decision diff (`changed_decisions[]`)
- deterministic decision input (the `DecisionInputV3` blob)
- final action, conviction, evidence band, fit, risk
- plain-English `rationale` text
- per-ticker `confidence` / `trust_status`

Stage 3.0a + 3.0b v1 together cover ~80% of the fields. Remaining: `evidence_ids[]` and per-ticker `confidence` calibration (§7) — lands in a later slice once the artifact contract is consumed by the read path.

No opaque "because AI said so." If a recommendation is wrong, the snapshot tells us exactly which evidence and policy produced it.

---

## 7. Confidence Calibration

Confidence is evidence-calibrated, not prose-calibrated.

**Downward pressure (lower confidence):**

- analyst evidence is stale (`source_freshness[analyst_thesis] >= STALE`)
- market evidence has moved materially since the thesis was produced
- only one weak source supports the thesis (single-source band)
- evidence conflicts (analyst BUY vs fundamentals weakening, or sentiment vs filing)
- key source missing (`MISSING` state in the freshness map)
- provider failed (`provider_error` non-null)
- ticker has sparse data (`source_quality=LOW` or sparse-history flag)
- valuation / fundamentals are stale
- risk flags are elevated

**Upward pressure (higher confidence):**

- market data fresh (Tier 0 sources FRESH)
- fundamentals / filings current (Tier 1 sources FRESH)
- analyst thesis fresh (Tier 2 source FRESH)
- source quality HIGH
- independent sources agree (`agreement_score` across artifact set)
- deterministic policy is stable across reruns

Calibration is **a layer over** `decide()`, not inside it. `decide()` still owns the final action label; confidence is the trust dial the UI surfaces.

---

## 8. Portfolio Intel vs Opportunity Scout

The platform is not 34-holdings-shaped. Three coexisting flows:

1. **Portfolio Intel** — owned holdings, weights, risk, Buy/Hold/Trim/Sell. (Today's Intel v3 page.)
2. **Deploy** — exact dollars for available capital across owned + actionable candidates. (Today's Deploy v3.)
3. **Opportunity Scout** — broad universe screen, hidden gems, non-owned opportunities. (Stage 4+.)
4. **Watchtower** — event-driven monitoring after Intel is certified. (Blocked behind §12 of `INTEL_V3_EVIDENCE_REFRESH_ORCHESTRATOR.md`.)

**Opportunity Scout future scope:**

- Universes: S&P 500, Nasdaq 100, profitable midcaps, ETFs, recent IPOs, high-quality small/mid caps, user watchlists.
- Cheap factor screens across thousands of tickers (no LLM at this stage).
- Shortlist top N candidates.
- Run deep research only on the shortlist (Tier 3).
- Promote shortlisted candidates into Intel as **opportunities** (own card type), never as owned holdings.

Hidden-gem discovery is enabled by the same orchestrator contract; the Tier 4 entry point doesn't yet exist but the registry + artifact contract make it tractable.

---

## 9. Runtime Behavior

**On page open:**

- cheap freshness check (read latest snapshot; classify against §4 SLA)
- refresh Tier 0 market data if safe (capped provider calls)
- show trust label (run_mode-driven banner — §10 of the orchestrator doc)
- **do not** run expensive LLM / agent refresh automatically

**On Run Intel v3:**

- inspect freshness via the contract
- refresh Tier 0 / Tier 1 as safe under budgets
- selectively refresh Tier 2 if stale and budget permits (`analyst_refresh` injected; v1 boundary still says not_supported_v1)
- re-read evidence
- run deterministic policy (`decide()`)
- persist snapshot with run_mode / trust_status
- show run_mode / trust label

**On explicit future "Deep Refresh" gesture:**

- larger LLM/agent budget
- refresh more tickers (or all)
- still deterministic final decision authority

---

## 10. Failure Behavior

Never silently degrade.

- Provider failures stamp `provider_error` on the affected artifact and increment `failed_provider_calls`.
- Previous certified value is preserved if available; never overwritten by a failed refresh.
- Trust status moves to `partial_trust` or `uncertified` accordingly.
- `certified_at` is **never** stamped to "now" unless the refresh call returned success.
- Stale evidence is **never** called fresh. The orchestrator's `RefreshResult.notes[]` carries the per-source reason.

---

## 11. Performance

- Batch market data requests where the provider supports batches.
- Concurrency with per-source caps (race-then-cache pattern in `price_engine.PriceService` is the template).
- Caching with TTL + source-level expiry (`_CacheEntry` pattern).
- Stale-while-revalidate **only when the UI label is honest** about the staleness.
- Budgeted LLM calls; per-run LLM budget surfaces in diagnostics.
- No per-ticker serial deep research path in normal runs.
- Background / job architecture is a future slice if request-time refresh becomes slow at universe scale (§8).

---

## 12. Stage 3.0b v1 implementation scope (this PR)

Implements the strongest safe vertical slice:

- §1 Market Data Plane — Tier 0 price refresh wired (existing `price_engine.PriceService.fetch_prices()`); future slices extend the same contract to market_history / market_meta.
- §2 Evidence Graph / Artifact Contract — pure module `evidence_artifact_contract_v1.py` with the canonical `EvidenceArtifact` dataclass + mappers for the most-trafficked existing rows (`recommendations`, `agent_insights`, portfolio price). Future mappers land per slice.
- §3 Provider Registry — pure module `provider_registry_v1.py` seeded with the 11 known providers; orchestrator diagnostics now surface registry health summary.
- §4 Refresh Tiers — Tier 0 implemented; Tier 1/2 documented.
- §5 Event-Driven Invalidation — time-based half implemented; event half documented.
- §6 Decision Replay — ~80% covered between Stage 3.0a + 3.0b v1.
- §7 Confidence Calibration — documented; calibration layer is the next slice.
- §8 Opportunity Scout — documented; out of scope for code.
- §9 Runtime Behavior — Run Intel v3 path implemented per spec.
- §10 Failure Behavior — implemented in orchestrator + diagnostics.
- §11 Performance — Tier 0 batching honored via existing `fetch_prices()`.

**Explicit next blockers (in priority order):**

1. **Analyst refresh adapter (§4 Tier 2 / Stage 3.0b.6)** — wire `analyst_refresh` to the existing `AgentOrchestrator` for stale tickers under the LLM budget. Until this lands, production-stale analyst inventory remains `BLOCKED_UNCERTIFIED`.
2. **Event-driven invalidation (§5)** — `EvidenceInvalidatedEvent` bus and orchestrator subscription.
3. **Confidence calibration layer (§7)** — read-side calibration over `decide()` output using the artifact contract.
4. **Tier 1 market_history / fundamentals refresh (§1, §4)** — extend the Tier 0 pattern.
5. **Opportunity Scout v1 (§8)** — Stage 4 slice; depends on the registry + universe_screener source class.
6. **Watchtower** — remains gated behind `INTEL_V3_EVIDENCE_REFRESH_ORCHESTRATOR.md §12`.

---

## What this doc does NOT do

- Does not add new providers in this PR. The registry is seeded with what already exists.
- Does not enable Phase 6+ artifact consumption (truth-adapter gates remain closed).
- Does not change `decide()` authority or thresholds.
- Does not add SQL schemas.
- Does not change Deploy v3, Watchtower, broker execution, tax, or wash-sale logic.
