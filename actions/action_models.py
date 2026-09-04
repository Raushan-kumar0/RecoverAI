"""
RecoverAI — Recovery Action Toolbox: Schema (Step 4)

Defines the structured, machine-readable representation of a recovery action.
This module contains NO logic that selects, authorizes, or executes an
action — it only defines what an action IS.

Locked separation (Step 1 §5, restated here):
    DETERMINISTIC FACTS      -> the case
    AI/MODEL REASONING       -> Step 3 diagnosis
    AI RECOMMENDATION        -> Step 5 decision engine (not yet built)
    DETERMINISTIC POLICY     -> Step 6 guardrails (not yet built)
    ACTUAL RAZORPAY RESULT   -> Step 7 execution (not yet built)

Step 4 is a deterministic CATALOG. It does not recommend, authorize, or act.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any


class ActionType(str, Enum):
    PAYMENT_RETRY = "payment_retry"
    MANDATE_RETRY = "mandate_retry"
    RECOVERY_PAYMENT_LINK = "recovery_payment_link"
    PAYMENT_REMINDER = "payment_reminder"
    CHECKOUT_RECOVERY_REMINDER = "checkout_recovery_reminder"
    RECEIVABLES_FOLLOWUP = "receivables_followup"
    ESCALATION = "escalation"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionStatus(str, Enum):
    # Step 4 only ever produces NOT_EXECUTED. Additional statuses
    # (EXECUTED, FAILED, APPROVAL_PENDING, ...) belong to Step 7+ and are
    # deliberately not defined here to avoid implying execution logic exists.
    NOT_EXECUTED = "not_executed"


@dataclass(frozen=True)
class ActionDefinition:
    """
    Static catalog metadata for an action TYPE (not tied to any specific case).
    One of these exists per ActionType, defined in action_catalog.py.
    """
    action_type: ActionType
    purpose: str
    applicable_categories: List[str]           # leakage_category values this action can apply to
    required_case_fields: List[str]             # case fields that must be present/valid for this action
    expected_effect: str
    risk_level: RiskLevel
    money_movement: bool                         # does executing this action directly attempt to move money?
    customer_communication: bool                  # does executing this action contact the customer?
    requires_customer_consent: bool                # mirrors customer_communication — communication requires consent
    requires_merchant_approval: bool                # INHERENT/DEFINITIONAL only — e.g. escalation is by definition
                                                      # a human handoff. This is NOT a computed guardrail decision;
                                                      # Step 6 independently decides per-case approval requirements
                                                      # for every action type, including ones marked False here.
    razorpay_integration_needed: bool                 # will Step 7 need a Razorpay API call for this action?
    guardrail_considerations: str                      # free-text notes for Step 6 — not implemented here


@dataclass
class RecoveryAction:
    """
    A specific action instance generated for a specific case. Represents an
    AVAILABLE, NOT-YET-AUTHORIZED, NOT-YET-EXECUTED option. Step 5 will later
    choose among a case's RecoveryActions; Step 6 will authorize; Step 7 will
    execute. This object never causes any of those things to happen.
    """
    action_id: str
    action_type: ActionType
    case_id: str
    leakage_category: str
    purpose: str
    applicable_categories: List[str]
    required_inputs: List[str]
    risk_level: RiskLevel
    money_movement: bool
    customer_communication: bool
    requires_customer_consent: bool
    requires_merchant_approval: bool
    razorpay_integration_needed: bool
    guardrail_considerations: str

    technically_applicable: bool                  # output of the Step 4 compatibility layer
    applicability_reason: str                       # why it is/isn't technically applicable

    execution_status: ExecutionStatus = ExecutionStatus.NOT_EXECUTED
    case_field_snapshot: Dict[str, Any] = field(default_factory=dict)  # audit-friendly snapshot of required_inputs values
    diagnosis_context: Optional[Dict[str, Any]] = None  # optional read-only diagnosis info, informational only

    def to_dict(self):
        d = dict(self.__dict__)
        d["action_type"] = self.action_type.value
        d["risk_level"] = self.risk_level.value
        d["execution_status"] = self.execution_status.value
        return d
