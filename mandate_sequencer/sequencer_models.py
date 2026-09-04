"""
RecoverAI — Mandate Retry Sequencer: Schema (Extension, post-Step 12)

Defines the structured representation of a multi-attempt mandate-retry
sequence for `failed_subscription` cases. This module contains NO logic
that decides whether/when to advance a sequence, and it never touches
Razorpay directly — it only defines what a sequence and an attempt ARE.

Design constraints (matching the rest of the project's honesty rules):
  - Every individual attempt still goes through the EXISTING, already-tested
    `client.simulate_retry_operation()` (Step 7) — this module adds
    scheduling/tracking on top, it never invents a new fake Razorpay result.
  - A sequence's attempts are NEVER counted as "recovered" by themselves —
    `mandate_retry` still has no real Razorpay endpoint. The only way a
    sequence can ever reach genuinely RECOVERED is via its fallback
    Payment Link (a real, executable `recovery_payment_link` action),
    checked the same way every other Payment Link in this project is:
    through `observe_recovery()`, never inferred.
  - Sequence state is stored SEPARATELY from `real_results_log.jsonl`
    (see `sequencer_store.py`) so retry-attempt bookkeeping can never be
    confused with, or accidentally summed into, genuine recovered revenue.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


class SequenceStatus(str, Enum):
    PENDING = "pending"                        # attempts remain, none yet due or in progress
    ATTEMPT_SCHEDULED = "attempt_scheduled"     # waiting for the next attempt's scheduled time
    EXHAUSTED = "exhausted"                     # all attempts used, none succeeded (mandate_retry can't self-report success anyway)
    FALLBACK_TRIGGERED = "fallback_triggered"   # attempts exhausted; a real recovery_payment_link fallback was executed
    FALLBACK_RECOVERED = "fallback_recovered"   # the fallback Payment Link was independently observed as paid


@dataclass
class RetryAttempt:
    """One simulated mandate-retry attempt within a sequence."""
    attempt_number: int                   # 1-indexed
    scheduled_offset_days: int            # e.g. 0, 3, 7 — offset from sequence start
    scheduled_at: float                    # unix timestamp this attempt becomes due
    executed_at: Optional[float] = None    # unix timestamp actually run, or None if not yet run
    result_source: Optional[str] = None    # always "bounded_simulation" once run — never fabricated as real
    raw_result: Dict[str, Any] = field(default_factory=dict)  # client.simulate_retry_operation()'s own output, verbatim

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class MandateRetrySequence:
    """
    The full retry history + current state for one case. One of these
    exists per case_id that has ever entered the sequencer.
    """
    case_id: str
    leakage_category: str
    amount_at_risk: float
    started_at: float
    max_attempts: int
    action_type: str = "mandate_retry"
    attempts: List[RetryAttempt] = field(default_factory=list)
    status: SequenceStatus = SequenceStatus.PENDING
    fallback_execution_record: Optional[Dict[str, Any]] = None  # real ExecutionRecord.to_dict(), if fallback ran
    fallback_recovery_result: Optional[Dict[str, Any]] = None   # real RecoveryResult.to_dict(), if fallback was checked

    def to_dict(self):
        d = dict(self.__dict__)
        d["attempts"] = [a.to_dict() for a in self.attempts]
        d["status"] = self.status.value if hasattr(self.status, "value") else self.status
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "MandateRetrySequence":
        attempts = [RetryAttempt(**a) for a in d.get("attempts", [])]
        return MandateRetrySequence(
            case_id=d["case_id"],
            leakage_category=d.get("leakage_category"),
            amount_at_risk=float(d.get("amount_at_risk", 0.0)),
            started_at=d["started_at"],
            max_attempts=d["max_attempts"],
            action_type=d.get("action_type", "mandate_retry"),
            attempts=attempts,
            status=SequenceStatus(d.get("status", "pending")),
            fallback_execution_record=d.get("fallback_execution_record"),
            fallback_recovery_result=d.get("fallback_recovery_result"),
        )
