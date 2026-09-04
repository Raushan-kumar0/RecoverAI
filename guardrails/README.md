# RecoverAI — Guardrail Engine (Step 6)

## 1. Scope
The deterministic authorization layer between the Decision Engine (Step 5)
and future execution (Step 7). Takes the case, the Step 3 diagnosis, and the
Step 5 recommendation, and returns **exactly one** outcome: `AUTO_EXECUTE`,
`APPROVAL_REQUIRED`, or `STOP`. **Never calls Razorpay, never executes,
never sends a communication.**

## 2. Files
```
recoverai/guardrails/
  guardrail_config.py     GuardrailConfig dataclass — configurable, documented, NOT empirically validated defaults
  guardrail_models.py       AuthorizationOutcome enum + GuardrailDecision/TriggeredRule dataclasses
  guardrail_engine.py        GuardrailEngine.authorize() — the actual rule logic
  test_guardrails.py          31 tests
  README.md                    this file
```

## 3. Interface
```python
from guardrail_engine import GuardrailEngine
from guardrail_config import GuardrailConfig

engine = GuardrailEngine()                      # uses DEFAULT_GUARDRAIL_CONFIG
# or: engine = GuardrailEngine(config=GuardrailConfig(monetary_ceiling=5000))

g = engine.authorize(case, diagnosis, decision, current_time=None)  # current_time injectable for testing
```
Returns a `GuardrailDecision`: `outcome`, `reason`, `triggered_rules` (list of
`{rule, description}`), `limits_checked` (every limit examined, pass/fail,
regardless of outcome), `approval_required` (bool), `config_used` (full
config snapshot — for audit reproducibility), `evaluated_at` (ISO timestamp).

## 4. Configurable defaults (restated from Step 1 §6 — NOT empirically validated)
| Parameter | Default | Note |
|---|---|---|
| `retry_limit` | 3 | max automated payment/mandate retries per case |
| `autonomous_attempt_cap` | 3 (7-day window) | max total autonomous touches, any channel |
| `confidence_threshold` | 0.6 | min Step 3 `diagnosis_confidence` for AUTO_EXECUTE eligibility |
| `monetary_ceiling` | ₹15,000 | above this, a money-movement action requires approval. Chosen as a round number just above the Step 2 dataset's 90th-percentile `amount_at_risk` (~₹12,422) — a reasonable starting point, not a cost-effectiveness-validated figure |
| `contact_window` | 09:00–20:00 local hour | communication actions only auto-authorized within this window |

All five are `GuardrailConfig` fields, passed as a single object — no
threshold is hardcoded inline in the rule logic (`guardrail_engine.py` reads
every value from `self.config`).

## 5. Authorization rules
Evaluated in this order — **STOP conditions checked first** (most restrictive wins):

**STOP:**
1. `suspicious_flag` is True → STOP, regardless of anything else.
2. Recommended action requires customer communication AND `customer_opt_out` is True → STOP.
3. Recommended action requires customer communication AND `communication_allowed` is False → STOP.
4. Recommended action requires customer communication AND current time is outside `contact_window` → STOP (see §7 on why this isn't a "schedule later" state).
5. Recommended action is `payment_retry`/`mandate_retry` AND `retry_count >= retry_limit` → STOP.
6. Decision is missing, or `decision_status` is `not_applicable` / `no_applicable_actions` / has no `recommended_action` → STOP ("invalid/missing recommendation").

**APPROVAL_REQUIRED** (checked only if no STOP condition fired):
7. `previous_attempt_count >= autonomous_attempt_cap` → APPROVAL_REQUIRED (see §7 — deliberately not STOP).
8. `diagnosis_confidence < confidence_threshold` → APPROVAL_REQUIRED.
9. Recommended action has `money_movement=True` AND `amount_at_risk > monetary_ceiling` → APPROVAL_REQUIRED.
10. Recommended action has `requires_merchant_approval=True` by Step 4 definition (e.g. `escalation`) → APPROVAL_REQUIRED.

**AUTO_EXECUTE:** none of the above fired.

Multiple APPROVAL_REQUIRED rules can fire simultaneously — all are recorded
in `triggered_rules`, and `reason` concatenates every one, so the audit
trail shows the complete picture, not just the first match.

## 6. Human-in-the-loop
Every `APPROVAL_REQUIRED` outcome sets `approval_required=True` and lists the
specific rule(s) that triggered it — a merchant reviewer sees exactly which
threshold(s) were crossed (low confidence, high amount, attempt cap, or the
action being definitionally human-gated) without needing to re-derive it.

## 7. Documented design decisions (not oversights)
- **`retry_limit` → STOP, but `autonomous_attempt_cap` → APPROVAL_REQUIRED.**
  Step 1 §6 describes the attempt cap as applying "before mandatory
  escalation" — language implying forced human review, not abandonment.
  `retry_limit` is worded more strictly. This asymmetry is intentional.
- **Outside `contact_window` → STOP, not "scheduled for later."** Step 1's
  example list mentions rescheduling, but Step 6's contract is strictly
  tri-state. Real queuing/rescheduling belongs to a future orchestrator
  (Step 9/12), not this engine.
- **Attempt-history source is the case's own `retry_count`/
  `previous_attempt_count` fields, not a live audit trail** — Step 8 (Audit
  Trail) doesn't exist yet. Once it does, these checks should be re-pointed
  at real historical data instead of the Step 2 dataset snapshot.

## 8. Leakage prevention
`guardrail_engine.py` only ever reads: case facts (`leakage_category`,
`amount_at_risk`, `retry_count`, `previous_attempt_count`,
`customer_opt_out`, `suspicious_flag`, `communication_allowed`), Step 3's
`diagnosis_confidence`, and Step 5's recommended-action metadata
(`action_type`, `money_movement`, `customer_communication`,
`requires_merchant_approval`). It never reads `amount_recovered`,
`ground_truth_recoverable`, `ground_truth_recovery_outcome`,
`recovery_observed`, or `recovery_reason` — verified by
`test_authorization_invariant_to_forbidden_fields` (tampering those fields on
an otherwise-identical case produces an identical authorization) and by a
source-scan test that checks actual code (docstrings/comments stripped) for
any reference to those names.

## 9. Tests (31/31 passing)
Covers: AUTO_EXECUTE on a clean case; APPROVAL_REQUIRED for low confidence,
monetary ceiling breach, attempt-cap breach, and definitional
(`requires_merchant_approval`) actions; STOP for suspicious flag, retry-limit
breach, opt-out-blocks-communication, communication-not-allowed,
outside-contact-window, and invalid/missing/not-applicable/no-actions
decisions; boundary check at exactly the confidence threshold; monetary
ceiling correctly *not* checked for non-money-movement actions; determinism
on repeated calls; forbidden-field invariance and source-scan leakage tests;
`approval_required` flag consistency; custom-config behavioral change
(proving defaults are genuinely configurable, not hardcoded); and full
end-to-end integration tests running real Step 3 → Step 4 → Step 5 → Step 6
for all four leakage categories plus the successful-case STOP path.

## 10. Regression
Step 2 dataset validation: 22/22. Step 3 tests: 17/17 (model artifact
checksum unchanged — not retrained). Step 4 tests: 29/29. Step 5 tests: 21/21.

## 11. Limitations
- `monetary_ceiling` is a reasonable percentile-informed guess, not a
  cost-effectiveness-validated threshold — same caveat class as every other
  Step 1 default.
- `contact_window` uses a single global 09:00–20:00 window with no timezone
  or per-merchant configuration — acceptable for a hackathon demo, a real
  deployment would need per-merchant/per-customer timezone awareness.
- Attempt-history checks use the dataset's static counters rather than a
  live audit trail (see §7) — expected to be revisited in Step 8.
- No rate-limiting or cooldown-between-attempts logic (e.g. "don't retry
  within 1 hour of the last attempt") — only cumulative counts are checked.

## 12. Ready for Step 7
Every `GuardrailDecision` carries `outcome`, the specific `recommended_action_type`,
and a full `config_used` snapshot — everything Step 7's Razorpay integration
needs to know whether (and under what authorization) it may act, without
Step 6 having called any external API itself.
