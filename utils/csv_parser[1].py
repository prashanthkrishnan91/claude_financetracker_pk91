"""
Robinhood CSV Parser v4
Handles: Buy · Sell · CDIV · DRIP · SPL · ACH · RTP · LIQ · REC · SXCH · DFEE · DTAX
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Transaction:
    date:    str
    ticker:  str
    code:    str
    qty:     float
    price:   float
    amt:     float
    desc:    str
    is_drip: bool = False   # True when desc contains "Reinvestment"


@dataclass
class ParsedPortfolio:
    positions:       Dict[str, dict]    # ticker → {shares, total_cost, drip_shares, drip_cost}
    dividends:       Dict[str, float]   # ticker → total cash dividends received
    drip_log:        List[dict]         # each DRIP event {date, ticker, shares, amt, price}
    cash_deposits:   float              # ACH + RTP inflows
    sell_proceeds:   float              # total proceeds from all Sell transactions
    total_tx:        int
    sells:           int
    buys:            int
    drip_count:      int
    cdiv_count:      int
    date_range:      str
    raw_txs:         List[Transaction]


def _safe_float(s: str) -> float:
    try:
        return float(re.sub(r'[$(,()]', '', (s or '').strip().rstrip(')')) or '0')
    except:
        return 0.0


def _parse_lines(content: str) -> List[Transaction]:
    lines = content.splitlines()
    txs = []
    i = 1
    while i < len(lines):
        raw = lines[i].strip()
        if not raw or 'The data provided' in raw or raw in ('""', ''):
            i += 1
            continue

        # Stitch multi-line fields (Robinhood wraps CUSIP / description onto next lines)
        full = raw
        while i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            # Stop if next line is a new date record or blank
            if re.match(r'^"?\d{1,2}/\d{1,2}/\d{4}"?', nxt) or not nxt:
                break
            full += ' ' + nxt
            i += 1

        # Parse CSV with proper quote handling
        fields, cur, inq = [], '', False
        for ch in full:
            if ch == '"':
                inq = not inq
                continue
            if ch == ',' and not inq:
                fields.append(cur.strip())
                cur = ''
                continue
            cur += ch
        fields.append(cur.strip())

        if len(fields) < 6:
            i += 1
            continue

        act_date = fields[0]
        ticker   = fields[3].upper().replace('BRK.B', 'BRK-B').strip() if fields[3] else ''
        desc     = fields[4] if len(fields) > 4 else ''
        code     = fields[5].strip() if fields[5] else ''
        qty      = _safe_float(fields[6]) if len(fields) > 6 else 0.0
        price_f  = _safe_float(fields[7]) if len(fields) > 7 else 0.0
        amt      = abs(_safe_float(fields[8])) if len(fields) > 8 else 0.0

        if act_date and code:
            is_drip = 'Reinvestment' in desc or 'Dividend Reinvestment' in desc
            txs.append(Transaction(
                date=act_date, ticker=ticker, code=code,
                qty=qty, price=price_f, amt=amt,
                desc=desc, is_drip=is_drip,
            ))
        i += 1

    return txs


def parse_robinhood_csv(content: str) -> ParsedPortfolio:
    """
    Full transaction reconciliation.

    Transaction codes handled:
      Buy   → add shares + cost (with DRIP detection)
      Sell  → reduce shares proportionally (KEY FIX)
      CDIV  → cash dividend received (tracked, no share change)
      SPL   → stock split (shares added, cost basis unchanged)
      ACH   → cash deposit (bank transfer)
      RTP   → instant cash deposit
      LIQ   → position liquidated
      REC   → shares received (transfer-in; use cost = avg price or 0)
      SXCH  → share exchange / ADR conversion
      DFEE  → dividend fee (deduct from cash)
      DTAX  → dividend withholding tax (deduct from cash)
      MISC  → miscellaneous (ignore)
    """
    txs = _parse_lines(content)
    txs.sort(key=lambda x: x.date)

    pos = defaultdict(lambda: {
        'shares': 0.0, 'total_cost': 0.0,
        'drip_shares': 0.0, 'drip_cost': 0.0,
        'sell_proceeds': 0.0,
    })
    dividends    = defaultdict(float)
    drip_log     = []
    cash_deposits = 0.0
    sell_proceeds = 0.0

    for tx in txs:
        t = tx.ticker
        code = tx.code
        qty, amt, price = tx.qty, tx.amt, tx.price

        # ── Deposits ────────────────────────────────────────────────────────
        if code in ('ACH', 'RTP'):
            cash_deposits += amt
            continue

        # ── Cash dividend (no shares) ────────────────────────────────────────
        if code == 'CDIV':
            dividends[t] += amt
            continue

        # ── Fees / taxes (ignore for position tracking) ───────────────────
        if code in ('DFEE', 'DTAX', 'MISC'):
            continue

        # ── Buy (including DRIP reinvestments) ────────────────────────────
        if code == 'Buy' and qty > 0 and amt > 0:
            pos[t]['shares']     += qty
            pos[t]['total_cost'] += amt
            if tx.is_drip:
                pos[t]['drip_shares'] += qty
                pos[t]['drip_cost']   += amt
                drip_log.append({
                    'date': tx.date, 'ticker': t,
                    'shares': qty, 'amt': amt,
                    'price': price if price else (amt / qty if qty else 0),
                })
            continue

        # ── SELL — THE CRITICAL HANDLER ──────────────────────────────────
        if code == 'Sell' and qty > 0:
            current_shares = pos[t]['shares']
            if current_shares > 0.00001:
                sell_frac = min(qty / current_shares, 1.0)
                # Reduce cost proportionally (FIFO approximation)
                pos[t]['total_cost']  *= (1.0 - sell_frac)
                pos[t]['drip_cost']   *= (1.0 - sell_frac)
                pos[t]['drip_shares'] *= (1.0 - sell_frac)
            pos[t]['shares'] = max(0.0, current_shares - qty)
            pos[t]['sell_proceeds'] += amt
            sell_proceeds += amt
            continue

        # ── Stock split (adds shares, total cost unchanged) ───────────────
        if code == 'SPL' and qty > 0:
            pos[t]['shares'] += qty
            continue

        # ── Liquidation ───────────────────────────────────────────────────
        if code == 'LIQ':
            pos[t]['sell_proceeds'] += amt
            sell_proceeds += amt
            pos[t]['shares']     = 0.0
            pos[t]['total_cost'] = 0.0
            continue

        # ── Transfer-in / ADR conversion ─────────────────────────────────
        if code in ('REC', 'SXCH') and qty > 0:
            pos[t]['shares'] += qty
            # If price known, record cost; otherwise 0 basis
            if price > 0 and amt == 0:
                pos[t]['total_cost'] += qty * price
            elif amt > 0:
                pos[t]['total_cost'] += amt
            continue

    # Build final active positions dict
    active = {}
    for t, v in pos.items():
        if v['shares'] > 0.0001 and t:
            avg_cost = v['total_cost'] / v['shares'] if v['shares'] > 0 else 0
            active[t] = {
                'shares':       round(v['shares'],      6),
                'avg_cost':     round(avg_cost,         4),
                'total_cost':   round(v['total_cost'],  2),
                'drip_shares':  round(v['drip_shares'],  6),
                'drip_cost':    round(v['drip_cost'],    2),
                'sell_proceeds':round(v['sell_proceeds'],2),
            }

    # Deduped dates
    dates = sorted(set(tx.date for tx in txs))
    date_range = f"{dates[0]} → {dates[-1]}" if dates else "—"

    return ParsedPortfolio(
        positions      = active,
        dividends      = dict(dividends),
        drip_log       = drip_log,
        cash_deposits  = round(cash_deposits, 2),
        sell_proceeds  = round(sell_proceeds,  2),
        total_tx       = len(txs),
        sells          = sum(1 for tx in txs if tx.code == 'Sell'),
        buys           = sum(1 for tx in txs if tx.code == 'Buy'),
        drip_count     = sum(1 for tx in txs if tx.is_drip),
        cdiv_count     = sum(1 for tx in txs if tx.code == 'CDIV'),
        date_range     = date_range,
        raw_txs        = txs,
    )


def merge_csvs(content_list: list[str]) -> ParsedPortfolio:
    """Merge multiple Robinhood CSVs, deduplicating by (date, ticker, code, qty)."""
    all_txs = []
    seen    = set()
    for content in content_list:
        for tx in _parse_lines(content):
            key = (tx.date, tx.ticker, tx.code, round(tx.qty, 6), round(tx.amt, 2))
            if key not in seen:
                seen.add(key)
                all_txs.append(tx)

    # Re-run reconciliation on merged set
    combined = '\n'.join([
        '"Activity Date","Process Date","Settle Date","Instrument","Description",'
        '"Trans Code","Quantity","Price","Amount"'
    ])
    # Reconstruct as synthetic CSV and re-parse is complex; instead replay txs directly
    all_txs.sort(key=lambda x: x.date)

    pos       = defaultdict(lambda: {'shares':0.0,'total_cost':0.0,'drip_shares':0.0,'drip_cost':0.0,'sell_proceeds':0.0})
    dividends = defaultdict(float)
    drip_log  = []
    cash_deposits = 0.0
    sell_proceeds = 0.0

    for tx in all_txs:
        t, code, qty, amt, price = tx.ticker, tx.code, tx.qty, tx.amt, tx.price
        if code in ('ACH', 'RTP'):
            cash_deposits += amt
        elif code == 'CDIV':
            dividends[t] += amt
        elif code in ('DFEE', 'DTAX', 'MISC'):
            pass
        elif code == 'Buy' and qty > 0 and amt > 0:
            pos[t]['shares'] += qty; pos[t]['total_cost'] += amt
            if tx.is_drip:
                pos[t]['drip_shares'] += qty; pos[t]['drip_cost'] += amt
                drip_log.append({'date':tx.date,'ticker':t,'shares':qty,'amt':amt,
                    'price':price if price else (amt/qty if qty else 0)})
        elif code == 'Sell' and qty > 0:
            if pos[t]['shares'] > 0.00001:
                frac = min(qty/pos[t]['shares'], 1.0)
                pos[t]['total_cost']  *= (1-frac)
                pos[t]['drip_shares'] *= (1-frac)
                pos[t]['drip_cost']   *= (1-frac)
            pos[t]['shares'] = max(0, pos[t]['shares']-qty)
            pos[t]['sell_proceeds'] += amt; sell_proceeds += amt
        elif code == 'SPL' and qty > 0:
            pos[t]['shares'] += qty
        elif code == 'LIQ':
            pos[t]['sell_proceeds'] += amt; sell_proceeds += amt
            pos[t] = {'shares':0.0,'total_cost':0.0,'drip_shares':0.0,'drip_cost':0.0,'sell_proceeds':amt}
        elif code in ('REC','SXCH') and qty > 0:
            pos[t]['shares'] += qty
            if price > 0: pos[t]['total_cost'] += qty*price

    active = {}
    for t, v in pos.items():
        if v['shares'] > 0.0001 and t:
            avg = v['total_cost']/v['shares'] if v['shares'] > 0 else 0
            active[t] = {'shares':round(v['shares'],6),'avg_cost':round(avg,4),
                'total_cost':round(v['total_cost'],2),'drip_shares':round(v['drip_shares'],6),
                'drip_cost':round(v['drip_cost'],2),'sell_proceeds':round(v['sell_proceeds'],2)}

    dates = sorted(set(tx.date for tx in all_txs))
    return ParsedPortfolio(positions=active, dividends=dict(dividends), drip_log=drip_log,
        cash_deposits=round(cash_deposits,2), sell_proceeds=round(sell_proceeds,2),
        total_tx=len(all_txs), sells=sum(1 for t in all_txs if t.code=='Sell'),
        buys=sum(1 for t in all_txs if t.code=='Buy'),
        drip_count=sum(1 for t in all_txs if t.is_drip),
        cdiv_count=sum(1 for t in all_txs if t.code=='CDIV'),
        date_range=f"{dates[0]} → {dates[-1]}" if dates else "—",
        raw_txs=all_txs)


def reconcile(parsed: ParsedPortfolio, base_positions: list) -> tuple[list, list[dict]]:
    """
    Merge CSV-derived positions into base portfolio.
    Returns (updated_positions, change_log).

    Key behaviour:
    - If CSV shows 0 shares for a SELL-flagged position → REMOVE it from active list
    - If CSV shows fewer shares than expected → update count
    - Never removes Crypto positions (not in equity CSV)
    """
    csv_pos  = parsed.positions
    changes  = []
    updated  = []

    for p in base_positions:
        cat, ticker = p[0], p[1]

        # Crypto not in equity CSV — always keep
        if p[10]:  # cg_id present
            updated.append(p)
            continue

        if ticker in csv_pos:
            c = csv_pos[ticker]
            old_sh   = p[3]
            old_cost = p[4]
            new_sh   = c['shares']
            new_cost = c['avg_cost']
            new_drip_sh   = c.get('drip_shares', p[11] if len(p) > 11 else 0)
            new_drip_cost = c.get('drip_cost',   p[12] if len(p) > 12 else 0)
            new_divs      = parsed.dividends.get(ticker, p[13] if len(p) > 13 else 0)

            new_p = (cat, ticker, p[2], new_sh, new_cost,
                     p[5], p[6], p[7], p[8], p[9], p[10],
                     new_drip_sh, new_drip_cost, new_divs)

            if abs(new_sh - old_sh) > 0.0001 or abs(new_cost - old_cost) > 0.50:
                changes.append({
                    'Ticker': ticker, 'Change': 'Updated',
                    'Old Shares': f'{old_sh:.4f}', 'New Shares': f'{new_sh:.4f}',
                    'Old Avg Cost': f'${old_cost:.2f}', 'New Avg Cost': f'${new_cost:.2f}',
                    'DRIP Shares': f'{new_drip_sh:.5f}',
                })
            updated.append(new_p)

        elif cat == 'SELL' and ticker not in csv_pos:
            # Position is in SELL list but CSV shows 0 shares → fully sold → remove
            changes.append({'Ticker': ticker, 'Change': '✅ Fully Sold — Removed',
                'Old Shares': f'{p[3]:.4f}', 'New Shares': '0',
                'Old Avg Cost': f'${p[4]:.2f}', 'New Avg Cost': '—', 'DRIP Shares': '—'})
            # Do NOT add to updated → effectively removed

        else:
            # Ticker in base but not in CSV (no buys yet or different lot tracking)
            updated.append(p)

    # Add brand-new tickers found in CSV
    base_tickers = {p[1] for p in base_positions}
    for t, v in csv_pos.items():
        if t not in base_tickers:
            divs = parsed.dividends.get(t, 0)
            updated.append(('Other', t, t, v['shares'], v['avg_cost'],
                            None, None, None, True, 'Check LT date', None,
                            v.get('drip_shares', 0), v.get('drip_cost', 0), divs))
            changes.append({'Ticker': t, 'Change': 'New Position',
                'Old Shares': '—', 'New Shares': f"{v['shares']:.4f}",
                'Old Avg Cost': '—', 'New Avg Cost': f"${v['avg_cost']:.2f}",
                'DRIP Shares': f"{v.get('drip_shares',0):.5f}"})

    return updated, changes
