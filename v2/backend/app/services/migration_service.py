"""Migration service — seed v1 bootstrap data into v2 Supabase."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from ..database import get_supabase_client


# ── v1 Position Data (from v1/data/portfolio.py) ─────────────────────────────
# This is the authoritative bootstrap data for v1 → v2 migration.

V1_POSITIONS = [
    {"cat": "Crypto", "ticker": "BTC", "name": "Bitcoin", "shares": 0.03433, "avg_cost": 66997.0, "target": 110000, "bear": 45000, "bull": 175000, "lt_ready": True, "lt_date": None, "cg_id": "bitcoin", "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Crypto", "ticker": "XRP", "name": "XRP/Ripple", "shares": 1.066, "avg_cost": 1.886, "target": 2.80, "bear": 0.60, "bull": 5.00, "lt_ready": True, "lt_date": None, "cg_id": "ripple", "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Core", "ticker": "NVDA", "name": "NVIDIA", "shares": 35.5042, "avg_cost": 116.02, "target": 175, "bear": 90, "bull": 250, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.01102, "drip_cost": 1.64, "divs": 2.04},
    {"cat": "Core", "ticker": "META", "name": "Meta Platforms", "shares": 2.3047, "avg_cost": 610.11, "target": 720, "bear": 400, "bull": 900, "lt_ready": False, "lt_date": "2026-09-23", "cg_id": None, "drip_shares": 0.00965, "drip_cost": 6.09, "divs": 6.09},
    {"cat": "Core", "ticker": "GOOGL", "name": "Alphabet", "shares": 4.006, "avg_cost": 299.83, "target": 210, "bear": 140, "bull": 280, "lt_ready": False, "lt_date": "2026-12-15", "cg_id": None, "drip_shares": 0.00368, "drip_cost": 1.13, "divs": 1.13},
    {"cat": "Core", "ticker": "AAPL", "name": "Apple", "shares": 16.1136, "avg_cost": 213.03, "target": 240, "bear": 170, "bull": 290, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.07762, "drip_cost": 18.87, "divs": 20.23},
    {"cat": "Core", "ticker": "MSFT", "name": "Microsoft", "shares": 0.0124, "avg_cost": 402.0, "target": 480, "bear": 330, "bull": 560, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.00013, "drip_cost": 0.06, "divs": 0.08},
    {"cat": "Core", "ticker": "NFLX", "name": "Netflix", "shares": 21.3325, "avg_cost": 101.32, "target": 1100, "bear": 700, "bull": 1400, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Core", "ticker": "COST", "name": "Costco", "shares": 2.3423, "avg_cost": 942.22, "target": 1050, "bear": 820, "bull": 1300, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.00712, "drip_cost": 6.99, "divs": 6.99},
    {"cat": "Core", "ticker": "TSM", "name": "Taiwan Semi", "shares": 1.984, "avg_cost": 302.85, "target": 230, "bear": 130, "bull": 320, "lt_ready": False, "lt_date": "2026-11-06", "cg_id": None, "drip_shares": 0.00271, "drip_cost": 0.87, "divs": 1.10},
    {"cat": "Core", "ticker": "CRM", "name": "Salesforce", "shares": 2.7404, "avg_cost": 263.92, "target": 320, "bear": 180, "bull": 400, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.02102, "drip_cost": 5.63, "divs": 7.25},
    {"cat": "Core", "ticker": "QCOM", "name": "Qualcomm", "shares": 2.3886, "avg_cost": 190.51, "target": 175, "bear": 100, "bull": 230, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.07915, "drip_cost": 12.31, "divs": 14.53},
    {"cat": "Core", "ticker": "WMT", "name": "Walmart", "shares": 13.5867, "avg_cost": 86.20, "target": 105, "bear": 75, "bull": 130, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.10067, "drip_cost": 9.98, "divs": 10.84},
    {"cat": "Core", "ticker": "BRK-B", "name": "Berkshire B", "shares": 4.5154, "avg_cost": 489.88, "target": 530, "bear": 400, "bull": 620, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Core", "ticker": "AMD", "name": "AMD", "shares": 1.5597, "avg_cost": 164.32, "target": 140, "bear": 80, "bull": 220, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Other", "ticker": "RDDT", "name": "Reddit", "shares": 1.0, "avg_cost": 34.00, "target": 130, "bear": 60, "bull": 200, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Other", "ticker": "ALK", "name": "Alaska Air", "shares": 0.6087, "avg_cost": 41.07, "target": 55, "bear": 28, "bull": 75, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Other", "ticker": "SNOW", "name": "Snowflake", "shares": 3.7353, "avg_cost": 158.37, "target": 190, "bear": 90, "bull": 250, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Other", "ticker": "CAVA", "name": "Cava Group", "shares": 1.0, "avg_cost": 91.66, "target": 120, "bear": 50, "bull": 160, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Other", "ticker": "RIVN", "name": "Rivian", "shares": 10.0, "avg_cost": 14.62, "target": 18, "bear": 5, "bull": 35, "lt_ready": False, "lt_date": "2027-03-30", "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "Other", "ticker": "BMWYY", "name": "BMW ADR", "shares": 1.0, "avg_cost": 39.72, "target": 55, "bear": 25, "bull": 70, "lt_ready": False, "lt_date": "2027-03-05", "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 2.16},
    {"cat": "IPO", "ticker": "BLSH", "name": "Bullish", "shares": 10.0, "avg_cost": 37.00, "target": 60, "bear": 15, "bull": 90, "lt_ready": False, "lt_date": "2026-08-14", "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "IPO", "ticker": "KLAR", "name": "Klarna", "shares": 11.0, "avg_cost": 40.00, "target": 65, "bear": 25, "bull": 100, "lt_ready": False, "lt_date": "2026-09-11", "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "IPO", "ticker": "STUB", "name": "StubHub", "shares": 23.3561, "avg_cost": 25.62, "target": 38, "bear": 12, "bull": 60, "lt_ready": False, "lt_date": "2026-09-18", "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "ETF", "ticker": "VOO", "name": "Vanguard S&P 500", "shares": 7.601, "avg_cost": 570.62, "target": 650, "bear": 420, "bull": 750, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.06235, "drip_cost": 36.47, "divs": 51.95},
    {"cat": "ETF", "ticker": "QQQ", "name": "Nasdaq-100", "shares": 2.753, "avg_cost": 606.29, "target": 620, "bear": 380, "bull": 750, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.00559, "drip_cost": 3.13, "divs": 3.30},
    {"cat": "ETF", "ticker": "VTI", "name": "Vanguard Total Mkt", "shares": 3.7163, "avg_cost": 309.23, "target": 370, "bear": 240, "bull": 430, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.02584, "drip_cost": 7.98, "divs": 9.68},
    {"cat": "ETF", "ticker": "VGT", "name": "Vanguard IT ETF", "shares": 1.4665, "avg_cost": 664.04, "target": 760, "bear": 480, "bull": 920, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.00565, "drip_cost": 3.80, "divs": 4.35},
    {"cat": "ETF", "ticker": "VHT", "name": "Vanguard Health", "shares": 1.8915, "avg_cost": 270.81, "target": 300, "bear": 200, "bull": 370, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.04614, "drip_cost": 12.23, "divs": 12.23},
    {"cat": "ETF", "ticker": "VIS", "name": "Vanguard Industrials", "shares": 1.9715, "avg_cost": 258.35, "target": 340, "bear": 210, "bull": 420, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.03348, "drip_cost": 9.33, "divs": 9.33},
    {"cat": "ETF", "ticker": "VYM", "name": "Vanguard Hi-Div Yield", "shares": 21.9148, "avg_cost": 136.97, "target": 160, "bear": 110, "bull": 190, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.46512, "drip_cost": 65.12, "divs": 68.87},
    {"cat": "ETF", "ticker": "SCHD", "name": "Schwab US Dividend", "shares": 19.2856, "avg_cost": 28.02, "target": 34, "bear": 20, "bull": 44, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.35875, "drip_cost": 10.35, "divs": 10.87},
    {"cat": "ETF", "ticker": "VXUS", "name": "Vanguard Intl", "shares": 21.0484, "avg_cost": 76.78, "target": 85, "bear": 55, "bull": 110, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.21327, "drip_cost": 16.15, "divs": 16.15},
    {"cat": "ETF", "ticker": "GLD", "name": "SPDR Gold", "shares": 6.6407, "avg_cost": 361.40, "target": 450, "bear": 250, "bull": 550, "lt_ready": True, "lt_date": "2026-04-04", "cg_id": None, "drip_shares": 0, "drip_cost": 0, "divs": 0},
    {"cat": "ETF", "ticker": "XLE", "name": "Energy SPDR", "shares": 15.3795, "avg_cost": 46.73, "target": 72, "bear": 44, "bull": 95, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.29084, "drip_cost": 18.66, "divs": 18.66},
    {"cat": "ETF", "ticker": "SPY", "name": "SPDR S&P 500", "shares": 0.5084, "avg_cost": 595.64, "target": None, "bear": None, "bull": None, "lt_ready": False, "lt_date": "2026-05-20", "cg_id": None, "drip_shares": 0.00425, "drip_cost": 2.83, "divs": 2.83},
    {"cat": "SELL", "ticker": "VTV", "name": "Vanguard Value", "shares": 0.1658, "avg_cost": 156.54, "target": None, "bear": None, "bull": None, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.01310, "drip_cost": 2.36, "divs": 4.13},
    {"cat": "SELL", "ticker": "VEA", "name": "Dev Mkts", "shares": 0.2523, "avg_cost": 49.23, "target": None, "bear": None, "bull": None, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.01281, "drip_cost": 0.72, "divs": 0.94},
    {"cat": "SELL", "ticker": "VWO", "name": "Emg Mkts", "shares": 0.1446, "avg_cost": 41.49, "target": None, "bear": None, "bull": None, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.00751, "drip_cost": 0.37, "divs": 0.42},
    {"cat": "SELL", "ticker": "BND", "name": "Bond ETF", "shares": 0.578, "avg_cost": 72.20, "target": None, "bear": None, "bull": None, "lt_ready": True, "lt_date": None, "cg_id": None, "drip_shares": 0.03198, "drip_cost": 2.35, "divs": 3.21},
    {"cat": "SELL", "ticker": "VUG", "name": "Vanguard Growth", "shares": 0.4647, "avg_cost": 441.03, "target": None, "bear": None, "bull": None, "lt_ready": False, "lt_date": "2026-07-15", "cg_id": None, "drip_shares": 0.00101, "drip_cost": 0.49, "divs": 0.71},
]


async def seed_v1_positions(user_id: UUID) -> list[dict]:
    """Seed positions from v1 bootstrap data into Supabase.

    Skips tickers that already exist for this user.
    """
    client = get_supabase_client()

    # Get existing tickers to avoid duplicates
    existing = (
        client.table("positions")
        .select("ticker")
        .eq("user_id", str(user_id))
        .execute()
    ).data
    existing_tickers = {r["ticker"] for r in existing}

    rows_to_insert = []
    for p in V1_POSITIONS:
        if p["ticker"] in existing_tickers:
            continue

        row = {
            "user_id": str(user_id),
            "ticker": p["ticker"],
            "name": p["name"],
            "category": p["cat"],
            "shares": p["shares"],
            "avg_cost": p["avg_cost"],
            "drip_shares": p["drip_shares"],
            "drip_cost": p["drip_cost"],
            "divs_received": p["divs"],
            "target_price": p["target"],
            "bear_price": p["bear"],
            "bull_price": p["bull"],
            "lt_eligible": p["lt_ready"],
            "lt_date": p["lt_date"],
            "coingecko_id": p["cg_id"],
            "source": "bootstrap",
        }
        rows_to_insert.append(row)

    if not rows_to_insert:
        return []

    result = client.table("positions").insert(rows_to_insert).execute()
    return result.data
