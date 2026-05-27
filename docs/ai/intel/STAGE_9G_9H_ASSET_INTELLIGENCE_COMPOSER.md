# Stage 9G/9H — ETF Intelligence Lens + Unified Asset Decision Composer

**Status:** Merged (Stage 9G/9H)
**Scope:** Backend-only, pure modules. No UI changes. No SQL. No provider calls.

---

## Problem Solved

Provider proof loops blocked all ETF intelligence. FMP free tier is paywalled (402), AV is supplemental-only (no as-of date), SEC/NPORT is canonical but incomplete. The app had no useful investment intelligence for ETFs.

Product correction: stocks and ETFs require **different intelligence lenses**. Blocking all intelligence on perfect ETF holdings data was the wrong decision.

---

## What Was Built

### 1. ETF Intelligence Classifier (`etf_intelligence_classifier_v1.py`)

Pure classifier. No IO, no DB, no LLM.

**Input:** ticker, asset_type, optional Stage 9F provider outputs dict.

**Output:** `EtfIntelligenceClassification` with:
- `etf_type`: equity_etf | sector_etf | dividend_etf | international_etf | bond_etf | commodity_trust | crypto_etf | unknown_fund
- `etf_role`: core_us_equity | growth_tilt | dividend_income | sector_tilt | international_diversifier | bond_stability | commodity_hedge | crypto_speculative | cash_like | unknown_role
- `evidence_tier`: holdings_ready | profile_ready | metadata_only | not_applicable
- `safety_flags`: safe_for_role_analysis | safe_for_overlap_analysis | safe_for_concentration_analysis | safe_for_cost_comparison | safe_for_decision (always False) | synthesis_ready (always False)

**Key decisions:**
- GLD and commodity trusts → `evidence_tier=not_applicable` — this is correct classification, NOT a failure. Equity holdings analysis is structurally inapplicable for commodity trusts.
- Partial/suspicious holdings (e.g. VXUS with 37 AV holdings) → never `holdings_ready`, never `safe_for_overlap_analysis`.
- AV missing date → `holdings_ready` impossible. Date absence is the canonical AV rejection reason.
- FMP 402/paywalled → contributes nothing to holdings readiness.
- Known ETF ticker (from 40+ ticker table) → at least `profile_ready` even without provider data.

**Evidence tier mapping:**

| Condition | Tier |
|---|---|
| holdings ≥ 5 + weights + as-of date + plausible coverage | `holdings_ready` |
| known ETF type OR AV has holdings signal | `profile_ready` |
| only ETF identity known | `metadata_only` |
| commodity trust or non-ETF asset | `not_applicable` |

### 2. Unified Asset Decision Composer (`asset_intelligence_composer_v1.py`)

Pure composer. No IO, no DB, no LLM.

**Input:** ticker, asset_type, portfolio_fit, evidence_quality, provider_outputs, upstream_signals.

**Output:** `AssetIntelligenceResult` with:
- `asset_class`: stock | etf | commodity_trust | crypto | unknown
- `lens_applied`: stock_fundamental_lens | etf_role_lens | commodity_hedge_lens | crypto_speculative_lens | unknown_lens
- `decision_drivers`: list of plain-English strings (no raw metric keys)
- `suggested_action`: BUY | HOLD | TRIM | SELL | None
- `hold_reason`: explicit HOLD reason code (never null when HOLD)
- `blocked_reason`: set when `suggested_action=None`

**Stock lens** focuses on: business quality, growth, margins, valuation, balance sheet, catalysts. Never ETF role/exposure language.

**ETF lens** focuses on: portfolio role, target-weight fit, exposure/asset class, concentration/overlap, cost/expense ratio, liquidity, structure/tracking. Never stock-style business analysis.

**Commodity trust lens** focuses on: portfolio hedge role, weight fit. Equity holdings overlap/concentration not applicable.

#### HOLD semantics (explicit, never generic)

| Reason Code | Meaning |
|---|---|
| `HOLD_ON_TARGET` | Role is correct AND weight is at/near target |
| `HOLD_STABLE_NO_TRIGGER` | Evidence stable but no action signal present |
| `HOLD_WATCH_EVIDENCE` | Evidence weak/partial — watching for improvement |
| `HOLD_WATCH_ROLE` | Role signal ambiguous — watching for clarification |
| `HOLD_COMMODITY_STABLE` | Commodity trust; hedge role intact, no weight change |

HOLD is **never** a silent fallback. When data is too weak for any suggestion, `suggested_action=None` with `blocked_reason` set.

#### ETF action semantics

- **BUY**: build/add underweight needed exposure or strong core role.
- **TRIM**: overweight, redundant, over-concentrated, or risk/role mismatch (without sell-tier indicators).
- **SELL**: remove/replace when role is wrong, duplicate, or structurally inferior (especially with overweight).
- **HOLD**: one of the five explicit reason codes above.

---

## Stage 9F Provider Output Mapping

| Provider Output | Tier Result |
|---|---|
| NPORT success + holdings≥5 + weights + date + plausible | `holdings_ready` |
| NPORT partial/suspicious coverage | `profile_ready` at most |
| AV: holdings + weights + date_verified | `holdings_ready` |
| AV: holdings + weights, NO date | `profile_ready` (never holdings_ready) |
| AV: partial_or_suspicious coverage | `profile_ready` (not overlap-safe) |
| FMP 402/paywalled | No contribution — `profile_ready` or `metadata_only` |
| No provider data, known ETF ticker | `profile_ready` |
| No provider data, unknown ETF ticker | `metadata_only` |
| Commodity trust (GLD) | `not_applicable` |
| Non-ETF asset | `not_applicable` |

---

## Architecture Invariants

- `safe_for_decision`: always False — these modules never own visible Buy/Hold/Trim/Sell authority.
- `synthesis_ready`: always False — backend synthesis gate not yet cleared.
- No UI changes in this PR. Output is available for diagnostic/future rendering only.
- No existing visible recommendation behavior changed.
- No SQL. No provider calls. No LLM. No paid provider chase.
- Partial ETF holdings cannot become overlap-safe or synthesis_ready.
- GLD/commodity trust does not fail equity analysis — it is correctly classified as not_applicable.

---

## Files Delivered

| File | Purpose |
|---|---|
| `v2/backend/app/services/intelligence/v3/etf_intelligence_classifier_v1.py` | ETF type/role/tier/safety classification |
| `v2/backend/app/services/intelligence/v3/asset_intelligence_composer_v1.py` | Unified asset lens router + decision driver composer |
| `v2/backend/tests/test_stage9g9h_etf_intelligence_composer.py` | 83 fixture tests |
| `docs/ai/intel/STAGE_9G_9H_ASSET_INTELLIGENCE_COMPOSER.md` | This doc |

---

## Test Coverage Summary

| Test Class | What It Proves |
|---|---|
| `TestVooHoldingsReady` | VOO/SPY with holdings+weights+date → holdings_ready + core_us_equity |
| `TestSchdDividendLens` | SCHD → dividend_income; no stock jargon in role description |
| `TestXleSectorTilt` | XLE → sector_tilt; concentration safe when holdings_ready, not safe otherwise |
| `TestVxusInternational` | VXUS shallow/no-date → not overlap-safe; profile_ready with known metadata |
| `TestGldCommodityTrust` | GLD → commodity_trust/not_applicable/not_failed; role analysis still allowed |
| `TestFmpPaywalled` | FMP 402 → no holdings readiness; overlap blocked |
| `TestAvMissingDate` | AV no date → never holdings_ready; AV with date CAN be holdings_ready |
| `TestStockTickerNotApplicable` | Stock/crypto tickers → is_etf=False; all safety flags False |
| `TestPartialCoverageNeverHoldingsReady` | Suspicious NPORT → not holdings_ready |
| `TestGovernanceInvariants` | safe_for_decision/synthesis_ready always False for all tickers |
| `TestSafetyFlagsByTier` | holdings_ready/profile_ready/metadata_only → correct flag sets |
| `TestStockLens` | Stock → stock_fundamental_lens; no ETF language; thin evidence blocks |
| `TestEtfBuySemantic` | SCHD/VOO underweight → BUY with role language |
| `TestEtfHoldOnTarget` | VOO on-target → HOLD_ON_TARGET |
| `TestEtfTrimSemantic` | XLE overweight → TRIM with explicit reason |
| `TestVxusRoleMismatch` | VXUS underweight + role_mismatch → HOLD_WATCH_ROLE (not BUY) |
| `TestGldCommoditySemantic` | GLD on-target → commodity_hedge_lens/HOLD_COMMODITY_STABLE; GLD underweight → BUY |
| `TestEtfSellSemantics` | role_mismatch+inferior → SELL; redundant+inferior → SELL; overweight+mismatch → SELL |
| `TestHoldNeverSilentFallback` | HOLD always has hold_reason code; never null |
| `TestWeakDataExplicitBlocked` | Weak data → blocked_reason set; never silent HOLD |
| `TestEtfLensLanguage` | ETF lens uses exposure/role language; no P/E/margin jargon |
| `TestComposerGovernanceInvariants` | safe_for_decision/synthesis_ready always False across all lenses |
| `TestCryptoLens` | BTC/ETH → crypto_speculative_lens |
| `TestUnknownAssetType` | Unknown type → unknown_lens + blocked_reason |
| `TestEtfConcentrationCostSignals` | concentration_risk/cost_elevated signals produce correct drivers |
| `TestHoldReasonByFit` | ON_TARGET→HOLD_ON_TARGET; unknown fit→HOLD_WATCH_EVIDENCE |
| `TestToDict` | to_dict() contains no raw provider payload fields |
| `TestRoleDescription` | Role descriptions are plain-English; no raw metric keys |

---

## Next Steps

1. Wire composer output into Intel v3 diagnostic endpoint (read-only, default-off flag).
2. Consider activating ETF role-based decision suggestions for portfolio fit signals once target-weight data is reliable.
3. Crypto lens can expand to use dedicated crypto provider when built.
4. `unknown_fund` / `unknown_role` tickers can be resolved by adding to `_KNOWN_ETF_MAP` as new funds appear in portfolios.
