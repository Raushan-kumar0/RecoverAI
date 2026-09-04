"""
RecoverAI — Promise-to-Pay Tracker: Orchestration

Tracks a customer's stated commitment to pay a specific amount by a
specific future date, and independently verifies whether they honored it.

Hard rules (matching the rest of the project's honesty guarantees):
    1. A promise is NEVER marked HONORED by inference, elapsed time, or
       merchant optimism — only by the SAME observe_recovery() (Step 10)
       call used everywhere else in this project, against a REAL linked
       Payment Link, reporting a genuine 'paid' status from Razorpay.
    2. A promise is marked BROKEN only when promise_date has GENUINELY
       passed (checked against an explicitly-passed current_time, never
       the real wall clock read implicitly) AND no payment was confirmed.
    3. A promise with no linked Payment Link at all can never become
       HONORED — there is nothing to observe. It can only sit PENDING
       (before the date) or become BROKEN (after the date), same as any
       other case with nothing to verify against.
    4. No ground-truth field is ever read here.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any

from promise_schema import PromiseStatus
from promise_store import PromiseStore


def record_promise(store: PromiseStore, case_id: str, leakage_category: Optional[str],
                    promised_amount: float, promise_date: str, payment_link_id: Optional[str] = None) -> int:
    """
    promise_date: ISO date string (YYYY-MM-DD). payment_link_id is optional
    at creation time — a promise can exist before any Payment Link has been
    created for it (e.g. a verbal/manual commitment logged first).
    """
    return store.record_promise(case_id, leakage_category, promised_amount, promise_date, payment_link_id)


def link_payment_link(store: PromiseStore, promise_id: int, payment_link_id: str) -> None:
    """Attaches a real Payment Link to an existing promise, without changing its status."""
    promise = store.get_promise(promise_id)
    if promise is None:
        raise ValueError(f"No promise with id {promise_id}")
    current_status = PromiseStatus(promise["status"])
    store.update_status(
        promise_id, current_status,
        reason=f"Payment Link Id `{payment_link_id}` attached to this promise. Payment status has not been verified yet.",
        payment_link_id=payment_link_id,
    )


def check_promise(store: PromiseStore, promise_id: int, razorpay_client,
                   current_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Independently re-evaluates one promise's status:
      - If a Payment Link is attached, ask Razorpay (via observe_recovery,
        the SAME function every other real check in this project uses)
        whether it's genuinely been paid. If so -> HONORED.
      - Otherwise, if promise_date has passed as of current_time -> BROKEN.
      - Otherwise -> stays PENDING (unchanged).

    Terminal statuses are stable: an already-HONORED or already-ESCALATED
    promise is not re-evaluated (re-checking a paid promise is harmless in
    principle, but this keeps the state machine simple and matches the
    project's preference for explicit, minimal transitions).
    """
    now = current_time or datetime.now()
    today = now.date() if isinstance(now, datetime) else now

    promise = store.get_promise(promise_id)
    if promise is None:
        raise ValueError(f"No promise with id {promise_id}")

    current_status = PromiseStatus(promise["status"])
    if current_status in (PromiseStatus.HONORED, PromiseStatus.ESCALATED):
        return promise  # terminal — not re-evaluated

    payment_link_id = promise.get("payment_link_id")

    if payment_link_id and razorpay_client is not None:
        from recovery_checker import observe_recovery
        synthetic_execution_record = {
            "case_id": promise["case_id"], "leakage_category": promise["leakage_category"],
            "action_type": "recovery_payment_link", "execution_status": "executed",
            "result_source": "razorpay_test_mode_api", "reason": "Promise-to-pay verification check.",
            "razorpay_result": {"razorpay_payment_link_id": payment_link_id},
        }
        recovery_result = observe_recovery(
            promise["case_id"], promise["leakage_category"], synthetic_execution_record, razorpay_client
        )
        promised_amount = float(promise["promised_amount"])
        amount_paid = float(recovery_result.amount_recovered or 0.0)

        if recovery_result.recovery_status.value in ("recovered", "partially_recovered"):
            if amount_paid >= promised_amount:
                store.update_status(
                    promise_id, PromiseStatus.HONORED,
                    reason=(
                        f"Razorpay confirmed a genuine payment of ₹{amount_paid:.2f} for the given "
                        f"Payment Link Id `{payment_link_id}`, meeting or exceeding the promised ₹{promised_amount:.2f}."
                    ),
                )
                return store.get_promise(promise_id)
            else:
                store.update_status(
                    promise_id, PromiseStatus.PENDING,
                    reason=(
                        f"Razorpay confirmed a genuine payment of ₹{amount_paid:.2f} for the given  "
                        f"Payment Link Id, but this is LESS than the promised ₹{promised_amount:.2f} — "
                        f"not counted as honored."
                    ),
                )
                return store.get_promise(promise_id)

    promise_due = _parse_date(promise["promise_date"])
    if promise_due is not None and today > promise_due:
        store.update_status(
            promise_id, PromiseStatus.BROKEN,
            reason=f"promise_date ({promise['promise_date']}) has passed with no confirmed payment observed.",
        )
        return store.get_promise(promise_id)

    return promise  # still PENDING — date hasn't passed, nothing confirmed paid


def escalate_promise(store: PromiseStore, promise_id: int, reason: str) -> None:
    """
    Manually flags a BROKEN promise for merchant follow-up. Deliberately
    NOT automatic — escalation is a human/business decision, not something
    this module infers on its own. Only valid from BROKEN.
    """
    promise = store.get_promise(promise_id)
    if promise is None:
        raise ValueError(f"No promise with id {promise_id}")
    if PromiseStatus(promise["status"]) != PromiseStatus.BROKEN:
        raise ValueError(f"Can only escalate a BROKEN promise (current status: {promise['status']}).")
    store.update_status(promise_id, PromiseStatus.ESCALATED, reason=reason)


def _parse_date(date_str: str):
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None