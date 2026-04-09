"""
Portfolio Master Data — verified from 583 transactions (Mar 2024 → Apr 2026)
Shares and cost basis derived from full CSV reconciliation.
DRIP data tracked separately for compound growth analytics.
"""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Position:
    cat:        str              # Crypto / Core / ETF / Other / IPO / SELL
    ticker:     str
    name:       str
    shares:     float
    avg_cost:   float            # weighted average cost per share
    target:     Optional[float]  # analyst price target
    bear:       Optional[float]  # bear-case floor
    bull:       Optional[float]  # bull-case ceiling
    lt_ready:   bool             # True if held > 366 days (long-term tax rate)
    lt_date:    str              # date becomes LT eligible
    cg_id:      Optional[str]    # CoinGecko ID (crypto only)
    # DRIP / dividend data
    drip_shares: float = 0.0    # shares acquired via dividend reinvestment
    drip_cost:   float = 0.0    # total cost of DRIP shares
    divs_received: float = 0.0  # total cash dividends received (before DRIP)


# ── All 39 active positions ───────────────────────────────────────────────────
# Shares & avg_cost: reconciled from 583 transactions (both CSVs merged)
# DRIP data: from 139 DRIP transactions totalling $255.91 reinvested

POSITIONS = [

    # ── Crypto (held in Robinhood Crypto — separate from equity CSV) ──────────
    Position("Crypto","BTC",  "Bitcoin",            0.03433,  66997.0, 110000,45000,175000, True,  "LT",           "bitcoin"),
    Position("Crypto","XRP",  "XRP/Ripple",          1.066,    1.886,   2.80,  0.60,  5.00,  True,  "LT",           "ripple"),

    # ── Core Stocks ────────────────────────────────────────────────────────────
    Position("Core","NVDA",  "NVIDIA",               35.5042,  116.02,  175,    90,   250,   True,  "LT",           None, 0.01102, 1.64,  2.04),
    Position("Core","META",  "Meta Platforms",        2.3047,   610.11,  720,   400,   900,   False, "Sep 23 2026",  None, 0.00965, 6.09,  6.09),
    Position("Core","GOOGL", "Alphabet",              4.006,    299.83,  210,   140,   280,   False, "Dec 15 2026",  None, 0.00368, 1.13,  1.13),
    Position("Core","AAPL",  "Apple",                16.1136,  213.03,  240,   170,   290,   True,  "LT",           None, 0.07762,18.87, 20.23),
    Position("Core","MSFT",  "Microsoft",             0.0124,   402.0,   480,   330,   560,   True,  "LT",           None, 0.00013, 0.06,  0.08),
    Position("Core","NFLX",  "Netflix",              21.3325,  101.32,  1100,  700,  1400,   True,  "LT",           None, 0,       0,     0),
    Position("Core","COST",  "Costco",                2.3423,   942.22,  1050,  820,  1300,   True,  "LT",           None, 0.00712, 6.99,  6.99),
    Position("Core","TSM",   "Taiwan Semi",           1.984,    302.85,  230,   130,   320,   False, "Nov 6 2026",   None, 0.00271, 0.87,  1.10),
    Position("Core","CRM",   "Salesforce",            2.7404,   263.92,  320,   180,   400,   True,  "LT",           None, 0.02102, 5.63,  7.25),
    Position("Core","QCOM",  "Qualcomm",              2.3886,   190.51,  175,   100,   230,   True,  "LT",           None, 0.07915,12.31, 14.53),
    Position("Core","WMT",   "Walmart",              13.5867,    86.20,  105,    75,   130,   True,  "LT",           None, 0.10067, 9.98, 10.84),
    Position("Core","BRK-B", "Berkshire B",           4.5154,   489.88,  530,   400,   620,   True,  "LT",           None, 0,       0,     0),
    Position("Core","AMD",   "AMD",                   1.5597,   164.32,  140,    80,   220,   True,  "LT",           None, 0,       0,     0),

    # ── Other Stocks ──────────────────────────────────────────────────────────
    Position("Other","RDDT", "Reddit",                1.0,       34.00,  130,    60,   200,   True,  "LT",           None),
    Position("Other","ALK",  "Alaska Air",            0.6087,    41.07,   55,    28,    75,   True,  "LT",           None),
    Position("Other","SNOW", "Snowflake",             3.7353,   158.37,  190,    90,   250,   True,  "LT",           None),
    Position("Other","CAVA", "Cava Group",            1.0,       91.66,  120,    50,   160,   True,  "LT",           None),
    Position("Other","RIVN", "Rivian",               10.0,       14.62,   18,     5,    35,   False, "Mar 30 2027",  None),
    Position("Other","BMWYY","BMW ADR",               1.0,       39.72,   55,    25,    70,   False, "Mar 5 2027",   None, 0,       0,     2.16),

    # ── IPOs ──────────────────────────────────────────────────────────────────
    Position("IPO","BLSH",  "Bullish",               10.0,       37.00,   60,    15,    90,  False, "Aug 14 2026",  None),
    Position("IPO","KLAR",  "Klarna",                11.0,       40.00,   65,    25,   100,  False, "Sep 11 2026",  None),
    Position("IPO","STUB",  "StubHub",               23.3561,    25.62,   38,    12,    60,  False, "Sep 18 2026",  None),

    # ── Core ETFs (DCA forever) ────────────────────────────────────────────────
    Position("ETF","VOO",   "Vanguard S&P 500",       7.601,    570.62,  650,   420,   750,  True,  "LT",           None, 0.06235,36.47, 51.95),
    Position("ETF","QQQ",   "Nasdaq-100",             2.753,    606.29,  620,   380,   750,  True,  "LT",           None, 0.00559, 3.13,  3.30),
    Position("ETF","VTI",   "Vanguard Total Mkt",     3.7163,   309.23,  370,   240,   430,  True,  "LT",           None, 0.02584, 7.98,  9.68),
    Position("ETF","VGT",   "Vanguard IT ETF",        1.4665,   664.04,  760,   480,   920,  True,  "LT",           None, 0.00565, 3.80,  4.35),
    Position("ETF","VHT",   "Vanguard Health",        1.8915,   270.81,  300,   200,   370,  True,  "LT",           None, 0.04614,12.23, 12.23),
    Position("ETF","VIS",   "Vanguard Industrials",   1.9715,   258.35,  340,   210,   420,  True,  "LT",           None, 0.03348, 9.33,  9.33),
    Position("ETF","VYM",   "Vanguard Hi-Div Yield", 21.9148,   136.97,  160,   110,   190,  True,  "LT",           None, 0.46512,65.12, 68.87),
    Position("ETF","SCHD",  "Schwab US Dividend",    19.2856,    28.02,   34,    20,    44,  True,  "LT",           None, 0.35875,10.35, 10.87),
    Position("ETF","VXUS",  "Vanguard Intl",         21.0484,    76.78,   85,    55,   110,  True,  "LT",           None, 0.21327,16.15, 16.15),
    Position("ETF","GLD",   "SPDR Gold",              6.6407,   361.40,  450,   250,   550,  True,  "Apr 4 2026",   None, 0,       0,     0),
    Position("ETF","XLE",   "Energy SPDR",           15.3795,    46.73,   72,    44,    95,  True,  "LT",           None, 0.29084,18.66, 18.66),
    Position("ETF","SPY",   "SPDR S&P 500",           0.5084,   595.64,  None,  None,  None, False, "May 20 2026",  None, 0.00425, 2.83,  2.83),

    # ── SELL / Consolidate — REMAINING after March 31 sells ───────────────────
    # NOTE: VTV, VEA, VWO, BND were SOLD on 3/31/2026
    # VTV: 0.489248 sold → 0.1658 remaining (partial — original 0.1658 from earlier lots)
    # VEA: 0.261968 sold → 0.2523 remaining (partial)
    # VWO: 0.150974 sold → 0.1446 remaining (partial)
    # BND: 0.594855 sold → 0.578 remaining (partial — DRIP keeps adding)
    Position("SELL","VTV",  "Vanguard Value → VOO",   0.1658,   156.54,  None,  None,  None, True,  "LT NOW",       None, 0.01310, 2.36,  4.13),
    Position("SELL","VEA",  "Dev Mkts → VXUS",        0.2523,    49.23,  None,  None,  None, True,  "LT NOW",       None, 0.01281, 0.72,  0.94),
    Position("SELL","VWO",  "Emg Mkts → VXUS",        0.1446,    41.49,  None,  None,  None, True,  "LT NOW",       None, 0.00751, 0.37,  0.42),
    Position("SELL","BND",  "Bond ETF → VYM",         0.578,     72.20,  None,  None,  None, True,  "LT NOW",       None, 0.03198, 2.35,  3.21),
    Position("SELL","VUG",  "Vanguard Growth → QQQ",  0.4647,   441.03,  None,  None,  None, False, "Jul 15 2026",  None, 0.00101, 0.49,  0.71),
]

# ── DRIP totals (for portfolio-level analytics) ────────────────────────────────
DRIP_SUMMARY = {
    "total_reinvested":  255.91,   # total $ reinvested via DRIP
    "total_divs":        290.07,   # total cash dividends declared
    "total_drip_txns":   139,      # number of DRIP buy transactions
    "top_drip_tickers":  ["VYM","VOO","XLE","VXUS","AAPL","SCHD","VHT","QCOM"],
}

# ── Income ETFs (never sell, always DRIP) ─────────────────────────────────────
INCOME_FOREVER = {"VYM", "SCHD"}

# ── Core index ETFs (always DCA, never sell) ──────────────────────────────────
DCA_ALWAYS = {"VOO", "QQQ", "VTI"}

# ── Biweekly deposit schedule 2026 ────────────────────────────────────────────
DEPOSIT_SCHEDULE = [
    "Apr 3","Apr 17","May 1","May 15","May 29","Jun 12","Jun 26",
    "Jul 10","Jul 24","Aug 7","Aug 21","Sep 4","Sep 18",
    "Oct 2","Oct 16","Oct 30","Nov 13","Nov 27","Dec 11",
]
DEPOSIT_ROTATION = ["META","GOOGL","AAPL","MSFT","COST","TSM","CRM","NVDA","NFLX","AMD"]

# ── Known cash from sold positions ────────────────────────────────────────────
CONFIRMED_CASH = 1042.17

# ── Action calendar ───────────────────────────────────────────────────────────
ACTION_CALENDAR = [
    {"date":"Apr 3",  "days":2,   "icon":"🟡","action":"GLD → LT eligible Apr 4 — trim 25% at $450 target"},
    {"date":"Apr 3",  "days":2,   "icon":"💰","action":"FIRST $900 DEPOSIT — deploy per formula"},
    {"date":"Apr 17", "days":16,  "icon":"💰","action":"$900 deposit #2"},
    {"date":"May 20", "days":49,  "icon":"🔴","action":"SPY turns LT — sell all, reinvest into VOO"},
    {"date":"Jul 15", "days":105, "icon":"🔴","action":"VUG turns LT — sell all, reinvest into QQQ"},
    {"date":"Aug 14", "days":135, "icon":"🔵","action":"BLSH hits 1 year — evaluate, consider trim"},
    {"date":"Sep 11", "days":163, "icon":"🔵","action":"KLAR hits 1 year — evaluate, consider trim"},
    {"date":"Sep 18", "days":170, "icon":"🔵","action":"STUB hits 1 year — evaluate"},
    {"date":"Nov 6",  "days":219, "icon":"🔵","action":"TSM big lot → LT — trim 20%"},
    {"date":"Dec 15", "days":258, "icon":"🔵","action":"GOOGL big lot → LT — trim 20%"},
]

def get_positions_list():
    """Return positions as list of tuples for backwards compatibility."""
    return [
        (p.cat, p.ticker, p.name, p.shares, p.avg_cost,
         p.target, p.bear, p.bull, p.lt_ready, p.lt_date, p.cg_id,
         p.drip_shares, p.drip_cost, p.divs_received)
        for p in POSITIONS
    ]
