"""Deploy Foundation v1 — domain contracts.

Pure data types only. No IO, no LLM, no DB, no broker.

These types represent the scaffolded action-plan candidates that Deploy
will eventually convert into exact-dollar action plans. In v1, all dollar
fields are null / not-evaluated placeholders.

Intel v3 remains the only Buy/Hold/Trim/Sell authority.
Deploy reads Intel output read-only and emits planning scaffolding only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class DeployPlanStatus(str, Enum):
    """Overall status of a Deploy plan output."""
    SCAFFOLD = "SCAFFOLD"       # Planning scaffold; not an executable trade instruction.
    SUPPRESSED = "SUPPRESSED"   # Evidence suppressed actionability for one or more items.
    HOLD_ONLY = "HOLD_ONLY"     # All Intel decisions are HOLD; no trade candidates.


class DeployActionSource(str, Enum):
    """Source of the action direction for a Deploy plan item."""
    INTEL_V3 = "INTEL_V3"      # Certified Intel v3 visible decision (only valid source in v1).


class DeployActionabilityStatus(str, Enum):
    """Per-ticker actionability classification for Deploy."""
    ACTIONABLE_CANDIDATE = "ACTIONABLE_CANDIDATE"                     # BUY/TRIM/SELL candidate (future planning only).
    NOT_ACTIONABLE_HOLD = "NOT_ACTIONABLE_HOLD"                       # HOLD; maintain/watch only; no trade amounts.
    SUPPRESSED_MISSING_EVIDENCE = "SUPPRESSED_MISSING_EVIDENCE"       # Missing evidence suppresses actionability.
    SUPPRESSED_STALE = "SUPPRESSED_STALE"                             # Stale evidence suppresses actionability.
    SUPPRESSED_WEAK = "SUPPRESSED_WEAK"                               # Weak evidence suppresses actionability.
    SUPPRESSED_BLOCKED = "SUPPRESSED_BLOCKED"                         # Blocked fit/risk suppresses actionability.
    SUPPRESSED_UNSAFE = "SUPPRESSED_UNSAFE"                           # Unsafe / unknown evidence state.
    SCAFFOLD_ONLY = "SCAFFOLD_ONLY"                                   # Placeholder; exact-dollar math not yet evaluated.


@dataclass
class DeployPlanInput:
    """Normalized input for the Deploy translation layer.

    Sourced read-only from a certified Intel v3 decision card.
    Must not be mutated after construction.
    """
    ticker: str
    intel_action: str           # BUY | HOLD | TRIM | SELL (Intel visible action, preserved verbatim)
    intel_conviction: str       # HIGH | MEDIUM | LOW
    intel_evidence_band: str    # STRONG | PARTIAL | THIN (display label from snapshot card)
    intel_snapshot_id: str
    intel_run_id: str

    # Evidence health flags derived from the Intel card.
    has_missing_evidence: bool = False
    has_stale_evidence: bool = False
    has_weak_evidence: bool = False
    is_blocked: bool = False

    # PriceBand context — passed through as supporting info only.
    # Must not drive Deploy action authority.
    price_context_label: Optional[str] = None   # "CHEAP"|"FAIR"|"FULL"|"EXPENSIVE"|"SUPPRESSED"|None

    action_source: DeployActionSource = DeployActionSource.INTEL_V3


@dataclass
class DeployPlanItem:
    """Scaffolded Deploy plan item for one ticker.

    In v1: all dollar/quantity fields are null or 'not_evaluated_yet' placeholders.
    This object is a scaffold only — not an executable trade instruction.
    """
    ticker: str
    intel_action: str                       # Preserved from Intel read-only; not overridden by Deploy.
    actionability_status: DeployActionabilityStatus
    action_source: DeployActionSource
    intel_snapshot_id: str
    intel_run_id: str
    plan_status: DeployPlanStatus

    # Exact-dollar placeholders — null in v1; evaluated in future phases.
    recommended_dollar_amount: Optional[float] = None
    estimated_share_quantity: Optional[float] = None
    rounding_policy: str = "not_applied_yet"
    cash_constraint_status: str = "not_evaluated_yet"
    target_allocation_status: str = "not_evaluated_yet"
    tax_guardrail_status: str = "not_evaluated_yet"
    wash_sale_guardrail_status: str = "not_evaluated_yet"

    # Suppression reason (populated when actionability_status is SUPPRESSED_*).
    suppression_reason: Optional[str] = None

    schema_version: str = "deploy_v1_scaffold"


@dataclass
class DeployGuardrailSummary:
    """Guardrail enforcement state for one Deploy plan run.

    Records that the Deploy/Intel boundary invariants were enforced.
    All boolean flags must be True for a well-formed Deploy plan.
    """
    total_items: int
    buy_candidates: int
    trim_candidates: int
    sell_candidates: int
    hold_items: int
    suppressed_items: int

    hold_never_actionable: bool = True      # HOLD items never have ACTIONABLE_CANDIDATE status.
    dollar_fields_null: bool = True         # All dollar/quantity fields are null in v1.
    priceband_not_authority: bool = True    # PriceBand not used as Deploy decision authority.
    intel_action_preserved: bool = True     # Intel action label preserved read-only in every item.

    schema_version: str = "deploy_v1_scaffold"


@dataclass
class DeployPlan:
    """Complete Deploy plan output for one Intel v3 snapshot.

    Contains all per-ticker scaffolded items and the guardrail summary.
    plan_status reflects the aggregate result.

    This is a scaffold / planning artefact only — not an executable trade instruction.
    """
    snapshot_id: str
    run_id: str
    plan_status: DeployPlanStatus
    items: List[DeployPlanItem] = field(default_factory=list)
    guardrail_summary: Optional[DeployGuardrailSummary] = None
    schema_version: str = "deploy_v1_scaffold"
