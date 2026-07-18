"""Tests for sync router helpers — PDF parser and CSV import integration."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from app.routers.sync import _parse_crypto_pdf


# ── PDF Parser ───────────────────────────────────────────────────────────────


class TestParseCryptoPdf:
    """Tests for _parse_crypto_pdf — Robinhood Crypto statement parser."""

    def _make_pdf_bytes(self, text: str) -> bytes:
        """Return fake PDF bytes that pdfplumber will intercept via mock."""
        return text.encode("utf-8")

    def _parse_with_text(self, text: str) -> dict:
        """Patch pdfplumber to return fixed text, then call _parse_crypto_pdf."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        with patch("pdfplumber.open", return_value=mock_pdf):
            return _parse_crypto_pdf(b"fake-pdf-bytes")

    # Primary pattern tests (Name  qty  TICKER  $value  pct)

    def test_primary_bitcoin(self):
        text = "Bitcoin  0.03432981  BTC  $2301.45  99.94%"
        result = self._parse_with_text(text)
        assert "BTC" in result
        assert abs(result["BTC"]["shares"] - 0.03432981) < 1e-6
        assert result["BTC"]["avg_cost"] == 0.0

    def test_primary_xrp(self):
        text = "XRP  1.066  XRP  $1.47  0.06%"
        result = self._parse_with_text(text)
        assert "XRP" in result
        assert abs(result["XRP"]["shares"] - 1.066) < 1e-6

    def test_primary_multiple_coins(self):
        text = (
            "Bitcoin  0.03432981  BTC  $2301.45  85.00%\n"
            "Ethereum  0.50000000  ETH  $1500.00  14.00%\n"
            "XRP  1.066  XRP  $1.47  0.06%\n"
            "Solana  2.00000000  SOL  $250.00  0.94%\n"
        )
        result = self._parse_with_text(text)
        assert set(result.keys()) == {"BTC", "ETH", "XRP", "SOL"}
        assert abs(result["ETH"]["shares"] - 0.5) < 1e-6

    def test_primary_case_insensitive_coin_name(self):
        text = "bitcoin  0.03432981  BTC  $2301.45  99.94%"
        result = self._parse_with_text(text)
        assert "BTC" in result

    def test_primary_dogecoin(self):
        text = "Dogecoin  150.0000  DOGE  $14.25  0.50%"
        result = self._parse_with_text(text)
        assert "DOGE" in result
        assert abs(result["DOGE"]["shares"] - 150.0) < 1e-6

    def test_primary_avalanche(self):
        text = "Avalanche  5.12345678  AVAX  $150.00  5.00%"
        result = self._parse_with_text(text)
        assert "AVAX" in result

    def test_primary_ignores_zero_quantity(self):
        text = "Bitcoin  0.0  BTC  $0.00  0%"
        result = self._parse_with_text(text)
        # qty=0 → skip
        assert "BTC" not in result

    def test_primary_comma_in_dollar_value(self):
        text = "Bitcoin  1.50000000  BTC  $95,000.00  100%"
        result = self._parse_with_text(text)
        assert "BTC" in result
        assert abs(result["BTC"]["shares"] - 1.5) < 1e-6

    # Fallback pattern tests (qty  TICKER)

    def test_fallback_4_decimal_places(self):
        """Fallback triggers when primary finds nothing — needs ≥4 decimal places."""
        text = "Your crypto holdings include 0.03432981 BTC and 1.50000000 ETH"
        result = self._parse_with_text(text)
        assert "BTC" in result
        assert "ETH" in result

    def test_fallback_not_triggered_when_primary_succeeds(self):
        """If primary pattern finds results, fallback must not double-add."""
        text = (
            "Bitcoin  0.03432981  BTC  $2301.45  99.94%\n"
            "Also: 0.99990000 BTC somewhere else\n"
        )
        result = self._parse_with_text(text)
        # Only one BTC entry — from primary
        assert "BTC" in result
        # Shares come from the primary match (0.03432981)
        assert abs(result["BTC"]["shares"] - 0.03432981) < 1e-6

    def test_fallback_requires_4_decimals(self):
        """Fewer than 4 decimal places → not matched by fallback."""
        text = "0.034 BTC holdings"  # only 3 decimal places
        result = self._parse_with_text(text)
        assert "BTC" not in result

    # Edge / error cases

    def test_empty_pdf_text(self):
        result = self._parse_with_text("")
        assert result == {}

    def test_unrelated_text(self):
        result = self._parse_with_text("Monthly Statement — Equities Only\nNVDA AAPL MSFT")
        assert result == {}

    def test_pdfplumber_open_error(self):
        """If pdfplumber.open raises, return empty dict — no crash."""
        with patch("pdfplumber.open", side_effect=Exception("corrupt pdf")):
            result = _parse_crypto_pdf(b"fake")
        assert result == {}

    def test_all_supported_tickers(self):
        """Verify every supported coin can be parsed via the primary pattern."""
        coins = [
            ("Bitcoin", "BTC"),
            ("Ethereum", "ETH"),
            ("XRP", "XRP"),
            ("Solana", "SOL"),
            ("Dogecoin", "DOGE"),
            ("Cardano", "ADA"),
            ("Litecoin", "LTC"),
            ("Avalanche", "AVAX"),
        ]
        for name, ticker in coins:
            text = f"{name}  1.00000000  {ticker}  $100.00  10%"
            result = self._parse_with_text(text)
            assert ticker in result, f"Expected {ticker} from '{name}' to be parsed"

    def test_realistic_robinhood_statement_excerpt(self):
        """Simulate realistic multi-line text extracted from a Robinhood PDF."""
        text = textwrap.dedent("""\
            Robinhood Crypto Monthly Statement
            April 2026

            Cryptocurrency Holdings
            Asset          Quantity      Symbol    Market Value   Portfolio %
            Bitcoin        0.03432981    BTC       $3,247.12      82.15%
            XRP            1.06600000    XRP       $1.54          0.04%
            Ethereum       0.15000000    ETH       $703.50        17.81%

            Total Crypto Value: $3,952.16
        """)
        result = self._parse_with_text(text)
        assert "BTC" in result
        assert "XRP" in result
        assert "ETH" in result
        assert abs(result["BTC"]["shares"] - 0.03432981) < 1e-6
        assert abs(result["XRP"]["shares"] - 1.066) < 1e-6
        assert abs(result["ETH"]["shares"] - 0.15) < 1e-6


# ── CSV Import Integration ────────────────────────────────────────────────────


class TestCsvImportService:
    """Integration-level tests for CsvImportService with a mock Supabase client."""

    SAMPLE_CSV = textwrap.dedent("""\
        Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount
        4/2/2026,4/2/2026,4/4/2026,NVDA,NVIDIA Corporation,Buy,10,116.02,-1160.20
        4/2/2026,4/2/2026,4/2/2026,,Account Funding,ACH,,,900.00
        3/15/2026,3/15/2026,3/17/2026,VYM,Vanguard High Dividend,CDIV,,,$45.32
        4/1/2026,4/1/2026,4/3/2026,NVDA,NVIDIA Corporation,Buy,5,115.00,-575.00
    """)

    def _make_client(self, existing_fingerprints: list[str] | None = None):
        """Build a minimal mock Supabase client."""
        existing_fingerprints = existing_fingerprints or []
        client = MagicMock()

        # .table("transactions").select("fingerprint").eq(...).execute()
        fp_response = MagicMock()
        fp_response.data = [{"fingerprint": fp} for fp in existing_fingerprints]

        # .table("transactions").insert(...).execute()
        insert_response = MagicMock()
        insert_response.data = []

        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.insert.return_value = table_mock
        table_mock.eq.return_value = table_mock
        table_mock.execute.return_value = fp_response  # default → fingerprints

        client.table.return_value = table_mock
        return client, table_mock

    @pytest.mark.asyncio
    async def test_import_basic(self):
        from uuid import uuid4
        from app.services.import_service import CsvImportService

        client, table_mock = self._make_client()
        # After fingerprint query, inserts should succeed
        insert_exec = MagicMock()
        insert_exec.data = []
        table_mock.insert.return_value.execute.return_value = insert_exec

        svc = CsvImportService(user_id=uuid4(), supabase_client=client)
        result = await svc.import_robinhood_csv(self.SAMPLE_CSV)

        assert result["total_rows"] == 4
        assert result["new_rows"] == 4
        assert result["duplicates_skipped"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_import_deduplication(self):
        """Re-importing the same CSV skips all rows as duplicates."""
        from uuid import uuid4
        from app.services.import_service import CsvImportService, make_fingerprint

        # Pre-compute fingerprints matching exactly what the importer will produce.
        # NVDA BUY: ticker present, qty != 0 → stock canonical
        # ACH:  ticker empty → cash canonical (amount=900.00, settle=4/2/2026)
        # CDIV: ticker=VYM but qty=0 → cash canonical (amount=$45.32, settle=3/17/2026)
        # NVDA BUY 2: stock canonical
        existing = [
            make_fingerprint("4/2/2026", "NVDA", "Buy", "10", "116.02"),
            make_fingerprint("4/2/2026", "", "ACH", "", "", amount="900.00", settle="4/2/2026"),
            make_fingerprint("3/15/2026", "VYM", "CDIV", "", "", amount="$45.32", settle="3/17/2026"),
            make_fingerprint("4/1/2026", "NVDA", "Buy", "5", "115.00"),
        ]

        client, _ = self._make_client(existing_fingerprints=existing)
        svc = CsvImportService(user_id=uuid4(), supabase_client=client)
        result = await svc.import_robinhood_csv(self.SAMPLE_CSV)

        assert result["total_rows"] == 4
        assert result["new_rows"] == 0
        assert result["duplicates_skipped"] == 4
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_import_empty_csv(self):
        from uuid import uuid4
        from app.services.import_service import CsvImportService

        client, _ = self._make_client()
        svc = CsvImportService(user_id=uuid4(), supabase_client=client)
        result = await svc.import_robinhood_csv("Activity Date,Instrument,Trans Code\n")

        assert result["total_rows"] == 0
        assert result["new_rows"] == 0

    @pytest.mark.asyncio
    async def test_import_partial_dedup(self):
        """Only 1 of 4 rows already in DB — 3 new rows inserted."""
        from uuid import uuid4
        from app.services.import_service import CsvImportService, make_fingerprint

        existing = [make_fingerprint("4/2/2026", "NVDA", "Buy", "10", "116.02")]

        # Build a smarter mock: fingerprint query returns existing, inserts succeed
        client = MagicMock()

        fp_response = MagicMock()
        fp_response.data = [{"fingerprint": fp} for fp in existing]

        insert_response = MagicMock()
        insert_response.data = []

        call_count = {"n": 0}

        def make_table(_name):
            tbl = MagicMock()
            tbl.select.return_value = tbl
            tbl.eq.return_value = tbl
            tbl.insert.return_value = tbl

            def execute():
                call_count["n"] += 1
                # First execute call is the fingerprint SELECT
                if call_count["n"] == 1:
                    return fp_response
                return insert_response

            tbl.execute.side_effect = execute
            return tbl

        client.table.side_effect = make_table

        svc = CsvImportService(user_id=uuid4(), supabase_client=client)
        result = await svc.import_robinhood_csv(self.SAMPLE_CSV)

        assert result["total_rows"] == 4
        assert result["duplicates_skipped"] == 1
        assert result["new_rows"] == 3

    @pytest.mark.asyncio
    async def test_import_maps_transaction_codes(self):
        """Buy/CDIV/ACH etc. are mapped to correct tx_type values."""
        from uuid import uuid4
        from app.services.import_service import CsvImportService

        client, table_mock = self._make_client()
        inserted_rows = []

        def capture_insert(rows):
            inserted_rows.extend(rows)
            return table_mock

        table_mock.insert.side_effect = capture_insert
        insert_exec = MagicMock()
        insert_exec.data = []
        table_mock.execute.return_value = insert_exec

        svc = CsvImportService(user_id=uuid4(), supabase_client=client)
        await svc.import_robinhood_csv(self.SAMPLE_CSV)

        tx_types = {r["ticker"]: r["tx_type"] for r in inserted_rows if r.get("ticker")}
        assert tx_types.get("NVDA") == "Buy"
        assert tx_types.get("VYM") == "CDIV"



# ── CORS Configuration ────────────────────────────────────────────────────────


class TestCorsConfig:
    """Tests for CORS settings — cors_allow_all flag and cors_origins list."""

    def test_default_cors_origins(self):
        """Default origins include localhost dev URLs."""
        from app.config import Settings

        s = Settings(
            supabase_url="https://x.supabase.co",
            supabase_anon_key="k",
            supabase_service_role_key="k",
            supabase_jwt_secret="s" * 32,
            encryption_key="a" * 64,
        )
        assert "http://localhost:3000" in s.cors_origins
        assert s.cors_allow_all is False

    def test_cors_allow_all_flag(self, monkeypatch):
        """CORS_ALLOW_ALL=true enables the wildcard mode."""
        monkeypatch.setenv("CORS_ALLOW_ALL", "true")
        from app.config import Settings

        s = Settings(
            supabase_url="https://x.supabase.co",
            supabase_anon_key="k",
            supabase_service_role_key="k",
            supabase_jwt_secret="s" * 32,
            encryption_key="a" * 64,
        )
        assert s.cors_allow_all is True

    def test_cors_origins_from_env(self, monkeypatch):
        """CORS_ORIGINS env var (JSON array) overrides defaults."""
        monkeypatch.setenv(
            "CORS_ORIGINS",
            '["https://myapp.vercel.app","http://localhost:3000"]',
        )
        from app.config import Settings

        s = Settings(
            supabase_url="https://x.supabase.co",
            supabase_anon_key="k",
            supabase_service_role_key="k",
            supabase_jwt_secret="s" * 32,
            encryption_key="a" * 64,
        )
        assert "https://myapp.vercel.app" in s.cors_origins
        assert "http://localhost:3000" in s.cors_origins
