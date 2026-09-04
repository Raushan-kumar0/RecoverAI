"""
RecoverAI — Graceful Failure Handling (Step 9)

Implements:
    AI attempts recovery
        -> Razorpay Test API fails
        -> RecoverAI detects the failure
        -> No uncontrolled/repeated retry
        -> Failure recorded in the Step 8 audit trail
        -> Fallback/alternate action selected where appropriate
        -> Escalate if fallback is not safe or permitted

This module adds NO new capability of its own — it only ORCHESTRATES
existing, already-tested Steps 4-8:
    - Step 4 (action_compatibility.get_actions_for_case) to find fallback candidates
    - Step 5 (Decision/DecisionStatus) to represent a fallback/escalation recommendation
    - Step 6 (GuardrailEngine.authorize) to re-authorize EVERY fallback/escalation
      action — the fallback is never executed without going through the same
      guardrail check the primary action went through. No guardrail bypass.
    - Step 7 (execute_guardrail_approved_action) to actually attempt execution —
      this is still the ONLY function that ever touches the Razorpay client.
    - Step 8 (record_decision/record_guardrail/record_execution) to log every
      attempt (primary, fallback, escalation) to the audit trail.

CRITICAL DISTINCTION (tested explicitly): a genuine Razorpay execution
FAILURE (execution_status in {"api_error", "error"} — we tried to call
Razorpay and it broke) is NOT the same thing as a guardrail-blocked
non-execution (execution_status == "not_executed" because the outcome was
STOP or APPROVAL_REQUIRED). Fallback/escalation logic here triggers ONLY on
a genuine failure. A STOP or APPROVAL_REQUIRED primary outcome is a correct
policy decision, not a failure, and this module does not try to "route
around" it — it is reported as NO_FAILURE (nothing to recover from) and left
exactly as Step 6 decided.

BOUNDED, NEVER LOOPING: at most one fallback action is attempted (never a
retry of the same failed action, never a sweep through every alternative),
and if that doesn't succeed, the flow terminates in ESCALATED — which itself
never calls Razorpay (escalation has `razorpay_integration_needed=False` in
the Step 4 catalog). So a case can touch the Razorpay client at most twice
here: once for the primary action, once for a fallback, ever.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "audit"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))

from typing import Optional
from datetime import datetime

from action_compatibility import get_actions_for_case  # noqa: E402
from decision_models import Decision, DecisionStatus, LikelihoodTier  # noqa: E402
from razorpay_execution import execute_guardrail_approved_action  # noqa: E402
from audit_recorder import record_decision, record_guardrail, record_execution  # noqa: E402

from failure_models import FailureHandlingOutcome, FailureHandlingResult

FAILURE_EXECUTION_STATUSES = {"api_error", "error"}
SUCCESS_EXECUTION_STATUSES = {"dry_run", "simulated", "executed"}


def _get(obj, field_name):
    if hasattr(obj, "get"):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


def _build_recommendation(case_id, leakage_category, action, reason, diagnosis) -> Decision:
    """Wraps a Step 4 RecoveryAction as a Step 5-shaped Decision, so it can be
    re-authorized by the real GuardrailEngine exactly like any other
    recommendation. `action` here is a real RecoveryAction from
    get_actions_for_case — never invented."""
    return Decision(
        case_id=case_id,
        leakage_category=leakage_category,
        decision_status=DecisionStatus.RECOMMENDED,
        recommended_action_type=action.action_type.value,
        recommended_action=action.to_dict(),
        likelihood_tier=diagnosis.get("likelihood_tier", LikelihoodTier.NOT_APPLICABLE) if isinstance(diagnosis, dict) else LikelihoodTier.NOT_APPLICABLE,
        predicted_recovery_likelihood=diagnosis.get("predicted_recovery_likelihood") if diagnosis else None,
        diagnosis_confidence=diagnosis.get("diagnosis_confidence") if diagnosis else None,
        recommendation_reason=reason,
        alternatives_considered=[],
    )


def handle_execution_with_fallback(case, diagnosis, decision, guardrail_decision,
                                    razorpay_client, guardrail_engine, audit_store,
                                    current_time: Optional[datetime] = None) -> FailureHandlingResult:
    """
    Executes the primary guardrail-approved recommendation via Step 7. If —
    and only if — that execution genuinely fails (a real attempted Razorpay
    call came back as api_error/error), attempts exactly one bounded
    fallback action (re-authorized by Step 6), and escalates if no safe
    fallback exists or the fallback also fails. Every attempt is recorded to
    the Step 8 audit trail.
    """
    current_time = current_time or datetime.now()
    case_id = _get(case, "case_id")
    leakage_category = _get(case, "leakage_category")
    calls_made = 0

    # ---- Primary execution (Step 7, unchanged) ----
    primary_execution = execute_guardrail_approved_action(case, decision, guardrail_decision, razorpay_client)
    record_execution(audit_store, primary_execution)
    if primary_execution.execution_status != "not_executed":
        calls_made += 1

    if primary_execution.execution_status not in FAILURE_EXECUTION_STATUSES:
        # Either it succeeded, or it was correctly blocked by guardrail
        # (STOP/APPROVAL_REQUIRED) — neither is a "failure" in the Step 9
        # sense, and neither triggers fallback/escalation logic.
        return FailureHandlingResult(
            case_id=case_id, leakage_category=leakage_category,
            outcome=FailureHandlingOutcome.NO_FAILURE,
            reason=f"Primary execution status was '{primary_execution.execution_status}' — not a genuine "
                   f"Razorpay execution failure; no fallback/escalation triggered.",
            primary_execution=primary_execution.to_dict(),
            razorpay_calls_made=calls_made,
        )

    # ---- Genuine failure detected. Look for exactly one fallback candidate. ----
    failed_action_type = _get(decision, "recommended_action_type")
    candidate_actions = get_actions_for_case(case, diagnosis=diagnosis)
    fallback_candidates = [
        a for a in candidate_actions
        if a.technically_applicable
        and a.action_type.value != failed_action_type
        and a.action_type.value != "escalation"  # escalation is the last resort, tried separately below
    ]
    # Deterministic choice: first candidate in Step 4's catalog-defined order
    # (get_actions_for_case already returns them in a fixed order — no
    # randomness, no sweeping through every alternative).
    fallback_action = fallback_candidates[0] if fallback_candidates else None

    fallback_execution_dict = None
    fallback_action_type = None
    if fallback_action is not None:
        fallback_action_type = fallback_action.action_type.value
        fallback_decision = _build_recommendation(
            case_id, leakage_category, fallback_action,
            f"Fallback after '{failed_action_type}' execution failure ({primary_execution.reason}).",
            diagnosis,
        )
        record_decision(audit_store, fallback_decision)

        diag_for_guard = {"diagnosis_confidence": _get(decision, "diagnosis_confidence")}
        fallback_guardrail = guardrail_engine.authorize(case, diag_for_guard, fallback_decision, current_time=current_time)
        record_guardrail(audit_store, fallback_guardrail)

        # Always route through Step 7 — it independently enforces the
        # AUTO_EXECUTE requirement, so a STOP/APPROVAL_REQUIRED fallback is
        # safely refused here too, with zero duplicated guardrail logic.
        fallback_execution = execute_guardrail_approved_action(case, fallback_decision, fallback_guardrail, razorpay_client)
        record_execution(audit_store, fallback_execution)
        if fallback_execution.execution_status != "not_executed":
            calls_made += 1
        fallback_execution_dict = fallback_execution.to_dict()

        if fallback_execution.execution_status in SUCCESS_EXECUTION_STATUSES:
            return FailureHandlingResult(
                case_id=case_id, leakage_category=leakage_category,
                outcome=FailureHandlingOutcome.FALLBACK_SUCCEEDED,
                reason=f"Primary action '{failed_action_type}' failed; fallback '{fallback_action_type}' succeeded.",
                primary_execution=primary_execution.to_dict(),
                fallback_attempted=True, fallback_action_type=fallback_action_type,
                fallback_execution=fallback_execution_dict,
                razorpay_calls_made=calls_made,
            )
        # Fallback was blocked by guardrail, or itself failed -> fall through to escalation.

    # ---- No safe/permitted fallback, or it also failed -> escalate. ----
    escalation_candidates = [a for a in candidate_actions if a.action_type.value == "escalation"]
    if not escalation_candidates:
        # Structurally shouldn't happen (Step 4 guarantees escalation is always
        # technically applicable to every leakage category), but fail safely.
        return FailureHandlingResult(
            case_id=case_id, leakage_category=leakage_category,
            outcome=FailureHandlingOutcome.ESCALATED,
            reason="Primary and fallback both failed/unavailable, and no escalation action was found "
                   "(unexpected — escalation should always be available).",
            primary_execution=primary_execution.to_dict(),
            fallback_attempted=fallback_action is not None, fallback_action_type=fallback_action_type,
            fallback_execution=fallback_execution_dict, escalated=True,
            razorpay_calls_made=calls_made,
        )

    escalation_action = escalation_candidates[0]
    escalation_reason = (
        f"Escalating case {case_id}: primary action '{failed_action_type}' failed"
        + (f", fallback '{fallback_action_type}' was not safe/permitted or also failed" if fallback_action is not None
           else " and no technically-applicable fallback action was available")
        + "."
    )
    escalation_decision = _build_recommendation(case_id, leakage_category, escalation_action, escalation_reason, diagnosis)
    record_decision(audit_store, escalation_decision)

    diag_for_guard = {"diagnosis_confidence": _get(decision, "diagnosis_confidence")}
    escalation_guardrail = guardrail_engine.authorize(case, diag_for_guard, escalation_decision, current_time=current_time)
    record_guardrail(audit_store, escalation_guardrail)

    # Escalation never calls Razorpay (razorpay_integration_needed=False in
    # the Step 4 catalog) — Step 7 will correctly return not_executed here
    # since escalation's guardrail outcome is APPROVAL_REQUIRED by
    # definition, never AUTO_EXECUTE.
    escalation_execution = execute_guardrail_approved_action(case, escalation_decision, escalation_guardrail, razorpay_client)
    record_execution(audit_store, escalation_execution)
    if escalation_execution.execution_status != "not_executed":
        calls_made += 1  # structurally should never happen for escalation, but counted honestly if it did

    return FailureHandlingResult(
        case_id=case_id, leakage_category=leakage_category,
        outcome=FailureHandlingOutcome.ESCALATED,
        reason=escalation_reason,
        primary_execution=primary_execution.to_dict(),
        fallback_attempted=fallback_action is not None, fallback_action_type=fallback_action_type,
        fallback_execution=fallback_execution_dict,
        escalated=True, escalation_execution=escalation_execution.to_dict(),
        razorpay_calls_made=calls_made,
    )
