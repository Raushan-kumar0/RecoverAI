"""
RecoverAI — RECOVER Stage: Schema (Step 10 prerequisite)

Defines the structured outcome of a RECOVER-stage observation. Contains NO
logic — see recovery_checker.py.

Locked separation (restated, this is the entire point of this module):
    EXECUTE  = "did the authorized API action execute?"      (Step 7)
    RECOVER  = "did the customer actually pay?"                (THIS module)
    MEASURE  = "how much OBSERVED revenue was recovered?"        (Step 10)

A RecoveryResult is never inferred from predicted_recovery_likelihood,
diagnosis_confidence, a recommendation, or Step 2 synthetic ground truth. It
comes ONLY from Razorpay's own reported payment-link status/amount_paid.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class RecoveryStatus(str, Enum):
    NOT_OBSERVED = "not_observed"            # no observation was possible/performed (no real link id, dry-run, etc.)
    PENDING = "pending"                        # observed; Razorpay reports the link as not yet paid (amount_paid == 0)
    PARTIALLY_RECOVERED = "partially_recovered"  # observed; amount_paid > 0 but link not fully paid
    RECOVERED = "recovered"                      # observed; Razorpay status == "paid"
    OBSERVATION_FAILED = "observation_failed"      # a status-check call was attempted but errored (network/API failure)


@dataclass
class RecoveryResult:
    case_id: Optional[str]
    leakage_category: Optional[str]
    recovery_status: RecoveryStatus
    amount_recovered: float                      # rupees; 0.0 unless status is RECOVERED or PARTIALLY_RECOVERED
    observation_source: str                        # e.g. "razorpay_payment_link_status" | "no_link_to_observe" | "dry_run_no_observation"
    payment_link_id: Optional[str] = None
    reason: str = ""
    checked_at: Optional[float] = None
    raw_status_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        d = dict(self.__dict__)
        d["recovery_status"] = self.recovery_status.value
        return d
