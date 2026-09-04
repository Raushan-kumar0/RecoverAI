# RecoverAI — Decision Engine (Step 5)

## 1. Scope
Selects **one recommended action per case**, with a clear reason and the
alternatives considered. This is a **recommendation only**:
- Does NOT authorize execution.
- Does NOT implement AUTO_EXECUTE / APPROVAL_REQUIRED / STOP.
- Does NOT enforce retry limits or monetary ceilings.
- Does NOT call Razorpay or send any communication.
- Does NOT use any post-action ground-truth field.

## 2. Files
```
recoverai/decision_engine/
  decision_models.py       DecisionStatus, LikelihoodTier enums + Decision/AlternativeConsidered dataclasses
  decision_engine.py         DecisionEngine — tier logic + priority-based selection + explanation
  test_decision_engine.py     21 tests
  README.md                    this file
```

## 3. Interface
```python
from decision_engine import DecisionEngine
from diagnose import DiagnosisEngine

diagnosis_engine = DiagnosisEngine("../diagnosis/model.joblib")
engine = DecisionEngine(diagnosis_engine=diagnosis_engine)

decision = engine.decide(case)                      # computes diagnosis + actions internally
# or, fully explicit / test-friendly:
decision = engine.decide(case, diagnosis=diag, actions=action_list)
```
Returns a `Decision`: `case_id`, `leakage_category`, `decision_status`
(`recommended` / `no_applicable_actions` / `not_applicable`),
`recommended_action_type`, `recommended_action` (full `RecoveryAction.to_dict()`),
`likelihood_tier`, `predicted_recovery_likelihood`, `diagnosis_confidence`,
`recommendation_reason`, `alternatives_considered` (every other action
Step 4 returned, applicable or not, with its rank/reason).

`DecisionStatus`'s vocabulary (`recommended` / `no_applicable_actions` /
`not_applicable`) is deliberately disjoint from Step 6's future
`AUTO_EXECUTE` / `APPROVAL_REQUIRED` / `STOP` — verified by
`test_decision_status_vocabulary_never_overlaps_guardrail_vocabulary` — so
the two layers can never be confused by a future reader or a future Step 6
implementation.

## 4. Decision logic
**Step 1 — tier the case** from Step 3's `predicted_recovery_likelihood` and
`diagnosis_confidence`:

| Likelihood | Confidence | Tier |
|---|---|---|
| < 0.35 | any | LOW |
| ≥ 0.35 and < 0.6 | any | MEDIUM |
| ≥ 0.6 | ≥ 0.3 | HIGH |
| ≥ 0.6 | < 0.3 | MEDIUM (capped down — high likelihood we aren't confident in doesn't earn the aggressive tier) |
| `None` (successful case) | — | NOT_APPLICABLE |

**Step 2 — rank actions for (category, tier)** using a documented, static
priority table (`PRIORITY_BY_CATEGORY_AND_TIER` in `decision_engine.py`):
- **HIGH** tier: prioritize the most direct fix (`payment_retry` /
  `mandate_retry` / `recovery_payment_link`) — likely to work, worth the
  direct attempt.
- **MEDIUM** tier: prioritize a lower-friction nudge (payment link / reminder)
  over forcing a direct retry.
- **LOW** tier: prioritize `escalation` — autonomous action is unlikely to
  help; hand to a human.

**Step 3 — select** the highest-priority action among Step 4's
*technically-applicable* actions for this case. If none of the tier's
preferred actions are applicable, later-ranked ones are still tried in
order; `escalation` (always technically applicable per Step 4) guarantees a
recommendation is always possible whenever any leakage-category actions
exist at all.

**Why rule-based, not another model:** the diagnosis (Step 3) is already the
system's ML/AI component. Layering a second opaque model here would blur the
Step 1 §5 separation between "AI reasoning" and "AI recommendation" without
adding real value — a transparent, deterministic policy over the diagnosis
output is more auditable and exactly what a hackathon judge needs to verify
by hand.

## 5. Example decisions
```
failed_payment,  high tier   (likelihood 85%, confidence 70%) -> payment_retry
checkout_abandonment, high tier (likelihood 89%, confidence 78%) -> recovery_payment_link
failed_subscription, high tier (likelihood 91%, confidence 83%) -> mandate_retry
overdue_receivable, high tier (likelihood 78%, confidence 55%) -> recovery_payment_link

synthetic failed_payment case, varying only diagnosis:
  likelihood 0.85, confidence 0.70 -> tier=high,   payment_retry
  likelihood 0.45, confidence 0.50 -> tier=medium, recovery_payment_link
  likelihood 0.15, confidence 0.20 -> tier=low,    escalation
  likelihood 0.90, confidence 0.05 -> tier=medium (capped), recovery_payment_link
```
Every recommendation reason explicitly states the tier, the likelihood/
confidence that produced it, and ends with "This is a RECOMMENDATION only —
it has not been authorized or executed."

## 6. Communication restrictions
Handled entirely by reusing Step 4's `technically_applicable` flag — Step 5
does not re-check `communication_allowed` itself. When a case has
communication disabled, every communication-type action is already excluded
by Step 4, so the priority ranking naturally falls through to `escalation`
(verified by `test_communication_disabled_only_escalation_applicable`).

## 7. Tests (21/21 passing)
Covers: all four leakage categories produce a recommendation; multiple
compatible actions produce ranked alternatives; zero compatible actions is
handled gracefully (not a crash); successful cases return `not_applicable`;
low vs. high likelihood produce genuinely different tiers/recommendations;
high-likelihood-low-confidence is correctly capped to medium; tier boundary
edge cases; communication-disabled cases fall through to escalation;
decisions are deterministic on repeated calls; decisions are **provably
invariant** to forbidden post-action fields (same case, tampered with fake
`amount_recovered`/`ground_truth_*` values, produces an identical decision);
no forbidden field name appears anywhere in decision output or in the
module's source; recommendation reasons are substantive and self-explanatory;
mismatched diagnosis `case_id` is rejected; missing `leakage_category` and
missing-diagnosis-with-no-engine both fail safely; decision vocabulary never
overlaps guardrail vocabulary.

## 8. Regression (unchanged from Step 4 handoff)
Step 2 dataset validation: 22/22. Step 3 diagnosis tests: 17/17 (model
artifact checksum unchanged — not retrained). Step 4 action toolbox tests: 29/29.

## 9. Limitations
- The tier thresholds (0.35 / 0.6 / confidence floor 0.3) are Step 5's own
  documented heuristic, not empirically tuned against outcome data — same
  caveat as Step 1's guardrail defaults, and for the same reason (no
  calibration signal exists yet at this stage of the project).
- Priority orderings are authored by hand per category/tier rather than
  learned; this is intentional for auditability but means they encode a
  business judgment, not a fitted result.
- The engine does not yet account for `amount_at_risk` magnitude in ranking
  (e.g. a ₹200,000 receivable and a ₹500 receivable get the same priority
  ordering) — cost/value-weighting is left for Step 6 (monetary ceiling) and
  Step 10 (measurement), consistent with the toolbox/decision layers staying
  policy-free per the Step 1 separation.
- No confidence-weighted tie-breaking within a priority rank (ties don't
  currently occur given the priority lists are total orderings over the
  catalog, but this is worth noting for future extension).

## 10. Ready for Step 6
Every `Decision` carries `recommended_action` (the full `RecoveryAction`,
including `risk_level`, `money_movement`, `requires_merchant_approval`,
`guardrail_considerations`) plus `predicted_recovery_likelihood` and
`diagnosis_confidence` — everything Step 6's guardrail engine needs to
authorize, require approval for, or stop, without Step 5 having made that
call itself.
