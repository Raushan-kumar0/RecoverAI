"""
RecoverAI — Decision Engine: Schema (Step 5)

Defines the structured output of a recommendation. Contains NO decision
logic itself — see decision_engine.py for that.

Locked separation (restated):
    AI/model reasoning   -> Step 3 diagnosis (predicted_recovery_likelihood, diagnosis_confidence)
    AI recommendation    -> Step 5 (THIS layer) — picks one action, explains why, NOT authorized
    Guardrail authorization -> Step 6 (AUTO_EXECUTE / APPROVAL_REQUIRED / STOP) — NOT built here
    Execution            -> Step 7

DecisionStatus is deliberately a different vocabulary from Step 6's future
AUTO_EXECUTE/APPROVAL_REQUIRED/STOP, so the two layers can never be confused
by a future reader.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any


class DecisionStatus(str, Enum):
    RECOMMENDED = "recommended"                    # a single action was selected
    NO_APPLICABLE_ACTIONS = "no_applicable_actions"  # actions existed for the category but none were technically applicable
    NOT_APPLICABLE = "not_applicable"                 # case has nothing at risk (successful/non-leakage)


class LikelihoodTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class AlternativeConsidered:
    """One entry in the audit trail of what else was considered and why it wasn't chosen."""
    action_type: str
    technically_applicable: bool
    priority_rank: Optional[int]   # position in the tier's priority list (0 = most preferred), None if not in tier's list
    reason_not_chosen: str


@dataclass
class Decision:
    """The structured Step 5 output for a single case. This is a RECOMMENDATION,
    not an authorization — nothing here permits execution."""
    case_id: Optional[str]
    leakage_category: str
    decision_status: DecisionStatus

    recommended_action_type: Optional[str]
    recommended_action: Optional[Dict[str, Any]]   # full RecoveryAction.to_dict(), or None

    likelihood_tier: LikelihoodTier
    predicted_recovery_likelihood: Optional[float]
    diagnosis_confidence: Optional[float]

    recommendation_reason: str
    alternatives_considered: List[AlternativeConsidered] = field(default_factory=list)

    def to_dict(self):
        d = dict(self.__dict__)
        d["decision_status"] = self.decision_status.value
        d["likelihood_tier"] = self.likelihood_tier.value
        d["alternatives_considered"] = [dict(a.__dict__) for a in self.alternatives_considered]
        return d
