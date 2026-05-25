# Stage 9F.1 Plan — Real ETF Fund Data Provider / Composition Adapter v1

Status: planning/spec only. No code in this PR. Roadmap: ETF parity lane (follows Stage 9F honest scaffold). Severity: Level 2.

## 1. Problem & current state

Stage 9F built an honest ETF scaffold (`canonical_etf_fund_dataset_v1.py`). It marks **all composition MISSING**, fund identity/cost/yield PARTIAL-at-best, and `etf_fund_intelligence_ready=False` always, because **no dedicated fund-data provider exists**. The scaffold never extracts ETF-specific fields even when the yfinance fundamentals lane is usable — by design, it only reads lane *usability labels*, not fund payloads.

The gap to close: a real provider + adapter that actually fetches and normalizes ETF holdings, sector exposure, expense, yield, issuer, and category, then writes them as evidence artifacts the canonical dataset can consume.

## 2. Source strategy decision (brutally honest)

**Recommendation: use yfinance `funds_data` as the v1 composition source — NOT issuer scraping.**

The repo pins `yfinance>=0.2.38`, which exposes `Ticker.funds_data` (`FundsData`): `top_holdings` (≈top 10 with weights), `sector_weightings`, `fund_overview` (family/issuer, categoryName, legalType), `fund_operations` (expense ratio), `asset_classes`, `description`. This is one normalized interface across **all** issuers, already a registered provider (`yfinance`, FREE/UNOFFICIAL_AGGREGATOR), and slots into the existing evidence-lane pattern with zero new HTTP-scraping surface.

**Why not issuer files (SSGA/Vanguard/Invesco/Schwab/iShares XLSX/CSV)?** They are the *authoritative* source, but for v1 they are brittle in ways that fail silently:
- URLs and filenames change without notice (SSGA `.xlsx`, Invesco `.csv`, Vanguard undocumented JSON). A 404 or markup change breaks one issuer's lane while others keep working — hard to detect.
- Each issuer needs a bespoke parser → 5+ fragile adapters before any value ships.
- ToS/robots constraints on automated bulk download vary per issuer.

So issuer official files are deferred to a **later credibility-upgrade phase (post-9F.2)**, added one issuer at a time, fixture-tested, and gated — only if yfinance coverage proves insufficient for a specific family.

**Honest limitations of the yfinance choice** (must be carried into artifact `limitations`):
- UNOFFICIAL_AGGREGATOR trust tier — never an official holdings file; values can be stale/incomplete.
- `top_holdings` is capped (~top 10), so full holdings and exact top-10 concentration are approximate (sum of returned weights only).
- **No reliable geography/country breakdown** from `funds_data` → geography stays MISSING even at 9F.2 (real gap; VXUS/VGT international exposure needs issuer data later).
- Commodity trusts (GLD) return little/no composition — handled as a special case (below).

## 3. ETF universe → source & available fields

Source for every row at v1 = **yfinance `funds_data`** (FREE). "Available" = expected to populate via that interface; "MISSING" = not provided by yfinance (needs issuer data later).

| Ticker | Family / Issuer | Top holdings | Top-10 conc. | Sector exp. | Geography exp. | Expense | Dist. yield | Issuer/category | Notes |
|--------|-----------------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|-------|
| SPY | SSGA / SPDR | ✅ | ✅(approx) | ✅ | ❌ | ✅ | ✅ | ✅ | Large-cap blend; strong yf coverage |
| VOO | Vanguard | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | S&P 500 |
| VTI | Vanguard | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | Total US market |
| QQQ | Invesco | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | Nasdaq-100 |
| VGT | Vanguard | ✅ | ✅ | ✅(tech-heavy) | ❌ | ✅ | ✅ | ✅ | Sector fund |
| VHT | Vanguard | ✅ | ✅ | ✅(healthcare) | ❌ | ✅ | ✅ | ✅ | Sector fund |
| VIS | Vanguard | ✅ | ✅ | ✅(industrials) | ❌ | ✅ | ✅ | ✅ | Sector fund |
| XLE | SSGA / SPDR | ✅ | ✅ | ✅(energy) | ❌ | ✅ | ✅ | ✅ | Sector fund |
| VXUS | Vanguard | ⚠️partial | ⚠️ | ✅ | ❌ **real gap** | ✅ | ✅ | ✅ | Int'l ex-US — geography matters most, yf weakest here |
| SCHD | Schwab | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | Dividend-quality |
| VYM | Vanguard | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | High dividend |
| GLD | SSGA / SPDR | N/A | N/A | N/A | N/A | ✅ | N/A | ✅ | **Commodity trust** — single asset (gold); composition NOT_APPLICABLE, only expense/category/AUM |

Also: scan current forensics for any ETF tickers beyond this list (`asset_type=etf`) and include them in the same lane — the adapter is ticker-agnostic, so no per-ticker code is needed.

## 4. Provider registry shape

Reuse the existing `evidence_provider_registry_v1.py` rather than a new registry.
- **Lane:** add `LANE_ETF_FUND_DATA = "etf_fund_data"` to `ALL_LANES`.
- **Provider:** extend the existing `yfinance` entry's `supported_lanes` to include `etf_fund_data` (capability already exists in the installed lib). Do **not** invent a new provider id.
- **max_stale_age_hours:** `{etf_fund_data: 24.0}` — holdings refresh daily; 24h matches fundamentals.
- **Failure modes:** not-a-fund (yfinance raises) → skipped honest; timeout/empty → no artifact, honest reason; never fabricate.
- **Credibility label:** sources written with `source_kind="vendor_fundamentals"`, `provider_name="yfinance"` → flows through Stage 5B/5E to USABLE_WITH_LIMITATIONS (same path as the existing usable yfinance fundamentals lane). Never PRIMARY_AUTHORITY.
- **Freshness:** FRESH if fetched within 24h; honest STALE/UNKNOWN otherwise.
- **Artifact model version:** `etf_fund_composition_v1`.

## 5. Normalized ETF artifact contract

Write via the existing `ResearchArtifactServiceV1.write_artifact()` → **`artifact_type="etf_fund_note"`** (already in the DB enum from migration 023 — **no SQL migration required**), `skill_pack="etf_fund_composition_evidence_v1"`, `scope_kind="ticker"`.

Payload (safe, no raw provider dump):
- `holdings_top_n`: list of `{symbol, name, weight_pct}` — **top 10 only**, weights rounded.
- `top_n_concentration_pct`: sum of returned top-N weights (labeled approximate).
- `sector_exposure`: list of `{sector, weight_pct}`.
- `geography_exposure`: `null` + reason (yfinance gap) — never fabricated.
- `expense_ratio_pct`, `distribution_yield_pct`: scalars when present, else null.
- `issuer_or_fund_family`, `category_or_index_strategy`, `legal_type`: from `fund_overview`.
- `aum_or_total_assets`: when available from `.info`, else null.
- Facts: one `FactRecord` per holding / sector weight (`fact_kind="metric_observation"`, `axis_hint="exposure"` — valid per migration 017 CHECK).
- Sources: one `SourceRecord` (`source_kind="vendor_fundamentals"`, `provider_name="yfinance"`, `source_url=https://finance.yahoo.com/quote/<TICKER>/holdings`).

Hard contract: no raw provider payloads serialized; no source URLs/keys in diagnostics; no fabricated values for missing fields (explicit MISSING + reason); `safe_for_decision` stays False (enforced by writer + DB).

**GLD special case:** if `funds_data` is empty/raises (commodity trust), write an honest `etf_fund_note` with composition fields NOT_APPLICABLE (reason: single-commodity trust) and only category/expense populated — never a fake holdings list.

## 6. Activation sequence (few PRs, no brittle+policy mixing)

- **9F.1 (this doc)** — plan only.
- **9F.2 — Provider + adapter + lane (FIRST implementation PR).** New `etf_fund_data_provider_v1.py` (yfinance `funds_data` client, injectable, fail-closed, never raises) + `etf_fund_data_adapter_v1.py` (pure → `WorkerOutput`) + `run_etf_fund_data_evidence()` runner + registry lane + config flag + fixture tests. Flag default OFF. **Does not touch the canonical dataset or forensics** (writes artifacts only). Self-contained capability slice (mirrors how Stage 5I FRED landed).
- **9F.3 — Canonical normalization.** Wire the new `etf_fund_note` artifact into `build_canonical_etf_fund_dataset_row()` so `composition`/`cost_and_yield`/`fund_identity` flip from MISSING → AVAILABLE/PARTIAL honestly, update the forensics bucket (`ETF_FUND_COMPOSITION_NOT_READY` → composition-present), and flip `etf_fund_intelligence_ready` gate logic. Separate PR because it changes a different module's contract + forensics + readiness — exactly the "do not combine scraping/provider work with readiness policy" boundary.

## 7. Validation

- **9F.2:** fixture-based unit tests (recorded `funds_data` shapes for SPY/VOO/QQQ/SCHD/VXUS + GLD edge + not-a-fund equity skip + empty/timeout). Pure-adapter tests assert no forbidden keys, no raw payload leak, honest MISSING/NOT_APPLICABLE, deterministic idempotency key. Live-provider test optional and gated (env flag), never in CI default.
- Failure cases: yfinance raises on equity ticker → skipped, no artifact; commodity trust → NOT_APPLICABLE composition; markup/shape change → empty parse → honest no-data artifact, never crash.
- **9F.3:** forensics expected-output test (ETF rows move bucket; geography still MISSING honestly); canonical row asserts composition AVAILABLE only when real holdings present.
- Cache/freshness: 24h SLA; runs only on explicit `POST /intel/v3/run`, never page load.

## 8. Final recommendation

- **First implementation PR (9F.2):** provider + adapter + runner + registry lane + config flag + fixtures. One coherent slice. No SQL (`etf_fund_note` already in enum). No UI, no decision policy, no canonical/forensics changes.
- **Model:** Claude Sonnet (multi-file but mechanical, mirrors the FRED lane precedent).
- **Expected usage:** Medium. 1 yfinance call/ETF/explicit run; ~12 ETFs → trivial cost; free.
- **Source robustness:** adequate for v1 holdings/sector/expense/yield/issuer; **geography is a known gap** (defer to issuer data); concentration is approximate (top-10 only). Honest and shippable.
- **Cost/API risk:** none paid. Only risk is yfinance `funds_data` instability (unofficial). Mitigated by fail-closed provider + honest MISSING + UNOFFICIAL_AGGREGATOR credibility label. Issuer-official upgrade remains the documented next lever if a family's coverage is weak.

---

## Next Claude Sonnet implementation prompt (first safe PR — 9F.2)

```md
Repo: prashanthkrishnan91/claude_financetracker_pk91
Branch: claude/stage-9f2-etf-fund-data-provider

Task: Stage 9F.2 — ETF Fund Data Provider + Adapter + Evidence Lane v1 (yfinance funds_data).
Severity: Level 2. One capability slice. No SQL, no UI, no decision-policy changes, no canonical-dataset or forensics changes (that is 9F.3).

Safety packs / archetype: Data Truth / Evidence Suppression Pack; free-first evidence-lane archetype (model the FRED lane, Stage 5I).

Read first:
- v2/backend/app/services/intelligence/research_workers/fred_provider_v1.py (provider template)
- v2/backend/app/services/intelligence/research_workers/fred_macro_adapter_v1.py (adapter template)
- v2/backend/app/services/intelligence/research_workers/evidence_lane_runner_v1.py (runner + flag wiring)
- v2/backend/app/services/intelligence/research_workers/evidence_provider_registry_v1.py (lane + provider entry)
- v2/backend/app/services/intelligence/research_workers/contracts.py (WorkerOutput/Source/Fact)
- v2/backend/app/services/agents/data_sources.py:680 (existing yfinance fetch pattern)
- artifacts/Stage_9F1_ETF_Fund_Data_Provider_Plan.md (this plan — §4/§5 are the contract)

Build:
1. etf_fund_data_provider_v1.py — typed sync client over yfinance Ticker.funds_data.
   Injectable funds_data_fn for tests; deferred yfinance import on the real path.
   Returns a typed EtfFundDataResult: top_holdings [{symbol,name,weight_pct}],
   sector_weightings, fund_overview (family, categoryName, legalType), expense_ratio,
   distribution_yield (from .info), asset_classes, aum. fetch_status:
   success | not_a_fund | empty | error. Never raises. Never fabricates.
   Commodity trust / empty funds_data → status "empty" (honest, not error).
2. etf_fund_data_adapter_v1.py — PURE, no IO. EtfFundDataResult -> WorkerOutput.
   artifact_type="etf_fund_note", skill_pack="etf_fund_composition_evidence_v1",
   model_version="etf_fund_composition_v1", scope_kind="ticker",
   source_kind="vendor_fundamentals", provider_name="yfinance",
   source_url=https://finance.yahoo.com/quote/<TICKER>/holdings.
   One FactRecord per holding + per sector (fact_kind="metric_observation",
   axis_hint="exposure"). geography_exposure=null with reason (yfinance gap).
   top_n_concentration_pct = sum of returned top-N weights (label approximate).
   Honest empty path -> thin artifact with limitations, no facts, never fabricated.
   No forbidden payload keys; no raw provider dump; deterministic idempotency key.
3. evidence_lane_runner_v1.py — add _is_etf_fund_data_enabled(settings) and
   run_etf_fund_data_evidence(...) (ETF-only guard: skip non-ETF tickers honestly,
   reuse asset_type/category from holding_context). Compact structured logs:
   etf_fund_data_evidence_start / _written / _skip / _complete. Persist via
   ResearchArtifactServiceV1.write_artifact.
4. evidence_provider_registry_v1.py — add LANE_ETF_FUND_DATA="etf_fund_data" to
   ALL_LANES; add it to the existing yfinance entry supported_lanes +
   max_stale_age_hours {etf_fund_data:24.0}. No new provider id.
5. config.py — intel_v3_etf_fund_data_evidence_enabled: bool = False.

Tests (fixture-based, no network): recorded funds_data shapes for SPY/VOO/QQQ/SCHD/VXUS;
GLD/commodity empty -> NOT_APPLICABLE-style thin artifact; equity ticker -> not_a_fund skip;
timeout/error -> honest no-data, no crash; adapter asserts no forbidden keys, no raw payload
leak, geography MISSING honest, deterministic replay key, safe_for_decision False.
Live yfinance test gated behind an env flag, excluded from default run.

Acceptance evidence: new tests pass; existing Stage 5I/9F suites still pass; flag default OFF;
no SQL; no canonical_etf_fund_dataset_v1.py / forensics / decision-policy edits. Update
docs/ai/HANDOFF.md (replace/summarize) with the new lane, flag, and "9F.3 normalizes into
canonical dataset" as next blocker. Fill the PR template; state Supabase SQL requirement = NONE.

Stop condition: stop after the provider+adapter+lane PR is opened. Do NOT wire the canonical
dataset or forensics, and do NOT propose the 9F.3 prompt.

Execution principles: before coding, state assumptions and success criteria; keep changes simple
and surgical; every changed line must trace to this task; fix root cause not symptom; if the
durable fix exceeds scope, stop and propose the split.
```
