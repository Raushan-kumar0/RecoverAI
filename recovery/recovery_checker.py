"""
RecoverAI — RECOVER Stage: Observation (Step 10 prerequisite)

Observes whether a customer actually paid, using ONLY Razorpay's own
reported Payment Link status. This is the ONLY function in the project that
produces a RecoveryResult, and it takes as input ONLY:
    - case_id / leakage_category (identifiers, for audit correlation)
    - a Step 7 ExecutionRecord (or its .to_dict() form)
    - the existing RazorpayTestModeClient (reused — no second client)

It NEVER reads:
    - predicted_recovery_likelihood / diagnosis_confidence (Step 3)
    - the Decision's recommendation or reasoning (Step 5)
    - Step 2 synthetic ground truth (ground_truth_recoverable,
      ground_truth_recovery_outcome, amount_recovered, recovery_observed,
      recovery_reason) — these are not even accepted as parameters here,
      so there is no code path by which they could influence a RecoveryResult.

CRITICAL: a "successful" EXECUTE (execution_status == "executed") only means
a Payment Link was created — Razorpay accepted the request. It says nothing
about whether the customer paid. Only a subsequent, real GET call to
/v1/payment_links/:id — via client.fetch_payment_link_status() — can
determine that, and only that call's response is used here.
"""

import time
from typing import Optional, Dict, Any

from recovery_models import RecoveryResult, RecoveryStatus

RECOVERABLE_ACTION_TYPES = {"recovery_payment_link"}  # only this action type can ever have a real link to check


def _get(obj, field_name):
    if hasattr(obj, "get"):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


def observe_recovery(case_id: str, leakage_category: Optional[str], execution_record, razorpay_client) -> RecoveryResult:
    """
    execution_record: a Step 7 ExecutionRecord (object or dict) for the
                       action whose recovery outcome should be checked.
                       Typically the primary or fallback execution from a
                       Step 9 FailureHandlingResult.
    razorpay_client:    the existing RazorpayTestModeClient — reused, not
                          a new client.
    """
    action_type = _get(execution_record, "action_type")
    execution_status = _get(execution_record, "execution_status")
    razorpay_result = _get(execution_record, "razorpay_result") or {}

    if action_type not in RECOVERABLE_ACTION_TYPES:
        return RecoveryResult(
            case_id=case_id, leakage_category=leakage_category,
            recovery_status=RecoveryStatus.NOT_OBSERVED, amount_recovered=0.0,
            observation_source="no_link_to_observe",
            reason=f"Action type '{action_type}' never creates a Payment Link; there is nothing to observe.",
            checked_at=time.time(),
        )

    if execution_status != "executed":
        return RecoveryResult(
            case_id=case_id, leakage_category=leakage_category,
            recovery_status=RecoveryStatus.NOT_OBSERVED, amount_recovered=0.0,
            observation_source="no_real_link_created",
            reason=f"execution_status was '{execution_status}', not 'executed' — no real Payment Link exists "
                   f"to check (dry-run, simulated, and failed executions never created a real link).",
            checked_at=time.time(),
        )

    payment_link_id = razorpay_result.get("razorpay_payment_link_id")
    if not payment_link_id:
        return RecoveryResult(
            case_id=case_id, leakage_category=leakage_category,
            recovery_status=RecoveryStatus.NOT_OBSERVED, amount_recovered=0.0,
            observation_source="no_link_id_present",
            reason="execution_status was 'executed' but no razorpay_payment_link_id was found in the "
                   "execution result — cannot observe a status without a real link id.",
            checked_at=time.time(),
        )

    status_response = razorpay_client.fetch_payment_link_status(payment_link_id)
    return _interpret_status_response(case_id, leakage_category, payment_link_id, status_response)


def _interpret_status_response(case_id, leakage_category, payment_link_id, status_response: Dict[str, Any]) -> RecoveryResult:
    call_status = status_response.get("status")
    checked_at = status_response.get("timestamp", time.time())

    if call_status == "dry_run":
        return RecoveryResult(
            case_id=case_id, leakage_category=leakage_category, payment_link_id=payment_link_id,
            recovery_status=RecoveryStatus.NOT_OBSERVED, amount_recovered=0.0,
            observation_source="dry_run_no_observation",
            reason="DRY_RUN is active — the status-check call itself was not made; no observation exists.",
            checked_at=checked_at, raw_status_payload=status_response,
        )

    if call_status in ("error", "api_error"):
        return RecoveryResult(
            case_id=case_id, leakage_category=leakage_category, payment_link_id=payment_link_id,
            recovery_status=RecoveryStatus.OBSERVATION_FAILED, amount_recovered=0.0,
            observation_source="razorpay_payment_link_status",
            reason=f"Status-check call failed ({call_status}); genuine payment status could not be observed. "
                   f"This is NOT the same as 'not paid' — it means we don't know.",
            checked_at=checked_at, raw_status_payload=status_response,
        )

    if call_status != "observed":
        # Fail safe on any unexpected response shape rather than guessing.
        return RecoveryResult(
            case_id=case_id, leakage_category=leakage_category, payment_link_id=payment_link_id,
            recovery_status=RecoveryStatus.OBSERVATION_FAILED, amount_recovered=0.0,
            observation_source="razorpay_payment_link_status",
            reason=f"Unexpected status-check response shape (status={call_status!r}); treated as unobserved "
                   f"rather than guessed.",
            checked_at=checked_at, raw_status_payload=status_response,
        )

    razorpay_status = status_response.get("razorpay_status")
    amount_paid_paise = status_response.get("amount_paid") or 0
    amount_paid_rupees = round(amount_paid_paise / 100.0, 2)

    if razorpay_status == "paid":
        recovery_status = RecoveryStatus.RECOVERED
        reason = f"Razorpay reports this Payment Link status as 'paid' with amount_paid={amount_paid_rupees}."
    elif amount_paid_rupees > 0:
        recovery_status = RecoveryStatus.PARTIALLY_RECOVERED
        reason = (f"Razorpay reports this Payment Link status as '{razorpay_status}' with a partial "
                  f"amount_paid={amount_paid_rupees} (link not fully paid).")
        amount_paid_rupees = amount_paid_rupees  # keep the genuinely-paid partial amount, not zero
    else:
        recovery_status = RecoveryStatus.PENDING
        reason = f"Razorpay reports this Payment Link status as '{razorpay_status}' with amount_paid=0 — not yet paid."
        amount_paid_rupees = 0.0

    return RecoveryResult(
        case_id=case_id, leakage_category=leakage_category, payment_link_id=payment_link_id,
        recovery_status=recovery_status, amount_recovered=amount_paid_rupees,
        observation_source="razorpay_payment_link_status",
        reason=reason, checked_at=checked_at, raw_status_payload=status_response,
    )
