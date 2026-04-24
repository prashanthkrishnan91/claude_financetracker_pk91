"""Portfolio Allocation Engine — converts compact_v1 analyst output into
exact dollar deployment decisions.

Pure scoring + constraint module. No DB or network IO, so it is trivial to
unit test. The Deploy tab consumes the output via the API wrapper.

Input:
  * cash_to_invest
  * current holdings (ticker, category, market_value, theme)
  * compact_v1 analyst insights (action, conviction, confidence, source)
  * optional target weights

Output:
  * ranked allocations (ticker, $ amount, before/after weight, reason)
  * exclusions with reason
  * trim candidates
  * portfolio-level explanation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


# ── Constants ────────────────────────────────────────────────────────────────

MIN_CONFIDENCE = 0.65
MIN_TICKER_ALLOCATION = 25.0
ROUNDING_STEP = 5.0
TOP_N_IDEAS = 5
MIN_TOP_N = 3

CONVICTION_WEIGHTS = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}

# Per-category max single-position target weight (% of portfolio)
MAX_SINGLE_STOCK_WEIGHT = 20.0
MAX_ETF_WEIGHT = 35.0
MAX_SPECULATIVE_WEIGHT = 5.0
MAX_SAME_THEME_WEIGHT = 40.0

# Schema versions and analysis sources we trust for deployment
ACCEPTED_SCHEMA_VERSIONS = {"compact_v1", "human_v2"}
ACCEPTED_SOURCES = {"live_llm", "fresh_llm"}

# Default theme keywords (used when holdings don't carry a theme)
_DEFAULT_THEME_MAP: dict[str, str] = {
    # AI / semis
    "NVDA": "ai_semis", "AMD": "ai_semis", "TSM": "ai_semis", "QCOM": "ai_semis",
    # Big tech / mag7
    "AAPL": "big_tech", "MSFT": "big_tech", "GOOGL": "big_tech",
    "META": "big_tech", "AMZN": "big_tech",
    # Broad ETFs
    "VOO": "broad_etf", "VTI": "broad_etf", "QQQ": "broad_etf", "SPY": "broad_etf",
    # Income ETFs
    "VYM": "income_etf", "SCHD": "income_etf",
    # Crypto
    "BTC": "crypto", "ETH": "crypto", "XRP": "crypto", "SOL": "crypto",
}


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Holding:
    """Current portfolio holding."""
    ticker: str
    market_value: float
    category: str = "Core"           # Crypto | Core | ETF | Other | IPO | SELL
    theme: Optional[str] = None


@dataclass
class InsightIn:
    """Compact_v1 analyst output for one ticker."""
    ticker: str
    action: str                      # BUY | HOLD | TRIM | SELL | REVIEW
    conviction_level: Optional[str] = None   # HIGH | MEDIUM | LOW
    conviction_score: Optional[float] = None # raw 0..1
    confidence: Optional[float] = None       # 0..1
    schema_version: Optional[str] = None     # compact_v1 | human_v2 | …
    analysis_source: Optional[str] = None    # live_llm | cached_run | deterministic_fallback
    used_fallback: bool = False
    category: Optional[str] = None           # override holding category
    theme: Optional[str] = None
    why: Optional[str] = None
    risk: Optional[str] = None
    do: Optional[str] = None
    alt_view: Optional[str] = None
    thesis: Optional[str] = None
    stale: bool = False
    quota_blocked: bool = False


@dataclass
class AllocationItem:
    ticker: str
    action: str
    amount: float
    current_weight: float
    after_weight: float
    target_weight: float              # % of THIS deployment
    conviction_level: str
    conviction_score: float
    confidence: float
    score: float
    reason: str                       # one-line reason
    why: Optional[str] = None
    risk: Optional[str] = None
    do: Optional[str] = None
    alt_view: Optional[str] = None
    category: str = "Core"


@dataclass
class Exclusion:
    ticker: str
    reason: str


@dataclass
class TrimCandidate:
    ticker: str
    action: str
    current_weight: float
    reason: str


@dataclass
class AllocationPlan:
    cash_to_invest: float
    total_deployed: float
    fully_allocated: bool
    allocations: list[AllocationItem]
    exclusions: list[Exclusion]
    trims: list[TrimCandidate]
    portfolio_explanation: str
    warning: Optional[str] = None
    strategy: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _round_to_step(amount: float, step: float = ROUNDING_STEP) -> float:
    if amount <= 0:
        return 0.0
    return round(amount / step) * step


def _infer_conviction_level(ins: InsightIn) -> str:
    lvl = (ins.conviction_level or "").upper()
    if lvl in CONVICTION_WEIGHTS:
        return lvl
    score = ins.conviction_score or 0.0
    if score >= 0.66:
        return "HIGH"
    if score >= 0.33:
        return "MEDIUM"
    return "LOW"


def _get_category(ticker: str, ins: InsightIn, holding: Optional[Holding]) -> str:
    if ins.category:
        return ins.category
    if holding and holding.category:
        return holding.category
    # Default: treat common ETFs as ETF; crypto tickers as Crypto
    t = ticker.upper()
    if t in {"VOO", "VTI", "QQQ", "SPY", "VYM", "SCHD", "XLE", "VGT", "VHT", "VIS", "VTV", "VUG", "BND", "GLD"}:
        return "ETF"
    if t in {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA"}:
        return "Crypto"
    return "Core"


def _get_theme(ticker: str, ins: InsightIn, holding: Optional[Holding]) -> Optional[str]:
    if ins.theme:
        return ins.theme
    if holding and holding.theme:
        return holding.theme
    return _DEFAULT_THEME_MAP.get(ticker.upper())


def _category_cap(category: str) -> float:
    c = (category or "").lower()
    if c == "etf":
        return MAX_ETF_WEIGHT
    if c in {"crypto", "ipo"}:
        return MAX_SPECULATIVE_WEIGHT
    return MAX_SINGLE_STOCK_WEIGHT


def _portfolio_total(holdings: list[Holding]) -> float:
    return sum(max(0.0, h.market_value or 0.0) for h in holdings)


def _current_weight(market_value: float, portfolio_total: float) -> float:
    if portfolio_total <= 0:
        return 0.0
    return max(0.0, (market_value / portfolio_total) * 100.0)


def _theme_exposure(holdings: list[Holding], portfolio_total: float) -> dict[str, float]:
    out: dict[str, float] = {}
    if portfolio_total <= 0:
        return out
    for h in holdings:
        theme = _get_theme(h.ticker, InsightIn(ticker=h.ticker, action="HOLD"), h)
        if not theme:
            continue
        w = (h.market_value or 0.0) / portfolio_total * 100.0
        out[theme] = out.get(theme, 0.0) + w
    return out


# ── Scoring ──────────────────────────────────────────────────────────────────

@dataclass
class _Candidate:
    ins: InsightIn
    holding: Optional[Holding]
    category: str
    theme: Optional[str]
    current_weight: float
    target_weight: float
    conviction_level: str
    score: float


def _score_insight(
    ins: InsightIn,
    *,
    current_weight: float,
    target_weight: float,
    category: str,
    theme: Optional[str],
    theme_exposure: dict[str, float],
) -> float:
    """allocation_score =
        conviction_weight
        + confidence
        + underweight_bonus
        + diversification_bonus
        - concentration_penalty
        - volatility_penalty
    """
    conviction_w = CONVICTION_WEIGHTS[_infer_conviction_level(ins)]
    conf = max(0.0, min(1.0, ins.confidence or 0.0))

    # Underweight bonus — favor names below their target weight
    gap = target_weight - current_weight
    if target_weight > 0 and gap > 0:
        underweight_bonus = min(1.5, gap / 10.0)  # +1 per 10% gap, cap 1.5
    else:
        underweight_bonus = 0.0

    # Diversification bonus — tiny theme exposure gets a boost
    theme_pct = theme_exposure.get(theme, 0.0) if theme else 0.0
    if theme_pct < 10.0:
        diversification_bonus = 0.5
    elif theme_pct < 20.0:
        diversification_bonus = 0.2
    else:
        diversification_bonus = 0.0

    # Concentration penalty — penalize positions near the cap
    cap = _category_cap(category)
    if current_weight >= cap:
        concentration_penalty = 3.0   # effectively excluded later, but belt+braces
    elif current_weight >= cap * 0.8:
        concentration_penalty = 1.0
    elif current_weight >= cap * 0.6:
        concentration_penalty = 0.3
    else:
        concentration_penalty = 0.0

    # Volatility penalty — speculative categories are riskier
    cat = (category or "").lower()
    if cat in {"crypto", "ipo"}:
        volatility_penalty = 0.75
    else:
        volatility_penalty = 0.0

    return max(
        0.0,
        conviction_w + conf + underweight_bonus + diversification_bonus
        - concentration_penalty - volatility_penalty,
    )


# ── Eligibility ──────────────────────────────────────────────────────────────

def _eligibility_reason(
    ins: InsightIn,
    *,
    current_weight: float,
    target_weight: float,
    category: str,
) -> Optional[str]:
    """Return None if eligible, else a short human-readable reason to exclude."""
    action = (ins.action or "").upper()
    if action != "BUY":
        return f"action={action or 'UNKNOWN'} (only BUY deploys)"

    sv = (ins.schema_version or "").lower()
    if sv and sv not in ACCEPTED_SCHEMA_VERSIONS:
        return f"schema={sv} (requires compact_v1)"

    src = (ins.analysis_source or "").lower()
    if src and src not in ACCEPTED_SOURCES:
        return f"source={src} (requires live analysis)"

    if ins.used_fallback:
        return "fallback analysis — not deployable"

    if ins.stale:
        return "stale analysis"

    if ins.quota_blocked:
        return "quota blocked"

    if not (ins.why or ins.do or ins.thesis):
        return "missing compact reasoning"

    conf = ins.confidence or 0.0
    if conf < MIN_CONFIDENCE:
        return f"confidence {conf:.2f} < {MIN_CONFIDENCE:.2f}"

    cap = _category_cap(category)
    if current_weight >= cap:
        return f"already at cap ({current_weight:.1f}% ≥ {cap:.0f}%)"

    if target_weight > 0 and current_weight >= target_weight:
        return f"at/above target ({current_weight:.1f}% ≥ {target_weight:.1f}%)"

    return None


# ── Allocation ───────────────────────────────────────────────────────────────

def _dollars_by_score(
    candidates: list[_Candidate],
    cash: float,
    portfolio_total: float,
    theme_exposure_after: dict[str, float],
) -> tuple[list[tuple[_Candidate, float]], list[Exclusion]]:
    """Distribute *cash* across *candidates* by normalized score.

    Respects per-category caps and same-theme cap; rounds to $5, drift is
    redistributed across candidates without breaching caps. Drops any
    candidate below MIN_TICKER_ALLOCATION.
    """
    extra_exclusions: list[Exclusion] = []
    if cash <= 0 or not candidates:
        return [], extra_exclusions

    # Max additional $ we can add without breaching the single-name cap.
    def _name_cap_dollars(c: _Candidate) -> float:
        cap = _category_cap(c.category)
        after_total = max(1.0, portfolio_total + cash)
        current_value = c.holding.market_value if c.holding else 0.0
        max_value = cap / 100.0 * after_total
        return max(0.0, max_value - current_value)

    def _theme_cap_dollars(theme: Optional[str]) -> float:
        if not theme:
            return float("inf")
        after_total = max(1.0, portfolio_total + cash)
        current_theme_pct = theme_exposure_after.get(theme, 0.0)
        max_theme_value = MAX_SAME_THEME_WEIGHT / 100.0 * after_total
        current_theme_value = current_theme_pct / 100.0 * portfolio_total
        return max(0.0, max_theme_value - current_theme_value)

    total_score = sum(c.score for c in candidates)
    if total_score <= 0:
        return [], extra_exclusions

    # Build per-candidate caps (min of name-cap + remaining theme-cap).
    theme_remaining: dict[str, float] = {}
    caps: list[float] = []
    # Compute theme caps once, consume as we allocate in score order.
    for c in candidates:
        if c.theme is not None and c.theme not in theme_remaining:
            theme_remaining[c.theme] = _theme_cap_dollars(c.theme)

    raw_plus_cap: list[tuple[_Candidate, float, float]] = []
    for c in candidates:
        name_cap = _name_cap_dollars(c)
        theme_cap = theme_remaining.get(c.theme, float("inf")) if c.theme else float("inf")
        effective_cap = min(name_cap, theme_cap)
        raw = (c.score / total_score) * cash
        initial = min(raw, effective_cap)
        if c.theme is not None and theme_remaining.get(c.theme, float("inf")) != float("inf"):
            theme_remaining[c.theme] = max(0.0, theme_remaining[c.theme] - initial)
        raw_plus_cap.append((c, initial, effective_cap))
        caps.append(effective_cap)

    # Drop tiny allocations — fold into exclusions.
    kept: list[tuple[_Candidate, float, float]] = []
    for c, amt, cap in raw_plus_cap:
        if amt < MIN_TICKER_ALLOCATION:
            if amt > 0:
                extra_exclusions.append(Exclusion(
                    ticker=c.ins.ticker,
                    reason=f"allocation ${amt:.0f} below ${MIN_TICKER_ALLOCATION:.0f} minimum",
                ))
            continue
        kept.append((c, amt, cap))

    if not kept:
        return [], extra_exclusions

    # Round each to $5 step but not above its cap (floor to step if needed).
    def _round_not_above_cap(a: float, cap: float) -> float:
        stepped = _round_to_step(a)
        if stepped > cap:
            # Floor to $5 below cap.
            import math
            return max(0.0, math.floor(cap / ROUNDING_STEP) * ROUNDING_STEP)
        return stepped

    rounded: list[list] = [
        [c, _round_not_above_cap(a, cap), cap] for c, a, cap in kept
    ]

    # Drop any that fell below the $25 minimum after cap-aware rounding.
    filtered: list[list] = []
    for c, amt, cap in rounded:
        if amt < MIN_TICKER_ALLOCATION:
            if amt > 0:
                extra_exclusions.append(Exclusion(
                    ticker=c.ins.ticker,
                    reason=f"allocation ${amt:.0f} below ${MIN_TICKER_ALLOCATION:.0f} minimum",
                ))
            continue
        filtered.append([c, amt, cap])

    if not filtered:
        return [], extra_exclusions

    # Fix drift — add/remove $5 from candidates in score order, never
    # exceeding per-name caps or dipping below the $25 minimum.
    def _total(rs: list[list]) -> float:
        return sum(r[1] for r in rs)

    # Reduce — cash < total
    while _total(filtered) > cash + 0.01:
        progressed = False
        for r in reversed(filtered):     # shave from lowest-score first
            if r[1] - ROUNDING_STEP >= MIN_TICKER_ALLOCATION and _total(filtered) > cash:
                r[1] -= ROUNDING_STEP
                progressed = True
                if _total(filtered) <= cash + 0.01:
                    break
        if not progressed:
            break

    # Grow — cash > total (respect caps)
    guard = 0
    while _total(filtered) < cash - 0.01 and guard < 10_000:
        guard += 1
        progressed = False
        for r in filtered:               # top-up highest-score first
            cap_room = r[2] - r[1]
            if cap_room >= ROUNDING_STEP and _total(filtered) < cash:
                r[1] += ROUNDING_STEP
                progressed = True
                if _total(filtered) >= cash - 0.01:
                    break
        if not progressed:
            break

    return [(c, amt) for c, amt, _cap in filtered], extra_exclusions


# ── Public API ───────────────────────────────────────────────────────────────

def build_allocation_plan(
    *,
    cash_to_invest: float,
    holdings: list[Holding],
    insights: list[InsightIn],
    target_weights: Optional[dict[str, float]] = None,
) -> AllocationPlan:
    """Build the ranked allocation plan for the Deploy tab."""
    cash = max(0.0, float(cash_to_invest or 0.0))
    targets = {k.upper(): float(v) for k, v in (target_weights or {}).items()}

    portfolio_total = _portfolio_total(holdings)
    holdings_by_ticker = {h.ticker.upper(): h for h in holdings if h.ticker}
    theme_expo = _theme_exposure(holdings, portfolio_total)

    exclusions: list[Exclusion] = []
    candidates: list[_Candidate] = []
    trims: list[TrimCandidate] = []

    for ins in insights:
        tkr = (ins.ticker or "").upper()
        if not tkr:
            continue
        holding = holdings_by_ticker.get(tkr)
        category = _get_category(tkr, ins, holding)
        theme = _get_theme(tkr, ins, holding)
        cw = _current_weight(holding.market_value if holding else 0.0, portfolio_total)
        tw = targets.get(tkr, 0.0)

        action = (ins.action or "").upper()
        if action in {"TRIM", "SELL"}:
            trims.append(TrimCandidate(
                ticker=tkr,
                action=action,
                current_weight=cw,
                reason=(ins.do or ins.risk or ins.thesis or
                        f"{action} signal from latest analysis"),
            ))
            continue

        reason_exclude = _eligibility_reason(ins, current_weight=cw,
                                             target_weight=tw, category=category)
        if reason_exclude:
            exclusions.append(Exclusion(ticker=tkr, reason=reason_exclude))
            continue

        score = _score_insight(ins, current_weight=cw, target_weight=tw,
                               category=category, theme=theme,
                               theme_exposure=theme_expo)
        if score <= 0:
            exclusions.append(Exclusion(ticker=tkr, reason="zero allocation score"))
            continue

        candidates.append(_Candidate(
            ins=ins, holding=holding, category=category, theme=theme,
            current_weight=cw, target_weight=tw,
            conviction_level=_infer_conviction_level(ins), score=score,
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)

    # Safety check — too few high-quality BUYs
    warning: Optional[str] = None
    if len(candidates) < 2:
        warning = (
            "Not enough fresh high-confidence analysis to deploy confidently."
        )

    # Prioritize top 3–5 ideas
    top_n = min(TOP_N_IDEAS, max(MIN_TOP_N, len(candidates)))
    selected = candidates[:top_n]
    for dropped in candidates[top_n:]:
        exclusions.append(Exclusion(
            ticker=dropped.ins.ticker,
            reason=(f"ranked #{candidates.index(dropped) + 1} — outside top "
                    f"{top_n} ideas"),
        ))

    dollars, extra_exclusions = _dollars_by_score(
        selected, cash, portfolio_total, theme_expo,
    )
    exclusions.extend(extra_exclusions)

    # Build the ranked allocation list
    allocations: list[AllocationItem] = []
    after_total = portfolio_total + cash if cash > 0 else portfolio_total
    for c, amount in dollars:
        current_value = c.holding.market_value if c.holding else 0.0
        after_value = current_value + amount
        after_weight = (after_value / after_total * 100.0) if after_total > 0 else 0.0
        target_weight_of_cash = (amount / cash * 100.0) if cash > 0 else 0.0
        reason = (
            c.ins.do or c.ins.why or c.ins.thesis or
            f"{c.conviction_level} conviction — score {c.score:.2f}"
        )
        allocations.append(AllocationItem(
            ticker=c.ins.ticker.upper(),
            action="BUY",
            amount=float(amount),
            current_weight=round(c.current_weight, 2),
            after_weight=round(after_weight, 2),
            target_weight=round(target_weight_of_cash, 2),
            conviction_level=c.conviction_level,
            conviction_score=float(c.ins.conviction_score or 0.0),
            confidence=float(c.ins.confidence or 0.0),
            score=round(c.score, 3),
            reason=reason[:200],
            why=c.ins.why,
            risk=c.ins.risk,
            do=c.ins.do,
            alt_view=c.ins.alt_view,
            category=c.category,
        ))

    total_deployed = sum(a.amount for a in allocations)
    fully_allocated = cash > 0 and abs(total_deployed - cash) < 0.5

    # Portfolio-level explanation
    if allocations:
        deployed_names = ", ".join(a.ticker for a in allocations)
        strategy = (
            f"Deploying ${cash:,.0f} across top {len(allocations)} "
            f"high-conviction ideas ({deployed_names})"
        )
        explanation = (
            f"This plan concentrates capital in the {len(allocations)} highest-"
            f"scoring BUY ideas from the latest compact_v1 analysis. Scoring "
            f"blends conviction, confidence, underweight gaps, and "
            f"diversification while penalizing concentration and volatility. "
            f"Per-category caps (single stock {MAX_SINGLE_STOCK_WEIGHT:.0f}%, "
            f"ETF {MAX_ETF_WEIGHT:.0f}%, speculative {MAX_SPECULATIVE_WEIGHT:.0f}%) "
            f"and {MAX_SAME_THEME_WEIGHT:.0f}% same-theme exposure is monitored and flagged."
        )
    else:
        strategy = "No deployment — preserve cash"
        explanation = (
            "No BUY cleared the eligibility gates (live compact_v1 analysis, "
            "confidence ≥ 0.65, below cap). Holding cash until a fresh agent "
            "run produces higher-quality signals."
        )

    return AllocationPlan(
        cash_to_invest=cash,
        total_deployed=round(total_deployed, 2),
        fully_allocated=fully_allocated,
        allocations=allocations,
        exclusions=exclusions,
        trims=trims,
        portfolio_explanation=explanation,
        warning=warning,
        strategy=strategy,
    )
