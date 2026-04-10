"""AI service — Anthropic-powered portfolio rebalance analysis."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from ..config import get_settings
from ..database import get_supabase_client

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict | None:
    """Try multiple strategies to extract a JSON object from AI response text."""
    # Strategy 1: Full text is valid JSON
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: ```json ... ``` code block
    m = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: ``` ... ``` code block (language-agnostic)
    m = re.search(r"```\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 4: Find outermost { ... } JSON object
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _clean_ai_text(text: str) -> str:
    """Strip JSON code blocks and bare JSON objects from AI text, leaving prose."""
    # Remove ```json ... ``` blocks
    text = re.sub(r"```json\s*[\s\S]*?```", "", text)
    # Remove ``` ... ``` blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove standalone JSON objects { ... }
    text = re.sub(r"\{[\s\S]*?\}", "", text)
    return text.strip()


_FALLBACK_RESPONSE = {
    "allocation_table": [],
    "narrative": (
        "AI analysis is unavailable. Please configure your Anthropic API key "
        "in the application settings (anthropic_api_key) to enable AI-powered "
        "portfolio rebalancing."
    ),
    "total_value": 0.0,
    "generated_at": "",
}


class AiService:
    """AI-driven portfolio analysis using Claude."""

    def __init__(self) -> None:
        self.client = get_supabase_client()

    async def generate_rebalance(self, user_id: UUID, price_service=None) -> dict:
        """Generate an AI-powered rebalance recommendation.

        Returns a dict with:
          - allocation_table: list of {ticker, name, current_pct, suggested_pct, change_pct, rationale}
          - narrative: str (3-5 bullet points)
          - total_value: float
          - generated_at: ISO timestamp
        """
        settings = get_settings()
        generated_at = datetime.now(timezone.utc).isoformat()

        # 1. Fetch positions
        positions = (
            self.client.table("positions")
            .select("*")
            .eq("user_id", str(user_id))
            .execute()
        ).data or []

        if not positions:
            return {
                **_FALLBACK_RESPONSE,
                "narrative": "No positions found. Add positions to your portfolio first.",
                "generated_at": generated_at,
            }

        # 2. Fetch live prices
        prices: dict[str, float] = {}
        if price_service:
            tickers = [p["ticker"] for p in positions]
            try:
                price_results = await price_service.fetch_prices(tickers)
                for ticker, pr in price_results.items():
                    if pr.is_valid:
                        prices[ticker] = pr.mid_price
            except Exception as exc:
                logger.warning("Price fetch failed in AI service: %s", exc)

        # 3. Build portfolio context
        total_value = 0.0
        position_values: list[dict] = []
        category_totals: dict[str, float] = {}

        for p in positions:
            ticker = p["ticker"]
            shares = float(p.get("shares") or 0)
            avg_cost = float(p.get("avg_cost") or 0)
            cost_basis = shares * avg_cost
            current_price = prices.get(ticker) or avg_cost
            market_value = shares * current_price
            pnl = market_value - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0

            total_value += market_value
            category = p.get("category", "Other")
            category_totals[category] = category_totals.get(category, 0.0) + market_value

            position_values.append({
                "ticker": ticker,
                "name": p.get("name", ticker),
                "category": category,
                "shares": round(shares, 4),
                "avg_cost": round(avg_cost, 2),
                "current_price": round(current_price, 2),
                "market_value": round(market_value, 2),
                "pnl_pct": round(pnl_pct, 2),
                "lt_eligible": p.get("lt_eligible", False),
                "lt_date": str(p.get("lt_date") or ""),
                "target_price": float(p.get("target_price") or 0),
            })

        # Compute current allocation percentages
        for pv in position_values:
            pv["current_pct"] = round(
                (pv["market_value"] / total_value * 100) if total_value > 0 else 0.0, 2
            )

        category_summary = {
            cat: round(val / total_value * 100, 1) if total_value > 0 else 0
            for cat, val in category_totals.items()
        }

        # 4. Build prompt context
        context_lines = [
            f"Total Portfolio Value: ${total_value:,.2f}",
            f"Category Breakdown: {json.dumps(category_summary)}",
            "",
            "Positions:",
        ]
        for pv in position_values:
            context_lines.append(
                f"  {pv['ticker']} ({pv['name']}) | {pv['category']} | "
                f"{pv['shares']} shares @ ${pv['avg_cost']} avg | "
                f"current ${pv['current_price']} | value ${pv['market_value']} | "
                f"P&L {pv['pnl_pct']}% | {pv['current_pct']}% of portfolio | "
                f"LT eligible: {pv['lt_eligible']} (since {pv['lt_date']}) | "
                f"target price: ${pv['target_price']}"
            )

        context = "\n".join(context_lines)

        prompt = (
            f"You are a portfolio analyst. Here is the portfolio:\n{context}\n\n"
            "Task 1: Return a JSON array called \"allocations\" with objects: "
            "{ticker, name, current_pct, suggested_pct, change_pct, rationale}. "
            "Keep only positions that need meaningful changes (suggested_pct != current_pct by >1%).\n\n"
            "Task 2: Provide a \"narrative\" key with a 3-5 bullet point analysis (max 200 words) "
            "covering: diversification, top risks, opportunities, and one specific action recommendation.\n\n"
            "Return valid JSON with keys \"allocations\" and \"narrative\"."
        )

        # 5. Check API key
        if not settings.anthropic_api_key:
            return {
                **_FALLBACK_RESPONSE,
                "total_value": round(total_value, 2),
                "generated_at": generated_at,
            }

        # 6. Call Anthropic
        try:
            import anthropic

            anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            message = anthropic_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text if message.content else ""

            # 7. Parse response — try multiple JSON extraction strategies
            allocation_table: list[dict] = []
            narrative: str = ""

            parsed = _extract_json(response_text)
            if parsed:
                allocation_table = parsed.get("allocations", [])
                raw_narrative = parsed.get("narrative", "")
                # narrative may be a list of strings or a single string
                if isinstance(raw_narrative, list):
                    narrative = "\n".join(str(item) for item in raw_narrative)
                else:
                    narrative = str(raw_narrative)
            else:
                # JSON extraction failed — use cleaned text as narrative
                allocation_table = []
                narrative = _clean_ai_text(response_text)

        except json.JSONDecodeError:
            allocation_table = []
            narrative = _clean_ai_text(response_text)
        except Exception as exc:
            logger.error("Anthropic API call failed: %s", exc)
            return {
                **_FALLBACK_RESPONSE,
                "narrative": (
                    f"AI analysis failed: {exc}. "
                    "Please verify your Anthropic API key is configured correctly."
                ),
                "total_value": round(total_value, 2),
                "generated_at": generated_at,
            }

        return {
            "allocation_table": allocation_table,
            "narrative": narrative,
            "total_value": round(total_value, 2),
            "generated_at": generated_at,
        }
