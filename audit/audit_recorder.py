"""
RecoverAI — Audit Trail: Recorder (Step 8)

High-level, per-stage recording functions. Each function takes the SAME
objects Steps 3-7 already produce (diagnosis dict, RecoveryAction list,
Decision, GuardrailDecision, ExecutionRecord) and turns them into one
AuditStore event, with a human-readable one-line `summary` a judge can read
directly plus the full structured `payload` for anyone who wants detail.

No pipeline/decision logic lives here — this module only records what
already happened elsewhere. It does not call the diagnosis model, the
decision engine, the guardrail engine, or Razorpay.

LEAKAGE PREVENTION: record_detection() builds its payload EXCLUSIVELY from
feature_config.PRE_DECISION_FEATURES plus case_id — never from the raw case
dict as-is, which (for Step 2 synthetic rows) also contains ground-truth
columns. This is the same discipline Steps 3-6 already enforce, applied here
so the audit trail itself can never leak a synthetic ground-truth field into
what is meant to be a pre-decision fact record.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))

from typing import List, Optional, Dict, Any

from feature_config import PRE_DECISION_FEATURES  # noqa: E402
from action_models import RecoveryAction  # noqa: E402

from audit_schema import AuditStage
from audit_store import AuditStore


def _get(obj, field_name):
    if hasattr(obj, "get"):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


def record_detection(store: AuditStore, case) -> int:
    """Records the DETECT-stage fact snapshot — pre-decision fields only."""
    case_id = _get(case, "case_id")
    leakage_category = _get(case, "leakage_category")
    payload = {"case_id": case_id}
    for field_name in PRE_DECISION_FEATURES:
        payload[field_name] = _get(case, field_name)

    if leakage_category == "successful":
        summary = f"Case {case_id}: successful transaction, no revenue at risk."
    else:
        amount = payload.get("amount_at_risk")
        summary = f"Case {case_id}: {leakage_category} detected, amount_at_risk={amount}."

    return store.record_event(case_id, leakage_category, AuditStage.DETECTION, summary, payload)


def record_diagnosis(store: AuditStore, case_id: str, leakage_category: Optional[str], diagnosis: Dict[str, Any]) -> int:
    """Records the Step 3 diagnosis output as-is (it is already leakage-safe by construction)."""
    likelihood = diagnosis.get("predicted_recovery_likelihood")
    confidence = diagnosis.get("diagnosis_confidence")
    if likelihood is None:
        summary = f"Case {case_id}: diagnosis not applicable (no revenue at risk)."
    else:
        summary = (f"Case {case_id}: root cause '{diagnosis.get('root_cause')}', "
                    f"predicted_recovery_likelihood={likelihood:.0%}, diagnosis_confidence={confidence:.0%}." )
    return store.record_event(case_id, leakage_category, AuditStage.DIAGNOSIS, summary, diagnosis)


def record_candidate_actions(store: AuditStore, case_id: str, leakage_category: Optional[str],
                              actions: List[RecoveryAction]) -> int:
    """Records every action Step 4 considered for this case (applicable or not)."""
    payload = {"candidate_actions": [a.to_dict() for a in actions]}
    applicable = [a.action_type.value for a in actions if a.technically_applicable]
    inapplicable = [a.action_type.value for a in actions if not a.technically_applicable]
    summary = (f"Case {case_id}: {len(actions)} candidate action(s) considered — "
               f"applicable: {applicable or 'none'}; inapplicable: {inapplicable or 'none'}.")
    return store.record_event(case_id, leakage_category, AuditStage.CANDIDATE_ACTIONS, summary, payload)


def record_decision(store: AuditStore, decision) -> int:
    """Records the Step 5 recommendation: selected action, reasoning, alternatives."""
    case_id = _get(decision, "case_id")
    leakage_category = _get(decision, "leakage_category")
    payload = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
    status = payload.get("decision_status")
    action_type = payload.get("recommended_action_type")
    if status == "recommended":
        summary = f"Case {case_id}: Decision Engine recommended '{action_type}'. {payload.get('recommendation_reason', '')}"
    else:
        summary = f"Case {case_id}: Decision Engine status '{status}' — {payload.get('recommendation_reason', '')}"
    return store.record_event(case_id, leakage_category, AuditStage.DECISION, summary, payload)


def record_guardrail(store: AuditStore, guardrail_decision) -> int:
    """Records the Step 6 policy decision: outcome, triggered rules, approval requirement."""
    case_id = _get(guardrail_decision, "case_id")
    leakage_category = _get(guardrail_decision, "leakage_category")
    payload = guardrail_decision.to_dict() if hasattr(guardrail_decision, "to_dict") else dict(guardrail_decision)
    outcome = payload.get("outcome")
    summary = f"Case {case_id}: Guardrail outcome = {outcome} (approval_required={payload.get('approval_required')}). {payload.get('reason', '')}"
    return store.record_event(case_id, leakage_category, AuditStage.GUARDRAIL, summary, payload)


def record_execution(store: AuditStore, execution_record) -> int:
    """Records the Step 7 execution outcome — covers API action, result, failure,
    fallback (simulation), escalation, and stop (via execution_status/result_source)."""
    case_id = _get(execution_record, "case_id")
    leakage_category = _get(execution_record, "leakage_category")
    payload = execution_record.to_dict() if hasattr(execution_record, "to_dict") else dict(execution_record)
    summary = (f"Case {case_id}: execution_status='{payload.get('execution_status')}', "
               f"result_source='{payload.get('result_source')}'. {payload.get('reason', '')}")
    return store.record_event(case_id, leakage_category, AuditStage.EXECUTION, summary, payload)


def record_recovery(store: AuditStore, recovery_result) -> int:
    """Records a Step 10 RECOVER-stage observation. Structurally separate from
    record_execution: this event answers 'did the customer actually pay?',
    never 'did the API call succeed?'. The payload is exactly the
    RecoveryResult's own fields — nothing here is inferred, predicted, or
    pulled from synthetic ground truth."""
    case_id = _get(recovery_result, "case_id")
    leakage_category = _get(recovery_result, "leakage_category")
    payload = recovery_result.to_dict() if hasattr(recovery_result, "to_dict") else dict(recovery_result)
    status = payload.get("recovery_status")
    amount = payload.get("amount_recovered")
    summary = f"Case {case_id}: RECOVER observation = '{status}', amount_recovered={amount}. {payload.get('reason', '')}"
    return store.record_event(case_id, leakage_category, AuditStage.RECOVERY, summary, payload)
