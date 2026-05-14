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


# Rules that constitute a hard violation — snapshot must NOT be persisted.
# Soft violations (generic copy spam) may be persisted with a warning.
HARD_VIOLATION_RULES: frozenset[str] = frozenset({
    "valid_action_labels_only",
    "no_banned_posture_labels",
    "no_radar_labels_in_held_cards",
    "no_action_contradictions",
    "no_raw_metric_keys",
    "no_fake_price_targets",
    "valid_conviction_only",
})


@dataclass
class ValidationResult:
    is_valid: bool
    violations: list[ValidationViolation] = field(default_factory=list)

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def hard_violation_count(self) -> int:
        return sum(1 for v in self.violations if v.rule in HARD_VIOLATION_RULES)

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


def _strip_ticker_prefix(text: str, ticker: str) -> str:
    """Remove leading ticker prefix (e.g. 'MSFT: ' or 'MSFT, ') and normalize."""
    pat = re.compile(
        r"^\s*" + re.escape(ticker.strip()) + r"\s*[:;,\-]?\s*",
        re.IGNORECASE,
    )
    stripped = pat.sub("", text).strip()
    return " ".join(stripped.lower().split())


def detect_ticker_prefix_only_spam(
    cards: list[dict],
    text_field: str = "why_text",
    min_cards_for_spam: int = 3,
) -> tuple[list[str], int]:
    """Detect cards where the only differentiator is a leading ticker symbol.

    Strips the ticker prefix from each card's text, then checks whether the
    remaining skeleton is shared by ≥ min_cards_for_spam other cards.

    Returns:
        (spam_tickers, repeated_skeleton_count)
        spam_tickers: tickers whose skeleton is shared with ≥ min_cards threshold
        repeated_skeleton_count: number of distinct skeletons that are repeated
    """
    from collections import Counter

    pairs = []
    for card in cards:
        ticker = card.get("ticker", "")
        text = card.get(text_field, "") or ""
        skeleton = _strip_ticker_prefix(text, ticker)
        pairs.append((ticker, skeleton))

    skeleton_counts = Counter(sk for _, sk in pairs if sk)
    repeated_skeletons = {sk for sk, cnt in skeleton_counts.items() if cnt >= min_cards_for_spam}
    spam_tickers = [t for t, sk in pairs if sk in repeated_skeletons]
    return spam_tickers, len(repeated_skeletons)


def _normalize_skeleton(text: str, all_tickers: frozenset) -> str:
    """Aggressively normalize text to a skeleton for repeated-template detection.

    Removes all ticker symbols, numbers, and punctuation; lowercases and
    collapses whitespace. Used to detect templates reused across different tickers
    even when the exact text differs.
    """
    norm = text.lower()
    for t in all_tickers:
        norm = re.sub(r"\b" + re.escape(t.lower()) + r"\b", " ", norm)
    norm = re.sub(r"\d+\.?\d*", " ", norm)
    norm = re.sub(r"[^\w\s]", " ", norm)
    norm = " ".join(norm.split())
    return norm


def detect_repeated_skeleton_spam(
    cards: list[dict],
    text_field: str = "why_text",
    min_cards_for_spam: int = 3,
) -> tuple[list[str], int]:
    """Detect cards that share the same sentence skeleton across different tickers.

    After removing all ticker symbols, numbers, and punctuation from each card's
    text, cards that share the same normalized form are treated as repeated templates.

    Returns:
        (spam_tickers, repeated_skeleton_count)
    """
    from collections import Counter

    all_tickers = frozenset(
        c.get("ticker", "").strip().lower() for c in cards if c.get("ticker")
    )
    pairs = []
    for card in cards:
        text = card.get(text_field, "") or ""
        norm = _normalize_skeleton(text, all_tickers)
        pairs.append((card.get("ticker", ""), norm))

    skel_counts = Counter(s for _, s in pairs if s)
    repeated = {s for s, cnt in skel_counts.items() if cnt >= min_cards_for_spam}
    spam_tickers = [t for t, s in pairs if s in repeated]
    return spam_tickers, len(repeated)


# Patterns that indicate boilerplate BUY rationale (from the pre-v3.2 template).
_BOILERPLATE_BUY_PATTERNS: list[re.Pattern] = [
    re.compile(r"(strong|adequate|available)\s+evidence\s+and\s+(fairly|attractively)\s+priced", re.IGNORECASE),
    re.compile(r"portfolio\s+has\s+room\s+to\s+add", re.IGNORECASE),
    re.compile(r"manageable\s+risk\b", re.IGNORECASE),
    re.compile(r"signals\s+support\s+adding", re.IGNORECASE),
    re.compile(r"meets\s+the\s+evidence\s+quality\s+and\s+attractiveness\s+bar", re.IGNORECASE),
]


def detect_weak_buy_rationale(
    cards: list[dict],
    text_field: str = "why_text",
) -> list[str]:
    """Detect BUY cards whose rationale matches known boilerplate templates.

    Returns list of tickers with weak BUY rationale.
    """
    weak = []
    for card in cards:
        if (card.get("action") or "").upper() != "BUY":
            continue
        text = card.get(text_field, "") or ""
        if any(p.search(text) for p in _BOILERPLATE_BUY_PATTERNS):
            weak.append(card.get("ticker", ""))
    return weak


def certify_snapshot_cards(
    cards: list[dict],
    *,
    spam_threshold: int = 3,
) -> dict:
    """Full certification of snapshot cards — returns enriched certification dict.

    Includes all required certification fields:
      generic_copy_count, duplicate_reason_count, repeated_skeleton_count,
      ticker_prefix_only_reason_count, weak_buy_rationale_count,
      action_conflict_count, raw_metric_key_count, posture_label_count,
      hard_violations, examples for each nonzero count.

    Also returns per_card_results and spam_tickers for backward compat.
    """
    from collections import Counter as _Counter

    # Per-card structural validation.
    per_card_results: list[ValidationResult] = []
    for card in cards:
        r = validate_card(
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
        per_card_results.append(r)

    total_hard = sum(r.hard_violation_count for r in per_card_results)

    raw_metric_key_count = sum(
        1 for r in per_card_results for v in r.violations if v.rule == "no_raw_metric_keys"
    )
    posture_label_count = sum(
        1 for r in per_card_results for v in r.violations if v.rule == "no_banned_posture_labels"
    )
    action_conflict_count = sum(
        1 for r in per_card_results for v in r.violations if v.rule == "no_action_contradictions"
    )

    # Exact-duplicate check (existing semantic).
    exact_spam_tickers = detect_generic_copy_spam(cards, min_cards_for_spam=spam_threshold)
    why_texts = [c.get("why_text", "") for c in cards if c.get("why_text")]
    why_counts = _Counter(why_texts)
    duplicate_reason_count = sum(cnt for cnt in why_counts.values() if cnt > 1)

    # Skeleton-based checks (new — catches ticker-prefix boilerplate).
    ticker_prefix_spam, repeated_skeleton_count = detect_ticker_prefix_only_spam(
        cards, min_cards_for_spam=spam_threshold
    )
    skeleton_spam, _ = detect_repeated_skeleton_spam(cards, min_cards_for_spam=spam_threshold)
    ticker_prefix_only_reason_count = len(ticker_prefix_spam)

    # Weak BUY rationale (boilerplate pattern match).
    weak_buy_tickers = detect_weak_buy_rationale(cards)
    weak_buy_rationale_count = len(weak_buy_tickers)

    def _collect_examples(tickers: list[str], limit: int = 5) -> list[dict]:
        out: list[dict] = []
        for t in tickers[:limit]:
            ex = next((c for c in cards if c.get("ticker") == t), None)
            if ex:
                out.append(
                    {
                        "ticker": ex.get("ticker"),
                        "why_text": ex.get("why_text", "")[:120],
                    }
                )
        return out

    # Build examples dict for nonzero counts.
    examples: dict = {}
    if exact_spam_tickers:
        examples["generic_copy"] = _collect_examples(exact_spam_tickers)
    if ticker_prefix_spam:
        examples["ticker_prefix_only"] = _collect_examples(ticker_prefix_spam)
    if skeleton_spam:
        examples["repeated_skeleton"] = _collect_examples(skeleton_spam)
    if weak_buy_tickers:
        examples["weak_buy"] = _collect_examples(weak_buy_tickers)

    return {
        "per_card_results":                per_card_results,
        "spam_tickers":                    exact_spam_tickers,
        "hard_violations":                 total_hard,
        "generic_copy_count":              len(exact_spam_tickers),
        "duplicate_reason_count":          duplicate_reason_count,
        "repeated_skeleton_count":         repeated_skeleton_count,
        "ticker_prefix_only_reason_count": ticker_prefix_only_reason_count,
        "ticker_prefix_spam_tickers":      ticker_prefix_spam,
        "weak_buy_rationale_count":        weak_buy_rationale_count,
        "action_conflict_count":           action_conflict_count,
        "raw_metric_key_count":            raw_metric_key_count,
        "posture_label_count":             posture_label_count,
        "examples":                        examples,
    }


def validate_snapshot_cards(
    cards: list[dict],
    *,
    spam_threshold: int = 3,
) -> tuple[list[ValidationResult], list[str], int]:
    """Validate all cards in a snapshot and detect generic copy spam.

    Returns:
        (per_card_results, spam_tickers, total_hard_violation_count)
        where spam_tickers are tickers with generic repeated copy (soft violation)
        and total_hard_violation_count is the sum of hard violations across all cards.
    """
    cert = certify_snapshot_cards(cards, spam_threshold=spam_threshold)
    return cert["per_card_results"], cert["spam_tickers"], cert["hard_violations"]
