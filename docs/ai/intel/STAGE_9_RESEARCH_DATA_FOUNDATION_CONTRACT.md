# Stage 9 — Canonical Research Data Foundation Contract

**Status:** Architecture planning only. No synthesizer, no LLM calls, no providers, no policy change in this doc or its PR.
**Roadmap stage:** Stage 9 (research data foundation — prerequisite to any qualitative/quantitative synthesis).
**Date:** 2026-05-23.

This contract answers six questions before any synthesis is built:
1. What normalized data model is required before synthesis?
2. What do we already have from current providers/artifacts?
3. What is missing per asset type (stock / ETF / crypto)?
4. What can be built from existing data vs. what needs a new provider later?
5. How should HOLD become a real decision state instead of a fallback?
6. What is the smallest safe build sequence to get there?

**Permanent boundary (does not change in Stage 9):** the deterministic Intel v3 policy (`decide()` in `decision_policy_v1.py`) owns visible Buy/Hold/Trim/Sell authority. The synthesizer, when it eventually exists, may only summarize structured, normalized, trusted inputs. It never decides, never fills gaps, never invents valuation, fundamentals, holdings, or target weights.

---

## 1. Current-state assessment

### 1.1 Current Intel v3 strengths

- **Certified decision plumbing.** All-or-nothing certified intelligence run contract (`certified_intel_run_contract_v1.py`): a snapshot is `worker_certified` only if every active holding passes 10 conditions with matched fresh analyst evidence. Green requires `worker_certified` + full coverage.
- **Deterministic decision authority.** `decide()` is the sole visible action owner. LLMs/agents/research workers cannot own visible actions (enforced by the LLM/Agent Authority Boundary in the Truth Adapter Readiness Contract).
- **Evidence lanes with truth assessments.** Stage 5A–5J wrote a research artifact substrate (`research_artifacts` / `_sources` / `_facts`) with per-artifact source credibility, contradiction, completeness, and truth-usability assessments injected at write time.
- **Coverage and readiness read models.** Stage 5J `research_evidence_coverage_read_model_v1` produces per-lane coverage; Stage 5K `research_evidence_decision_input_adapter_v1` maps lanes to four axis readiness signals. Both are **shadow-only** (`safe_for_decision=False`, `shadow_only=True`, immutable).
- **Watchtower re-certification.** Fresh price/evidence triggers zero-LLM snapshot republish.

### 1.2 Current evidence lanes and providers (real, grounded)

Lanes registered today (`TICKER_LANE_REGISTRY` + macro):

| Lane | Artifact type | Skill pack | Provider | Asset scope |
|---|---|---|---|---|
| `sec_company_facts` | fundamental_quality | `sec_companyfacts_evidence_v1` | SEC EDGAR (free) | equity only |
| `fundamentals` | fundamental_quality | `fundamentals_evidence_v1` | yfinance (free) | equity-leaning |
| `technicals` | technical_signal | `technicals_evidence_v1` | yfinance (free) | all (price-derived) |
| `news_sentiment` | sentiment_event | `news_sentiment_evidence_v1` | yfinance (editorial → suppressed) | all |
| `sec_catalyst_sentiment` | sentiment_event | `sec_catalyst_sentiment_evidence_v1` | SEC EDGAR (free) | equity only |
| `macro_context` | portfolio_exposure | `fred_macro_evidence_v1` | FRED (free, keyed) | portfolio-scope |

Coverage statuses emitted per lane: `READY`, `LIMITED`, `SUPPRESSED`, `NOT_EVALUABLE`, `STALE_OR_UNKNOWN`, `MISSING`. Axis readiness (Stage 5K): `READY`, `LIMITED`, `INSUFFICIENT`, `MISSING`, `SUPPRESSED`, `STALE_OR_UNKNOWN`, `NOT_EVALUABLE`, `NOT_APPLICABLE`. Instrument categories recognized: `equity`, `etf`, `crypto`, `unknown`.

Also present today: certified price/market value in `portfolio_snapshots`, target allocations (`target_allocations`), valuation context (price-band, flag-gated), decision/thesis history via recommendations + decision logs, action feedback events.

### 1.3 Why current data is insufficient for deep synthesis

- **Lane coverage is sparse and inconsistent across tickers.** A given holding may have `READY` SEC company facts but `MISSING` technicals and `SUPPRESSED` sentiment. Synthesis over that mix would produce uneven output (deep paragraph for one ticker, empty for the next) — violating "consistent output across tickers."
- **No normalized cross-asset research model.** Lanes are evidence-shaped (artifact + facts), not synthesis-shaped (typed, per-asset fact categories). Nothing assembles a single canonical record per ticker.
- **Fundamentals are equity-only and trend-poor.** SEC CompanyFacts gives point observations; we have no normalized revenue/margin/FCF *trend* series ready for "is this improving?" claims.
- **ETF/fund facts are essentially absent.** No holdings, sector/geo exposure, expense ratio, yield, or overlap data lane exists.
- **Crypto has no durable fundamental source** and should not pretend to. Only price/technical/portfolio-role data is honest today.
- **Sentiment is mostly suppressed by design** (editorial context → THIN). Only SEC catalyst sentiment graduates to LIMITED.
- **No coverage/trust matrix gates synthesis.** Stage 5J/5K describe readiness but nothing yet says "this ticker has enough to synthesize block X."

### 1.4 Specific partial-data risk examples

- **Valuation claim with no valuation data.** If a synthesizer ran on a ticker with `MISSING` valuation, it could hallucinate "looks cheap" — forbidden. Valuation Safety Pack already bans price target / fair value text; partial data makes the temptation worse.
- **Fund-overlap claim with no holdings.** "This ETF overlaps your tech exposure" with zero holdings data would be fabricated. We have no ETF holdings lane today, so this is currently impossible to ground.
- **Crypto business-quality claim.** "Strong balance sheet" for BTC is category-incoherent. The model must not force crypto into the stock fact set.
- **Uneven HOLD explanations.** Two HOLDs today both render the same generic copy because there is no structured HOLD reason — see §5.

---

## 2. Canonical Research Dataset v1

A normalized, per-ticker backend record assembled **from already-trusted artifacts and snapshots only**, before any synthesis. Three asset-type shapes; asset types are not forced into one model. Every field carries provenance + a coverage status (§3); a field is either grounded or explicitly absent — never inferred.

Common envelope (all asset types):

```
CanonicalResearchRecord:
  ticker
  asset_type            # equity | etf | crypto  (from instrument classification)
  as_of                 # max source freshness across populated fields
  coverage_matrix       # §3, per category STRONG/PARTIAL/WEAK/MISSING/NOT_APPLICABLE
  portfolio_context:    # all asset types
    current_market_value, current_weight, target_weight (if set), gap_to_target,
    position_role (core / satellite / speculative if classifiable)
  decision_history:     # all asset types
    last_action, last_action_at, prior_thesis_summary (sourced), recommendation_history_ref
  provenance:           # per populated field: source_kind, provider, artifact_id, freshness
```

### 2.1 Stocks (`asset_type = equity`)

Required field categories (each grounded or absent):

- **price / current market data** — certified price + market value from `portfolio_snapshots`.
- **revenue / growth trend** — normalized multi-period revenue series + direction (improving / flat / declining), from SEC CompanyFacts / fundamentals facts.
- **margins / profitability trend** — gross/operating/net margin series + direction.
- **cash flow / FCF trend** — operating cash flow and free cash flow series + direction.
- **balance sheet** — cash, total debt, net debt, leverage indicator (no derived ratio leakage to UI).
- **valuation metrics + historical context** — only metrics already grounded (e.g., price-band context); flagged absent when not available. No price target / fair value (Valuation Safety Pack).
- **SEC company facts** — latest filed XBRL observations already collected by `sec_companyfacts_evidence_v1`.
- **SEC filings / catalysts** — recent filing types + plain-English labels (Stage 8E/8F).
- **news / event quality** — sentiment lane status; editorial stays suppressed, SEC catalyst sentiment promotes to LIMITED.
- **technical regime** — trend / volatility regime from `technicals_evidence_v1`.
- **portfolio position / target weight / context** — from common envelope.
- **decision / thesis history** — from common envelope.

### 2.2 ETFs / funds (`asset_type = etf`)

SEC company-fact and single-issuer fundamentals are **NOT_APPLICABLE**. Required categories:

- **holdings / top holdings** — *new lane needed (§4)*.
- **sector / industry exposure** — *new lane needed*.
- **geography exposure** (if available) — *new lane, optional*.
- **expense ratio** — *new lane needed*.
- **yield / distribution context** — *new lane needed*.
- **overlap with existing holdings** — derived from holdings ∩ portfolio positions (needs holdings lane first).
- **performance / risk / volatility** — derivable now from price history (`technicals` lane).
- **fund role in portfolio** — from common envelope + classification.
- **replacement / overlap candidates** — derived later, only once holdings + overlap exist.

### 2.3 Crypto (`asset_type = crypto`)

Single-issuer fundamentals, SEC facts, ETF holdings are **NOT_APPLICABLE**. No fake fundamentals unless a durable source exists. Required categories:

- **price / volatility / trend** — derivable now from price history.
- **allocation / risk contribution** — from portfolio context + volatility.
- **liquidity / market regime** — partial today; full version needs a crypto market-data provider (§4).
- **correlation / portfolio role** — derivable from price history once a correlation step exists.
- **event / risk context** (if available) — only if a durable source exists; otherwise absent.
- **explicitly no business-quality / balance-sheet block.**

---

## 3. Coverage & Trust Matrix v1

A per-ticker matrix that classifies each research category into one of five states and **gates which insight blocks are allowed**.

States:

- **STRONG** — grounded, fresh, multi-source or primary-authority; safe to feature.
- **PARTIAL** — grounded but thin / single-source / limited window; usable with hedged language only.
- **WEAK** — present but low trust (editorial-only, contradicted, stale-leaning); show as context, never as a claim.
- **MISSING** — no usable artifact; show "not enough data," never synthesize.
- **NOT_APPLICABLE** — category does not apply to this asset type (e.g., SEC facts for crypto/ETF); hidden, never penalized.

Mapping from existing lane/readiness vocabulary (so the matrix is buildable from Stage 5J/5K, not a parallel system):

| Existing status | Matrix state |
|---|---|
| `READY` (multi/primary source) | STRONG |
| `READY` (single source) / `LIMITED` | PARTIAL |
| `SUPPRESSED` (editorial), `STALE_OR_UNKNOWN`, `INSUFFICIENT` | WEAK |
| `MISSING`, `NOT_EVALUABLE` (no artifact) | MISSING |
| `NOT_APPLICABLE` (sec_lane_applicable=False, etc.) | NOT_APPLICABLE |

Categories covered by the matrix:

- fundamentals
- valuation
- technicals
- news / sentiment
- SEC / company facts
- SEC catalysts
- ETF / fund composition
- crypto market context
- portfolio sizing / target weight
- thesis / history

Allowed / suppressed insight blocks per state:

| State | Allowed | Suppressed | UI |
|---|---|---|---|
| STRONG | full block, plain claim | — | render |
| PARTIAL | hedged block ("some data suggests…") | strong claims | render with hedge |
| WEAK | context line only | any claim | "limited data" |
| MISSING | — | everything for that category | "not enough data yet" |
| NOT_APPLICABLE | — | everything | hidden |

This matrix is the **single source of truth for what synthesis may touch**. It is computed deterministically, before synthesis, and is itself `safe_for_decision=False` (diagnostic, not a decision).

---

## 4. Synthesis Gate v1

The exact, deterministic gate the (future) synthesizer must pass before it is even invoked for a ticker. Defined now; **not implemented in Stage 9 coding PRs except as dry-run diagnostics (9F).**

**Required normalized fields by asset type** (must be STRONG or PARTIAL in the matrix):

- **equity:** at least price + (one of: fundamentals trend, SEC company facts) + portfolio_context. Valuation, sentiment, technicals optional but each gate-checked individually before its block runs.
- **etf:** at least price + portfolio_context + (holdings OR sector exposure) for any composition block.
- **crypto:** at least price + portfolio_context. Fundamentals/SEC blocks are NOT_APPLICABLE and must never be requested.

**Minimum coverage thresholds:**

- A synthesis block runs only if its backing category is STRONG or PARTIAL.
- Portfolio-level summary runs only if ≥1 category is STRONG/PARTIAL and portfolio_context is present.
- If all decision-relevant categories are WEAK/MISSING, the synthesizer is **not invoked**; UI shows "not enough data yet."

**Forbidden inputs (never passed to a synthesizer):**

- raw API payloads, source URLs, API keys, internal diagnostic labels, shadow/posture labels.
- any field below PARTIAL.
- the deterministic Buy/Hold/Trim/Sell action as something to *justify after the fact* — the synthesizer summarizes evidence, it does not rationalize a predetermined verdict into a different one.

**Fallback behavior:**

- weak coverage → do **not** ask the LLM to fill gaps; suppress the block.
- valuation MISSING → synthesizer may make **no** valuation claim.
- ETF holdings MISSING → **no** fund-overlap claim.
- crypto (no durable fundamentals) → **no** stock-like business-quality output.

**Expected synthesizer output shape (later):** a structured object keyed by block (e.g., `business_quality`, `valuation_context`, `technical_regime`, `fund_composition`, `crypto_role`), each carrying `coverage_state`, `hedge_level`, `summary_text`, and `evidence_refs`. No action field. No new numbers. Every sentence traces to an `evidence_ref`.

---

## 5. HOLD semantics

Today HOLD too often reads as a default/fallback. Future HOLD must carry a structured **reason taxonomy** (backend contract; deterministic — not LLM-decided):

| HOLD reason | Meaning | Primary signals |
|---|---|---|
| `target_weight_hold` | at correct target weight | gap_to_target within band |
| `watchlist_hold` | waiting for specific future event | named catalyst pending (SEC filing window, earnings) |
| `evidence_gap_hold` | evidence does not justify buy/trim/sell | coverage matrix mostly WEAK/MISSING |
| `valuation_wait_hold` | waiting for valuation trigger | valuation PARTIAL/STRONG but not actionable |
| `catalyst_wait_hold` | waiting for catalyst | known upcoming catalyst, thesis intact |
| `risk_contained_hold` | risk acceptable, no change warranted | risk contribution within limits |
| `better_opportunity_hold` | no better use of capital right now | relative ranking vs other BUYs |

How this replaces HOLD-as-default:

- The deterministic policy continues to **decide** HOLD. The taxonomy is a **reason classifier** layered on the existing decision — it explains, it does not decide.
- Each HOLD snapshot carries exactly one primary reason (deterministic precedence order), optionally secondary context.
- `evidence_gap_hold` is the honest label for "we don't have enough data" — distinct from the others, so a HOLD on a well-covered ticker reads differently from a HOLD on a thin one.
- This makes HOLD auditable and consistent across tickers, and removes the lazy "Hold" with generic copy.

The HOLD reason is itself deterministic and `safe_for_decision`-neutral: it annotates the existing decision, never changes it.

---

## 6. Stage 9 build sequence

Small, ordered PRs after this contract. Each is a backend capability slice unless noted. **No synthesizer / LLM / paid provider until the foundation passes.**

### 9A — Data coverage audit endpoint + matrix (no synthesis)

- **Model:** Sonnet.
- **Scope:** deterministic per-ticker Coverage & Trust Matrix (§3) computed from existing Stage 5J/5K outputs; read-only diagnostic endpoint (cert-gated, flag-gated, capped ticker count). Maps existing statuses → STRONG/PARTIAL/WEAK/MISSING/NOT_APPLICABLE.
- **Tests:** Tier 1 contract — status mapping, asset-type NOT_APPLICABLE rules, read-only (no writes), no payload/secret leakage, fail-soft.
- **Acceptance:** endpoint returns matrix per ticker; statuses match underlying lane statuses; `safe_for_decision=False`.
- **Must not build yet:** any synthesis, any UI beyond optional diagnostic, any provider call.

### 9B — Canonical stock research dataset adapter

- **Model:** Sonnet (Opus only if trend-normalization design proves complex).
- **Scope:** pure adapter assembling `CanonicalResearchRecord` for `asset_type=equity` from existing artifacts/snapshots, incl. revenue/margin/FCF **trend direction** from already-collected SEC/fundamentals facts. No new provider.
- **Tests:** Tier 1 — field grounding, absent-when-missing, trend direction correctness, no fabricated fields, provenance present.
- **Acceptance:** record assembles for a real equity with mixed coverage; missing categories explicitly absent; matrix attached.
- **Must not build yet:** ETF/crypto shapes, synthesis, valuation invention.

### 9C — Canonical ETF/fund dataset adapter

- **Model:** Sonnet.
- **Scope:** `asset_type=etf` record shape with SEC/single-issuer fields NOT_APPLICABLE; populate what's derivable now (price-based performance/volatility, portfolio role); mark holdings / sector / expense / yield / overlap as MISSING pending a fund-data lane. **Note explicitly:** ETF composition needs a new provider (§4) — not built here.
- **Tests:** Tier 1 — NOT_APPLICABLE rules, MISSING composition fields honest, derivable fields grounded.
- **Acceptance:** ETF record assembles with honest MISSING composition; no fabricated holdings.
- **Must not build yet:** the fund-data provider, overlap/replacement claims, synthesis.

### 9D — Canonical crypto dataset adapter

- **Model:** Sonnet.
- **Scope:** `asset_type=crypto` record; price/volatility/trend + portfolio role derivable now; fundamentals/SEC/ETF blocks NOT_APPLICABLE; no fake fundamentals.
- **Tests:** Tier 1 — NOT_APPLICABLE business blocks, no fabricated fundamentals, derivable fields grounded.
- **Acceptance:** crypto record assembles with no business-quality block; risk/role present.
- **Must not build yet:** crypto market-data provider, correlation engine, synthesis.

### 9E — HOLD reason taxonomy backend contract

- **Model:** Sonnet.
- **Scope:** deterministic HOLD reason classifier (§5) annotating existing HOLD decisions; precedence order; one primary reason per HOLD. Shadow-only first (does not change visible copy until a later UI slice).
- **Tests:** Tier 1 — precedence correctness, `evidence_gap_hold` vs others, no decision mutation (`decide()` output unchanged), deterministic.
- **Acceptance:** every HOLD gets exactly one primary reason; BUY/TRIM/SELL unaffected; visible action unchanged.
- **Must not build yet:** LLM-chosen reasons, visible copy change (separate UI slice), any change to the decision itself.

### 9F — Synthesis gate dry-run diagnostics

- **Model:** Sonnet.
- **Scope:** deterministic evaluation of the Synthesis Gate (§4) per ticker — reports *would-run / would-suppress* per block with reasons. **No LLM invoked.** Pure dry-run over 9A–9D outputs.
- **Tests:** Tier 1 — gate thresholds, forbidden-input exclusion, asset-type rules, fallback (weak → suppress), no LLM call.
- **Acceptance:** dry-run report shows which blocks would be allowed for real tickers and why; matches matrix states.
- **Must not build yet:** the synthesizer, any LLM call.

### Later (explicitly gated, not in Stage 9)

- **Fund-data provider** (ETF holdings/sector/expense/yield) — a deliberate, gated provider-expansion slice; likely paid. Required before ETF composition/overlap synthesis.
- **Crypto market-data provider** (liquidity/market regime, correlation inputs) — gated provider-expansion slice.
- **LLM synthesizer** — only after 9A–9F pass and the foundation produces consistent, gated, grounded records across tickers. Summarizes structured trusted inputs only; never decides; never fills gaps.

---

## Guardrails recap (apply to every Stage 9 PR)

- Deterministic policy keeps Buy/Hold/Trim/Sell authority; no LLM in the visible decision path.
- `safe_for_decision=False` everywhere in Stage 9; foundation is diagnostic.
- No fabricated valuation, target weights, holdings, or fundamentals.
- Asset types keep distinct shapes; never force crypto/ETF into the stock model.
- No paid providers, no LLM calls, no new prompt surface in Stage 9 coding PRs (9A–9F).
- This is a foundation stage — not S-grade readiness, not a synthesis ship.
