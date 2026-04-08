import os, sys, time, asyncio, unittest, json, tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta

for k,v in {"PLAID_CLIENT_ID":"c","PLAID_SECRET":"s","PLAID_ENV":"sandbox","PLAID_ACCESS_TOKEN":"t","FINNHUB_API_KEY":"f","POLYGON_API_KEY":"p"}.items():
    os.environ.setdefault(k,v)
sys.path.insert(0,"/home/claude/plaid_mock"); sys.path.insert(0,"/home/claude")

from holdings_manager import HoldingsManager, HoldingsCache, CachedHolding
from price_service import PriceService, PriceResult
from portfolio_aggregator import PortfolioAggregator, PortfolioSnapshot

def _h(ticker="AAPL",qty=10.0,cost=150.0,inst=175.0):
    return CachedHolding(ticker=ticker,quantity=qty,cost_basis=cost,
                         institution_price=inst,security_type="equity",
                         name=ticker,account_id="acc1")

def _c(holdings,cash=0.0,age=1.0):
    ts=(datetime.now(tz=timezone.utc)-timedelta(hours=age)).isoformat()
    return HoldingsCache(last_synced=ts,account_ids=["acc1"],cash_usd=cash,holdings=holdings)

def _p(ticker,last):
    return PriceResult(ticker=ticker,mid_price=last,bid=last-0.1,ask=last+0.1,
                       last_trade=last,source="finnhub",timestamp=time.time())

def _agg(cache, prices):
    mm=MagicMock(); mm.needs_plaid_sync.return_value=(False,"fresh"); mm.get_holdings.return_value=cache
    ms=MagicMock(); ms.fetch_prices.return_value=prices
    return PortfolioAggregator(holdings_manager=mm,price_service=ms)


class T1_CacheDataclass(unittest.TestCase):
    def test_age_fresh(self):
        c=_c([],age=2.0); self.assertAlmostEqual(c.age_hours,2.0,delta=0.1)
    def test_not_stale_young(self):
        self.assertFalse(_c([],age=1.0).is_stale)
    def test_stale_after_24h(self):
        self.assertTrue(_c([],age=25.0).is_stale)
    def test_tickers(self):
        c=_c([_h("NVDA"),_h("VOO")]); self.assertIn("NVDA",c.tickers); self.assertIn("VOO",c.tickers)
    def test_round_trip(self):
        c=_c([_h("META",qty=2.0)]); r=HoldingsCache.from_dict(c.to_dict())
        self.assertEqual(r.holdings[0].ticker,"META"); self.assertAlmostEqual(r.holdings[0].quantity,2.0)

class T2_HoldingsManager(unittest.TestCase):
    def _path(self): return Path(tempfile.mktemp(suffix=".json"))
    def test_missing_file_triggers_sync(self):
        p=self._path(); m=HoldingsManager(cache_path=p)
        ok,reason=m.needs_plaid_sync(); self.assertTrue(ok); self.assertIn("not found",reason)
    def test_fresh_cache_no_sync(self):
        p=self._path(); c=_c([_h()],age=2.0)
        p.write_text(json.dumps(c.to_dict()))
        ok,r=HoldingsManager(cache_path=p).needs_plaid_sync()
        self.assertFalse(ok); self.assertIn("fresh",r); p.unlink()
    def test_stale_triggers_sync(self):
        p=self._path(); c=_c([_h()],age=25.0)
        p.write_text(json.dumps(c.to_dict()))
        ok,_=HoldingsManager(cache_path=p).needs_plaid_sync()
        self.assertTrue(ok); p.unlink()
    def test_force_refresh(self):
        p=self._path(); c=_c([_h()],age=1.0)
        p.write_text(json.dumps(c.to_dict()))
        ok,r=HoldingsManager(cache_path=p).needs_plaid_sync(force_refresh=True)
        self.assertTrue(ok); self.assertIn("force",r); p.unlink()
    def test_status_fresh(self):
        p=self._path(); c=_c([_h("A"),_h("B")],age=3.0)
        p.write_text(json.dumps(c.to_dict()))
        s=HoldingsManager(cache_path=p).get_cache_status()
        self.assertEqual(s["status"],"fresh"); self.assertEqual(s["holdings_count"],2); p.unlink()
    def test_stale_plaid_fallback(self):
        p=self._path(); c=_c([_h()],age=25.0)
        p.write_text(json.dumps(c.to_dict()))
        mp=MagicMock(); mp.get_holdings.side_effect=Exception("Plaid down")
        m=HoldingsManager(cache_path=p,plaid_client=mp)
        r=m.get_holdings()
        self.assertEqual(len(r.holdings),1); p.unlink()
    def test_no_cache_and_plaid_fails_raises(self):
        p=Path(tempfile.mktemp(suffix=".json"))
        mp=MagicMock(); mp.get_holdings.side_effect=Exception("down")
        with self.assertRaises(RuntimeError):
            HoldingsManager(cache_path=p,plaid_client=mp).get_holdings()
    def test_delete_cache(self):
        p=self._path(); c=_c([_h()])
        p.write_text(json.dumps(c.to_dict()))
        self.assertTrue(HoldingsManager(cache_path=p).delete_cache_file())
        self.assertFalse(p.exists())

class T3_PriceService(unittest.TestCase):
    def setUp(self): self.svc=PriceService()
    def test_mid_price(self):
        pr=_p("NVDA",875.0); self.assertAlmostEqual(pr.mid_price,875.0,places=1)
    def test_is_stale_institution(self):
        pr=PriceResult("X",10.0,None,None,10.0,"institution",time.time())
        self.assertTrue(pr.is_stale)
    def test_is_stale_cache(self):
        pr=PriceResult("X",10.0,None,None,10.0,"cache(finnhub)",time.time())
        self.assertTrue(pr.is_stale)
    def test_not_stale_live(self):
        pr=_p("AAPL",180.0); self.assertFalse(pr.is_stale)
    def test_institution_fallback(self):
        h=_h("STUB",inst=22.50); c=_c([h])
        r=self.svc._fallback("STUB","err",c)
        self.assertAlmostEqual(r.mid_price,22.50,places=2); self.assertEqual(r.source,"institution")
    def test_memory_cache_wins_over_institution(self):
        h=_h("META",inst=400.0); c=_c([h])
        self.svc._memory_cache["META"]=_p("META",550.0)
        r=self.svc._fallback("META","err",c)
        self.assertAlmostEqual(r.mid_price,550.0,places=1); self.assertIn("cache",r.source)
    def test_normalise(self):
        from price_service import PriceService as PS
        self.assertEqual(PS._normalise("BRK.B"),"BRK-B")
        self.assertEqual(PS._normalise("AAPL"),"AAPL")

class T4_Aggregator(unittest.TestCase):
    def test_market_value(self):
        a=_agg(_c([_h("NVDA",5.0,700.0)]),{"NVDA":_p("NVDA",875.0)})
        s=a.calculate_total_value()
        self.assertAlmostEqual(s.positions[0].market_value,5.0*875.0,places=2)
    def test_equity_buckets(self):
        a=_agg(_c([_h("AAPL",10.0,150.0),_h("BTC",0.03,52000.0)],cash=500.0),
               {"AAPL":_p("AAPL",180.0),"BTC":_p("BTC",95000.0)})
        s=a.calculate_total_value()
        self.assertAlmostEqual(s.total_equity,s.stocks_equity+s.crypto_equity+s.cash_usd,places=4)
    def test_plaid_not_triggered(self):
        a=_agg(_c([_h("VOO",2.0,400.0)]),{"VOO":_p("VOO",500.0)})
        self.assertFalse(a.calculate_total_value().plaid_sync_triggered)
    def test_plaid_triggered_stale(self):
        c=_c([_h()],age=25.0)
        mm=MagicMock(); mm.needs_plaid_sync.return_value=(True,"25h old"); mm.get_holdings.return_value=c
        ms=MagicMock(); ms.fetch_prices.return_value={"AAPL":_p("AAPL",180.0)}
        s=PortfolioAggregator(holdings_manager=mm,price_service=ms).calculate_total_value()
        self.assertTrue(s.plaid_sync_triggered)
    def test_cache_age_propagated(self):
        a=_agg(_c([_h("META",1.0,450.0)],age=5.5),{"META":_p("META",550.0)})
        self.assertAlmostEqual(a.calculate_total_value().holdings_cache_age_h,5.5,delta=0.1)
    def test_institution_fallback_on_failed_price(self):
        h=_h("KLAR",10.0,28.0,inst=32.0); c=_c([h])
        mm=MagicMock(); mm.needs_plaid_sync.return_value=(False,"fresh"); mm.get_holdings.return_value=c
        ms=MagicMock(); ms.fetch_prices.return_value={"KLAR":PriceResult("KLAR",0.0,None,None,0.0,"none",time.time(),"fail")}
        s=PortfolioAggregator(holdings_manager=mm,price_service=ms).calculate_total_value()
        self.assertAlmostEqual(s.positions[0].mid_price,32.0,places=2)
        self.assertEqual(s.positions[0].price_source,"institution_fallback")
    def test_cash_only_on_empty_holdings(self):
        c=HoldingsCache(last_synced=datetime.now(tz=timezone.utc).isoformat(),account_ids=[],cash_usd=1042.17,holdings=[])
        mm=MagicMock(); mm.needs_plaid_sync.return_value=(False,"fresh"); mm.get_holdings.return_value=c
        s=PortfolioAggregator(holdings_manager=mm,price_service=MagicMock()).calculate_total_value()
        self.assertEqual(s.positions_count,0); self.assertAlmostEqual(s.total_equity,1042.17,places=2)
    def test_sync_alias(self):
        c=_c([_h("QQQ",1.0,430.0)])
        mm=MagicMock(); mm.needs_plaid_sync.return_value=(False,"fresh"); mm.get_holdings.return_value=c
        ms=MagicMock(); ms.fetch_prices.return_value={"QQQ":_p("QQQ",450.0)}
        s=PortfolioAggregator(holdings_manager=mm,price_service=ms).sync_portfolio_total()
        self.assertIsInstance(s,PortfolioSnapshot); self.assertAlmostEqual(s.total_equity,450.0,places=2)

class T5_Async(unittest.IsolatedAsyncioTestCase):
    async def test_async_snapshot(self):
        c=_c([_h("QQQ",1.0,430.0)],cash=100.0)
        mm=MagicMock(); mm.needs_plaid_sync.return_value=(False,"fresh"); mm.get_holdings.return_value=c
        ms=MagicMock(); ms.fetch_prices_async=AsyncMock(return_value={"QQQ":_p("QQQ",450.0)})
        s=await PortfolioAggregator(holdings_manager=mm,price_service=ms).calculate_total_value_async()
        self.assertIsInstance(s,PortfolioSnapshot); self.assertAlmostEqual(s.total_equity,550.0,places=2)

if __name__=="__main__":
    unittest.main(verbosity=2)
