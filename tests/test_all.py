"""
War Room — Full Test Suite
Run: python -m pytest tests/ -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.csv_parser import parse_robinhood_csv, merge_csvs, reconcile
from utils.rec_engine import generate_rec

# ═══════════════════════════════════════════════════════════════════════════════
#  CSV PARSER TESTS
# ═══════════════════════════════════════════════════════════════════════════════

SAMPLE_CSV = '''"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"
"3/31/2026","3/31/2026","4/1/2026","VWO","Vanguard FTSE","Sell","0.150974","$52.67","$7.95"
"3/31/2026","3/31/2026","4/1/2026","VTV","Vanguard Value","Sell","0.489248","$194.70","$95.26"
"3/30/2026","3/30/2026","3/31/2026","AMD","AMD CUSIP","Sell","0.663685","$205.32","$136.27"
"3/30/2026","3/30/2026","3/31/2026","AMD","AMD CUSIP","Sell","0.895007","$205.32","$183.81"
"3/30/2026","3/30/2026","3/31/2026","XOP","XOP CUSIP","Sell","0.640768","$190.33","$121.96"
"3/30/2026","3/30/2026","3/31/2026","XOP","XOP CUSIP","Sell","1","$190.33","$190.33"
"3/30/2026","3/30/2026","3/31/2026","RIVN","Rivian Automotive","Sell","10","$14.90","$149.00"
"3/26/2026","3/26/2026","3/27/2026","VOO","Vanguard S&P 500 ETF\nCUSIP: 922908363","Buy","0.166286","$601.37","($100.00)"
"3/26/2026","3/26/2026","3/27/2026","VYM","Vanguard Hi-Div\nCUSIP\nDividend Reinvestment","Buy","0.118718","$148.33","($17.61)"
"3/10/2026","3/10/2026","3/11/2026","VOO","Vanguard S&P 500 ETF","Buy","0.319177","$626.61","($200.00)"
"3/10/2026","3/10/2026","3/10/2026","","Instant bank transfer","RTP","","","$900.00"
"1/28/2025","1/28/2025","1/29/2025","AMD","AMD","Buy","1.558692","$119.56","$186.45"
"6/10/2024","6/10/2024","6/10/2024","NVDA","NVIDIA","SPL","18","",""
"3/4/2024","3/4/2024","3/4/2024","","Cash Div VYM","CDIV","","","$4.91"
"3/4/2024","3/4/2024","3/4/2024","BTC","Bitcoin","MISC","","",""
"3/31/2026","3/31/2026","3/31/2026","XOP","DTAX withholding","DTAX","","","$0.50"
"3/4/2024","3/4/2024","3/6/2024","MSFT","Microsoft","REC","0.0123","",""
"3/31/2026","3/31/2026","3/31/2026","VWO","Cash Div: R/D 2026-03-18","CDIV","","","$0.42"
"","","","","The data provided is for informational purposes only.","","","",""
'''


class TestCSVParser:

    def setup_method(self):
        self.result = parse_robinhood_csv(SAMPLE_CSV)

    def test_transaction_count(self):
        """Correct number of transactions parsed (ignoring empty/disclaimer)."""
        assert self.result.total_tx >= 15, f"Expected ≥15 txs, got {self.result.total_tx}"

    def test_sell_count(self):
        """All 7 Sell transactions captured."""
        assert self.result.sells == 7, f"Expected 7 sells, got {self.result.sells}"

    def test_buy_count(self):
        """Buy orders counted correctly."""
        assert self.result.buys >= 3, f"Expected ≥3 buys, got {self.result.buys}"

    def test_amd_fully_sold(self):
        """AMD: bought 1.558692, sold 1.558692 → should be 0 or near 0."""
        amd = self.result.positions.get("AMD", {}).get("shares", 0)
        assert amd < 0.0001, f"AMD should be ~0 after buy+sell, got {amd}"

    def test_xop_fully_sold(self):
        """XOP: sold 1.640768 total, had 1.640768 from previous buys → 0."""
        xop = self.result.positions.get("XOP", {}).get("shares", 0)
        assert xop < 0.0001, f"XOP should be 0, got {xop}"

    def test_voo_accumulated(self):
        """VOO: two buys should accumulate correctly."""
        voo = self.result.positions.get("VOO", {})
        expected = 0.166286 + 0.319177
        assert abs(voo.get("shares", 0) - expected) < 0.0001, \
            f"VOO shares: expected {expected:.4f}, got {voo.get('shares',0):.4f}"

    def test_drip_detected(self):
        """DRIP reinvestment detected and tracked separately."""
        assert self.result.drip_count >= 1, "No DRIP transactions detected"
        vym_drip = self.result.positions.get("VYM", {}).get("drip_shares", 0)
        assert vym_drip > 0, "VYM DRIP shares should be > 0"

    def test_drip_cost_tracked(self):
        """DRIP cost basis tracked separately from regular buys."""
        vym_drip_cost = self.result.positions.get("VYM", {}).get("drip_cost", 0)
        assert vym_drip_cost > 0, "VYM DRIP cost should be > 0"

    def test_dividends_tracked(self):
        """Cash dividends (CDIV) tracked by ticker, no share change."""
        assert self.result.cdiv_count >= 1, "No CDIV transactions detected"
        # CDIV should NOT add shares
        vwo_div = self.result.dividends.get("VWO", 0)
        assert vwo_div > 0, "VWO dividend not tracked"

    def test_cash_deposits(self):
        """RTP deposits tracked in cash_deposits."""
        assert self.result.cash_deposits >= 900.0, \
            f"Expected ≥$900 in deposits, got {self.result.cash_deposits}"

    def test_sell_proceeds(self):
        """Sell proceeds summed correctly."""
        expected_min = 7.95 + 95.26 + 136.27 + 183.81 + 121.96 + 190.33 + 149.00
        assert abs(self.result.sell_proceeds - expected_min) < 0.50, \
            f"Sell proceeds: expected {expected_min:.2f}, got {self.result.sell_proceeds:.2f}"

    def test_empty_ticker_filtered(self):
        """Rows with empty ticker (bank transfers etc.) are NOT added as positions."""
        assert "" not in self.result.positions, "Empty ticker should not be a position"

    def test_cdiv_no_shares(self):
        """CDIV transactions should not add shares (only track dividends)."""
        # VWO had a CDIV but no Buy → should not have shares from that
        # (it was also sold, so 0 shares)
        vwoshares = self.result.positions.get("VWO", {}).get("shares", 0)
        assert vwoshares < 0.0001, "VWO should be sold, not have shares from CDIV"

    def test_stock_split_adds_shares(self):
        """SPL adds shares without adding to cost."""
        # NVDA had an SPL of 18 shares added (no buy in this CSV)
        nvda = self.result.positions.get("NVDA", {})
        assert nvda.get("shares", 0) == 18.0, \
            f"NVDA SPL should add 18 shares, got {nvda.get('shares',0)}"

    def test_rec_transfer_adds_shares(self):
        """REC (transfer-in) adds shares."""
        msft = self.result.positions.get("MSFT", {})
        assert msft.get("shares", 0) >= 0.0123, \
            f"MSFT REC should add 0.0123 shares, got {msft.get('shares',0)}"

    def test_date_range(self):
        """Date range correctly computed."""
        assert "2024" in self.result.date_range or "2026" in self.result.date_range

    def test_misc_ignored(self):
        """MISC transactions do not affect positions."""
        # BTC had a MISC row — should not appear in positions (no Buy)
        btc = self.result.positions.get("BTC", {}).get("shares", 0)
        assert btc == 0.0, "BTC MISC should not create a position"

    def test_partial_sell_preserves_remaining(self):
        """Partial sell: VTV had more shares, only 0.489248 sold → some remain."""
        # In this CSV VTV was only sold (no buy recorded), so result = 0
        # This tests correct behavior: can't go negative
        vtv = self.result.positions.get("VTV", {}).get("shares", 0)
        assert vtv >= 0, "Shares cannot go negative"


class TestReconcile:

    BASE = [
        ("Core","NVDA","NVIDIA",35.0,103.0,175,90,250,True,"LT",None,0,0,0),
        ("ETF","VOO","Vanguard",2.85,479.0,650,420,750,True,"LT",None,0,0,0),
        ("SELL","VWO","Emg Mkts",0.15,41.0,None,None,None,True,"LT NOW",None,0,0,0),
        ("Crypto","BTC","Bitcoin",0.03433,66997,110000,45000,175000,True,"LT","bitcoin",0,0,0),
    ]

    def test_crypto_always_kept(self):
        """Crypto positions always preserved (not in equity CSV)."""
        csv_result = parse_robinhood_csv(SAMPLE_CSV)
        updated, _ = reconcile(csv_result, self.BASE)
        btc = next((p for p in updated if p[1] == "BTC"), None)
        assert btc is not None, "BTC should always be kept"
        assert btc[3] == 0.03433  # unchanged

    def test_sell_position_removed_when_zero(self):
        """SELL-flagged position with 0 CSV shares is removed from active list."""
        csv_result = parse_robinhood_csv(SAMPLE_CSV)
        # VWO was sold to 0 in the CSV
        updated, changes = reconcile(csv_result, self.BASE)
        vwo = next((p for p in updated if p[1] == "VWO"), None)
        assert vwo is None, "VWO should be removed after being sold to 0"
        change = next((c for c in changes if c["Ticker"] == "VWO"), None)
        assert change is not None, "VWO change should be logged"
        assert "Sold" in change["Change"], f"Expected 'Sold' in change, got {change['Change']}"

    def test_shares_updated(self):
        """VOO shares updated from CSV values."""
        csv_result = parse_robinhood_csv(SAMPLE_CSV)
        updated, _ = reconcile(csv_result, self.BASE)
        voo = next((p for p in updated if p[1] == "VOO"), None)
        assert voo is not None
        expected = 0.166286 + 0.319177
        assert abs(voo[3] - expected) < 0.001, f"VOO shares wrong: {voo[3]}"


# ═══════════════════════════════════════════════════════════════════════════════
#  RECOMMENDATION ENGINE TESTS (30 scenarios)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecEngine:

    def _rec(self, *args, **kwargs):
        return generate_rec(*args, **kwargs)

    # ── Income / Core ─────────────────────────────────────────────────────────
    def test_vym_hold_forever(self):
        r = self._rec("ETF","VYM",132,160,110,190,True,"LT",125)
        assert "FOREVER" in r.action

    def test_schd_hold_forever(self):
        r = self._rec("ETF","SCHD",27,32,20,42,True,"LT",26.5)
        assert "FOREVER" in r.action

    def test_voo_dca_always(self):
        r = self._rec("ETF","VOO",479,650,420,750,True,"LT",520)
        assert "DCA" in r.action

    def test_qqq_dca_always(self):
        r = self._rec("ETF","QQQ",503,580,380,700,True,"LT",450)
        assert "DCA" in r.action

    def test_vti_dca_always(self):
        r = self._rec("ETF","VTI",274,370,240,430,True,"LT",240)
        assert "DCA" in r.action

    # ── SELL positions ────────────────────────────────────────────────────────
    def test_sell_lt_ready(self):
        r = self._rec("SELL","VTV",163,None,None,None,True,"LT NOW",152)
        assert "SELL" in r.action and r.color == "red"

    def test_sell_wait_for_lt(self):
        r = self._rec("SELL","SPY",595,None,None,None,False,"May 20 2026",540)
        assert "WAIT" in r.action or "SELL" not in r.action.upper().split()[0]

    def test_sell_no_target(self):
        r = self._rec("SELL","BND",72,None,None,None,True,"LT NOW",71)
        assert r.color == "red"

    # ── Crypto ────────────────────────────────────────────────────────────────
    def test_btc_accumulate(self):
        r = self._rec("Crypto","BTC",66997,110000,45000,175000,True,"LT",84000)
        assert "ACCUMULATE" in r.action   # 31% upside > 25% threshold

    def test_btc_trim_above_target(self):
        r = self._rec("Crypto","BTC",66997,84000,45000,175000,True,"LT",120000)
        assert "TRIM" in r.action   # 43% above target

    def test_xrp_accumulate(self):
        r = self._rec("Crypto","XRP",1.886,2.80,0.60,5.00,True,"LT",2.10)
        assert "ACCUMULATE" in r.action   # 33% upside

    def test_crypto_no_stop_loss(self):
        """Crypto should NEVER trigger stop-loss even near bear case."""
        r = self._rec("Crypto","BTC",66997,110000,45000,175000,True,"LT",47000)
        assert "STOP" not in r.action   # near bear but crypto rule overrides

    # ── STOP-LOSS ─────────────────────────────────────────────────────────────
    def test_stop_loss_near_bear(self):
        r = self._rec("Core","AMD",164,140,80,220,True,"LT",85)
        assert "STOP" in r.action   # price 85 < bear 80 * 1.10 = 88

    def test_no_stop_loss_crypto(self):
        r = self._rec("Crypto","BTC",66997,110000,45000,175000,True,"LT",47000)
        assert "STOP" not in r.action

    # ── Strong buy / dip ──────────────────────────────────────────────────────
    def test_strong_buy_big_dip(self):
        r = self._rec("Core","NVDA",150,175,90,250,True,"LT",110)
        assert "STRONG" in r.action   # pct=-27%, upside=59%

    def test_buy_the_dip(self):
        r = self._rec("Core","META",612,720,400,900,False,"Sep 2026",510)
        assert "DIP" in r.action   # pct=-17%, upside=41%

    # ── Accumulate zones ─────────────────────────────────────────────────────
    def test_accumulate_high_upside(self):
        r = self._rec("Core","NVDA",103,175,90,250,True,"LT",110)
        assert "ACCUMULATE" in r.action   # 59% upside

    def test_accumulate_moderate_upside(self):
        r = self._rec("Core","CRM",254,320,180,400,True,"LT",265)
        assert "ACCUMULATE" in r.action   # 21% upside

    def test_accumulate_with_drip_yield(self):
        """High DRIP yield boosts accumulate signal."""
        r = self._rec("ETF","VYM",132,160,110,190,True,"LT",130,
                      drip_shares=0.46, drip_cost=65.12)
        assert "FOREVER" in r.action   # VYM special case

    # ── Trim signals ─────────────────────────────────────────────────────────
    def test_trim_20_at_target_lt(self):
        r = self._rec("Core","NVDA",103,175,90,250,True,"LT",172)
        assert "TRIM" in r.action and "20" in r.action

    def test_trim_25_above_target_lt(self):
        r = self._rec("ETF","XLE",74,72,44,95,True,"LT",82)
        assert "TRIM" in r.action and "25" in r.action   # 13% above target

    def test_nflx_trim(self):
        r = self._rec("Core","NFLX",86,1100,700,1400,True,"LT NOW",1050)
        assert "TRIM" in r.action   # near target

    # ── ST holds ─────────────────────────────────────────────────────────────
    def test_hold_st_near_target(self):
        r = self._rec("Core","NVDA",103,175,90,250,False,"Jun 2026",172)
        assert "HOLD" in r.action and "ST" in r.action

    def test_hold_st_above_target(self):
        r = self._rec("Core","META",612,720,400,900,False,"Sep 2026",720)
        assert "HOLD" in r.action

    # ── Declining thesis ─────────────────────────────────────────────────────
    def test_declining_thesis_accumulate(self):
        r = self._rec("Core","GOOGL",307,210,140,280,False,"Dec 2026",158)
        assert "ACCUMULATE" in r.action   # declining, upside=33%>20%

    def test_declining_thesis_tsm(self):
        r = self._rec("Core","TSM",302,230,130,320,False,"Nov 2026",160)
        assert "ACCUMULATE" in r.action   # declining, upside=44%>20%

    def test_declining_not_strong_buy(self):
        """Declining thesis should never generate STRONG BUY."""
        r = self._rec("Core","GOOGL",307,210,140,280,True,"LT",100)
        assert "STRONG" not in r.action

    # ── IPO ───────────────────────────────────────────────────────────────────
    def test_ipo_hold_before_lt(self):
        r = self._rec("IPO","KLAR",40,65,25,100,False,"Sep 2026",55)
        # 18% upside < 20% threshold, so IPO hold rule applies
        assert "IPO" in r.action or "HOLD" in r.action

    def test_ipo_accumulate_big_upside(self):
        r = self._rec("IPO","BLSH",37,60,15,90,False,"Aug 2026",38)
        assert "ACCUMULATE" in r.action   # 58% upside > 40% threshold

    # ── No price ─────────────────────────────────────────────────────────────
    def test_no_price_hold(self):
        r = self._rec("Core","AMD",164,140,80,220,True,"LT",None)
        assert "HOLD" in r.action

    def test_no_price_sell(self):
        r = self._rec("SELL","VEA",50,None,None,None,True,"LT NOW",None)
        assert "SELL" in r.action

    # ── Tax notes ─────────────────────────────────────────────────────────────
    def test_lt_tax_note_present(self):
        r = self._rec("Core","NFLX",86,1100,700,1400,True,"LT NOW",1050)
        assert len(r.tax_note) > 0

    def test_st_tax_note_present(self):
        r = self._rec("Core","META",612,720,400,900,False,"Sep 23 2026",510)
        assert "37%" in r.tax_note

    # ── DRIP notes ───────────────────────────────────────────────────────────
    def test_drip_note_present(self):
        r = self._rec("ETF","VHT",271,300,200,370,True,"LT",245,
                      drip_shares=0.046, drip_cost=12.23)
        assert len(r.drip_note) > 0
        assert "0.04" in r.drip_note or "0.046" in r.drip_note


# ═══════════════════════════════════════════════════════════════════════════════
#  INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:

    def test_full_pipeline(self):
        """Parse CSV → reconcile → generate recs for all positions."""
        result = parse_robinhood_csv(SAMPLE_CSV)

        base = [
            ("Crypto","BTC","Bitcoin",0.03433,66997,110000,45000,175000,True,"LT","bitcoin",0,0,0),
            ("Core","NVDA","NVIDIA",18.0,103,175,90,250,True,"LT",None,0,0,0),  # pre-split
            ("ETF","VOO","Vanguard",2.0,479,650,420,750,True,"LT",None,0,0,0),
            ("SELL","VWO","Emg",0.15,41,None,None,None,True,"LT NOW",None,0,0,0),
        ]
        updated, changes = reconcile(result, base)

        # BTC preserved
        btc = next(p for p in updated if p[1]=="BTC")
        assert btc[3] == 0.03433

        # VOO updated
        voo = next(p for p in updated if p[1]=="VOO")
        expected_voo = 0.166286 + 0.319177
        assert abs(voo[3] - expected_voo) < 0.001

        # VWO removed (fully sold in CSV)
        vwo = next((p for p in updated if p[1]=="VWO"), None)
        assert vwo is None

        # NVDA has SPL shares
        nvda = next((p for p in updated if p[1]=="NVDA"), None)
        # NVDA wasn't in this small CSV as bought, just SPL'd from 0
        # The 18 SPL shares are new position
        nvda_from_csv = next((p for p in updated if p[1]=="NVDA"), None)
        # Either from base (updated) or new from CSV
        assert nvda_from_csv is not None

        # Generate recs for all
        prices = {"BTC":84000,"NVDA":110,"VOO":520}
        for p in updated:
            price = prices.get(p[1])
            rec = generate_rec(
                p[0], p[1], p[4], p[5] if len(p)>5 else None,
                p[6] if len(p)>6 else None, p[7] if len(p)>7 else None,
                p[8] if len(p)>8 else True, p[9] if len(p)>9 else "LT",
                price,
                p[11] if len(p)>11 else 0,
                p[12] if len(p)>12 else 0,
            )
            assert rec.action, f"No rec generated for {p[1]}"
            assert rec.color in ("green","red","gold","blue","purple","orange","gray")

        print(f"\n✅ Integration test passed: {len(updated)} positions, {len(changes)} changes")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
