# Stage 9F.1 Plan — Real ETF Fund Data Provider / Composition Adapter v1

Status: planning/spec only. No code in this PR. Roadmap: ETF parity lane (follows Stage 9F honest scaffold). Severity: Level 2.

> **v2 revision (provider-research-driven).** The earlier draft was yfinance-first. This revision broadens it into a best-value, S-grade ETF data-source evaluation. Headline correction: **Massive's API does provide real ETF fund intelligence** (full constituents, exposure, profiles, taxonomies, fund flows, analytics) via its ETF Global partnership — it is **not** price/reference-only. The honest bottom line is in §8: there is **no confirmed self-serve low-cost provider that guarantees full S-grade coverage today**, so the corrected next step is a **provider decision checkpoint**, not coding.

## 1. Problem & current state

Stage 9F built an honest ETF scaffold (`canonical_etf_fund_dataset_v1.py`). It marks **all composition MISSING**, fund identity/cost/yield PARTIAL-at-best, and `etf_fund_intelligence_ready=False` always, because **no dedicated fund-data provider exists**. The scaffold never extracts ETF-specific fields even when the yfinance fundamentals lane is usable — by design, it only reads lane *usability labels*, not fund payloads.

The gap to close: a real provider + adapter that fetches and normalizes ETF holdings, sector exposure, geography, expense, yield, issuer, and category, then writes them as evidence artifacts the canonical dataset can consume — at **S-grade** quality (full holdings + weights + sector + geography + cost/yield + identity + AUM + as-of date), not easiest-free quality.

## 2. S-grade bar (hard definition used throughout)

A source qualifies as **S-grade foundational ETF data** only if it can deliver, for our universe, with a holdings as-of date:
full holdings (NOT top-N) · holdings weights · sector exposure · geography/country exposure · expense ratio · distribution/SEC yield · issuer/fund family · category/index/strategy · AUM/liquidity.

Explicit disqualifiers (per task constraints): top-10-only holdings is **not** full composition; missing geography is **not** S-grade for VXUS; price/reference market data is **not** ETF fund intelligence; yfinance is **not** an S-grade primary.

## 3. Credibility tiers

`PRIMARY_AUTHORITY` (issuer/SEC) · `INSTITUTIONAL_VENDOR` (Morningstar/FactSet/LSEG/Intrinio) · `SPECIALIST_ETF_VENDOR` (ETF Global, Trackinsight, VettaFi/ETFdb) · `LOW_COST_VENDOR` (FMP, EODHD) · `UNOFFICIAL_AGGREGATOR` (yfinance) · `MARKET_DATA_ONLY` (Polygon, Alpha Vantage, IEX) · `SCRAPED_PUBLIC_SOURCE` (HTML scraping of pages with no stable file/endpoint).

## 4. Provider evaluation (researched May 2026)

Legend for holdings: **Full** = all constituents; **Top-N** = capped (~top 10); **—** = not provided; **?** = unverified. Pricing marked *"sales/contact"* means no public self-serve price → implies a procurement cycle, minimums, and likely annual contracts unsuitable for a personal app.

### 4.1 Massive (via ETF Global partnership) — **verify entitlement; strongest affordable S-grade candidate**
- Holdings: **Full** (ETF Constituents endpoint; "complete transparency into holdings/composition", 3,000+ ETFs, normalized daily). Weights: **Yes**. Sector: **Yes** (Profiles & Exposure). Geography: **Yes** (Profiles & Exposure). Expense: **Yes**. Yield: **likely Yes** (profile). Issuer/family: **Yes**. Category/index/strategy: **Yes** (Taxonomies). AUM/flows: **Yes** (Fund Flows + analytics). As-of date: **Yes** (daily-updated).
- API: REST, JSON. Pricing: base Massive plans are **public self-serve** — Starter $29/mo (15-min delayed), Developer $79/mo (real-time), Advanced $199/mo (unlimited + WebSocket). **UNVERIFIED:** whether the ETF Global partner endpoints are included in those tiers or require a separate entitlement/add-on/sales-contact. This is the single most important open question in this plan.
- Credibility: **SPECIALIST_ETF_VENDOR** (ETF Global is an established ETF data specialist). Reliability/maintainability: high (vendor REST, normalized, daily). Terms: vendor ToS; personal-app use likely fine, redistribution restricted. Effort: low–medium (one REST adapter).
- S-grade verdict: **Yes, if ETF Global endpoints are reachable on a ≤~$199/mo self-serve tier.** If they require sales-contact, it drops to the institutional bucket below.

### 4.2 Intrinio — self-serve S-grade, but institutional price
- Holdings: **Full** (all US-traded ETF constituents). Weights: Yes. Sector: Yes. Geography: ? (US-exchange country of listing, not full look-through). Expense/yield/issuer/category/AUM: Yes (118 ETF attributes + analytics). As-of: Yes.
- API: REST/CSV. Pricing: **~$9,000/yr** for holdings/constituents **+ ~$9,000/yr** for ETF analytics/metadata ⇒ ~**$18k/yr**. Self-serve quoted but custom contact recommended.
- Credibility: **INSTITUTIONAL_VENDOR**. Reliability: high. S-grade verdict: **Yes** technically, **No** on affordability for a personal portfolio app.

### 4.3 Financial Modeling Prep (FMP) — best low-cost full-holdings option, with caveats
- Holdings: **Full** (ETF holdings endpoint returns all stocks held with weight, shares, assets). Weights: Yes. Sector: Yes (sector-weighting). Geography: **Yes** (country-weighting endpoint). Expense/yield: **Partial** (via ETF info/profile, not always complete). Issuer/category: Yes. AUM: Partial. As-of: Yes (but many ETF holdings are **NPORT-derived/quarterly**, not daily — a real freshness limitation).
- API: REST, JSON/CSV, bulk download. Pricing: **public self-serve**, individual tiers Starter / Premium / Ultimate (≈ **$22 / $59 / $99 per month** range; confirm current figures on the pricing page — automated fetch was blocked). ETF holdings/sector/country are on **paid** tiers; **commercial-use license is separate/higher**.
- Credibility: **LOW_COST_VENDOR**. Reliability: good API; data depth/freshness for holdings weaker than specialists (NPORT-lagged for some funds). S-grade verdict: **Near-S-grade at low cost** — full holdings + sector + geography ✅, but quarterly-ish holdings freshness and partial expense/yield keep it just under strict daily-S-grade.

### 4.4 EODHD — rich metadata + geography, but **top-10 holdings only**
- Holdings: **Top-N (top 10 only)** ⇒ **fails** the full-composition bar. Weights: Yes (top 10). Sector: Yes. Geography: **Yes** (world regions). Expense: Yes (net expense ratio). Yield: Yes (current yield). Issuer/category: Yes. AUM: Yes (total net assets). As-of: Yes.
- API: REST JSON, ~$80/mo all-in fundamentals tier. Credibility: **LOW_COST_VENDOR**. S-grade verdict: **No** for full holdings; **excellent for fund metadata + geography** as a complement.

### 4.5 Trackinsight (direct + Nasdaq Data Link "TRACK") — ETF specialist, mostly enterprise
- Holdings/exposure/ESG/classification: comprehensive global ETF DB. Free tier exists (limited); paid via Nasdaq Data Link or enterprise. Pricing: **largely sales/contact** for full API capacity.
- Credibility: **SPECIALIST_ETF_VENDOR**. S-grade verdict: **Yes on data**, **uncertain/contact on affordable self-serve**.

### 4.6 VettaFi / ETFdb — specialist, no public self-serve API
- Strong ETF classification/screening. **No documented public developer API**; enterprise/asset-manager solutions ⇒ **sales/contact**. Credibility: **SPECIALIST_ETF_VENDOR**. S-grade verdict: data Yes, **not self-serve** ⇒ impractical for this app.

### 4.7 ETF Global (direct) — specialist; reachable affordably *through Massive*
- The same dataset behind §4.1. Direct enterprise licensing = **sales/contact**. The practical affordable path to ETF Global is **via Massive** (§4.1). Credibility: **SPECIALIST_ETF_VENDOR**.

### 4.8 Morningstar / FactSet / LSEG / Cbonds — institutional, unaffordable, sales/contact
- All four: deep/authoritative ETF data (Morningstar & FactSet are de-facto ETF analytics standards; LSEG/Refinitiv broad; Cbonds is bond-centric with secondary ETF coverage). All **sales/contact**, enterprise contracts typically **$10k–$100k+/yr**, minimums, redistribution controls. Credibility: **INSTITUTIONAL_VENDOR** (Cbonds borderline specialist for fixed income). S-grade verdict: **Yes on data, No on cost/onboarding** for a personal app.

### 4.9 Polygon.io / Alpha Vantage / IEX Cloud — **market-data-only, not fund intelligence**
- Provide price/aggregates/reference and some fundamentals, but **no full ETF holdings/sector/geography composition**. Credibility: **MARKET_DATA_ONLY**. S-grade verdict: **No** — explicitly out of scope as ETF fund intelligence; usable only for price/liquidity context.

### 4.10 Issuer-official sources (PRIMARY_AUTHORITY) — free, full, but per-issuer brittleness
All issuers post a post-close downloadable holdings file. Stability varies; only build adapters against **named stable endpoints** with fixture-tested parsers.
- **iShares/BlackRock:** cleanest — stable AJAX CSV (`.../{productId}/{slug}/{dossierId}.ajax?fileType=csv&fileName={TICKER}_holdings&dataType=fund`), daily, full holdings + weights + sector + tickers. (No iShares ticker in our universe, but the cleanest pattern.)
- **State Street / SPDR (SPY, XLE, GLD):** daily holdings file (XLSX) per product page; reasonably stable but format/markup changes happen. GLD = single-commodity trust (gold bullion) ⇒ no equity holdings/sector/geography; only expense/AUM/bullion.
- **Vanguard (VOO, VTI, VGT, VHT, VIS, VXUS, VYM):** holdings on product pages via undocumented JSON/multi-tab; **less stable**, no documented file API. Vanguard does publish region/country for international funds (relevant for **VXUS geography**).
- **Invesco (QQQ):** product-page holdings CSV; moderate stability.
- **Schwab (SCHD):** product-page holdings; moderate stability.
- Credibility: **PRIMARY_AUTHORITY** (the fund's own data). Freshness: daily. Effort: **high** (5 bespoke parsers, ongoing maintenance). Redistribution: personal use generally OK, redistribution restricted. Geography: only some issuers publish country breakdown.

### 4.11 SEC NPORT-P (EDGAR) — free PRIMARY_AUTHORITY full holdings, but stale
- Full portfolio holdings filed monthly; **only the third month of each quarter is public, with ~60-day lag** ⇒ quarterly + stale for "current" composition. Geography derivable from constituents. Free, official, structured (XML). Credibility: **PRIMARY_AUTHORITY**. S-grade verdict: **Yes for periodic full-composition ground-truth + geography**, **No for daily freshness**. Excellent free cross-check/baseline.

### 4.12 yfinance (`funds_data`) — fallback/metadata only
- Top-10 holdings only, no reliable geography, UNOFFICIAL_AGGREGATOR. **Disqualified as S-grade primary** by task constraint. Keep strictly as metadata/fallback.

## 5. Comparison table (cost / value / reliability / S-grade)

| Provider | Holdings | Wts | Sector | Geo | Expense | Yield | Issuer | Cat/Index | AUM | As-of | API | Public price (personal-app est.) | Credibility | Reliability | S-grade? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Massive + ETF Global** | Full | ✅ | ✅ | ✅ | ✅ | ✅? | ✅ | ✅ | ✅ | ✅ | REST | $29–$199/mo public **(ETF Global entitlement UNVERIFIED)** | SPECIALIST_ETF | High | **Yes\*** (pending entitlement) |
| Intrinio | Full | ✅ | ✅ | ? | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST/CSV | ~$18k/yr (holdings+analytics) | INSTITUTIONAL | High | Yes / unaffordable |
| FMP | Full | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ (qtrly-ish) | REST | ~$22–$99/mo + commercial license | LOW_COST | Good | **Near-S** |
| EODHD | Top-10 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST | ~$80/mo | LOW_COST | Good | No (holdings) |
| Trackinsight | Full | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST/Nasdaq | free tier / **sales/contact** | SPECIALIST_ETF | High | Yes / contact |
| VettaFi / ETFdb | Full | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | none public | **sales/contact** | SPECIALIST_ETF | High | Yes / not self-serve |
| ETF Global (direct) | Full | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST | **sales/contact** (use via Massive) | SPECIALIST_ETF | High | Yes / via Massive |
| Morningstar | Full | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST | **sales/contact** $10k–$100k+/yr | INSTITUTIONAL | High | Yes / unaffordable |
| FactSet | Full | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST | **sales/contact** $$$$ | INSTITUTIONAL | High | Yes / unaffordable |
| LSEG (Refinitiv) | Full | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | REST | **sales/contact** $$$$ | INSTITUTIONAL | High | Yes / unaffordable |
| Cbonds | Partial(ETF) | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ | ⚠️ | ⚠️ | ⚠️ | REST | **sales/contact** | INSTITUTIONAL/bond | Med | No (ETF secondary) |
| Polygon / AlphaVantage / IEX | — | — | — | — | — | — | ⚠️ | ⚠️ | ⚠️ | n/a | REST | $0–$199/mo | MARKET_DATA_ONLY | High(price) | **No** |
| Issuer-official | Full | ✅ | ⚠️(some) | ⚠️(some) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ daily | files | **free** | PRIMARY_AUTHORITY | **brittle/per-issuer** | Yes\* (high effort) |
| SEC NPORT-P | Full | ✅ | derive | derive | — | — | ✅ | ✅ | ✅ | ✅ **but quarterly+lag** | XML/EDGAR | **free** | PRIMARY_AUTHORITY | High | Periodic only |
| yfinance | Top-10 | ✅ | ⚠️ | — | ⚠️ | ⚠️ | ✅ | ⚠️ | ⚠️ | ⚠️ | lib | free | UNOFFICIAL_AGGREGATOR | low/unstable | **No** |

`*` conditional — Massive on entitlement verification; issuer-official on per-fund stable endpoints + fixture-tested parsers.

## 6. Answers to the required questions

1. **Best S-grade provider if cost is acceptable:** **Morningstar / FactSet** are the gold standard but enterprise-priced (sales/contact, $10k–$100k+/yr). The best *self-serve* S-grade is **Intrinio (~$18k/yr)**. The best *potentially affordable* S-grade is **Massive + ETF Global** — pending entitlement verification.
2. **Best value provider under low monthly cost:** **FMP (~$22–$99/mo)** — full holdings + sector + country at low cost (caveat: quarterly-ish holdings freshness, partial expense/yield). **Massive Developer ($79/mo)** ties or beats it *iff* ETF Global endpoints are included.
3. **Best free / near-free option:** **Issuer-official files + SEC NPORT-P** (both free, PRIMARY_AUTHORITY, full holdings), with **yfinance funds_data** only as metadata fallback. Trade-off: issuer parsers are brittle/high-effort and NPORT is quarterly+lagged ⇒ free path is not uniformly daily-S-grade.
4. **Best fallback / hybrid path if no single provider is affordable:** tiered-credibility hybrid — (a) one **low-cost vendor (FMP or Massive)** for uniform daily holdings/sector/**geography** across all 12; (b) **SEC NPORT-P** as official periodic full-composition ground-truth + geography cross-check; (c) **issuer-official adapters** only for funds where the vendor is weak/stale and a stable endpoint exists (start with the cleanest); (d) **yfinance** strictly as metadata fallback. Credibility labels carried per field.
5. **Explicit recommendation:** see §8.

## 7. Universe → realistic best source (S-grade lens)

| Ticker | Family | Primary candidate | Geography note |
|---|---|---|---|
| SPY, XLE | SSGA/SPDR | Massive/ETF Global **or** FMP; SSGA file as PRIMARY cross-check | US — geography trivial |
| GLD | SSGA/SPDR | **Special case** — commodity trust; expense/AUM/category only; composition NOT_APPLICABLE | N/A |
| VOO, VTI, VGT, VHT, VIS, VYM | Vanguard | Massive/ETF Global **or** FMP; Vanguard page as cross-check | US — geography trivial |
| **VXUS** | Vanguard | **Needs true country breakdown** — Massive/ETF Global or FMP country-weighting; Vanguard region data as PRIMARY | **Geography is the gating field; yfinance/EODHD top-10 insufficient** |
| QQQ | Invesco | Massive/ETF Global **or** FMP; Invesco file cross-check | US |
| SCHD | Schwab | Massive/ETF Global **or** FMP; Schwab page cross-check | US |

## 8. Recommendation for this app — **decision checkpoint, do not code yet**

**Honest bottom line:** There is **no confirmed self-serve, low-monthly-cost provider that guarantees full S-grade coverage** for the universe right now. The only *confirmed* self-serve S-grade option (Intrinio) is ~$18k/yr. The most promising affordable S-grade option (**Massive + ETF Global**) is **unverified** on the one question that decides everything: are the ETF Global endpoints reachable on a ≤~$199/mo self-serve tier, and is "constituents" full (not top-N) with an as-of date?

Therefore the correct next move is **not** to build a provider lane. It is a **provider decision checkpoint**:

- **Pause and verify Massive/ETF Global** entitlement + price + constituent depth (free-tier or trial spike against SPY/VXUS/QQQ; inspect one constituents + one profiles/exposure response). This single check determines the whole strategy.
- **If Massive/ETF Global is affordable & full** → adopt it as the **primary S-grade provider** (best value). Build the registry entry + adapter against it.
- **If not** → adopt the **hybrid (§6.4)**: pay for **FMP** (low-cost, full holdings+sector+country) as the daily spine, add **SEC NPORT-P** as free official ground-truth + geography, add **issuer-official adapters** only for stable named endpoints with fixture-tested parsers, keep **yfinance** as metadata fallback only.
- **Do we contact a provider?** Yes for Massive/ETF Global entitlement clarification (and only escalate to Morningstar/FactSet/Trackinsight sales if the app's value justifies enterprise cost — unlikely for a personal app).
- **Do we build issuer adapters?** Only as **hybrid fallback** for funds where the chosen vendor is weak, and only against **named stable endpoints** (iShares AJAX CSV is the model; SSGA daily XLSX; Vanguard/Invesco/Schwab require endpoint verification first). Never blind HTML scraping.
- **Do we use a paid provider?** Likely yes (FMP or Massive) — the free-only path cannot deliver uniform daily S-grade without high-maintenance issuer parsers.
- **yfinance?** Fallback/metadata only — never the S-grade primary.

**No S-grade-coverage honesty statement (required):** *Today, no affordable single provider is confirmed to give S-grade coverage for this ETF universe. Proceed to a provider decision checkpoint (verify Massive/ETF Global, else commit to the FMP+NPORT+issuer hybrid) before any implementation.*

## 9. Activation sequence (revised)

- **9F.1 (this doc)** — provider research + decision framing. No code.
- **9F.1-checkpoint** — provider verification spike (Massive/ETF Global trial; confirm depth/price/entitlement; confirm FMP geography/holdings freshness for VXUS). Research only; output = a one-line provider decision. No production code.
- **9F.2** — build the registry entry + evidence-lane adapter for the **chosen** provider (Massive/ETF Global **or** FMP-hybrid spine). `etf_fund_note` artifact_type already in the DB enum (no SQL). Flag-gated. Fixture tests.
- **9F.3** — canonical normalization into `canonical_etf_fund_dataset_v1.py` + forensics bucket + readiness gate (kept separate from provider work).

## 10. Validation (unchanged in spirit)

Fixture-based unit tests (recorded provider responses for SPY/VOO/QQQ/SCHD/**VXUS** + GLD edge + non-fund skip + empty/timeout). Live-provider test gated behind an env flag, excluded from CI default. Forensics expected-output test in 9F.3. Cache/freshness: holdings 24h SLA for daily vendors; NPORT treated as quarterly. Provenance + credibility label per field; no raw payloads; no fabricated MISSING data.

---

## Corrected next implementation prompt (provider decision checkpoint — research/spike only)

> The previous yfinance-first build prompt is **withdrawn**. Per §8, the next action is a provider decision checkpoint, not a lane build. Use this prompt next.

```md
Repo: prashanthkrishnan91/claude_financetracker_pk91
Branch: claude/stage-9f1-checkpoint-etf-provider-verification

Task: Stage 9F.1-checkpoint — ETF data provider verification spike (research/decision only).
Severity: Level 2. NO production code, NO SQL, NO UI, NO provider lane yet. Output is a one-line
provider decision + a short verification note appended to
artifacts/Stage_9F1_ETF_Fund_Data_Provider_Plan.md.

Why: §8 of the plan concludes no affordable self-serve provider is *confirmed* to give full
S-grade ETF coverage. The whole strategy hinges on one unknown: are Massive's ETF Global
endpoints (constituents, profiles/exposure) reachable on a <=~$199/mo self-serve tier, full
(not top-N), with an as-of date — for SPY, VXUS, QQQ?

Read first:
- artifacts/Stage_9F1_ETF_Fund_Data_Provider_Plan.md (§2 S-grade bar, §4 evaluation, §8 decision)

Do (research/verification only, no repo code changes beyond the plan doc):
1. Verify Massive + ETF Global: plan/tier that exposes ETF Constituents + Profiles & Exposure;
   confirm constituents = FULL holdings with weights + holdings as-of date; confirm sector +
   geography on profiles/exposure; confirm self-serve price (else mark "sales/contact required").
   Use only free docs / free-tier / trial responses for SPY, VXUS, QQQ. Do NOT commit any API key.
2. Cross-check the affordable fallback: confirm FMP returns FULL holdings + sector + COUNTRY
   weighting for VXUS, and assess holdings freshness (daily vs NPORT-quarterly). Confirm current
   FMP self-serve price tiers and whether a commercial license is required.
3. Record findings in a new "## 11. Provider verification results" section of the plan doc, then
   state ONE decision line: either "PRIMARY = Massive/ETF Global (<=$X/mo, full+geo+as-of)" or
   "PRIMARY = FMP-hybrid spine (FMP + SEC NPORT-P + issuer-official fallback)".

Hard constraints: do not recommend yfinance as primary; do not accept top-10 holdings as full;
do not accept missing geography as S-grade for VXUS; do not treat price/reference data as fund
intelligence; do not hand-wave pricing (mark sales/contact when unavailable); no blind scraping.

Stop condition: stop after the decision line + plan-doc update + PR. Do NOT build the provider
lane (that becomes 9F.2 once the provider is chosen).

Execution principles: before coding, state assumptions and success criteria; keep changes simple
and surgical; every changed line must trace to this task; fix root cause not symptom; if the
durable fix exceeds scope, stop and propose the split.
```
