"""
RecoverAI — Guardrail Engine: Schema (Step 6)

Defines the structured, audit-ready output of an authorization decision.
Contains NO decision logic — see guardrail_engine.py.

Locked separation (restated):
    Step 3 = AI/model diagnosis
    Step 5 = action recommendation (NOT authorized)
    Step 6 = deterministic authorization (THIS layer) — never executes
    Step 7 = Razorpay execution (NOT built here)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any


class AuthorizationOutcome(str, Enum):
    AUTO_EXECUTE = "auto_execute"
    APPROVAL_REQUIRED = "approval_required"
    STOP = "stop"


@dataclass
class TriggeredRule:
    rule: str
    description: str


@dataclass
class GuardrailDecision:
    case_id: Optional[str]
    leakage_category: Optional[str]
    recommended_action_type: Optional[str]

    outcome: AuthorizationOutcome
    reason: str
    triggered_rules: List[TriggeredRule] = field(default_factory=list)
    limits_checked: Dict[str, Any] = field(default_factory=dict)

    approval_required: bool = False
    config_used: Dict[str, Any] = field(default_factory=dict)
    evaluated_at: Optional[str] = None

    def to_dict(self):
        d = dict(self.__dict__)
        d["outcome"] = self.outcome.value
        d["triggered_rules"] = [dict(r.__dict__) for r in self.triggered_rules]
        return d
