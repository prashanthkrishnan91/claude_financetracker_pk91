"""Tests that would have failed PR #217.

PR #217 fixed generic_copy_count=29 by prepending ticker symbols to a shared
template, making each card's why_text unique in exact-string comparison.
That approach defeated only exact-duplicate detection; it did not produce
meaningful per-ticker intelligence.

These tests prove:
  1. Ticker-prefixed boilerplate is detected by skeleton/prefix detectors.
  2. generic_copy_count cannot become 0 merely because ticker symbols differ.
  3. Evidence-aware rationale (with primary_driver) is ticker-specific.
  4. BUY language rules are upheld.
  5. HOLD language explains WHY not adding.
  6. ETF cards avoid operating-company phrasing.
  7. Crypto cards avoid stock/company-fundamental phrasing.
  8. Raw metric keys are not visible.
  9. Action-conflicting language is detected.
  10. New certification fields are reported correctly.

Synthetic fixtures only — no real user or account data.
"""
from __future__ import annotations

import re

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.intelligence.v3.source_validator_lite import (
    certify_snapshot_cards,
    detect_generic_copy_spam,
    detect_repeated_skeleton_spam,
    detect_ticker_prefix_only_spam,
    detect_weak_buy_rationale,
    validate_snapshot_cards,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_BUY_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
_HOLD_TICKERS = [f"HOLD{i:02d}" for i in range(23)]


def _inp_buy_no_evidence(ticker: str) -> DecisionInputV3:
    """BUY input with identical axis bands and NO evidence text — worst-case scenario."""
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.UNDERWEIGHT,
        risk_band=RiskBand.NONE,
        raw_action="BUY",
        raw_analyst_action="BUY",
        upstream_conviction="MEDIUM",
    )


def _inp_buy_with_driver(ticker: str, driver: str, risk_flag: str = "") -> DecisionInputV3:
    """BUY input with per-ticker primary_driver evidence text."""
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.STRONG,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.UNDERWEIGHT,
        risk_band=RiskBand.NONE,
        raw_action="BUY",
        raw_analyst_action="BUY",
        upstream_conviction="HIGH",
        primary_driver=driver,
        risk_flag_text=risk_flag or None,
    )


def _inp_hold_thin_evidence(ticker: str) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.THIN,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.UNKNOWN,
        raw_action="HOLD",
        upstream_conviction="LOW",
    )


def _inp_hold_elevated_risk(ticker: str, risk_note: str = "") -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.HIGH,
        raw_action="HOLD",
        upstream_conviction="LOW",
        risk_flag_text=risk_note or "elevated risk signals present",
    )


def _inp_hold_on_target(ticker: str) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.NONE,
        raw_action="HOLD",
        upstream_conviction="MEDIUM",
    )


def _inp_etf_buy(ticker: str) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.UNDERWEIGHT,
        risk_band=RiskBand.NONE,
        raw_action="BUY",
        upstream_conviction="MEDIUM",
        asset_type_hint="etf",
    )


def _inp_etf_hold(ticker: str) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.NONE,
        raw_action="HOLD",
        upstream_conviction="MEDIUM",
        asset_type_hint="etf",
    )


def _inp_crypto_hold(ticker: str) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.SUPPRESSED,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=FitBand.BLOCKED,
        risk_band=RiskBand.UNKNOWN,
        raw_action="HOLD",
        upstream_conviction="LOW",
        asset_type_hint="crypto",
    )


def _make_card_from_inp(inp: DecisionInputV3, name: str = "", category: str = "stock") -> dict:
    """Run inp through decide() and build a snapshot card."""
    decision = decide(inp)
    snap = build_snapshot(
        run_id="test-evidence-001",
        decisions=[decision],
        card_metas=[{
            "ticker": inp.ticker,
            "name": name or f"{inp.ticker} Corp",
            "category": inp.asset_type_hint or category,
            "thesis_state": "intact",
        }],
    )
    return snap["current_holdings"][0]


def _make_cards_no_evidence(tickers: list[str]) -> list[dict]:
    """Build BUY cards with identical axis bands and NO evidence text."""
    cards = []
    for t in tickers:
        cards.append(_make_card_from_inp(_inp_buy_no_evidence(t)))
    return cards


# ── Section 1: Ticker-prefix boilerplate detection ────────────────────────────

class TestTickerPrefixBoilerplateDetected:
    """PR #217's ticker-prefix fix games generic_copy_count but must be caught
    by the new skeleton detectors."""

    def test_11_buy_no_evidence_cards_trigger_prefix_only_detector(self):
        """11 BUY cards with same axis bands and no evidence → ticker-prefix-only detected."""
        cards = _make_cards_no_evidence(_BUY_TICKERS)
        spam_tickers, skeleton_count = detect_ticker_prefix_only_spam(cards, min_cards_for_spam=3)
        assert len(spam_tickers) >= 3, (
            f"Expected at least 3 ticker-prefix-only spam cards, got {len(spam_tickers)}. "
            f"Texts sample: {[c['why_text'] for c in cards[:3]]}"
        )
        assert skeleton_count >= 1, (
            f"Expected at least 1 repeated skeleton, got {skeleton_count}"
        )

    def test_11_buy_no_evidence_repeated_skeleton_detected(self):
        """detect_repeated_skeleton_spam catches template reuse across different tickers."""
        cards = _make_cards_no_evidence(_BUY_TICKERS)
        spam_tickers, _ = detect_repeated_skeleton_spam(cards, min_cards_for_spam=3)
        assert len(spam_tickers) >= 3, (
            f"Expected skeleton spam tickers, got: {spam_tickers}"
        )

    def test_generic_copy_count_not_gamed_by_ticker_prefix(self):
        """Certification as a whole must not pass merely because tickers differ.

        Even if generic_copy_count=0 (exact duplicates), skeleton-based
        detectors must report the problem.
        """
        cards = _make_cards_no_evidence(_BUY_TICKERS)
        cert = certify_snapshot_cards(cards, spam_threshold=3)

        # The combination of skeleton detectors must catch the problem.
        total_skeleton_spam = (
            cert["ticker_prefix_only_reason_count"]
            + cert["repeated_skeleton_count"]
        )
        assert total_skeleton_spam > 0, (
            "Certification should detect ticker-prefix-only boilerplate via skeleton counts. "
            f"cert={cert}"
        )

    def test_certification_ticker_prefix_only_reason_count_nonzero(self):
        """Certification explicitly reports ticker_prefix_only_reason_count > 0."""
        cards = _make_cards_no_evidence(_BUY_TICKERS)
        cert = certify_snapshot_cards(cards, spam_threshold=3)
        assert cert["ticker_prefix_only_reason_count"] > 0, (
            f"Expected ticker_prefix_only_reason_count > 0, got cert={cert}"
        )

    def test_old_template_still_detected_by_prefix_detector(self):
        """The exact PR #217 template 'MSFT: strong evidence and fairly priced...' is detected."""
        old_template_cards = [
            {"ticker": t, "action": "BUY", "conviction": "MEDIUM",
             "why_text": (
                 f"{t}: strong evidence and fairly priced. "
                 "Portfolio has room to add. Manageable risk."
             )}
            for t in _BUY_TICKERS
        ]
        spam_tickers, _ = detect_ticker_prefix_only_spam(old_template_cards, min_cards_for_spam=3)
        assert len(spam_tickers) >= 3, (
            f"PR #217 template should be detected as ticker-prefix-only spam. Got: {spam_tickers}"
        )


# ── Section 2: Evidence-aware BUY rationale ───────────────────────────────────

class TestEvidenceAwareBuyRationale:
    """BUY cards with primary_driver must produce ticker-specific, evidence-grounded text."""

    _DRIVERS = {
        "AAPL": "services revenue growth driven by App Store and subscription bundling",
        "MSFT": "cloud computing demand accelerating for Azure workloads",
        "NVDA": "AI accelerator demand far exceeds current supply capacity",
        "GOOG": "search and YouTube ad revenue recovering with stronger CPM trends",
        "META": "advertising platform efficiency gains from Advantage+ AI tools",
    }

    def test_buy_with_driver_uses_driver_text(self):
        """BUY card with primary_driver must include the driver in why_text."""
        for ticker, driver in self._DRIVERS.items():
            inp = _inp_buy_with_driver(ticker, driver)
            card = _make_card_from_inp(inp)
            driver_fragment = driver[:40].lower()
            assert driver_fragment in card["why_text"].lower(), (
                f"{ticker}: expected driver fragment '{driver_fragment}' in why_text, "
                f"got: {card['why_text']!r}"
            )

    def test_buy_with_driver_no_ticker_prefix_only(self):
        """5 BUY cards with unique drivers → NOT detected as ticker-prefix-only spam."""
        cards = [
            _make_card_from_inp(_inp_buy_with_driver(t, d))
            for t, d in self._DRIVERS.items()
        ]
        spam_tickers, _ = detect_ticker_prefix_only_spam(cards, min_cards_for_spam=3)
        assert spam_tickers == [], (
            f"Evidence-aware cards should not trigger prefix detector. Spam: {spam_tickers}"
        )

    def test_buy_with_driver_no_repeated_skeleton(self):
        """5 BUY cards with unique drivers → NOT detected as repeated-skeleton spam."""
        cards = [
            _make_card_from_inp(_inp_buy_with_driver(t, d))
            for t, d in self._DRIVERS.items()
        ]
        spam_tickers, _ = detect_repeated_skeleton_spam(cards, min_cards_for_spam=3)
        assert spam_tickers == [], (
            f"Evidence-aware cards should not trigger skeleton detector. Spam: {spam_tickers}"
        )

    def test_buy_with_risk_flag_includes_risk_for_medium_risk(self):
        """BUY card with MEDIUM risk + risk_flag_text must include the risk caveat."""
        inp = DecisionInputV3(
            ticker="MSFT",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.MEDIUM,
            raw_action="BUY",
            upstream_conviction="HIGH",
            primary_driver="cloud computing demand accelerating for Azure",
            risk_flag_text="regulatory scrutiny on AI practices could slow growth",
        )
        card = _make_card_from_inp(inp)
        assert "regulatory scrutiny" in card["why_text"].lower() or "risk" in card["why_text"].lower(), (
            f"Expected risk caveat in why_text for MEDIUM risk BUY, got: {card['why_text']!r}"
        )

    def test_buy_no_evidence_fallback_avoids_old_boilerplate_patterns(self):
        """BUY fallback (no evidence) must NOT match pre-v3.2 boilerplate patterns."""
        old_patterns = [
            "strong evidence and fairly priced",
            "portfolio has room to add",
            "manageable risk",
            "signals support adding",
            "meets the evidence quality and attractiveness bar",
        ]
        cards = _make_cards_no_evidence(_BUY_TICKERS[:5])
        for card in cards:
            text_lower = card["why_text"].lower()
            for pattern in old_patterns:
                assert pattern not in text_lower, (
                    f"{card['ticker']}: fallback rationale still matches old boilerplate "
                    f"'{pattern}': {card['why_text']!r}"
                )

    def test_buy_cards_with_driver_do_not_trigger_weak_buy_detector(self):
        """Evidence-aware BUY cards must not be flagged as weak."""
        cards = [
            _make_card_from_inp(_inp_buy_with_driver(t, d))
            for t, d in self._DRIVERS.items()
        ]
        weak = detect_weak_buy_rationale(cards)
        assert weak == [], (
            f"Evidence-aware BUY cards should not be flagged as weak. Got: {weak}"
        )

    def test_certification_weak_buy_count_zero_for_evidence_aware_cards(self):
        """certify_snapshot_cards: weak_buy_rationale_count=0 when evidence is used."""
        cards = [
            _make_card_from_inp(_inp_buy_with_driver(t, d))
            for t, d in self._DRIVERS.items()
        ]
        cert = certify_snapshot_cards(cards)
        assert cert["weak_buy_rationale_count"] == 0, (
            f"Expected 0 weak BUY cards with evidence, got {cert['weak_buy_rationale_count']}"
        )


# ── Section 3: HOLD language explains WHY ─────────────────────────────────────

class TestHoldLanguageExplainsWhy:
    """HOLD cards must distinguish WHY not adding — not generic 'hold' boilerplate."""

    def test_hold_thin_evidence_explains_thin_evidence(self):
        """HOLD with thin evidence must mention evidence/data insufficiency."""
        card = _make_card_from_inp(_inp_hold_thin_evidence("RIVN"))
        text_lower = card["why_text"].lower()
        evidence_keywords = {"evidence", "data", "signal", "incomplete", "insufficient", "thin"}
        assert any(kw in text_lower for kw in evidence_keywords), (
            f"HOLD thin evidence card should explain data gap, got: {card['why_text']!r}"
        )

    def test_hold_elevated_risk_mentions_risk(self):
        """HOLD with elevated risk must mention risk in rationale."""
        card = _make_card_from_inp(_inp_hold_elevated_risk("TSLA", "leverage elevated and macro headwinds"))
        text_lower = card["why_text"].lower()
        assert "risk" in text_lower, (
            f"HOLD elevated-risk card should mention risk, got: {card['why_text']!r}"
        )

    def test_hold_on_target_mentions_position_size(self):
        """HOLD with ON_TARGET portfolio fit should explain position is sized appropriately."""
        card = _make_card_from_inp(_inp_hold_on_target("GOOG"))
        text_lower = card["why_text"].lower()
        size_keywords = {"sized", "target", "appropriate", "position", "on plan", "no new capital"}
        assert any(kw in text_lower for kw in size_keywords), (
            f"HOLD on-target card should mention position sizing, got: {card['why_text']!r}"
        )

    def test_hold_not_sound_like_buy(self):
        """HOLD cards must not sound like a BUY recommendation."""
        hold_cards = [
            _make_card_from_inp(_inp_hold_thin_evidence("RIVN")),
            _make_card_from_inp(_inp_hold_elevated_risk("TSLA")),
            _make_card_from_inp(_inp_hold_on_target("GOOG")),
        ]
        buy_phrases = ["add to position", "increase exposure", "add to this", "room to add"]
        for card in hold_cards:
            text_lower = card["why_text"].lower()
            for phrase in buy_phrases:
                assert phrase not in text_lower, (
                    f"{card['ticker']} HOLD why_text sounds like a BUY: '{phrase}' found. "
                    f"Text: {card['why_text']!r}"
                )

    def test_hold_different_reasons_produce_different_text(self):
        """Thin-evidence HOLD and elevated-risk HOLD must produce distinct why_text."""
        thin_card = _make_card_from_inp(_inp_hold_thin_evidence("RIVN"))
        risk_card = _make_card_from_inp(_inp_hold_elevated_risk("TSLA"))
        # After stripping ticker prefix, text should differ.
        def strip_ticker(text: str, ticker: str) -> str:
            return re.sub(r"^\s*" + re.escape(ticker) + r"\s*[:;,]?\s*", "", text, flags=re.IGNORECASE)
        thin_body = strip_ticker(thin_card["why_text"], "RIVN").lower()
        risk_body = strip_ticker(risk_card["why_text"], "TSLA").lower()
        assert thin_body != risk_body, (
            f"Different HOLD reasons produced identical skeleton text:\n"
            f"  thin: {thin_card['why_text']!r}\n"
            f"  risk: {risk_card['why_text']!r}"
        )


# ── Section 4: ETF language ───────────────────────────────────────────────────

class TestEtfCardLanguage:
    """ETF cards must not be described as operating companies."""

    _COMPANY_PHRASES = [
        "revenue",
        "earnings growth",
        "management team",
        "operating",
        "business driver",
        "profit margin",
        "customer",
    ]

    def test_etf_buy_avoids_operating_company_phrasing(self):
        """ETF BUY card must not use stock/company fundamental language."""
        for ticker in ["VOO", "VGT", "SCHD", "QQQ"]:
            card = _make_card_from_inp(_inp_etf_buy(ticker), category="etf")
            text_lower = card["why_text"].lower()
            for phrase in self._COMPANY_PHRASES:
                assert phrase not in text_lower, (
                    f"ETF BUY '{ticker}' why_text uses company phrasing '{phrase}': "
                    f"{card['why_text']!r}"
                )

    def test_etf_hold_avoids_operating_company_phrasing(self):
        """ETF HOLD card must not use stock/company fundamental language."""
        for ticker in ["SCHD", "VOO"]:
            card = _make_card_from_inp(_inp_etf_hold(ticker), category="etf")
            text_lower = card["why_text"].lower()
            for phrase in self._COMPANY_PHRASES:
                assert phrase not in text_lower, (
                    f"ETF HOLD '{ticker}' why_text uses company phrasing '{phrase}': "
                    f"{card['why_text']!r}"
                )

    def test_etf_buy_uses_portfolio_appropriate_language(self):
        """ETF BUY card should use portfolio/contribution/exposure-type language."""
        card = _make_card_from_inp(_inp_etf_buy("VOO"), category="etf")
        etf_keywords = {"exposure", "diversified", "contribution", "core", "allocation"}
        text_lower = card["why_text"].lower()
        assert any(kw in text_lower for kw in etf_keywords), (
            f"ETF BUY should use portfolio language, got: {card['why_text']!r}"
        )


# ── Section 5: Crypto card language ───────────────────────────────────────────

class TestCryptoCardLanguage:
    """Crypto cards must not use stock/company fundamental phrasing."""

    _STOCK_PHRASES = [
        "revenue growth",
        "earnings",
        "management team",
        "business driver",
        "profit margin",
        "balance sheet",
        "free cash flow",
    ]

    def test_crypto_hold_avoids_company_fundamentals(self):
        """Crypto HOLD card must not use company fundamentals language."""
        for ticker in ["BTC", "XRP"]:
            card = _make_card_from_inp(_inp_crypto_hold(ticker), category="crypto")
            text_lower = card["why_text"].lower()
            for phrase in self._STOCK_PHRASES:
                assert phrase not in text_lower, (
                    f"Crypto HOLD '{ticker}' uses company phrasing '{phrase}': "
                    f"{card['why_text']!r}"
                )


# ── Section 6: Raw metric keys absent ─────────────────────────────────────────

class TestNoRawMetricKeysInRationale:
    """Raw metric key names must never appear in visible card text."""

    _RAW_KEYS = [
        "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
        "peg_ratio", "p_fcf", "ebit_margin", "net_margin_ttm",
        "debt_to_equity", "current_ratio", "altman_z",
    ]

    def _all_card_text(self, card: dict) -> str:
        return " ".join(filter(None, [
            card.get("why_text", ""),
            card.get("risk_text", ""),
            card.get("action_text", ""),
            card.get("evidence_text", ""),
            card.get("what_would_change_view", ""),
        ])).lower()

    def test_buy_with_driver_contaminated_by_raw_key_falls_back(self):
        """If primary_driver contains a raw metric key, it is silently discarded."""
        inp = _inp_buy_with_driver(
            "MSFT",
            "fcf_margin expanded 3pp due to Azure efficiency gains",
        )
        card = _make_card_from_inp(inp)
        assert "fcf_margin" not in card["why_text"].lower(), (
            f"Raw metric key 'fcf_margin' must not appear in why_text: {card['why_text']!r}"
        )

    def test_no_raw_keys_in_any_card_text_34_card_portfolio(self):
        """Full 34-card portfolio produces zero raw-metric-key violations."""
        all_cards = []
        for t in _BUY_TICKERS:
            all_cards.append(_make_card_from_inp(_inp_buy_no_evidence(t)))
        for t in _HOLD_TICKERS[:23]:
            all_cards.append(_make_card_from_inp(_inp_hold_thin_evidence(t)))

        for card in all_cards:
            text = self._all_card_text(card)
            for key in self._RAW_KEYS:
                assert key not in text, (
                    f"{card['ticker']}: raw metric key '{key}' found in visible text"
                )

    def test_certify_raw_metric_key_count_zero(self):
        """certify_snapshot_cards must report raw_metric_key_count=0 for clean cards."""
        cards = [
            _make_card_from_inp(_inp_buy_no_evidence(t)) for t in _BUY_TICKERS[:5]
        ] + [
            _make_card_from_inp(_inp_hold_thin_evidence(t)) for t in _HOLD_TICKERS[:5]
        ]
        cert = certify_snapshot_cards(cards)
        assert cert["raw_metric_key_count"] == 0


# ── Section 7: Action-conflicting language ────────────────────────────────────

class TestActionConflictDetection:
    """Action-conflicting language must be detected by certification."""

    def test_buy_card_with_watchlist_language_is_detected(self):
        """A BUY card that says 'stay on watchlist' triggers action_conflict_count."""
        # Inject a card with contradictory text
        card = {
            "ticker": "FAKE",
            "action": "BUY",
            "conviction": "MEDIUM",
            "why_text": "stay on watchlist until momentum confirms.",
        }
        cert = certify_snapshot_cards([card])
        assert cert["action_conflict_count"] > 0, (
            f"Expected action_conflict_count > 0 for watchlist phrase in BUY card. cert={cert}"
        )

    def test_clean_buy_cards_have_zero_action_conflicts(self):
        """Normal BUY cards must not have action conflicts."""
        cards = [_make_card_from_inp(_inp_buy_no_evidence(t)) for t in _BUY_TICKERS[:5]]
        cert = certify_snapshot_cards(cards)
        assert cert["action_conflict_count"] == 0


# ── Section 8: Certification field completeness ───────────────────────────────

class TestCertificationFieldCompleteness:
    """certify_snapshot_cards must return all required certification fields."""

    _REQUIRED_FIELDS = [
        "generic_copy_count",
        "duplicate_reason_count",
        "repeated_skeleton_count",
        "ticker_prefix_only_reason_count",
        "weak_buy_rationale_count",
        "action_conflict_count",
        "raw_metric_key_count",
        "posture_label_count",
        "hard_violations",
        "examples",
        "per_card_results",
        "spam_tickers",
    ]

    def test_all_required_fields_present(self):
        """certify_snapshot_cards must return all required certification fields."""
        cards = [_make_card_from_inp(_inp_buy_no_evidence("AAPL"))]
        cert = certify_snapshot_cards(cards)
        for field in self._REQUIRED_FIELDS:
            assert field in cert, f"Missing required certification field: {field!r}"

    def test_old_validate_snapshot_cards_still_returns_3_tuple(self):
        """validate_snapshot_cards (backward compat) still returns 3-tuple."""
        cards = [_make_card_from_inp(_inp_buy_no_evidence("AAPL"))]
        result = validate_snapshot_cards(cards)
        assert len(result) == 3, f"Expected 3-tuple, got {type(result)}"
        per_card, spam, hard = result
        assert isinstance(per_card, list)
        assert isinstance(spam, list)
        assert isinstance(hard, int)

    def test_examples_populated_when_counts_nonzero(self):
        """Examples dict must contain entries for nonzero counts."""
        cards = _make_cards_no_evidence(_BUY_TICKERS)
        cert = certify_snapshot_cards(cards, spam_threshold=3)
        if cert["ticker_prefix_only_reason_count"] > 0:
            assert "ticker_prefix_only" in cert["examples"] or cert["examples"], (
                "Examples should be populated when nonzero counts exist."
            )

    def test_clean_full_snapshot_has_zero_hard_violations(self):
        """34-card clean snapshot must produce zero hard violations in certification."""
        all_cards = []
        for t in _BUY_TICKERS:
            all_cards.append(_make_card_from_inp(_inp_buy_no_evidence(t)))
        for t in _HOLD_TICKERS[:23]:
            all_cards.append(_make_card_from_inp(_inp_hold_thin_evidence(t)))

        cert = certify_snapshot_cards(all_cards)
        assert cert["hard_violations"] == 0
        assert cert["raw_metric_key_count"] == 0
        assert cert["posture_label_count"] == 0
        assert cert["action_conflict_count"] == 0

    def test_evidence_aware_snapshot_has_all_zero_spam_counts(self):
        """Snapshot with evidence-aware rationale must pass all spam checks."""
        unique_drivers = {
            "AAPL": "services revenue growth driven by App Store bundling",
            "MSFT": "cloud computing demand accelerating for Azure workloads",
            "NVDA": "AI accelerator demand exceeds current supply capacity",
            "GOOG": "search advertising recovering with improved CPM trends",
            "META": "ad platform efficiency gains from Advantage+ AI tools",
        }
        cards = [
            _make_card_from_inp(_inp_buy_with_driver(t, d))
            for t, d in unique_drivers.items()
        ]
        cert = certify_snapshot_cards(cards, spam_threshold=3)
        assert cert["ticker_prefix_only_reason_count"] == 0, (
            f"Evidence-aware cards should have 0 ticker-prefix-only spam. cert={cert}"
        )
        assert cert["repeated_skeleton_count"] == 0, (
            f"Evidence-aware cards should have 0 repeated skeletons. cert={cert}"
        )
        assert cert["weak_buy_rationale_count"] == 0, (
            f"Evidence-aware cards should have 0 weak BUY rationale. cert={cert}"
        )
        assert cert["hard_violations"] == 0


# ── Section 9: Schema version and ticker presence ─────────────────────────────

class TestCardContractInvariantsAfterFix:
    """Core card contract invariants must still hold after the evidence-aware fix."""

    def test_ticker_still_present_in_why_text(self):
        """Each card's why_text must contain its ticker symbol."""
        for t in ["AAPL", "MSFT", "NVDA"]:
            card = _make_card_from_inp(_inp_buy_no_evidence(t))
            assert t in card["why_text"], (
                f"Ticker {t!r} not found in why_text: {card['why_text']!r}"
            )

    def test_action_is_valid_for_all_cards(self):
        """All generated cards must have valid BUY/HOLD/TRIM/SELL actions."""
        valid = {"BUY", "HOLD", "TRIM", "SELL"}
        inputs = (
            [_inp_buy_no_evidence(t) for t in _BUY_TICKERS[:5]]
            + [_inp_hold_thin_evidence(t) for t in ["HOLD00", "HOLD01"]]
            + [_inp_etf_buy("VOO")]
            + [_inp_crypto_hold("BTC")]
        )
        for inp in inputs:
            card = _make_card_from_inp(inp)
            assert card["action"] in valid, (
                f"{inp.ticker}: invalid action {card['action']!r}"
            )

    def test_evidence_aware_rationale_passes_hard_validation(self):
        """Cards with evidence-aware rationale must pass hard validation."""
        cards = [
            _make_card_from_inp(_inp_buy_with_driver(
                "MSFT", "cloud computing demand accelerating for Azure"
            )),
            _make_card_from_inp(_inp_hold_elevated_risk("TSLA")),
            _make_card_from_inp(_inp_etf_buy("VOO")),
        ]
        _, _, hard = validate_snapshot_cards(cards)
        assert hard == 0, f"Expected 0 hard violations, got {hard}"
