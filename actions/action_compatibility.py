"""
RecoverAI — Recovery Action Toolbox: Compatibility Layer (Step 4)

Answers exactly one question per action: "Is this action TECHNICALLY
APPLICABLE to this case?" — i.e. does the case have the data this action
needs, and does the action's nature (e.g. requires customer communication)
conflict with a hard case fact (e.g. opted out)?

ACTION AVAILABILITY != ACTION AUTHORIZATION.

This layer NEVER:
  - checks retry_count against a limit (that's Step 6's retry_limit rule)
  - checks amount against a monetary ceiling (Step 6)
  - checks contact-hour windows (Step 6, runtime)
  - decides AUTO_EXECUTE / APPROVAL_REQUIRED / STOP (Step 6)
  - picks a single best action (Step 5)
  - executes anything (Step 7)

"Repeated payment failure" is a deliberate example of what this layer must
NOT gate on: payment_retry remains technically applicable to a failed_payment
case no matter how many prior retries occurred — whether ANOTHER retry is
permitted is a guardrail decision, not a toolbox decision.
"""

import math
from typing import List, Optional, Dict, Any

from action_models import RecoveryAction, ExecutionStatus
from action_catalog import get_actions_for_category, ACTION_CATALOG

SUCCESSFUL_CATEGORY = "successful"


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _get_case_value(case, field_name):
    if hasattr(case, "get"):
        return case.get(field_name)
    return getattr(case, field_name, None)


def _check_required_fields(case, required_fields: List[str]):
    """Returns (ok: bool, missing: list[str])."""
    missing = [f for f in required_fields if _is_missing(_get_case_value(case, f))]
    return (len(missing) == 0, missing)


def _build_action(case, definition, technically_applicable, reason, diagnosis_context):
    case_id = _get_case_value(case, "case_id")
    leakage_category = _get_case_value(case, "leakage_category")
    action_id = f"{case_id}-{definition.action_type.value}"

    snapshot = {f: _get_case_value(case, f) for f in definition.required_case_fields}

    return RecoveryAction(
        action_id=action_id,
        action_type=definition.action_type,
        case_id=case_id,
        leakage_category=leakage_category,
        purpose=definition.purpose,
        applicable_categories=list(definition.applicable_categories),
        required_inputs=list(definition.required_case_fields),
        risk_level=definition.risk_level,
        money_movement=definition.money_movement,
        customer_communication=definition.customer_communication,
        requires_customer_consent=definition.requires_customer_consent,
        requires_merchant_approval=definition.requires_merchant_approval,
        razorpay_integration_needed=definition.razorpay_integration_needed,
        guardrail_considerations=definition.guardrail_considerations,
        technically_applicable=technically_applicable,
        applicability_reason=reason,
        execution_status=ExecutionStatus.NOT_EXECUTED,
        case_field_snapshot=snapshot,
        diagnosis_context=diagnosis_context,
    )


def _evaluate_compatibility(case, definition):
    """
    Returns (technically_applicable: bool, reason: str).
    Two independent checks, both must pass:
      1. All required_case_fields are present (not missing/NaN).
      2. If the action involves customer_communication, communication_allowed
         must be True on the case (opt-out / suspicious-flag safe by construction).
    Neither check references retry_count, amounts vs. limits, or contact hours —
    those are guardrail (Step 6) concerns, not toolbox concerns.
    """
    fields_ok, missing = _check_required_fields(case, definition.required_case_fields)
    if not fields_ok:
        return False, f"missing required case field(s): {missing}"

    if definition.customer_communication:
        comm_allowed = _get_case_value(case, "communication_allowed")
        # comm_allowed may be a numpy.bool_ (pandas-sourced), plain bool, or
        # int; normalize with bool() rather than an `is True` identity check,
        # which fails for numpy.bool_.
        if not bool(comm_allowed):
            return False, "customer communication not permitted for this case (opt-out or suspicious flag)"

    return True, "compatible: all required fields present and communication constraints satisfied"


def get_actions_for_case(case, diagnosis: Optional[Dict[str, Any]] = None) -> List[RecoveryAction]:
    """
    Returns the list of RecoveryAction instances whose action TYPE applies to
    this case's leakage_category, each annotated with whether it is
    technically applicable right now and why. Includes both applicable and
    inapplicable actions (flagged accordingly) so Step 5 has full visibility.

    Successful/non-leakage cases have nothing at risk and return an empty list.

    `diagnosis`, if provided, must be the dict returned by
    DiagnosisEngine.diagnose(case) for this SAME case. It is attached to each
    returned action purely as read-only informational context (e.g. so Step 5
    doesn't need to re-run diagnosis) — it never influences technical
    applicability, which is determined solely from case facts.
    """
    leakage_category = _get_case_value(case, "leakage_category")
    if leakage_category is None:
        raise ValueError("case is missing required field: leakage_category")

    if leakage_category == SUCCESSFUL_CATEGORY:
        return []

    known_categories = {c for d in ACTION_CATALOG.values() for c in d.applicable_categories}
    if leakage_category not in known_categories:
        raise ValueError(
            f"Unknown leakage_category: {leakage_category!r}. "
            f"No actions are defined for this category."
        )

    diagnosis_context = None
    if diagnosis is not None:
        case_id = _get_case_value(case, "case_id")
        if diagnosis.get("case_id") is not None and case_id is not None and diagnosis["case_id"] != case_id:
            raise ValueError(
                f"diagnosis case_id ({diagnosis.get('case_id')!r}) does not match "
                f"case case_id ({case_id!r}); refusing to attach mismatched diagnosis context."
            )
        diagnosis_context = {
            "predicted_recovery_likelihood": diagnosis.get("predicted_recovery_likelihood"),
            "diagnosis_confidence": diagnosis.get("diagnosis_confidence"),
            "root_cause": diagnosis.get("root_cause"),
        }

    definitions = get_actions_for_category(leakage_category)
    actions = []
    for definition in definitions:
        applicable, reason = _evaluate_compatibility(case, definition)
        actions.append(_build_action(case, definition, applicable, reason, diagnosis_context))
    return actions


def get_applicable_actions(case, diagnosis: Optional[Dict[str, Any]] = None) -> List[RecoveryAction]:
    """Convenience filter: only the technically_applicable subset."""
    return [a for a in get_actions_for_case(case, diagnosis) if a.technically_applicable]
