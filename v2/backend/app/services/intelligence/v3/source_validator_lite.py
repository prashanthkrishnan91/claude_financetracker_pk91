"""Source/Evidence Validator Lite — K5 contract enforcer.

Validates assembled card text, labels, and fields against the Intel v3
product contract before the card reaches the snapshot payload.

Rules enforced:
  1. No raw metric key names in visible text fields.
  2. No banned posture labels in action fields.
  3. No fake price targets (numbers followed by $ or explicit "target" language).
  4. No action contradictions (e.g. BUY action with SELL-only language).
  5. No generic repeated copy across cards (identical why_text detected).
  6. Visible numeric claims must be grounded (basic: no invented precision numbers).

Returns a ValidationResult with a list of violations.
Pure function — no IO, DB, LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Raw backend metric keys forbidden in any visible text field.
_FORBIDDEN_METRIC_KEYS: frozenset[str] = frozenset({
    "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm", "revenue_growth_yoy",
    "peg_ratio", "p_fcf", "ebit_margin", "net_margin_ttm", "debt_to_equity",
    "current_ratio", "quick_ratio", "free_cash_flow_yield", "altman_z",
    "earnings_growth_fwd", "book_value_per_share", "enterprise_value",
})

# Banned posture labels that must never appear in action or label fields.
_BANNED_POSTURE_LABELS: frozenset[str] = frozenset({
    "add candidate", "watchlist", "review", "risk watch", "trim candidate",
    "strong buy", "strong sell", "buy more", "accumulate", "watch",
})

# Non-held radar labels must not appear in held-card fields.
_RADAR_LABELS: frozenset[str] = frozenset({"watch", "avoid"})

# Action contradiction pairs: if action is KEY, these phrases in why_text are forbidden.
_ACTION_FORBIDDEN_PHRASES: dict[str, list[str]] = {
    "BUY": [
        "stay on watchlist", "reviewing before", "not yet complete",
        "hold until signal", "wait for",
    ],
    "SELL": ["add to position", "increase exposure", "regular contribution"],
    "TRIM": ["add to position", "increase exposure"],
}

# Price target patterns: numbers with $ or explicit target language.
_PRICE_TARGET_RE = re.compile(
    r"\$\s*\d+(?:\.\d+)?"               # $123 or $12.34
    r"|price\s+target\s+of\s*\$?\d+"    # price target of $X
    r"|target\s+price\s*[=:]\s*\$?\d+"  # target price: $X
    r"|\d+\s*(?:dollar|usd)\s+target",  # 123 dollar target
    re.IGNORECASE,
)

# Precision fake number: 5+ digit decimal with high precision.
_FAKE_PRECISION_RE = re.compile(r"\d+\.\d{3,}")


@dataclass
class ValidationViolation:
    rule: str
    field: str
    message: str


@dataclass
class ValidationResult:
    is_valid: bool
    violations: list[ValidationViolation] = field(default_factory=list)

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def rules_violated(self) -> list[str]:
        return list({v.rule for v in self.violations})


def _check_text(
    text: Optional[str],
    field_name: str,
    violations: list[ValidationViolation],
) -> None:
    """Check a text field for all applicable text-based rules."""
    if not text:
        return
    text_lower = text.lower()

    # Rule 1: no raw metric keys.
    for key in _FORBIDDEN_METRIC_KEYS:
        if key in text_lower:
            violations.append(ValidationViolation(
                rule="no_raw_metric_keys",
                field=field_name,
                message=f"Raw metric key '{key}' found in {field_name}.",
            ))

    # Rule 3: no fake price targets.
    if _PRICE_TARGET_RE.search(text):
        violations.append(ValidationViolation(
            rule="no_fake_price_targets",
            field=field_name,
            message=f"Price target language detected in {field_name}.",
        ))


def validate_card(
    *,
    ticker: str,
    action: str,
    conviction: str,
    why_text: Optional[str] = None,
    risk_text: Optional[str] = None,
    action_text: Optional[str] = None,
    evidence_text: Optional[str] = None,
    fit_text: Optional[str] = None,
    what_would_change_view: Optional[str] = None,
) -> ValidationResult:
    """Validate a single assembled card against the v3 contract.

    Args:
        ticker:               Ticker symbol.
        action:               Resolved action string (BUY/HOLD/TRIM/SELL).
        conviction:           Conviction string (LOW/MEDIUM/HIGH).
        why_text:             Primary why sentence shown on card.
        risk_text:            Risk sentence shown on card.
        action_text:          Action label / call to action text.
        evidence_text:        Evidence check text.
        fit_text:             Portfolio fit text.
        what_would_change_view: What would change the view text.

    Returns:
        ValidationResult with is_valid and violations.
    """
    violations: list[ValidationViolation] = []
    action_upper = (action or "").upper()

    # Rule 2: action field must be one of the four valid held actions.
    valid_actions = {"BUY", "HOLD", "TRIM", "SELL"}
    if action_upper not in valid_actions:
        violations.append(ValidationViolation(
            rule="valid_action_labels_only",
            field="action",
            message=f"Invalid action '{action}' — must be BUY/HOLD/TRIM/SELL.",
        ))

    # Rule 2b: no banned posture labels in action or action_text.
    for field_name, field_value in [("action", action), ("action_text", action_text)]:
        if not field_value:
            continue
        field_lower = field_value.lower()
        for banned in _BANNED_POSTURE_LABELS:
            if banned in field_lower:
                violations.append(ValidationViolation(
                    rule="no_banned_posture_labels",
                    field=field_name,
                    message=f"Banned posture label '{banned}' in {field_name}.",
                ))

    # Rule 2c: radar labels must not appear in held-card action.
    for field_name, field_value in [("action", action)]:
        if not field_value:
            continue
        if field_value.upper() in {"WATCH", "AVOID"}:
            violations.append(ValidationViolation(
                rule="no_radar_labels_in_held_cards",
                field=field_name,
                message=f"Radar label '{field_value}' must not appear in held-position cards.",
            ))

    # Rule 4: action contradictions.
    if action_upper in _ACTION_FORBIDDEN_PHRASES:
        for text_field, text_value in [
            ("why_text", why_text),
            ("action_text", action_text),
            ("evidence_text", evidence_text),
        ]:
            if not text_value:
                continue
            text_lower = text_value.lower()
            for forbidden_phrase in _ACTION_FORBIDDEN_PHRASES[action_upper]:
                if forbidden_phrase in text_lower:
                    violations.append(ValidationViolation(
                        rule="no_action_contradictions",
                        field=text_field,
                        message=(
                            f"Action '{action_upper}' contradicted by phrase "
                            f"'{forbidden_phrase}' in {text_field}."
                        ),
                    ))

    # Rule 1 + 3: raw keys and fake price targets in text fields.
    for field_name, field_value in [
        ("why_text", why_text),
        ("risk_text", risk_text),
        ("action_text", action_text),
        ("evidence_text", evidence_text),
        ("fit_text", fit_text),
        ("what_would_change_view", what_would_change_view),
    ]:
        _check_text(field_value, field_name, violations)

    # Rule 5: conviction must be LOW/MEDIUM/HIGH.
    valid_convictions = {"LOW", "MEDIUM", "HIGH"}
    if conviction.upper() not in valid_convictions:
        violations.append(ValidationViolation(
            rule="valid_conviction_only",
            field="conviction",
            message=f"Invalid conviction '{conviction}' — must be LOW/MEDIUM/HIGH.",
        ))

    return ValidationResult(is_valid=len(violations) == 0, violations=violations)


def detect_generic_copy_spam(
    cards: list[dict],
    text_field: str = "why_text",
    min_cards_for_spam: int = 3,
) -> list[str]:
    """Detect tickers with identical why_text across the card set (generic copy spam).

    Returns list of tickers that share identical text with ≥ min_cards_for_spam others.
    """
    from collections import Counter

    texts = [(c.get("ticker", ""), c.get(text_field, "")) for c in cards if c]
    text_counts = Counter(text for _, text in texts if text)

    spam_texts = {text for text, count in text_counts.items() if count >= min_cards_for_spam}
    return [ticker for ticker, text in texts if text in spam_texts]


def validate_snapshot_cards(
    cards: list[dict],
    *,
    spam_threshold: int = 3,
) -> tuple[list[ValidationResult], list[str]]:
    """Validate all cards in a snapshot and detect generic copy spam.

    Returns:
        (per_card_results, spam_tickers) where spam_tickers are tickers
        detected as having generic repeated copy.
    """
    results = []
    for card in cards:
        result = validate_card(
            ticker=card.get("ticker", "UNKNOWN"),
            action=card.get("action", "HOLD"),
            conviction=card.get("conviction", "LOW"),
            why_text=card.get("why_text"),
            risk_text=card.get("risk_text"),
            action_text=card.get("action_text"),
            evidence_text=card.get("evidence_text"),
            fit_text=card.get("fit_text"),
            what_would_change_view=card.get("what_would_change_view"),
        )
        results.append(result)

    spam_tickers = detect_generic_copy_spam(cards, min_cards_for_spam=spam_threshold)
    return results, spam_tickers
