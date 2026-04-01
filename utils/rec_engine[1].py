"""
Recommendation Engine v4
Factors in: price vs target · LT/ST tax status · DRIP yield · declining thesis
            bear proximity · momentum · income ETF special rules
"""
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class RecResult:
    action:     str
    detail:     str
    color:      str      # CSS variable key: green/red/gold/blue/purple/orange/gray
    urgency:    int      # 0=low … 4=critical
    tax_note:   str = ""
    drip_note:  str = ""


# ── DRIP yield estimates (annual %, approximate 2026 values) ─────────────────
DRIP_YIELD = {
    'VYM': 2.8, 'SCHD': 3.5, 'BND': 3.2, 'VWO': 2.1, 'VXUS': 1.8,
    'VEA': 1.9, 'XLE': 3.0, 'VTV': 2.2, 'VUG': 0.3, 'SPY': 1.2,
    'VGT': 0.4, 'VHT': 1.4, 'VIS': 1.3, 'VTI': 1.3, 'VOO': 1.2,
    'QQQ': 0.5, 'GLD': 0.0, 'VHT': 1.4, 'QCOM': 2.1, 'AAPL': 0.5,
    'MSFT': 0.7, 'META': 0.4, 'COST': 0.7, 'WMT': 0.9, 'CRM': 0.0,
    'GOOGL': 0.3, 'TSM': 1.4, 'AMD': 0.0, 'NVDA': 0.1, 'NFLX': 0.0,
    'BRK-B': 0.0, 'RDDT': 0.0, 'ALK': 0.0, 'SNOW': 0.0, 'CAVA': 0.0,
    'RIVN': 0.0, 'BMWYY': 4.5, 'BTC': 0.0, 'XRP': 0.0, 'KLAR': 0.0,
    'BLSH': 0.0, 'STUB': 0.0, 'VXUS': 1.8,
}

INCOME_FOREVER = {'VYM', 'SCHD'}    # never sell — compound income machines
DCA_ALWAYS     = {'VOO', 'QQQ', 'VTI'}  # core index — always buy, never sell


def _tax_note(lt_ready: bool, lt_date: str, pct_gain: float) -> str:
    """Generate a tax-awareness note."""
    if lt_ready:
        if pct_gain > 20:
            tax_saved = pct_gain * 0.17  # ~17% difference LT vs ST
            return f"LT eligible → save ~{tax_saved:.0f}% vs ST rate"
        return "LT eligible — sell triggers 15-20% cap gains rate"
    return f"ST status until {lt_date} — selling now triggers 37% ordinary income tax"


def _drip_note(ticker: str, drip_shares: float, drip_cost: float,
               current_price: Optional[float]) -> str:
    """Show DRIP compound growth value."""
    if not drip_shares or not current_price:
        return ""
    drip_current_val = drip_shares * current_price
    drip_gain = drip_current_val - drip_cost if drip_cost else drip_current_val
    yield_pct = DRIP_YIELD.get(ticker, 0)
    return (
        f"DRIP: {drip_shares:.4f} free shares worth ${drip_current_val:.2f}"
        f" (${drip_gain:+.2f} gain)"
        f"{f' · {yield_pct:.1f}% annual yield' if yield_pct else ''}"
    )


def generate_rec(
    cat:        str,
    ticker:     str,
    cost:       float,
    target:     Optional[float],
    bear:       Optional[float],
    bull:       Optional[float],
    lt_ready:   bool,
    lt_date:    str,
    price:      Optional[float],
    drip_shares: float = 0.0,
    drip_cost:   float = 0.0,
    divs_received: float = 0.0,
) -> RecResult:

    # ── No price yet ──────────────────────────────────────────────────────────
    if not price:
        if cat == "SELL":
            action = "🔴 SELL NOW" if lt_ready else f"⏳ WAIT → SELL {lt_date}"
            return RecResult(action, "Sell and consolidate into target ETF", "red", 3)
        return RecResult("⏸ HOLD", "Awaiting live price — tap Refresh", "gray", 0)

    # ── No target (SELL positions) ─────────────────────────────────────────────
    if not target:
        if cat == "SELL":
            action = "🔴 SELL NOW — LT eligible" if lt_ready else f"⏳ WAIT → SELL {lt_date}"
            tax = _tax_note(lt_ready, lt_date, (price-cost)/cost*100 if cost else 0)
            return RecResult(action, "Consolidate into target ETF per plan", "red", 3, tax)
        return RecResult("⏸ HOLD", "No analyst target set", "gray", 0)

    pct       = (price - cost) / cost * 100 if cost else 0
    upside    = (target - price) / price * 100 if price else 0
    declining = target < cost   # analyst sees limited recovery
    yield_pct = DRIP_YIELD.get(ticker, 0)
    drip_n    = _drip_note(ticker, drip_shares, drip_cost, price)
    tax_n     = _tax_note(lt_ready, lt_date, pct)

    # ── Income ETFs — never sell ───────────────────────────────────────────────
    if ticker in INCOME_FOREVER:
        annual_income = price * (yield_pct/100) * (drip_shares or 1)
        return RecResult(
            "♾ HOLD FOREVER — DRIP on",
            f"Compound income machine. Yield: {yield_pct:.1f}%."
            f" Est. annual income: ${annual_income:.2f}. Never sell.",
            "purple", 0, "", drip_n
        )

    # ── Core index ETFs — always DCA ───────────────────────────────────────────
    if ticker in DCA_ALWAYS:
        return RecResult(
            "📈 DCA ALWAYS",
            f"Core index — add every biweekly deposit. Never sell. {drip_n}",
            "green", 0, "", drip_n
        )

    # ── SELL-flagged positions ─────────────────────────────────────────────────
    if cat == "SELL":
        action = "🔴 SELL NOW — LT eligible" if lt_ready else f"⏳ WAIT → SELL {lt_date}"
        detail = f"Consolidate into target ETF. {tax_n}"
        return RecResult(action, detail, "red", 3, tax_n)

    # ── Bear proximity — ALWAYS highest priority for non-crypto ──────────────
    if bear and price < bear * 1.10 and cat != "Crypto":
        return RecResult(
            "🚨 STOP-LOSS ALERT",
            f"Price ${price:.2f} within 10% of bear case ${bear:,.0f}. "
            f"Review position immediately. {tax_n}",
            "red", 4, tax_n, drip_n
        )

    # ── Crypto special rules ──────────────────────────────────────────────────
    if cat == "Crypto":
        if upside > 25:
            return RecResult("🟢 ACCUMULATE",
                f"{upside:.0f}% upside to target ${target:,.0f}. Long-term hold.",
                "green", 3)
        if upside < -20:
            return RecResult("✂️ TRIM 15%",
                f"{abs(upside):.0f}% above target. Take some off. LT rate applies.",
                "orange", 2, tax_n)
        return RecResult("⏸ HOLD",
            f"{upside:.0f}% to target ${target:,.0f}. Hold position.",
            "blue", 1)

    # ── Declining thesis (analyst target < cost) — conservative cap ───────────
    if declining:
        if upside > 20:
            return RecResult("🟡 ACCUMULATE",
                f"{upside:.0f}% to analyst target (below cost — declining thesis). {drip_n}",
                "gold", 2, tax_n, drip_n)
        if 5 >= upside > -10:
            if lt_ready:
                return RecResult("✂️ TRIM 20% (LT)",
                    f"At analyst target. Take partial profits at LT rate. {tax_n}",
                    "orange", 2, tax_n, drip_n)
            return RecResult("⏳ HOLD (ST)",
                f"Near target — wait for LT: {lt_date}. Avoid 37% tax. {tax_n}",
                "gold", 1, tax_n, drip_n)
        if upside <= -10:
            if lt_ready:
                return RecResult("✂️ TRIM 25% (LT)",
                    f"Above analyst target. Lock gains at LT rate. {tax_n}",
                    "orange", 2, tax_n, drip_n)
            return RecResult("⏳ HOLD (ST)",
                f"Above target — hold until {lt_date} for LT rate. {tax_n}",
                "gold", 1, tax_n, drip_n)
        return RecResult("⏸ HOLD",
            f"Declining thesis — monitor analyst revisions. {drip_n}",
            "gray", 0, tax_n, drip_n)

    # ── Normal thesis — dip buying ────────────────────────────────────────────
    if pct < -20 and upside > 20:
        return RecResult("🔥 STRONG BUY",
            f"Down {abs(pct):.0f}% from cost with {upside:.0f}% to target! "
            f"Maximum opportunity. {drip_n}",
            "green", 4, tax_n, drip_n)

    if pct < -15 and upside > 15:
        return RecResult("🟢 BUY THE DIP",
            f"Down {abs(pct):.0f}% from cost. {upside:.0f}% upside. {drip_n}",
            "green", 3, tax_n, drip_n)

    # ── Standard upside zones ─────────────────────────────────────────────────
    if upside > 40:
        return RecResult("🟢 ACCUMULATE",
            f"{upside:.0f}% upside — add aggressively on any weakness. {drip_n}",
            "green", 3, tax_n, drip_n)

    if upside > 20:
        # Factor in DRIP yield — high yield = stronger accumulate signal
        if yield_pct > 2.0:
            return RecResult("🟢 ACCUMULATE + DRIP",
                f"{upside:.0f}% price upside + {yield_pct:.1f}% dividend yield. {drip_n}",
                "green", 3, tax_n, drip_n)
        return RecResult("🟢 ACCUMULATE",
            f"{upside:.0f}% upside — buy on weakness. {drip_n}",
            "green", 2, tax_n, drip_n)

    # ── At / above target ─────────────────────────────────────────────────────
    if 5 >= upside > -10:
        if lt_ready:
            return RecResult("✂️ TRIM 20% (LT)",
                f"At analyst target ${target:,.0f}. Sell 20% at LT rate. {tax_n}",
                "orange", 2, tax_n, drip_n)
        return RecResult("⏳ HOLD (ST)",
            f"Near target — hold until {lt_date} for LT cap-gains rate. {tax_n}",
            "gold", 1, tax_n, drip_n)

    if upside <= -10:
        if lt_ready:
            return RecResult("✂️ TRIM 25% (LT)",
                f"{abs(upside):.0f}% above target. Trim 25% at LT rate. {tax_n}",
                "orange", 2, tax_n, drip_n)
        return RecResult("⏳ HOLD (ST)",
            f"{abs(upside):.0f}% above target — wait for LT: {lt_date}. {tax_n}",
            "gold", 1, tax_n, drip_n)

    # ── IPO lockup ─────────────────────────────────────────────────────────────
    if cat == "IPO" and not lt_ready:
        return RecResult("🔒 HOLD (IPO)",
            f"IPO lockup — hold until LT: {lt_date}. "
            f"Target ${target:,.0f} ({upside:.0f}% upside).",
            "blue", 0, tax_n)

    # ── Normal hold zone (10-20% upside) ─────────────────────────────────────
    if upside > 10:
        return RecResult("⏸ HOLD",
            f"{upside:.0f}% upside to target ${target:,.0f}. {drip_n}",
            "blue", 1, tax_n, drip_n)

    # ── Default ───────────────────────────────────────────────────────────────
    return RecResult("⏸ HOLD",
        f"Monitoring — {upside:.0f}% to target ${target:,.0f}. {drip_n}",
        "gray", 0, tax_n, drip_n)
