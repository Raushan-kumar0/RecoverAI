# RecoverAI — Recovery Action Toolbox (Step 4)

## 1. Scope
Defines the **available, bounded recovery actions** and a **technical
compatibility layer**. This module does NOT choose a final action (Step 5),
does NOT authorize an action (Step 6), and does NOT execute anything or call
Razorpay (Step 7). Every `RecoveryAction` produced here has
`execution_status = NOT_EXECUTED`, always.

## 2. Files
```
recoverai/actions/
  action_models.py         enums (ActionType, RiskLevel, ExecutionStatus) + dataclasses (ActionDefinition, RecoveryAction)
  action_catalog.py          the static, centralized catalog of 7 actions + their metadata
  action_compatibility.py     get_actions_for_case() — technical applicability only, never authorization
  test_actions.py               29 tests
  README.md                      this file
```

## 3. Action catalog (7 actions — smallest coherent set)
| Action | Applicable categories | Money movement | Customer comm. | Razorpay needed (Step 7) |
|---|---|---|---|---|
| `payment_retry` | failed_payment | Yes | No | Yes |
| `mandate_retry` | failed_subscription | Yes | No | Yes |
| `recovery_payment_link` | failed_payment, checkout_abandonment, failed_subscription, overdue_receivable | No (link creation only) | Yes | Yes |
| `payment_reminder` | failed_payment, failed_subscription, overdue_receivable | No | Yes | No |
| `checkout_recovery_reminder` | checkout_abandonment | No | Yes | No |
| `receivables_followup` | overdue_receivable | No | Yes | No |
| `escalation` | all four leakage categories | No | No (merchant-side handoff) | No |

**Why not the full suggested list (9 names → 7 actions):** `subscription_retry`
and `mandate_retry` are the same underlying action (retry via mandate) —
kept as one. A separate `subscription_recovery_reminder` was skipped:
`payment_reminder` already covers failed_subscription's generic nudge;
`checkout_recovery_reminder` and `receivables_followup` earned their own
types only because their framing/purpose genuinely differs from a plain
reminder (cart-recovery language vs. an escalating invoice-followup cadence).

## 4. Action schema
`ActionDefinition` (static, one per `ActionType`, in `action_catalog.py`):
`action_type`, `purpose`, `applicable_categories`, `required_case_fields`,
`expected_effect`, `risk_level`, `money_movement`, `customer_communication`,
`requires_customer_consent`, `requires_merchant_approval` (see §6 caveat),
`razorpay_integration_needed`, `guardrail_considerations` (free-text notes
for Step 6 — not implemented here).

`RecoveryAction` (per-case instance, in `action_models.py`): everything above
plus `action_id`, `case_id`, `leakage_category`, `technically_applicable`,
`applicability_reason`, `execution_status` (always `NOT_EXECUTED`),
`case_field_snapshot` (audit-friendly copy of the required inputs actually
read from the case), and optional `diagnosis_context`.

## 5. Compatibility layer — availability ≠ authorization
`get_actions_for_case(case, diagnosis=None)` returns every action whose type
applies to the case's `leakage_category`, each flagged `technically_applicable`
with a `applicability_reason`. Two checks only:
1. All of the action's `required_case_fields` are present (not missing/NaN).
2. If the action involves `customer_communication`, the case's
   `communication_allowed` must be `True`.

**Deliberately NOT checked here** (per the Step 4 brief's explicit boundary):
`retry_count` against a limit, amount against a monetary ceiling, contact-hour
windows, or anything resembling AUTO_EXECUTE/APPROVAL_REQUIRED/STOP. A case
with 3 prior failed retries still shows `payment_retry` as technically
applicable — whether *another* retry is *permitted* is entirely Step 6's call.

`escalation` is the universal fallback: it's always technically applicable
for any leakage case, since it involves no autonomous customer contact and no
money movement.

`diagnosis` is optional and, if supplied, is validated to match the case's
`case_id` and attached to each action purely as read-only context
(`predicted_recovery_likelihood`, `diagnosis_confidence`, `root_cause`) — it
never influences `technically_applicable`, which is verified by
`test_diagnosis_context_does_not_affect_technical_applicability`.

## 6. Note on `requires_merchant_approval`
This field is **definitional metadata only** — e.g. `escalation` is *by
definition* a human handoff, so it's `True`; all other actions default to
`False` here because whether a *specific case* needs approval (high-value
transaction, low confidence, monetary incentive, etc.) is a guardrail
decision this layer has no visibility into. Step 6 makes the real per-case
call independently, for every action type, including ones marked `False`
here.

## 7. Safety boundaries (verified by tests)
- Every `RecoveryAction.execution_status` is always `ExecutionStatus.NOT_EXECUTED`.
- `RecoveryAction` has no `execute`/`run`/`send`/`charge`/`call_razorpay` method — it is a plain data object.
- No Step 4 module imports `requests`, calls any HTTP method, or references a Razorpay API endpoint.
- Action generation is deterministic: `action_id = f"{case_id}-{action_type.value}"`, no randomness; repeated calls produce identical output.
- Unknown action types and unknown leakage categories raise `ValueError` rather than silently returning something.

## 8. Tests (29/29 passing)
Covers: schema conformance, every leakage category has defined and (when
communication-eligible) applicable actions, successful cases get zero
actions, missing required fields correctly mark an action inapplicable,
opted-out/communication-disabled cases correctly block only
communication-type actions (retry/escalation remain applicable), money vs.
non-money flags, no-execution guarantees, no-Razorpay-import guarantee,
safe failure on invalid input, diagnosis consumption without influencing
applicability, mismatched-diagnosis rejection, determinism/reproducibility,
and dict/Series input equivalence.

Regression: Step 2 dataset validation still 22/22, Step 3 diagnosis tests
still 17/17, Step 3 model artifact untouched (verified via checksum —
Step 4 did not retrain or modify the diagnosis model).

## 9. Limitations
- Compatibility rules are intentionally simple (required-fields + communication
  check only); richer technical rules (e.g. "don't offer `recovery_payment_link`
  if a link was already sent in the last hour") are left for Step 6, since
  they're policy/guardrail concerns, not toolbox concerns.
- `money_movement=False` for `recovery_payment_link` reflects that *creating*
  the link doesn't move money — but a customer completing it later does. This
  distinction is documented, not glossed over.
- No cost/pricing model exists yet for any action; Step 6's `monetary_ceiling`
  guardrail and Step 10's measurement layer will need one.

## 10. Ready for Step 5
`get_actions_for_case(case, diagnosis)` gives Step 5 (Decision Engine) a
clean, pre-filtered list of technically-applicable actions per case, each
carrying enough metadata (`risk_level`, `money_movement`,
`customer_communication`, `requires_merchant_approval`,
`guardrail_considerations`) for a decision engine to reason about — without
Step 4 having made any decision itself.
