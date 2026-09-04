"""
RecoverAI — Batch Revenue Recovery Measurement (Step 10)

Aggregates a list of BatchEntry (one per case) into a BatchMeasurement.
Pure aggregation — runs no diagnosis, decision, guardrail, execution, or
recovery-observation logic itself; it only sums up what already happened,
as recorded by Steps 5/6/9/10.

CRITICAL, TESTED GUARANTEE: total_amount_recovered is computed EXCLUSIVELY
from `entry.recovery_result["amount_recovered"]` where
`recovery_result["recovery_status"]` is "recovered" or "partially_recovered".
Nothing else on a BatchEntry — not a successful execution_status, not a
guardrail outcome, not predicted_recovery_likelihood, not Step 2 ground
truth (which isn't even a field this module reads) — can contribute to that
number. This is enforced structurally: BatchEntry has no
predicted_recovery_likelihood or ground_truth_* field at all.
"""

from typing import List, Dict, Any, Optional

from measurement_models import BatchEntry, BatchMeasurement

RECOVERED_STATUSES = {"recovered", "partially_recovered"}
UNRESOLVED_STATUSES = {"not_observed", "pending", "observation_failed"}
SUCCESS_EXECUTION_STATUSES = {"dry_run", "simulated", "executed"}
FAILURE_EXECUTION_STATUSES = {"api_error", "error"}


def _get(obj, field_name, default=None):
    if obj is None:
        return default
    if hasattr(obj, "get"):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


def compute_batch_measurement(entries: List[BatchEntry]) -> BatchMeasurement:
    cases_analyzed = 0
    recovery_opportunities = 0
    total_amount_at_risk = 0.0
    total_amount_processed = 0.0
    total_amount_recovered = 0.0
    actions_attempted = 0
    successful_executions = 0
    failed_executions = 0
    fallback_actions = 0
    escalated_cases = 0
    stopped_cases = 0
    approval_required_cases = 0
    unresolved_recovery_cases = 0
    per_case: List[Dict[str, Any]] = []

    for entry in entries:
        cases_analyzed += 1
        amount_at_risk = entry.amount_at_risk or 0.0
        total_amount_at_risk += amount_at_risk

        is_leakage_case = entry.leakage_category != "successful"
        if is_leakage_case:
            recovery_opportunities += 1

        guardrail_outcome = entry.primary_guardrail_outcome
        if guardrail_outcome == "stop":
            stopped_cases += 1
        elif guardrail_outcome == "approval_required":
            approval_required_cases += 1

        fhr = entry.failure_handling_result  # Step 9 FailureHandlingResult.to_dict(), or None
        primary_execution = _get(fhr, "primary_execution", {}) or {}
        fallback_execution = _get(fhr, "fallback_execution")
        fallback_attempted = bool(_get(fhr, "fallback_attempted", False))
        step9_escalated = bool(_get(fhr, "escalated", False))
        step9_outcome = _get(fhr, "outcome")

        primary_status = primary_execution.get("execution_status") if primary_execution else None
        case_processed = False

        if primary_status and primary_status != "not_executed":
            actions_attempted += 1
            case_processed = True
            if primary_status in SUCCESS_EXECUTION_STATUSES:
                successful_executions += 1
            elif primary_status in FAILURE_EXECUTION_STATUSES:
                failed_executions += 1

        if fallback_attempted and fallback_execution:
            fallback_status = fallback_execution.get("execution_status")
            if fallback_status and fallback_status != "not_executed":
                actions_attempted += 1
                case_processed = True
                if fallback_status in SUCCESS_EXECUTION_STATUSES:
                    successful_executions += 1
                elif fallback_status in FAILURE_EXECUTION_STATUSES:
                    failed_executions += 1

        if step9_outcome == "fallback_succeeded":
            fallback_actions += 1

        # Escalation counted from two independent legitimate sources:
        # (a) Step 9 had to escalate after a genuine EXECUTE failure/unsafe fallback, or
        # (b) Step 5 recommended 'escalation' directly (e.g. LOW-tier cases) and
        #     Step 6 correctly marked it APPROVAL_REQUIRED — never an EXECUTE
        #     failure, but still a real escalation for batch-reporting purposes.
        direct_escalation = (entry.primary_recommended_action_type == "escalation"
                              and guardrail_outcome == "approval_required")
        if step9_escalated or direct_escalation:
            escalated_cases += 1
            actions_attempted += 1  # escalation is itself an attempted recovery action

        if case_processed:
            total_amount_processed += amount_at_risk

        # ---- The one number this whole module exists to protect ----
        recovery_result = entry.recovery_result
        recovery_status = _get(recovery_result, "recovery_status")
        amount_recovered_this_case = 0.0
        if recovery_status in RECOVERED_STATUSES:
            amount_recovered_this_case = float(_get(recovery_result, "amount_recovered", 0.0) or 0.0)
        total_amount_recovered += amount_recovered_this_case

        if is_leakage_case and case_processed and recovery_status not in RECOVERED_STATUSES:
            # An action was genuinely attempted, but there is no confirmed
            # RECOVERED/PARTIALLY_RECOVERED observation — this is distinct
            # from a failure: the action may have worked, we just don't
            # (yet) have a payment confirmation for it.
            unresolved_recovery_cases += 1

        per_case.append({
            "case_id": entry.case_id,
            "leakage_category": entry.leakage_category,
            "amount_at_risk": amount_at_risk,
            "guardrail_outcome": guardrail_outcome,
            "step9_outcome": step9_outcome,
            "recovery_status": recovery_status,
            "amount_recovered": amount_recovered_this_case,
        })

    recovery_rate = (total_amount_recovered / total_amount_at_risk) if total_amount_at_risk > 0 else 0.0
    recovery_cost: Optional[float] = None  # not modeled — see BatchMeasurement.recovery_cost_note
    net_recovered_revenue = total_amount_recovered - (recovery_cost or 0.0)

    return BatchMeasurement(
        cases_analyzed=cases_analyzed,
        recovery_opportunities=recovery_opportunities,
        total_amount_at_risk=round(total_amount_at_risk, 2),
        total_amount_processed=round(total_amount_processed, 2),
        total_amount_recovered=round(total_amount_recovered, 2),
        recovery_rate=round(recovery_rate, 4),
        recovery_cost=recovery_cost,
        net_recovered_revenue=round(net_recovered_revenue, 2),
        actions_attempted=actions_attempted,
        successful_executions=successful_executions,
        failed_executions=failed_executions,
        fallback_actions=fallback_actions,
        escalated_cases=escalated_cases,
        stopped_cases=stopped_cases,
        approval_required_cases=approval_required_cases,
        unresolved_recovery_cases=unresolved_recovery_cases,
        per_case=per_case,
    )
