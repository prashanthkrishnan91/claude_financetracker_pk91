"""Phase 4 — portfolio synthesis layer.

Single LLM call that produces cross-asset insights from the Phase 3
per-ticker verdicts + portfolio composition + macro snapshot.

This is the ONLY portfolio-level LLM call in the pipeline. The Phase 3
analyst owns per-ticker reasoning; this stage owns everything that
crosses ticker boundaries — concentration risk, sector overexposure,
portfolio bias, and rebalancing hints.

Contract invariants:
  * Strict output schema: ``portfolio_bias``, ``key_themes``,
    ``risk_concentrations``, ``overexposure_flags``,
    ``rebalancing_suggestions``. Every field is always populated.
  * NEVER returns ``{}`` — a failed LLM call yields a deterministic
    synthesis derived from the verdicts themselves so the UI still
    sees structured output.
  * Degrades gracefully when individual tickers carry
    ``INSUFFICIENT_DATA`` verdicts — they're surfaced in the
    ``data_quality`` block so the LLM knows which signals to trust.
  * One retry on malformed JSON; second failure → deterministic
    fallback (no empty dicts).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .feature_engine import FeatureSet
from .market_snapshot import MarketSnapshot
from .per_ticker_analyst import AnalystVerdict

logger = logging.getLogger(__name__)


ALLOWED_BIASES = {"bullish", "neutral", "defensive"}
UNKNOWN_SECTOR_KEYS = {"unknown", "n/a", "none", ""}
HIGH_QUALITY_THRESHOLD = 0.75

TICKER_SECTOR_FALLBACK: dict[str, str] = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "AMD": "Technology",
    "CRM": "Technology",
    "SNOW": "Technology",
    "GOOGL": "Communication Services",
    "META": "Communication Services",
    "NFLX": "Communication Services",
    "RDDT": "Communication Services",
    "COST": "Consumer",
    "WMT": "Consumer",
    "CAVA": "Consumer",
    "QCOM": "Semiconductors",
    "TSM": "Semiconductors",
    "BRK-B": "Financials",
    "ALK": "Industrials/Autos",
    "RIVN": "Industrials/Autos",
    "BMWYY": "Industrials/Autos",
    "VOO": "Broad Market ETF",
    "VTI": "Broad Market ETF",
    "SPY": "Broad Market ETF",
    "QQQ": "Growth ETF",
    "SCHD": "Dividend ETF",
    "VYM": "Dividend ETF",
    "BND": "Bonds",
    "VXUS": "International ETF",
    "VEA": "International ETF",
    "VWO": "International ETF",
    "GLD": "Gold/Commodity",
    "BTC": "Crypto",
    "XRP": "Crypto",
    "KLAR": "Recent IPO/Speculative",
    "BLSH": "Recent IPO/Speculative",
    "STUB": "Recent IPO/Speculative",
}


# ── Result shape ───────────────────────────────────────────────────────────


@dataclass
class PortfolioSynthesis:
    """Strictly-validated portfolio-synthesis output.

    ``used_fallback`` flags whether the deterministic fallback was
    invoked — the synthesis is still usable either way, but downstream
    UX can badge a "deterministic synthesis — fresh LLM context
    unavailable" hint when True.
    """

    portfolio_bias: str = "neutral"
    key_themes: list[str] = field(default_factory=list)
    risk_concentrations: list[str] = field(default_factory=list)
    overexposure_flags: list[str] = field(default_factory=list)
    rebalancing_suggestions: list[str] = field(default_factory=list)
    summary: str = ""
    aggregate_quality: Optional[str] = None
    quality_breakdown: Optional[dict[str, int]] = None
    used_fallback: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_required_signal(self) -> bool:
        """True when the synthesis carries the Phase-4-mandated signal.

        Spec: ≥ 2 cross-ticker themes and ≥ 1 risk concentration.
        """
        return len(self.key_themes) >= 2 and len(self.risk_concentrations) >= 1


# ── System prompt ──────────────────────────────────────────────────────────


SYNTHESIS_SYSTEM_PROMPT = """You are the portfolio-level synthesist for a long-term retail investor.

INPUTS — you receive a JSON object with these keys:
  - "portfolio_composition": total_value, sector_exposure (percent by sector),
    category_weights (percent by category), top_positions (largest 5 by weight).
  - "per_ticker_verdicts": list of {ticker, action, conviction, confidence,
    key_drivers, risks, sector, trend_regime} from the Phase 3 analyst.
  - "action_summary": counts of BUY / HOLD / REDUCE / INSUFFICIENT_DATA.
  - "macro": optional {summary, regime, fallback}.
  - "data_quality": {avg_quality, insufficient_data_tickers}.

OBJECTIVE — you produce CROSS-TICKER insights only. Do NOT rehash the
per-ticker reasoning; that's already in "per_ticker_verdicts". Focus
on patterns that span multiple tickers: sector rotation, correlated
risks, concentration in a single theme, rebalancing opportunities
between positions.

RULES (hard requirements):
  1. Base every theme / risk / suggestion on ≥ 2 tickers or a
     portfolio-level metric (sector %, category %, top-position weight).
     If you can only name one ticker, it belongs in the per-ticker
     layer, not here.
  2. Ignore tickers with action="INSUFFICIENT_DATA" when inferring
     themes — you can mention them only in a risk_concentration if
     they're a large weight.
  3. ``portfolio_bias`` ∈ {"bullish", "neutral", "defensive"}.
  4. ``key_themes``: 2-5 short phrases. Each cites the supporting
     tickers or the portfolio metric it rests on.
  5. ``risk_concentrations``: 1-4 items (sector / correlation /
     concentration). Name the sector or theme.
  6. ``overexposure_flags``: 0-3 items. Use only when a single
     sector / category / ticker exceeds a prudent weight (> 30%).
  7. ``rebalancing_suggestions``: 0-5 items. Cross-ticker moves like
     "trim TSLA into NVDA", "reduce Auto exposure via REDUCE verdicts".
  8. ``summary``: 2-3 sentences. Portfolio-level only.

OUTPUT CONTRACT (strict):
  - Return exactly one JSON object.
  - No markdown fences.
  - No prose before or after the JSON object.
  - Include all keys; if uncertain, use null/[] defaults.

OUTPUT — return ONLY this JSON:
{
  "portfolio_bias": "bullish" | "neutral" | "defensive",
  "key_themes": ["theme 1", "theme 2"],
  "risk_concentrations": ["risk 1"],
  "overexposure_flags": [],
  "rebalancing_suggestions": ["suggestion 1"],
  "summary": "2-3 sentence portfolio-level narrative."
}
"""


# ── Input builder ──────────────────────────────────────────────────────────


def build_synthesis_inputs(
    *,
    verdicts: dict[str, AnalystVerdict],
    snapshots: dict[str, MarketSnapshot],
    features: dict[str, FeatureSet],
    positions: list[dict[str, Any]],
    macro: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the compact JSON payload fed to the synthesis LLM.

    Intentionally excludes raw price arrays, full fundamentals, and
    news headlines — the synthesist works on top of the per-ticker
    verdicts the Phase 3 analyst already distilled.
    """
    # Position weights from the orchestrator-built portfolio.
    total_value = 0.0
    position_map: dict[str, dict[str, Any]] = {}
    for p in positions:
        ticker = (p.get("ticker") or "").upper()
        if not ticker:
            continue
        shares = _to_float(p.get("shares"))
        snap = snapshots.get(ticker)
        price = (snap.price if snap else None) or _to_float(p.get("avg_cost"))
        mv = shares * (price or 0.0)
        total_value += mv
        position_map[ticker] = {
            "ticker": ticker,
            "market_value": round(mv, 2),
            "category": p.get("category") or "",
            "sector": p.get("sector") or "",
            "asset_class": p.get("asset_class") or "",
            "avg_cost": _to_float(p.get("avg_cost")),
            "price": price,
        }

    # Sector exposure + category weights in percent of total.
    sector_exposure: dict[str, float] = {}
    category_weights: dict[str, float] = {}
    weight_rows: list[tuple[str, float]] = []
    for ticker, p in position_map.items():
        snap = snapshots.get(ticker)
        sector = _best_bucket(ticker=ticker, snapshot=snap, position=p, prefer_sector=True)
        mv = p["market_value"]
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + mv
        category = _best_bucket(ticker=ticker, snapshot=snap, position=p, prefer_sector=False)
        category_weights[category] = category_weights.get(category, 0.0) + mv
        weight_rows.append((ticker, mv))
    sector_exposure = _percents(sector_exposure, total_value)
    category_weights = _percents(category_weights, total_value)

    # Top 5 positions by weight.
    weight_rows.sort(key=lambda x: x[1], reverse=True)
    top_positions: list[dict[str, Any]] = []
    for ticker, mv in weight_rows[:5]:
        snap = snapshots.get(ticker)
        fs = features.get(ticker)
        v = verdicts.get(ticker)
        top_positions.append({
            "ticker": ticker,
            "weight_pct": round((mv / total_value * 100) if total_value > 0 else 0, 1),
            "sector": _best_bucket(ticker=ticker, snapshot=snap, position=position_map[ticker], prefer_sector=True),
            "category": _best_bucket(ticker=ticker, snapshot=snap, position=position_map[ticker], prefer_sector=False),
            "action": v.action if v else "INSUFFICIENT_DATA",
            "trend_regime": fs.trend_regime if fs else "range",
        })

    # Per-ticker verdict rows — compact, cross-ticker relevant fields only.
    verdict_rows: list[dict[str, Any]] = []
    action_summary: dict[str, int] = {"BUY": 0, "HOLD": 0, "REDUCE": 0, "INSUFFICIENT_DATA": 0}
    for ticker, v in verdicts.items():
        action_summary[v.action] = action_summary.get(v.action, 0) + 1
        snap = snapshots.get(ticker)
        fs = features.get(ticker)
        verdict_rows.append({
            "ticker": ticker,
            "action": v.action,
            "conviction": round(v.conviction, 2),
            "confidence": round(v.confidence, 2),
            "key_drivers": list(v.key_drivers),
            "risks": list(v.risks),
            "sector": _best_bucket(ticker=ticker, snapshot=snap, position=position_map.get(ticker), prefer_sector=True),
            "category": _best_bucket(ticker=ticker, snapshot=snap, position=position_map.get(ticker), prefer_sector=False),
            "trend_regime": fs.trend_regime if fs else "range",
        })

    # Data-quality roll-up.
    qualities = [s.data_quality_score for s in snapshots.values()]
    avg_quality = round(sum(qualities) / len(qualities), 3) if qualities else 0.0
    insufficient_tickers = sorted(
        t for t, v in verdicts.items() if v.action == "INSUFFICIENT_DATA"
    )
    quality = _compute_portfolio_quality(verdicts=verdicts, snapshots=snapshots, total_cards=len(position_map))

    macro_block = macro or {}
    if not isinstance(macro_block, dict):
        macro_block = {}

    return {
        "portfolio_composition": {
            "total_value": round(total_value, 2),
            "sector_exposure": sector_exposure,
            "category_weights": category_weights,
            "top_positions": top_positions,
            "ticker_count": len(position_map),
        },
        "per_ticker_verdicts": verdict_rows,
        "action_summary": action_summary,
        "macro": {
            "summary": macro_block.get("summary") or "",
            "regime": macro_block.get("regime") or "unknown",
            "fallback": bool(macro_block.get("fallback", True)),
        },
        "data_quality": {
            "avg_quality": avg_quality,
            "insufficient_data_tickers": insufficient_tickers,
            "total_cards": quality["total_cards"],
            "enriched_cards": quality["enriched"],
            "high_quality_cards": quality["high_quality"],
            "fallback_cards": quality["fallback"],
            "aggregate_quality": quality["aggregate_quality"],
        },
    }


# ── Validator ──────────────────────────────────────────────────────────────




def _normalize_synthesis_schema(raw: Any) -> Optional[dict[str, Any]]:
    """Normalize common alias keys + defaults for synthesis schema."""
    if not isinstance(raw, dict):
        return None

    alias_map = {
        "bias": "portfolio_bias",
        "portfolio_view": "portfolio_bias",
        "themes": "key_themes",
        "top_themes": "key_themes",
        "risks": "risk_concentrations",
        "concentration_risks": "risk_concentrations",
        "overexposures": "overexposure_flags",
        "overweight_flags": "overexposure_flags",
        "rebalance_suggestions": "rebalancing_suggestions",
        "rebalance_actions": "rebalancing_suggestions",
        "narrative": "summary",
        "portfolio_summary": "summary",
    }

    normalized = dict(raw)
    for alias, canonical in alias_map.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]

    normalized.setdefault("portfolio_bias", "neutral")
    normalized.setdefault("key_themes", [])
    normalized.setdefault("risk_concentrations", [])
    normalized.setdefault("overexposure_flags", [])
    normalized.setdefault("rebalancing_suggestions", [])
    normalized.setdefault("summary", "")
    return normalized


def validate_synthesis(raw: Any) -> Optional[PortfolioSynthesis]:
    """Return a :class:`PortfolioSynthesis` when ``raw`` matches the schema.

    Returns ``None`` on any violation; the caller retries once and
    falls back to deterministic synthesis on the second failure.
    """
    normalized = _normalize_synthesis_schema(raw)
    if normalized is None:
        return None

    bias = str(normalized.get("portfolio_bias") or "").strip().lower()
    if bias not in ALLOWED_BIASES:
        bias = "neutral"

    key_themes = _coerce_string_list(normalized.get("key_themes"), max_items=6)
    risk_concentrations = _coerce_string_list(
        normalized.get("risk_concentrations"), max_items=5,
    )
    overexposure_flags = _coerce_string_list(
        normalized.get("overexposure_flags"), max_items=4,
    )
    rebalancing_suggestions = _coerce_string_list(
        normalized.get("rebalancing_suggestions"), max_items=6,
    )
    summary = str(normalized.get("summary") or "").strip()[:800]

    return PortfolioSynthesis(
        portfolio_bias=bias,
        key_themes=key_themes,
        risk_concentrations=risk_concentrations,
        overexposure_flags=overexposure_flags,
        rebalancing_suggestions=rebalancing_suggestions,
        summary=summary,
    )


# ── Deterministic fallback ─────────────────────────────────────────────────


def deterministic_synthesis(
    payload: dict[str, Any],
    *,
    reason: str = "llm_failed",
) -> PortfolioSynthesis:
    """Produce a spec-compliant synthesis from the payload without an LLM.

    Drives ``portfolio_bias`` from the BUY / REDUCE count balance, pulls
    ``risk_concentrations`` from the top sector if > 30%, and threads
    ``rebalancing_suggestions`` from any REDUCE verdicts. Guarantees the
    acceptance-gate minimums (≥ 2 themes, ≥ 1 risk concentration) so the
    UI never sees a degenerate synthesis.
    """
    composition = payload.get("portfolio_composition") or {}
    sector_exposure: dict[str, float] = composition.get("sector_exposure") or {}
    top_positions: list[dict[str, Any]] = composition.get("top_positions") or []
    action_summary: dict[str, int] = payload.get("action_summary") or {}
    verdicts: list[dict[str, Any]] = payload.get("per_ticker_verdicts") or []

    buy = int(action_summary.get("BUY", 0))
    reduce = int(action_summary.get("REDUCE", 0))
    total_active = buy + reduce + int(action_summary.get("HOLD", 0))
    if total_active <= 0:
        bias = "neutral"
    elif buy >= reduce * 2 and buy > 0:
        bias = "bullish"
    elif reduce > buy:
        bias = "defensive"
    else:
        bias = "neutral"

    # Sector / concentration signals.
    top_sector, top_sector_pct = ("", 0.0)
    if sector_exposure:
        top_sector, top_sector_pct = max(
            sector_exposure.items(), key=lambda kv: kv[1] or 0.0
        )
    known_sector = bool(top_sector and str(top_sector).strip().lower() not in UNKNOWN_SECTOR_KEYS)
    category_weights: dict[str, float] = composition.get("category_weights") or {}
    top_category, top_category_pct = ("", 0.0)
    if category_weights:
        top_category, top_category_pct = max(
            category_weights.items(), key=lambda kv: kv[1] or 0.0
        )
    risk_concentrations: list[str] = []
    if known_sector and top_sector_pct >= 30:
        risk_concentrations.append(
            f"{top_sector} sector concentration at {top_sector_pct:.0f}% of book"
        )
    elif top_category and top_category_pct >= 35:
        risk_concentrations.append(
            f"{top_category} category concentration at {top_category_pct:.0f}% of book"
        )
    # Correlated-risk fallback: if top 2 positions share a sector, flag it.
    if len(top_positions) >= 2 and top_positions[0].get("sector") and \
       top_positions[0].get("sector") == top_positions[1].get("sector") and \
       str(top_positions[0].get("sector")).strip().lower() not in UNKNOWN_SECTOR_KEYS:
        risk_concentrations.append(
            f"Top-2 positions both in {top_positions[0]['sector']} — "
            "correlated single-sector exposure"
        )
    if not risk_concentrations:
        # Last-resort signal: flag the largest position as a
        # concentration risk so we never emit an empty list.
        if top_positions:
            tp = top_positions[0]
            risk_concentrations.append(
                f"{tp.get('ticker', 'top position')} at "
                f"{tp.get('weight_pct', 0):.0f}% of portfolio"
            )
        else:
            risk_concentrations.append("Portfolio breadth unclear — data incomplete")

    # Themes — at least 2.
    key_themes: list[str] = []
    if buy >= 2:
        buy_sectors = sorted({
            (v.get("sector") or "Unknown")
            for v in verdicts if v.get("action") == "BUY"
        } - {"Unknown"})[:3]
        if buy_sectors:
            key_themes.append(
                f"Accumulation tilt across {', '.join(buy_sectors)} "
                f"({buy} BUY signals)"
            )
        else:
            key_themes.append(f"{buy} BUY verdicts across the book")
    if reduce >= 2:
        key_themes.append(
            f"Risk-off rotation out of {reduce} positions flagged for REDUCE"
        )
    if known_sector and top_sector_pct >= 20:
        key_themes.append(
            f"{top_sector}-heavy book ({top_sector_pct:.0f}% of value)"
        )
    elif top_category and top_category_pct >= 20:
        key_themes.append(
            f"{top_category}-tilted allocation ({top_category_pct:.0f}% of value)"
        )
    insufficient = len(payload.get("data_quality", {}).get("insufficient_data_tickers") or [])
    if insufficient >= 2:
        key_themes.append(
            f"{insufficient} tickers returned INSUFFICIENT_DATA — refresh "
            "upstream providers before increasing exposure"
        )
    if len(key_themes) < 2:
        # Always emit at least 2 themes — cross-reference action mix.
        key_themes.append(
            f"Mixed signals: {buy} BUY, {action_summary.get('HOLD', 0)} HOLD, "
            f"{reduce} REDUCE"
        )
    if len(key_themes) < 2:
        key_themes.append("Deterministic synthesis — LLM synthesis unavailable")

    # Overexposure flags.
    overexposure_flags: list[str] = []
    for sector, pct in sector_exposure.items():
        sector_norm = str(sector or "").strip().lower()
        if sector_norm in UNKNOWN_SECTOR_KEYS:
            continue
        if pct > 35:
            overexposure_flags.append(
                f"{sector} exposure at {pct:.0f}% exceeds 35% ceiling"
            )
        if len(overexposure_flags) >= 3:
            break

    # Rebalancing suggestions.
    rebalancing_suggestions: list[str] = []
    if reduce and buy:
        rebalancing_suggestions.append(
            f"Reallocate proceeds from {reduce} REDUCE verdicts into the "
            f"{buy} BUY candidates, preserving sector diversification."
        )
    for flag in overexposure_flags[:2]:
        rebalancing_suggestions.append(f"Trim to reduce {flag}.")
    if not rebalancing_suggestions:
        rebalancing_suggestions.append(
            "Hold current allocation; no high-conviction cross-ticker rotation."
        )

    unknown_pct = float(sector_exposure.get("Unknown", 0.0) or 0.0)
    known_sectors = sorted(
        [(k, float(v or 0.0)) for k, v in sector_exposure.items() if str(k or "").strip().lower() not in UNKNOWN_SECTOR_KEYS],
        key=lambda kv: kv[1],
        reverse=True,
    )
    top_known = ", ".join([f"{k} {v:.0f}%" for k, v in known_sectors[:2]]) if known_sectors else ""
    strongest_buy_groups = sorted({(v.get("sector") or "Unknown") for v in verdicts if v.get("action") == "BUY"} - {"Unknown"})
    strongest_reduce_groups = sorted({(v.get("sector") or "Unknown") for v in verdicts if v.get("action") == "REDUCE"} - {"Unknown"})
    style = "core ETF/mega-cap tech" if any(s in {"Broad Market ETF", "Technology"} for s, _ in known_sectors[:3]) else (
        "dividend tilt" if any(s == "Dividend ETF" for s, _ in known_sectors[:3]) else (
            "speculative/crypto tilt" if any(s in {"Crypto", "Recent IPO/Speculative"} for s, _ in known_sectors[:3]) else "mixed allocation"
        )
    )
    sector_sentence = (
        f"Top exposures: {top_known}."
        if top_known
        else ("Sector coverage partial." if top_category else "Sector data unavailable.")
    )
    if top_known and unknown_pct > 0:
        sector_sentence += f" Sector coverage partial ({unknown_pct:.0f}% unmapped)."
    concentration_note = ""
    if known_sector and top_sector_pct >= 30:
        concentration_note = f" Concentration risk remains in {top_sector} ({top_sector_pct:.0f}%)."
    elif top_category and top_category_pct >= 35:
        concentration_note = f" Concentration risk remains in {top_category} ({top_category_pct:.0f}%)."

    buy_group_txt = ", ".join(strongest_buy_groups[:2]) if strongest_buy_groups else "selective names"
    reduce_group_txt = ", ".join(strongest_reduce_groups[:2]) if strongest_reduce_groups else "isolated positions"
    summary = (
        f"Action mix: {buy} BUY / {action_summary.get('HOLD', 0)} HOLD / {reduce} REDUCE, with a {bias} posture. "
        f"{sector_sentence}{concentration_note} "
        f"Strongest BUY groups: {buy_group_txt}; strongest REDUCE groups: {reduce_group_txt}. "
        f"Portfolio profile is mostly {style}."
    )

    return PortfolioSynthesis(
        portfolio_bias=bias,
        key_themes=key_themes[:6],
        risk_concentrations=risk_concentrations[:5],
        overexposure_flags=overexposure_flags[:4],
        rebalancing_suggestions=rebalancing_suggestions[:6],
        summary=summary[:800],
        aggregate_quality=str(payload.get("data_quality", {}).get("aggregate_quality") or "").upper() or None,
        quality_breakdown={
            "total_cards": int(payload.get("data_quality", {}).get("total_cards") or 0),
            "enriched": int(payload.get("data_quality", {}).get("enriched_cards") or 0),
            "high_quality": int(payload.get("data_quality", {}).get("high_quality_cards") or 0),
            "fallback": int(payload.get("data_quality", {}).get("fallback_cards") or 0),
        },
        used_fallback=True,
        error=reason,
    )


def _best_bucket(
    *,
    ticker: str,
    snapshot: Optional[MarketSnapshot],
    position: Optional[dict[str, Any]],
    prefer_sector: bool,
) -> str:
    pos = position or {}
    candidates = (
        [snapshot.sector if snapshot else "", pos.get("sector"), pos.get("category"), pos.get("asset_class"), snapshot.category if snapshot else ""]
        if prefer_sector
        else [snapshot.category if snapshot else "", pos.get("category"), pos.get("asset_class"), snapshot.sector if snapshot else "", pos.get("sector")]
    )
    for value in candidates:
        label = str(value or "").strip()
        if label and label.lower() not in UNKNOWN_SECTOR_KEYS:
            return label
    return TICKER_SECTOR_FALLBACK.get(ticker, "Unknown")


def _compute_portfolio_quality(
    *,
    verdicts: dict[str, AnalystVerdict],
    snapshots: dict[str, MarketSnapshot],
    total_cards: int,
) -> dict[str, Any]:
    universe = set(verdicts.keys()) | set(snapshots.keys())
    if total_cards > 0:
        universe_count = max(total_cards, len(universe))
    else:
        universe_count = len(universe)
    enriched = 0
    high_quality = 0
    fallback = 0
    for ticker in universe:
        verdict = verdicts.get(ticker)
        snap = snapshots.get(ticker)
        is_fallback = bool(
            verdict is None
            or verdict.used_fallback
            or verdict.action == "INSUFFICIENT_DATA"
            or (verdict.analysis_source and verdict.analysis_source != "live_llm")
        )
        if is_fallback:
            fallback += 1
        else:
            enriched += 1
        score = float(snap.data_quality_score) if snap and snap.data_quality_score is not None else 0.0
        confidence = float(verdict.confidence) if verdict and verdict.confidence is not None else 0.0
        if not is_fallback and (score >= HIGH_QUALITY_THRESHOLD or confidence >= 0.65):
            high_quality += 1
    ratio_enriched = (enriched / universe_count) if universe_count else 0.0
    ratio_high = (high_quality / universe_count) if universe_count else 0.0
    ratio_fallback = (fallback / universe_count) if universe_count else 1.0
    if universe_count > 0 and ratio_enriched >= 0.8 and ratio_high >= 0.8 and ratio_fallback <= 0.2:
        aggregate_quality = "HIGH"
    elif universe_count > 0 and ratio_enriched >= 0.5 and ratio_high >= 0.5:
        aggregate_quality = "MEDIUM"
    else:
        aggregate_quality = "LOW"
    logger.info(
        "portfolio_synthesis.quality total_cards=%d enriched=%d high_quality=%d fallback=%d aggregate_quality=%s",
        universe_count,
        enriched,
        high_quality,
        fallback,
        aggregate_quality,
    )
    return {
        "total_cards": universe_count,
        "enriched": enriched,
        "high_quality": high_quality,
        "fallback": fallback,
        "aggregate_quality": aggregate_quality,
    }


# ── Single LLM synthesis call ──────────────────────────────────────────────




def _augment_with_deterministic_minimums(
    synthesis: PortfolioSynthesis,
    payload: dict[str, Any],
) -> PortfolioSynthesis:
    """Fill missing cross-ticker signal from deterministic synthesis only."""
    det = deterministic_synthesis(payload, reason="llm_partial_schema")
    changed = False

    if len(synthesis.key_themes) < 2:
        synthesis.key_themes = (synthesis.key_themes + det.key_themes)[:6]
        changed = True
    if len(synthesis.risk_concentrations) < 1:
        synthesis.risk_concentrations = (
            synthesis.risk_concentrations + det.risk_concentrations
        )[:5]
        changed = True
    if not synthesis.rebalancing_suggestions:
        synthesis.rebalancing_suggestions = det.rebalancing_suggestions[:6]
        changed = True
    if not synthesis.summary:
        synthesis.summary = det.summary
        changed = True

    synthesis.used_fallback = False
    if changed and not synthesis.error:
        synthesis.error = "llm_partial_schema_normalized"
    return _attach_quality_metadata(synthesis, payload)


def _attach_quality_metadata(
    synthesis: PortfolioSynthesis,
    payload: dict[str, Any],
) -> PortfolioSynthesis:
    dq = payload.get("data_quality") or {}
    synthesis.aggregate_quality = str(dq.get("aggregate_quality") or "").upper() or None
    synthesis.quality_breakdown = {
        "total_cards": int(dq.get("total_cards") or 0),
        "enriched": int(dq.get("enriched_cards") or 0),
        "high_quality": int(dq.get("high_quality_cards") or 0),
        "fallback": int(dq.get("fallback_cards") or 0),
    }
    return synthesis


async def synthesize_portfolio(
    *,
    verdicts: dict[str, AnalystVerdict],
    snapshots: dict[str, MarketSnapshot],
    features: dict[str, FeatureSet],
    positions: list[dict[str, Any]],
    macro: Optional[dict[str, Any]] = None,
    llm=None,  # duck-typed LLMClient with ``ask_json``
    max_tokens: int = 1000,
) -> PortfolioSynthesis:
    """Single LLM call producing the :class:`PortfolioSynthesis`.

    Never raises. A missing LLM, malformed response, or exhausted retries
    falls back to :func:`deterministic_synthesis` so the UI always sees a
    populated synthesis object.
    """
    payload = build_synthesis_inputs(
        verdicts=verdicts,
        snapshots=snapshots,
        features=features,
        positions=positions,
        macro=macro,
    )

    if llm is None or not getattr(llm, "api_key", None):
        logger.warning("synthesis skipped — no LLM client available")
        return deterministic_synthesis(payload, reason="no_llm_client")

    user_msg = json.dumps(payload, default=str)

    async def _call_once() -> Any:
        return await llm.ask_json(
            system=SYNTHESIS_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=max_tokens,
            normalizer=lambda obj: _normalize_synthesis_schema(obj) or obj,
        )

    # Attempt 1
    try:
        raw = await _call_once()
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesis attempt 1 raised: %s", exc)
        raw = {}

    synthesis = validate_synthesis(raw)
    if synthesis is not None:
        synthesis = _augment_with_deterministic_minimums(synthesis, payload)
        if synthesis.has_required_signal():
            return synthesis

    logger.info(
        "synthesis retry — attempt 1 invalid or under-specified "
        "(themes=%d risks=%d)",
        len(synthesis.key_themes) if synthesis else 0,
        len(synthesis.risk_concentrations) if synthesis else 0,
    )

    # Attempt 2
    try:
        raw = await _call_once()
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesis attempt 2 raised: %s", exc)
        raw = {}

    synthesis2 = validate_synthesis(raw)
    if synthesis2 is not None:
        synthesis2 = _augment_with_deterministic_minimums(synthesis2, payload)
        if synthesis2.has_required_signal():
            return synthesis2

    # Fall back to deterministic synthesis. Both attempts either failed
    # validation or came back under-specified; deterministic_synthesis
    # guarantees the Phase 4 acceptance-gate minimums.
    logger.warning(
        "synthesis exhausted retries — deterministic fallback (attempt1_ok=%s "
        "attempt2_ok=%s)",
        synthesis is not None, synthesis2 is not None,
    )
    return deterministic_synthesis(payload, reason="schema_or_signal_missing")


# ── Small helpers ──────────────────────────────────────────────────────────


def _percents(bucket: dict[str, float], total: float) -> dict[str, float]:
    if total <= 0:
        return {}
    return {
        k: round(v / total * 100, 1)
        for k, v in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)
    }


def _coerce_string_list(v: Any, *, max_items: int) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s:
            out.append(s[:240])
        if len(out) >= max_items:
            break
    return out


def _to_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
