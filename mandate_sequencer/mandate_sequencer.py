"""
RecoverAI — Retry Sequencer: Orchestration

Extends `mandate_retry` (Step 7) from a single simulated attempt into a
scheduled, multi-attempt sequence, with an automatic fallback to a REAL
`recovery_payment_link` execution once attempts are exhausted.

Hard rules (matching the rest of the project's honesty guarantees):
    1. Every individual retry attempt is still exactly
       `client.simulate_retry_operation("mandate_retry", ...)` — the SAME
       already-tested Step 7 call. This module adds scheduling/tracking
       on top; it never invents a new kind of fake result.
    2. mandate_retry attempts can NEVER be marked "recovered" — Razorpay has
       no real endpoint for it, so there is nothing to independently
       observe. The only way a sequence becomes RECOVERED is via its
       fallback, which is a REAL `recovery_payment_link` — executed
       through the SAME `execute_guardrail_approved_action()` (Step 7)
       used everywhere else, and checked through the SAME
       `observe_recovery()` (Step 10) used everywhere else.
    3. The fallback is RE-AUTHORIZED from scratch via GuardrailEngine — it
       is never assumed safe just because the original mandate_retry was
       approved (same principle as failure_handler.py's own fallback path).
    4. No ground-truth field is ever read here.


Directly answers two specific requirements from the track brief:
    - "reduce involuntary churn" — a subscriber's payment failing is not
      a choice to leave; this sequencer actively fights to keep them
      subscribed via scheduled retries before falling back to a direct
      payment request.
    - "recover failed EMI mandates" — structurally identical to a failed
      subscription mandate (both are pre-authorized recurring charges);
      the exact same retry-then-fallback mechanism applies.
"""

import time
from typing import Optional, Dict, Any, List

from sequencer_models import MandateRetrySequence, RetryAttempt, SequenceStatus

DEFAULT_SCHEDULE_OFFSET_DAYS: List[int] = [0, 3, 7]  # attempt 1 immediate, attempt 2 +3d, attempt 3 +7d
SECONDS_PER_DAY = 86400


def _get(obj, field_name, default=None):
    if hasattr(obj, "get"):
        return obj.get(field_name, default)
    return getattr(obj, field_name, default)


def start_sequence(case, current_time: Optional[float] = None,
                    schedule_offset_days: Optional[List[int]] = None,
                    action_type: str = "mandate_retry") -> MandateRetrySequence:
    """
    Begins a new retry sequence for a case whose decision/guardrail already
    resulted in `mandate_retry` + AUTO_EXECUTE. Does not run any attempt

    itself — that happens via run_due_attempts().
    """
    schedule = schedule_offset_days or DEFAULT_SCHEDULE_OFFSET_DAYS
    now = current_time if current_time is not None else time.time()
    case_id = _get(case, "case_id")
    leakage_category = _get(case, "leakage_category")
    amount_at_risk = float(_get(case, "amount_at_risk", 0.0) or 0.0)

    attempts = [
        RetryAttempt(
            attempt_number=i + 1,
            scheduled_offset_days=offset,
            scheduled_at=now + (offset * SECONDS_PER_DAY),
        )
        for i, offset in enumerate(schedule)
    ]

    return MandateRetrySequence(
        case_id=case_id,
        leakage_category=leakage_category,
        amount_at_risk=amount_at_risk,
        action_type=action_type,
        started_at=now,
        max_attempts=len(schedule),
        attempts=attempts,
        status=SequenceStatus.PENDING,
    )


def get_due_attempt(sequence: MandateRetrySequence, current_time: Optional[float] = None) -> Optional[RetryAttempt]:
    """Returns the earliest not-yet-run attempt whose scheduled time has passed, or None."""
    now = current_time if current_time is not None else time.time()
    for attempt in sequence.attempts:
        if attempt.executed_at is None and attempt.scheduled_at <= now:
            return attempt
    return None


def run_due_attempt(sequence: MandateRetrySequence, client, current_time: Optional[float] = None) -> MandateRetrySequence:
    """
    Runs exactly one due attempt (if any), via the existing, already-tested
    client.simulate_retry_operation() — never a new fake result. Advances
    sequence.status to EXHAUSTED once all attempts have been run, since
    mandate_retry attempts can never self-report success.
    """
    now = current_time if current_time is not None else time.time()
    attempt = get_due_attempt(sequence, now)
    if attempt is None:
        return sequence  # nothing due yet — caller should try again later

    raw_result = client.simulate_retry_operation(
    sequence.action_type,
    sequence.case_id,
    sequence.amount_at_risk
)
    attempt.executed_at = now
    attempt.result_source = "bounded_simulation"
    attempt.raw_result = raw_result

    if all(a.executed_at is not None for a in sequence.attempts):
        sequence.status = SequenceStatus.EXHAUSTED
    else:
        sequence.status = SequenceStatus.ATTEMPT_SCHEDULED

    return sequence


def trigger_fallback(sequence: MandateRetrySequence, case, original_diagnosis: Dict[str, Any],
                      guardrail_engine, razorpay_client, audit_store=None,
                      current_time=None) -> MandateRetrySequence:
    """
    Once a sequence is EXHAUSTED, attempts a REAL recovery_payment_link as a
    last resort. Re-authorizes from scratch (fresh GuardrailEngine.authorize
    call) rather than reusing the original mandate_retry approval — a
    different action type must earn its own authorization, same principle
    Step 9's fallback handling already follows.

    Only runs if sequence.status == EXHAUSTED. No-op (returns sequence
    unchanged) otherwise, including if it's already been triggered.
    """
    if sequence.status != SequenceStatus.EXHAUSTED:
        return sequence

    # Lazy imports: keep this module importable without the full sys.path
    # wiring dashboard_data.py sets up, for isolated unit testing.
    from decision_models import Decision, DecisionStatus, LikelihoodTier
    from razorpay_execution import execute_guardrail_approved_action
    from action_catalog import ACTION_CATALOG
    from action_models import ActionType

    # Use the REAL, locked Step 4 catalog definition for recovery_payment_link
    # (money_movement, customer_communication, requires_merchant_approval, ...)
    # rather than hand-constructing one — this is exactly the metadata
    # GuardrailEngine.authorize() reads to decide AUTO_EXECUTE vs
    # APPROVAL_REQUIRED vs STOP, so it must be authentic, not approximated.
    catalog_def = ACTION_CATALOG[ActionType.RECOVERY_PAYMENT_LINK]
    recommended_action = {
        "action_type": catalog_def.action_type.value,
        "money_movement": catalog_def.money_movement,
        "customer_communication": catalog_def.customer_communication,
        "requires_merchant_approval": catalog_def.requires_merchant_approval,
    }

    fallback_decision = Decision(
        case_id=sequence.case_id,
        leakage_category=sequence.leakage_category,
        decision_status=DecisionStatus.RECOMMENDED,
        recommended_action_type="recovery_payment_link",
        recommended_action=recommended_action,
        likelihood_tier=LikelihoodTier.HIGH,
        predicted_recovery_likelihood=original_diagnosis.get("predicted_recovery_likelihood"),
        diagnosis_confidence=original_diagnosis.get("diagnosis_confidence"),
        recommendation_reason=(
            f"Fallback after {sequence.max_attempts} exhausted mandate_retry attempts "
            f"for case {sequence.case_id}: switching to a real, directly payable "
            f"Payment Link since retrying the mandate has no further avenue."
        ),
    )

    diag_for_guard = {"diagnosis_confidence": original_diagnosis.get("diagnosis_confidence")}
    fallback_guardrail = guardrail_engine.authorize(case, diag_for_guard, fallback_decision, current_time=current_time)
    guardrail_outcome = fallback_guardrail.outcome.value if hasattr(fallback_guardrail.outcome, "value") else fallback_guardrail.outcome

    if guardrail_outcome != "auto_execute":
        # Fresh authorization did not approve the fallback either (e.g. outside
        # contact hours right now) — leave the sequence EXHAUSTED, not
        # fabricated as triggered. Caller can retry trigger_fallback() later.
        sequence.fallback_execution_record = {
            "not_executed_reason": f"Fallback re-authorization returned {guardrail_outcome!r}, not auto_execute.",
        }
        return sequence

    execution_record = execute_guardrail_approved_action(case, fallback_decision, fallback_guardrail, razorpay_client)
    sequence.fallback_execution_record = execution_record.to_dict()

    if execution_record.execution_status == "executed":
        sequence.status = SequenceStatus.FALLBACK_TRIGGERED

    return sequence


def check_fallback_recovery(sequence: MandateRetrySequence, razorpay_client) -> MandateRetrySequence:
    """
    Independently checks the fallback Payment Link's real status via the
    SAME observe_recovery() (Step 10) used everywhere else in this project.
    No-op if no fallback was ever triggered.
    """
    if sequence.status not in (SequenceStatus.FALLBACK_TRIGGERED, SequenceStatus.FALLBACK_RECOVERED):
        return sequence
    if not sequence.fallback_execution_record or sequence.fallback_execution_record.get("execution_status") != "executed":
        return sequence

    from recovery_checker import observe_recovery

    recovery_result = observe_recovery(
        sequence.case_id, sequence.leakage_category, sequence.fallback_execution_record, razorpay_client
    )
    sequence.fallback_recovery_result = recovery_result.to_dict()

    if recovery_result.recovery_status.value in ("recovered", "partially_recovered"):
        sequence.status = SequenceStatus.FALLBACK_RECOVERED

    return sequence