"""
RecoverAI — Recovery Action Toolbox: Catalog (Step 4)

Centralized, static catalog of every recovery action RecoverAI can later
choose among. This module defines WHAT actions exist and their properties.
It does not choose, authorize, or execute anything.

==================================================
WHY THIS ACTION SET (smallest coherent catalog)
==================================================

The Step 4 brief suggested a longer list (payment_retry, recovery_payment_link,
payment_reminder, checkout_recovery_reminder, subscription_retry, mandate_retry,
subscription_recovery_reminder, receivables_followup, escalation). Rather than
implementing every suggested name, two consolidations were made:

- subscription_retry and mandate_retry are the same underlying action (retrying
  a recurring charge via the payment mandate) — kept as a single MANDATE_RETRY.
- A separate subscription_recovery_reminder was not added: PAYMENT_REMINDER
  already covers "your payment needs attention" for failed_subscription and
  failed_payment cases; only checkout_abandonment (different framing: "you left
  something in your cart") and overdue_receivable (different framing: a
  structured invoice follow-up, not a one-off nudge) warranted their own
  distinct reminder-style actions.

This produces 7 actions instead of 9 suggested names, each with a genuinely
distinct purpose — avoiding two actions that would differ in name only.
"""

from action_models import ActionDefinition, ActionType, RiskLevel

FAILED_PAYMENT = "failed_payment"
CHECKOUT_ABANDONMENT = "checkout_abandonment"
FAILED_SUBSCRIPTION = "failed_subscription"
OVERDUE_RECEIVABLE = "overdue_receivable"

ACTION_CATALOG = {
    ActionType.PAYMENT_RETRY: ActionDefinition(
        action_type=ActionType.PAYMENT_RETRY,
        purpose="Re-attempt a failed one-off payment charge via the original payment method/gateway.",
        applicable_categories=[FAILED_PAYMENT],
        required_case_fields=["payment_method", "failure_reason", "amount_at_risk"],
        expected_effect="A new charge attempt is made against the same transaction; may succeed or fail again.",
        risk_level=RiskLevel.LOW,
        money_movement=True,
        customer_communication=False,
        requires_customer_consent=False,
        requires_merchant_approval=False,
        razorpay_integration_needed=True,
        guardrail_considerations=(
            "Must respect a configurable retry_limit (Step 1 default: 3). Must not retry "
            "indefinitely. Step 6 must check retry_count before authorizing another attempt."
        ),
    ),
    ActionType.MANDATE_RETRY: ActionDefinition(
        action_type=ActionType.MANDATE_RETRY,
        purpose="Re-attempt a failed recurring subscription charge via the existing payment mandate.",
        applicable_categories=[FAILED_SUBSCRIPTION],
        required_case_fields=["mandate_status", "subscription_status", "amount_at_risk"],
        expected_effect="A new mandate-based charge attempt is made; may succeed, fail, or reveal a broken mandate.",
        risk_level=RiskLevel.LOW,
        money_movement=True,
        customer_communication=False,
        requires_customer_consent=False,
        requires_merchant_approval=False,
        razorpay_integration_needed=True,
        guardrail_considerations=(
            "Must respect retry_limit. A failed/invalid mandate_status may make this action "
            "technically inapplicable (see action_compatibility.py) rather than a guardrail concern."
        ),
    ),
    ActionType.RECOVERY_PAYMENT_LINK: ActionDefinition(
        action_type=ActionType.RECOVERY_PAYMENT_LINK,
        purpose="Generate a fresh payment link for the customer to complete payment through any method.",
        applicable_categories=[FAILED_PAYMENT, CHECKOUT_ABANDONMENT, FAILED_SUBSCRIPTION, OVERDUE_RECEIVABLE],
        required_case_fields=["amount_at_risk", "communication_allowed"],
        expected_effect="Customer receives a link; payment only completes if they act on it. No charge is attempted directly.",
        risk_level=RiskLevel.LOW,
        money_movement=False,   # link creation itself does not move money; completion is a separate customer action
        customer_communication=True,
        requires_customer_consent=True,
        requires_merchant_approval=False,
        razorpay_integration_needed=True,
        guardrail_considerations=(
            "Must not arbitrarily modify amount_at_risk. Any bounded incentive/discount attached "
            "to the link is a Step 6 monetary_ceiling concern, not decided here. Must respect "
            "contact_window and opt-out at guardrail time even though technical applicability "
            "already checks communication_allowed."
        ),
    ),
    ActionType.PAYMENT_REMINDER: ActionDefinition(
        action_type=ActionType.PAYMENT_REMINDER,
        purpose="Send a generic reminder nudging the customer that a payment needs attention.",
        applicable_categories=[FAILED_PAYMENT, FAILED_SUBSCRIPTION, OVERDUE_RECEIVABLE],
        required_case_fields=["communication_allowed"],
        expected_effect="Customer is notified; no guarantee of action taken.",
        risk_level=RiskLevel.LOW,
        money_movement=False,
        customer_communication=True,
        requires_customer_consent=True,
        requires_merchant_approval=False,
        razorpay_integration_needed=False,
        guardrail_considerations="Must respect contact_window and autonomous_attempt_cap.",
    ),
    ActionType.CHECKOUT_RECOVERY_REMINDER: ActionDefinition(
        action_type=ActionType.CHECKOUT_RECOVERY_REMINDER,
        purpose="Send a cart-recovery-style reminder specific to an abandoned checkout.",
        applicable_categories=[CHECKOUT_ABANDONMENT],
        required_case_fields=["communication_allowed", "amount_at_risk"],
        expected_effect="Customer is reminded of the incomplete checkout; framing differs from a generic payment reminder.",
        risk_level=RiskLevel.LOW,
        money_movement=False,
        customer_communication=True,
        requires_customer_consent=True,
        requires_merchant_approval=False,
        razorpay_integration_needed=False,
        guardrail_considerations="Must respect contact_window and autonomous_attempt_cap.",
    ),
    ActionType.RECEIVABLES_FOLLOWUP: ActionDefinition(
        action_type=ActionType.RECEIVABLES_FOLLOWUP,
        purpose="Structured follow-up communication for an overdue invoice, distinct from a single reminder.",
        applicable_categories=[OVERDUE_RECEIVABLE],
        required_case_fields=["communication_allowed", "days_overdue", "amount_at_risk"],
        expected_effect="Customer receives an invoice-specific follow-up; may be part of an escalating cadence.",
        risk_level=RiskLevel.MEDIUM,
        money_movement=False,
        customer_communication=True,
        requires_customer_consent=True,
        requires_merchant_approval=False,
        razorpay_integration_needed=False,
        guardrail_considerations=(
            "Cadence/frequency bounded by autonomous_attempt_cap. Very high days_overdue may be "
            "a Step 6 signal to require approval or escalate instead — not decided here."
        ),
    ),
    ActionType.ESCALATION: ActionDefinition(
        action_type=ActionType.ESCALATION,
        purpose="Hand the case off to merchant staff for manual review/follow-up.",
        applicable_categories=[FAILED_PAYMENT, CHECKOUT_ABANDONMENT, FAILED_SUBSCRIPTION, OVERDUE_RECEIVABLE],
        required_case_fields=[],
        expected_effect="No autonomous action taken; a human follow-up requirement is created.",
        risk_level=RiskLevel.LOW,
        money_movement=False,
        customer_communication=False,   # merchant-side handoff, not itself a customer touchpoint
        requires_customer_consent=False,
        requires_merchant_approval=True,   # definitional: escalation IS a human handoff by design
        razorpay_integration_needed=False,
        guardrail_considerations=(
            "Universal fallback — always technically applicable regardless of opt-out/suspicious "
            "flags, since it involves no autonomous customer contact or money movement."
        ),
    ),
}


def get_action_definition(action_type: ActionType) -> ActionDefinition:
    if action_type not in ACTION_CATALOG:
        raise ValueError(f"Unknown action_type: {action_type!r}. Not present in ACTION_CATALOG.")
    return ACTION_CATALOG[action_type]


def get_actions_for_category(leakage_category: str):
    """Returns all ActionDefinitions whose applicable_categories include this category."""
    return [d for d in ACTION_CATALOG.values() if leakage_category in d.applicable_categories]
