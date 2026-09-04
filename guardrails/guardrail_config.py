"""
RecoverAI — Guardrail Engine: Configuration (Step 6)

All thresholds here are CONFIGURABLE DESIGN DEFAULTS, restated from the
locked Step 1 specification (§6). They are explicitly NOT empirically
validated — no real merchant outcome data exists to calibrate them against.
They exist so the guardrail engine has a sane, adjustable starting point.

Nothing in this file enforces anything by itself — see guardrail_engine.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardrailConfig:
    # Max automated payment/mandate retries for a single case before
    # further autonomous retries are stopped outright.
    retry_limit: int = 3

    # Max total autonomous recovery touches (any action type/channel) on a
    # case within the lookback window before mandatory human escalation
    # (Step 1 §6: "before mandatory escalation" — modeled here as
    # APPROVAL_REQUIRED rather than STOP; see guardrail_engine.py rationale).
    autonomous_attempt_cap: int = 3
    attempt_cap_window_days: int = 7

    # Minimum Step 3 diagnosis_confidence required for AUTO_EXECUTE
    # eligibility. Below this, the case falls to APPROVAL_REQUIRED.
    confidence_threshold: float = 0.6

    # Amount (INR) above which a money-movement action (payment_retry,
    # mandate_retry) requires merchant approval rather than auto-executing.
    # Chosen as a round number just above the 90th percentile of
    # amount_at_risk in the Step 2 dataset (~₹12,422) — a reasonable
    # starting point, NOT a validated cost-effectiveness threshold.
    monetary_ceiling: float = 15000.0

    # Customer-facing communication actions are only auto-executable within
    # this local-hour window. Outside it, the action is not authorized now
    # (mapped to STOP — see guardrail_engine.py; true "schedule for later"
    # queuing belongs to a future orchestrator, not this tri-state engine).
    contact_window_start_hour: int = 9
    contact_window_end_hour: int = 20


DEFAULT_GUARDRAIL_CONFIG = GuardrailConfig()
