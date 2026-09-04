"""
RecoverAI — Graceful Failure Handling: Schema (Step 9)

Defines the structured outcome of a failure-handling attempt. Contains NO
logic — see failure_handler.py.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class FailureHandlingOutcome(str, Enum):
    NO_FAILURE = "no_failure"                    # primary execution succeeded (or was correctly blocked by guardrail — not a failure)
    FALLBACK_SUCCEEDED = "fallback_succeeded"       # primary failed, one alternate action was tried and succeeded
    ESCALATED = "escalated"                          # primary failed, no safe/permitted fallback existed (or it also failed) -> escalated


@dataclass
class FailureHandlingResult:
    case_id: Optional[str]
    leakage_category: Optional[str]
    outcome: FailureHandlingOutcome
    reason: str

    primary_execution: Dict[str, Any] = field(default_factory=dict)
    fallback_attempted: bool = False
    fallback_action_type: Optional[str] = None
    fallback_execution: Optional[Dict[str, Any]] = None
    escalated: bool = False
    escalation_execution: Optional[Dict[str, Any]] = None

    razorpay_calls_made: int = 0   # bounded-retry proof: counts real client calls this handler made

    def to_dict(self):
        d = dict(self.__dict__)
        d["outcome"] = self.outcome.value
        return d
