"""
RecoverAI — Razorpay Test-Mode Integration: Execution Orchestration (Step 7)

The single entry point that connects a Guardrail-authorized recommendation to
safe sandbox execution. This is the ONLY place in the project allowed to
touch a Razorpay client.

Hard rules enforced here, in order:
    1. Guardrail outcome must be AUTO_EXECUTE. APPROVAL_REQUIRED and STOP are
       both refused identically — this layer never bypasses APPROVAL_REQUIRED
       and never executes on STOP. (A future workflow where a merchant
       actually approves an APPROVAL_REQUIRED case would re-run Step 6 to
       produce a fresh AUTO_EXECUTE outcome before reaching this function —
       that re-authorization flow is not built here.)
    2. The action being executed must be exactly the one Step 5 recommended
       and Step 6 authorized — case_id and action_type are cross-checked
       across decision, guardrail_decision, and case. No substitution.
    3. Only actions Step 4 marked `razorpay_integration_needed=True`
       (payment_retry, mandate_retry, recovery_payment_link) are supported
       here at all. Everything else is rejected as out of scope for this
       module (they don't need Razorpay).
    4. No ground-truth field (amount_recovered, ground_truth_recoverable,
       ground_truth_recovery_outcome, recovery_observed, recovery_reason) is
       ever read from `case`.

Result-source labeling (so nothing is ever misrepresented):
    "razorpay_test_mode_dry_run"   -> no network call made (default safety)
    "razorpay_test_mode_api"        -> a real Razorpay TEST MODE API call was made
    "bounded_simulation"             -> payment_retry/mandate_retry (no real endpoint exists)
    "not_executed"                    -> refused before any call (STOP/APPROVAL_REQUIRED/mismatch/unsupported)
Live-mode results are never produced by this module — see razorpay_config.py's
unconditional live-key rejection.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from razorpay_client import RazorpayTestModeClient

SUPPORTED_ACTION_TYPES = {"payment_retry", "mandate_retry", "recovery_payment_link"}
SIMULATED_ACTION_TYPES = {"payment_retry", "mandate_retry"}


def _get(obj, field_name):
    if hasattr(obj, "get"):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


@dataclass
class ExecutionRecord:
    case_id: Optional[str]
    leakage_category: Optional[str]
    action_type: Optional[str]
    guardrail_outcome: Optional[str]
    execution_status: str            # not_executed | dry_run | simulated | executed | api_error | error
    result_source: str                # razorpay_test_mode_dry_run | razorpay_test_mode_api | bounded_simulation | not_executed
    reason: str
    razorpay_result: Dict[str, Any] = field(default_factory=dict)
    executed_at: Optional[float] = None
    mode: str = "test"

    def to_dict(self):
        return dict(self.__dict__)


def _refuse(case_id, leakage_category, action_type, guardrail_outcome, reason) -> ExecutionRecord:
    return ExecutionRecord(
        case_id=case_id, leakage_category=leakage_category, action_type=action_type,
        guardrail_outcome=guardrail_outcome, execution_status="not_executed",
        result_source="not_executed", reason=reason, razorpay_result={}, executed_at=time.time(),
    )


def execute_guardrail_approved_action(case, decision, guardrail_decision, client: RazorpayTestModeClient) -> ExecutionRecord:
    """
    case:               dict or pandas.Series (pre-decision fields only).
    decision:            Step 5 Decision.
    guardrail_decision:   Step 6 GuardrailDecision.
    client:                a RazorpayTestModeClient (test-mode credentials only).
    """
    case_id = _get(case, "case_id")
    leakage_category = _get(case, "leakage_category")

    guardrail_outcome = _get(guardrail_decision, "outcome")
    guardrail_outcome_value = guardrail_outcome.value if hasattr(guardrail_outcome, "value") else guardrail_outcome

    # ---- Rule 1: guardrail outcome must be AUTO_EXECUTE ----
    if guardrail_outcome_value != "auto_execute":
        return _refuse(
            case_id, leakage_category, _get(guardrail_decision, "recommended_action_type"), guardrail_outcome_value,
            f"Refusing to execute: guardrail outcome is {guardrail_outcome_value!r}, not AUTO_EXECUTE. "
            f"STOP and APPROVAL_REQUIRED are never executed by this layer."
        )

    # ---- Rule 2: cross-check identity across decision/guardrail/case ----
    decision_status = _get(decision, "decision_status")
    decision_status_value = decision_status.value if hasattr(decision_status, "value") else decision_status
    if decision_status_value != "recommended":
        return _refuse(case_id, leakage_category, None, guardrail_outcome_value,
                        f"Refusing to execute: decision_status is {decision_status_value!r}, not 'recommended'.")

    decision_case_id = _get(decision, "case_id")
    guardrail_case_id = _get(guardrail_decision, "case_id")
    if not (case_id == decision_case_id == guardrail_case_id):
        return _refuse(case_id, leakage_category, None, guardrail_outcome_value,
                        f"Refusing to execute: case_id mismatch across case ({case_id!r}), "
                        f"decision ({decision_case_id!r}), guardrail ({guardrail_case_id!r}).")

    decision_action_type = _get(decision, "recommended_action_type")
    guardrail_action_type = _get(guardrail_decision, "recommended_action_type")
    if decision_action_type != guardrail_action_type:
        return _refuse(case_id, leakage_category, decision_action_type, guardrail_outcome_value,
                        f"Refusing to execute: action_type mismatch between decision "
                        f"({decision_action_type!r}) and guardrail authorization ({guardrail_action_type!r}).")

    action_type = decision_action_type

    # ---- Rule 3: only Razorpay-integration actions are in scope here ----
    if action_type not in SUPPORTED_ACTION_TYPES:
        return _refuse(case_id, leakage_category, action_type, guardrail_outcome_value,
                        f"Action type {action_type!r} does not require Razorpay integration "
                        f"(not in {sorted(SUPPORTED_ACTION_TYPES)}); nothing to execute here.")

    amount_at_risk = _get(case, "amount_at_risk")

    # ---- Dispatch (Rule 4 is structural: only pre-decision fields read above) ----
    try:
        if action_type in SIMULATED_ACTION_TYPES:
            result = client.simulate_retry_operation(action_type, case_id, amount_at_risk)
            result_source = "bounded_simulation"
            execution_status = "simulated"
        else:  # recovery_payment_link
            # Razorpay enforces uniqueness on reference_id per Payment Link.
            # Using bare case_id would make a case fail with a real
            # "already exists" error on any re-run (e.g. re-testing the same
            # demo case twice). We keep case_id as the canonical identifier
            # everywhere else (ExecutionRecord.case_id, audit trail, etc.) —
            # only Razorpay's own dedup field gets a per-attempt suffix, so
            # each execution attempt is a genuinely distinct Payment Link.
            unique_reference_id = f"{case_id}-{uuid.uuid4().hex[:10]}"
            result = client.create_payment_link(
                amount_rupees=amount_at_risk,
                description=f"RecoverAI payment recovery link for case {case_id}",
                reference_id=unique_reference_id,
            )
            if result["status"] == "dry_run":
                result_source = "razorpay_test_mode_dry_run"
                execution_status = "dry_run"
            elif result["status"] == "executed":
                result_source = "razorpay_test_mode_api"
                execution_status = "executed"
            else:
                result_source = "razorpay_test_mode_api"
                execution_status = result["status"]  # api_error | error
    except Exception as e:  # noqa: BLE001 — deliberately broad: must never crash the pipeline
        return ExecutionRecord(
            case_id=case_id, leakage_category=leakage_category, action_type=action_type,
            guardrail_outcome=guardrail_outcome_value, execution_status="error",
            result_source="not_executed",
            reason=f"Unexpected error during execution: {type(e).__name__}",
            razorpay_result={}, executed_at=time.time(),
        )

    return ExecutionRecord(
        case_id=case_id, leakage_category=leakage_category, action_type=action_type,
        guardrail_outcome=guardrail_outcome_value, execution_status=execution_status,
        result_source=result_source,
        reason="Executed via the guardrail-approved path.",
        razorpay_result=result, executed_at=time.time(),
    )
