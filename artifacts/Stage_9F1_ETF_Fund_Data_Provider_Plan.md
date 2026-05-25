# Stage 9F.1 Plan — Real ETF Fund Data Provider / Composition Adapter v1

Status: planning/spec only. No code in this PR. Roadmap: ETF parity lane (follows Stage 9F honest scaffold). Severity: Level 2.

> **v2 (provider-research-driven).** Broadened the earlier yfinance-first draft into a best-value S-grade evaluation. Headline correction: **Massive's API does provide real ETF fund intelligence** (full constituents, exposure, profiles, taxonomies, fund flows, analytics) via its ETF Global partnership — **not** price/reference-only (§4).
>
> **v3 (free-tier audit).** Audited a "free FMP + free Massive" hybrid for a personal 2-user app (§11). Verdict: **not certifiable as S-grade as-is** — Massive free almost certainly excludes ETF Global data, FMP free ETF endpoints are unverified, and FMP's individual/free license restricts displaying data to end users.
>
> **v4 (DECISION LOCKED — this revision).** The owner approved proceeding **without free-tier testing**. That is sound because the chosen S-grade authority is **keyless and PRIMARY_AUTHORITY by construction** and needs no testing to certify: **SEC NPORT-P (official full holdings) + issuer-official files (daily)** for ETF composition, layered on the repo's already-wired official sources for the other asset classes. FMP/Massive are demoted to **optional future convenience layers** (wire only if/when keys + the §11 ToS check are accepted). §12 locks the full-app S-grade data architecture + the synthesis-readiness contract; §13 is the build sequence. The next build slice (§ prompt) is keyless and fixture-testable today.

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

## 11. Audit — is "free FMP + free Massive" a certified S-grade path? (personal 2-user app, stocks+ETFs+crypto)

**Context update:** personal investment-intelligence engine, max 2 users (owner + spouse), all decisions rely on this data → needs S-grade but at low cost. Universe is all **passive index/sector ETFs** (+ GLD commodity trust), so holdings freshness is non-critical (passive funds rebalance with their index, not daily).

**Verdict: NOT a certified S-grade path as specified.** Stacking two free tiers is the wrong model. Honest reasons:

1. **Massive free almost certainly excludes the ETF Global data.** Free tier = delayed stock prices, 5 calls/min. The ETF Global partner endpoints (constituents/profiles/exposure — the actual S-grade ETF intelligence, with a 1–2 business-day processing delay) are premium/partner data; **free-tier availability is undocumented and unverified, and very likely paid.** If paid, "Massive free" adds only delayed prices the repo already gets from yfinance/CoinGecko ⇒ contributes ~nothing to the S-grade goal.
2. **FMP free ETF endpoints are unverified.** Free = 250 req/day (ample for 2 users), 500 MB/30-day, 5y history / 5q statements. Most endpoints are reportedly free-accessible, but some are paid-gated and **whether ETF holdings/sector/country weighting work on a free key is not publicly documented** — requires a live key test to certify.
3. **FMP license restricts displaying data to end users — even free/individual.** "Individual plans don't allow the display or distributing of the data to end users or the public"; displaying in an app needs a separate Data Display Licensing Agreement. A spouse viewing the app UI is technically a second end-user ⇒ real ToS grey area to accept knowingly.
4. **For stocks + crypto, free FMP/Massive add little over the repo's existing free sources:** SEC EDGAR (OFFICIAL fundamentals) + yfinance (prices/metadata) for stocks; CoinGecko (keyless) for crypto are already wired. The unique gap is **ETF composition** — exactly the part most likely gated/paid on both free tiers.

**Certifiable near-zero-cost S-grade backbone (corrected recommendation):**
- **ETF S-grade authority = SEC NPORT-P (free, official, full holdings) + issuer-official files (free, daily full holdings+weights; iShares AJAX CSV is cleanest, SSGA XLSX, Vanguard region data for VXUS geography).** This is certifiable without depending on any vendor's free-tier whims or display license.
- **FMP = convenience/normalization layer** for uniform holdings/sector/**country (VXUS)** across all 12 — free **iff** its ETF endpoints test out on a free key (within 250/day easily); else cheapest paid individual plan. Accept the display-ToS caveat for private use.
- **Massive = only if you pay** for the tier that bundles ETF Global (~$79/mo). Do not rely on Massive free for ETF intelligence.
- **Crypto = CoinGecko** (already in repo). **Stock fundamentals = SEC EDGAR + yfinance** (already in repo).
- **yfinance = fallback/metadata only**, never S-grade primary.

**Two cheap, certifiable end states:**
- **$0/mo:** SEC NPORT-P + issuer-official files (ETF) + CoinGecko (crypto) + SEC EDGAR/yfinance (stocks). S-grade authority; cost is engineering effort (≈5 issuer parsers for a fixed 12-ticker list) + NPORT lag.
- **~$0–50/mo:** add FMP free/individual as the daily normalization spine over the same free authorities. Lowest effort; carries the display-ToS caveat.

**What must be verified before any build (10-minute spike, no production code):**
1. Does an FMP **free** key return ETF holdings + sector-weighting + country-weighting for SPY, VXUS, QQQ? (certifies the cheap spine)
2. Are Massive's ETF Global endpoints reachable on any **free** tier, or only paid? (settles whether Massive free is useful at all)
3. Confirm FMP display-ToS acceptability for private 2-user use.

Until 1–3 are answered with real responses, **the free-tier hybrid cannot be certified S-grade** — proceed to the spike below, not to a build.

## 12. LOCKED S-grade data architecture (all asset classes) + synthesis-readiness contract

This is the committed data foundation for the app. Everything the app synthesizes into actionable insight must trace to a source at or above the credibility tier below, with provenance and an as-of date, and may never be fabricated.

### 12.1 S-grade data contract (the bar every datum must meet)
A datum may be marked `synthesis_ready` only if **all** hold:
- **Authority** ≥ the tier required for its domain (§12.2). ETF composition requires PRIMARY_AUTHORITY (issuer/SEC); equity fundamentals require OFFICIAL (SEC); macro requires OFFICIAL (FRED).
- **Completeness** — required fields present. For ETF holdings this means **full constituents (not top-N)**; for VXUS it means **country/geography present**; for equity it means the needed XBRL facts.
- **Freshness** within the domain SLA, with an explicit as-of/holdings date.
- **Provenance** recorded internally (source id, url, as-of). No raw provider payloads, source URLs, or keys in diagnostics.
- **No fabrication** — missing data is explicit `MISSING` + reason, never invented or inferred.
- **safe_for_decision stays False at the data/evidence layer** — readiness gates feed the deterministic policy; the data layer never asserts Buy/Hold/Trim/Sell.

### 12.2 Locked source map (domain × asset class → certified source)

| Domain | Asset class | Certified source (authority) | Freshness | Feeds | Status |
|---|---|---|---|---|---|
| Fundamentals / financials | Equity | **SEC EDGAR CompanyFacts** (PRIMARY/OFFICIAL); yfinance metadata fallback | ≤7d filings | `canonical_equity_dataset`, valuation | **wired** |
| Valuation inputs (EPS/price) | Equity | `portfolio_snapshots` certified price + SEC facts | price ≤5min; EPS ≤7d | `equity_valuation_evidence` (9E/9E.1) | **wired** |
| Technicals / price history | Equity, ETF | yfinance history (UNOFFICIAL — context only, not authority) | ≤24h | technicals lane | **wired** |
| News / catalyst | Equity | SEC filing catalyst lane (OFFICIAL) | ≤30d | sentiment/catalyst lanes | **wired** |
| **Holdings / weights** | **ETF** | **SEC NPORT-P (PRIMARY, periodic) + issuer-official files (PRIMARY, daily)** | NPORT quarterly+lag; issuer daily | `canonical_etf_fund_dataset` | **BUILD (9F.2)** |
| **Sector exposure** | **ETF** | issuer-official files / derive from NPORT constituents | daily / quarterly | ETF dataset | **BUILD** |
| **Geography / country** | **ETF** | issuer-official (Vanguard region for **VXUS**) / derive from NPORT | daily / quarterly | ETF dataset | **BUILD** |
| Expense / yield / AUM / issuer / category | ETF | issuer product data (PRIMARY) | daily/near | ETF dataset | **BUILD** |
| Holdings (commodity) | ETF (**GLD**) | SSGA/SPDR sponsor (PRIMARY) — bullion + expense/AUM only; composition **N/A** | daily | ETF dataset (special-case) | **BUILD** |
| Market / supply / fundamentals | Crypto | **CoinGecko** (keyless, free) | ≤15min | `canonical_crypto_dataset` | source wired; **dataset BUILD (9G)** |
| Macro context | Portfolio | **FRED** (OFFICIAL) | ≤24h | macro lane | **wired** |
| Convenience normalization (optional) | Equity/ETF | **FMP** (LOW_COST) — only after §11 free-endpoint + display-ToS check | per-tier | normalization layer | **optional/deferred** |
| Fallback / metadata only | All | **yfinance** (UNOFFICIAL) — never S-grade primary | n/a | fallback | wired |

Massive + ETF Global remain a **paid** alternative (~$79/mo, SPECIALIST_ETF) that could replace the NPORT+issuer ETF backbone with one REST source — adopt only if the owner later prefers paying over maintaining issuer parsers.

### 12.3 Synthesis-readiness contract (data → actionable insight, authority preserved)
- Each asset class has a **canonical dataset**: equity ✅, ETF scaffold ✅ (to be filled by 9F.2/9F.3), crypto ❌ (build at 9G). Parity across all three is the goal so synthesis treats every holding uniformly.
- A canonical row is `synthesis_ready=True` only when its required S-grade domains (§12.2) are `AVAILABLE` and within freshness; otherwise it stays degraded with explicit reasons (no silent gaps).
- Synthesis-ready canonical datasets feed the **existing deterministic Intel v3 evidence + decision layers**. They make insights *grounded and complete*; they do **not** relocate decision authority.
- **INVARIANT (unchanged, non-negotiable):** the deterministic backend policy owns the final visible **Buy/Hold/Trim/Sell** authority. Research/synthesis/LLM layers produce evidence + plain-English explanation only; `safe_for_decision` stays False at the data layer. The "actionable insight you act on" = the deterministic policy verdict + evidence-grounded explanation, fed by S-grade complete data — never an LLM's unilateral call. (See Deterministic Decision Authority Pack + `NORTH_STAR.md`.)
- Net effect for the owner: every holding (stock, ETF, crypto) reaches a complete, high-credibility, fresh canonical dataset → the deterministic engine can explain *why* an action is recommended with no missing-data blind spots, which is what makes the recommendation safe to act on.

## 13. Build sequence (capability slices — keyless first, no testing dependency)

1. **9F.2a — SEC NPORT-P ETF holdings lane (keyless, build first).** Provider + pure adapter + flag-gated lane → `etf_fund_note` artifacts (full constituents + weights; sector/geography derivable from constituents). Fixtures from a real NPORT-P XML. PRIMARY_AUTHORITY, no key, fully unit-testable now. No SQL (`etf_fund_note` already in enum).
2. **9F.2b — issuer-official daily adapters (one issuer per slice, fixture-tested).** Add daily freshness + native geography. Start with the cleanest stable endpoint (SSGA SPY/XLE/GLD XLSX; Vanguard region data for **VXUS**); GLD handled as commodity special-case. Named stable endpoints only; never blind scraping.
3. **9F.3 — canonical ETF normalization.** Wire 9F.2 artifacts into `canonical_etf_fund_dataset_v1` (flip composition `MISSING → AVAILABLE/PARTIAL`), update forensics bucket + readiness gate. Separate from provider work.
4. **9G — canonical crypto dataset.** `canonical_crypto_dataset_v1` from CoinGecko (keyless) for asset-class parity.
5. **(optional, later) FMP/Massive convenience layer.** Only after the §11 free-endpoint + display-ToS verification (FMP) or a paid decision (Massive). Not required for S-grade — the keyless backbone already meets the bar.

Every slice: flag-gated (default OFF), fixture tests, `safe_for_decision=False`, no decision-policy change, no SQL, HANDOFF updated.

---

## Next implementation prompt (9F.2a — SEC NPORT-P ETF holdings lane; keyless build)

> Decision locked (§12, v4). The free-tier verification spike is **deferred/optional** — the keyless PRIMARY_AUTHORITY backbone needs no testing to certify. The first real build slice is the SEC NPORT-P ETF holdings lane.

```md
Repo: prashanthkrishnan91/claude_financetracker_pk91
Branch: claude/stage-9f2a-etf-nport-holdings-lane

Task: Stage 9F.2a — SEC NPORT-P ETF holdings evidence lane v1 (keyless, fixture-tested).
Severity: Level 2. One capability slice. No SQL (etf_fund_note already in the migration-023 enum),
no UI, no decision-policy changes, no canonical-dataset/forensics edits (that is 9F.3).

Safety packs / archetype: Data Truth / Evidence Suppression Pack; free-first evidence-lane
archetype (model the FRED lane, Stage 5I). PRIMARY_AUTHORITY source, keyless — no API key, no
free-tier uncertainty, fully unit-testable from a recorded NPORT-P XML fixture.

Read first:
- artifacts/Stage_9F1_ETF_Fund_Data_Provider_Plan.md (§2 S-grade bar, §5 contract, §12 locked
  architecture, §13 build sequence)
- v2/backend/app/services/intelligence/research_workers/fred_provider_v1.py (provider template)
- v2/backend/app/services/intelligence/research_workers/fred_macro_adapter_v1.py (adapter template)
- v2/backend/app/services/intelligence/research_workers/evidence_lane_runner_v1.py (runner + flag)
- v2/backend/app/services/intelligence/research_workers/evidence_provider_registry_v1.py
- v2/backend/app/services/intelligence/research_workers/contracts.py (WorkerOutput/Source/Fact)
- v2/backend/app/services/intelligence/v3/canonical_etf_fund_dataset_v1.py (downstream consumer; do NOT edit)

How SEC NPORT-P works (ground the implementation): a fund's NPORT-P filings are on SEC EDGAR
(submissions API → filing index → primary_doc.xml). The XML lists every portfolio holding
(name, identifiers, value, pctVal weight) plus issuer country, enabling FULL holdings + weights +
geography derivation. Public disclosure is the 3rd month of each quarter with a lag → treat as
PRIMARY but periodic/stale (record the report period + as-of). Map our ETF tickers to the filing
entity CIK (SPY/XLE/GLD = SSGA trusts; VOO/VTI/VGT/VHT/VIS/VXUS/VYM = Vanguard; QQQ = Invesco;
SCHD = Schwab). GLD is a commodity trust → may have no equity holdings; handle as composition
NOT_APPLICABLE (bullion + expense/AUM only), never fabricated.

Build (mirror the FRED lane shape):
1. nport_provider_v1.py — keyless EDGAR client: ticker→CIK→latest NPORT-P→parse holdings.
   Injectable http_get_fn; deferred httpx import on the real path; SEC User-Agent header; bounded
   request budget; fail-closed (never raises) returning a typed NportProviderResult with report
   period/as-of, full holdings [{name, cusip/isin/ticker?, weight_pct, value, country}], and a
   status (success | not_found | no_holdings | error). Never fabricates.
2. etf_nport_adapter_v1.py — PURE, no IO. NportProviderResult → WorkerOutput.
   artifact_type="etf_fund_note", skill_pack="etf_nport_holdings_evidence_v1",
   model_version="etf_nport_holdings_v1", scope_kind="ticker",
   source_kind="sec_filing", provider_name="sec_edgar",
   source_url=SEC filing URL. One FactRecord per holding (fact_kind="metric_observation",
   axis_hint="exposure"). Derive sector? (only if present) and geography from holding countries.
   No raw payload dump; no forbidden keys; deterministic replay key; honest NOT_APPLICABLE for GLD.
3. evidence_lane_runner_v1.py — _is_etf_nport_enabled(settings) + run_etf_nport_holdings_evidence(...)
   (ETF-only guard using holding_context asset_type/category; skip non-ETF honestly). Compact logs:
   etf_nport_evidence_start/_written/_skip/_complete. Persist via ResearchArtifactServiceV1.
4. evidence_provider_registry_v1.py — add LANE_ETF_FUND_DATA="etf_fund_data" to ALL_LANES; extend
   the sec_edgar entry supported_lanes + max_stale_age_hours {etf_fund_data: 2160.0 (~90d, periodic)}.
5. config.py — intel_v3_etf_nport_evidence_enabled: bool = False.

Tests (fixture-based, no network): recorded NPORT-P XML for a real Vanguard/SSGA fund → full
holdings + weights + geography; GLD-style no-equity-holdings → NOT_APPLICABLE composition; non-ETF
ticker → skip; not_found/parse-error → honest no-data artifact, no crash; adapter asserts no
forbidden keys, no raw payload leak, geography derived (not fabricated), deterministic replay key,
safe_for_decision False, report period/as-of recorded.

Acceptance: new tests pass; existing Stage 5I/9F suites still pass; flag default OFF; no SQL; no
canonical_etf_fund_dataset_v1.py / forensics / decision-policy edits. Update docs/ai/HANDOFF.md
(replace/summarize) — new lane, flag, "9F.2b issuer-official daily adapters + 9F.3 canonical
normalization" as next. Fill the PR template; Supabase SQL requirement = NONE.

Stop condition: stop after the lane PR is opened. Do NOT build issuer adapters, canonical
normalization, or the crypto dataset (those are 9F.2b / 9F.3 / 9G).

Execution principles: before coding, state assumptions and success criteria; keep changes simple
and surgical; every changed line must trace to this task; fix root cause not symptom; if the
durable fix exceeds scope, stop and propose the split.
```
