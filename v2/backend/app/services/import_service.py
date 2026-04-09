"""CSV import service — Robinhood CSV ingestion with SHA-256 dedup.

Ported from v1 data_engine.py with the canonical fingerprinting logic
that ensures the same transaction never gets imported twice, regardless
of date/number formatting differences between CSV exports.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


def _norm_decimal(val: str, places: int = 6) -> str:
    """Normalize a decimal value for fingerprinting.

    Strips $, commas, parentheses. Converts to Decimal at fixed precision.
    "$874.63", "874.63", "874.630000" all → "874.630000"
    """
    if not val:
        return "0." + "0" * places
    s = str(val).strip().replace("$", "").replace(",", "")
    # Handle parenthetical negatives: (123.45) → -123.45
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        d = Decimal(s).quantize(Decimal("0." + "0" * places))
        return str(d)
    except (InvalidOperation, ValueError):
        return "0." + "0" * places


def _norm_date(val: str) -> str:
    """Normalize a date for fingerprinting.

    "4/2/2026" and "2026-04-02" → "2026-04-02"
    """
    if not val:
        return "1970-01-01"
    s = str(val).strip()
    # ISO fast path
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    # MM/DD/YYYY or M/D/YYYY
    try:
        from datetime import datetime
        dt = datetime.strptime(s, "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    try:
        from datetime import datetime
        # Try pandas-style flexible parsing
        for fmt in ("%m/%d/%y", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    except Exception:
        pass
    return s


def make_fingerprint(
    tx_date: str, ticker: str, code: str, qty: str, price: str,
    amount: str = "", settle: str = ""
) -> str:
    """Create a SHA-256 canonical fingerprint for a transaction.

    Canonical string: NormDate | Ticker | Code | NormQty | NormPrice
    For cash-only rows: NormDate | Code | NormAmt | NormSettle
    """
    d = _norm_date(tx_date)
    t = (ticker or "").strip().upper()
    c = (code or "").strip().upper()
    q = _norm_decimal(qty)
    p = _norm_decimal(price)

    if t and q != "0.000000":
        canonical = f"{d}|{t}|{c}|{q}|{p}"
    else:
        # Cash-only row (ACH, RTP)
        a = _norm_decimal(amount)
        s = _norm_date(settle) if settle else d
        canonical = f"{d}|{c}|{a}|{s}"

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CsvImportService:
    """Import Robinhood CSV exports with SHA-256 dedup."""

    def __init__(self, user_id: UUID, supabase_client):
        self.user_id = user_id
        self.client = supabase_client

    async def import_robinhood_csv(self, csv_text: str) -> dict:
        """Parse and import a Robinhood CSV.

        Returns import result with counts.
        """
        # Get existing fingerprints for dedup
        existing = (
            self.client.table("transactions")
            .select("fingerprint")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data
        existing_fps = {r["fingerprint"] for r in existing}

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_text))
        rows_to_insert = []
        total = 0
        dupes = 0
        errors = 0
        error_details = []

        for row in reader:
            total += 1
            try:
                # Extract fields (Robinhood CSV column names)
                tx_date = row.get("Activity Date", row.get("Date", ""))
                ticker = row.get("Instrument", row.get("Symbol", row.get("Ticker", "")))
                code = row.get("Trans Code", row.get("Type", row.get("Transaction Type", "")))
                qty = row.get("Quantity", row.get("Qty", "0"))
                price = row.get("Price", "0")
                amount = row.get("Amount", row.get("Total", "0"))
                settle = row.get("Settle Date", "")
                desc = row.get("Description", row.get("Desc", ""))

                # Generate fingerprint
                fp = make_fingerprint(tx_date, ticker, code, qty, price, amount, settle)

                if fp in existing_fps:
                    dupes += 1
                    continue

                # Map transaction code
                code_upper = code.strip().upper() if code else "Other"
                tx_type_map = {
                    "BUY": "Buy", "SELL": "Sell", "CDIV": "CDIV",
                    "DRIP": "DRIP", "SPL": "SPL", "ACH": "ACH", "RTP": "RTP",
                }
                tx_type = tx_type_map.get(code_upper, "Other")

                # Parse date
                parsed_date = _norm_date(tx_date)

                rows_to_insert.append({
                    "user_id": str(self.user_id),
                    "fingerprint": fp,
                    "ticker": ticker.strip().upper() if ticker else None,
                    "tx_type": tx_type,
                    "quantity": float(_norm_decimal(qty)) if qty else None,
                    "price": float(_norm_decimal(price)) if price else None,
                    "amount": float(_norm_decimal(amount)) if amount else None,
                    "tx_date": parsed_date,
                    "settle_date": _norm_date(settle) if settle else None,
                    "description": desc,
                    "raw_data": row,
                })

                # Add to session dedup set
                existing_fps.add(fp)

            except Exception as e:
                errors += 1
                error_details.append(f"Row {total}: {str(e)[:100]}")

        # Batch insert
        new_count = len(rows_to_insert)
        if rows_to_insert:
            batch_size = 100
            for i in range(0, len(rows_to_insert), batch_size):
                batch = rows_to_insert[i:i + batch_size]
                try:
                    self.client.table("transactions").insert(batch).execute()
                except Exception as e:
                    errors += len(batch)
                    new_count -= len(batch)
                    error_details.append(f"Batch insert error: {str(e)[:200]}")

        return {
            "total_rows": total,
            "new_rows": new_count,
            "duplicates_skipped": dupes,
            "errors": errors,
            "error_details": error_details[:10],  # Cap at 10 error messages
        }
