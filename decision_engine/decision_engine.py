"""
RecoverAI — Decision Engine (Step 5)

Selects ONE recommended action per case from the technically-applicable
actions Step 4 provides, using Step 3's diagnosis as input. This is a
RECOMMENDATION only:

    - It does NOT authorize execution.
    - It does NOT implement AUTO_EXECUTE / APPROVAL_REQUIRED / STOP.
    - It does NOT enforce retry limits or monetary ceilings.
    - It does NOT call Razorpay or send any communication.
    - It does NOT use any post-action ground-truth field.

The selection logic is a deterministic, documented priority scheme driven by
Step 3's predicted_recovery_likelihood (bucketed into a tier) and
diagnosis_confidence (which can cap how aggressive a tier's pick may be),
combined with a per-category, per-tier action priority ordering. This is
intentionally rule-based rather than another ML model or LLM call — the
"AI" input here is Step 3's prediction; Step 5's job is to reason over that
prediction transparently and reproducibly, not to add a second opaque model.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))

from action_models import ActionType, RecoveryAction  # noqa: E402
from action_compatibility import get_actions_for_case  # noqa: E402

from decision_models import Decision, DecisionStatus, LikelihoodTier, AlternativeConsidered  # noqa: E402

# ----------------------------------------------------------------------------
# Tier thresholds. These are Step 5's OWN decision heuristic — distinct from
# and not to be confused with Step 6's future guardrail confidence_threshold.
# ----------------------------------------------------------------------------
LIKELIHOOD_HIGH_CUTOFF = 0.6
LIKELIHOOD_LOW_CUTOFF = 0.35
CONFIDENCE_FLOOR_FOR_HIGH_TIER = 0.3  # below this, a high-likelihood case is
                                       # capped down to the medium tier: we are
                                       # not confident enough to justify the
                                       # most direct/aggressive action.

# ----------------------------------------------------------------------------
# Per-category, per-tier priority orderings. Position 0 = most preferred.
# Rationale for the ordering direction:
#   HIGH tier   -> invest in the most direct fix (retry/mandate) since it's
#                  likely to work.
#   MEDIUM tier -> prefer a lower-friction nudge (payment link / reminder)
#                  over forcing a direct retry.
#   LOW tier    -> autonomous actions are unlikely to help; prefer escalation.
# ----------------------------------------------------------------------------
PRIORITY_BY_CATEGORY_AND_TIER = {
    "failed_payment": {
        LikelihoodTier.HIGH: [ActionType.PAYMENT_RETRY, ActionType.RECOVERY_PAYMENT_LINK,
                               ActionType.PAYMENT_REMINDER, ActionType.ESCALATION],
        LikelihoodTier.MEDIUM: [ActionType.RECOVERY_PAYMENT_LINK, ActionType.PAYMENT_REMINDER,
                                 ActionType.PAYMENT_RETRY, ActionType.ESCALATION],
        LikelihoodTier.LOW: [ActionType.ESCALATION, ActionType.PAYMENT_REMINDER,
                              ActionType.RECOVERY_PAYMENT_LINK, ActionType.PAYMENT_RETRY],
    },
    "checkout_abandonment": {
        LikelihoodTier.HIGH: [ActionType.RECOVERY_PAYMENT_LINK, ActionType.CHECKOUT_RECOVERY_REMINDER,
                               ActionType.ESCALATION],
        LikelihoodTier.MEDIUM: [ActionType.CHECKOUT_RECOVERY_REMINDER, ActionType.RECOVERY_PAYMENT_LINK,
                                 ActionType.ESCALATION],
        LikelihoodTier.LOW: [ActionType.ESCALATION, ActionType.CHECKOUT_RECOVERY_REMINDER,
                              ActionType.RECOVERY_PAYMENT_LINK],
    },
    "failed_subscription": {
        LikelihoodTier.HIGH: [ActionType.MANDATE_RETRY, ActionType.RECOVERY_PAYMENT_LINK,
                               ActionType.PAYMENT_REMINDER, ActionType.ESCALATION],
        LikelihoodTier.MEDIUM: [ActionType.RECOVERY_PAYMENT_LINK, ActionType.PAYMENT_REMINDER,
                                 ActionType.MANDATE_RETRY, ActionType.ESCALATION],
        LikelihoodTier.LOW: [ActionType.ESCALATION, ActionType.PAYMENT_REMINDER,
                              ActionType.RECOVERY_PAYMENT_LINK, ActionType.MANDATE_RETRY],
    },
    "overdue_receivable": {
        LikelihoodTier.HIGH: [ActionType.RECOVERY_PAYMENT_LINK, ActionType.PAYMENT_REMINDER,
                               ActionType.RECEIVABLES_FOLLOWUP, ActionType.ESCALATION],
        LikelihoodTier.MEDIUM: [ActionType.PAYMENT_REMINDER, ActionType.RECEIVABLES_FOLLOWUP,
                                 ActionType.RECOVERY_PAYMENT_LINK, ActionType.ESCALATION],
        LikelihoodTier.LOW: [ActionType.ESCALATION, ActionType.RECEIVABLES_FOLLOWUP,
                              ActionType.PAYMENT_REMINDER, ActionType.RECOVERY_PAYMENT_LINK],
    },
}


def determine_likelihood_tier(predicted_recovery_likelihood, diagnosis_confidence) -> LikelihoodTier:
    if predicted_recovery_likelihood is None:
        return LikelihoodTier.NOT_APPLICABLE
    if predicted_recovery_likelihood < LIKELIHOOD_LOW_CUTOFF:
        return LikelihoodTier.LOW
    if predicted_recovery_likelihood >= LIKELIHOOD_HIGH_CUTOFF:
        confidence = diagnosis_confidence if diagnosis_confidence is not None else 0.0
        if confidence >= CONFIDENCE_FLOOR_FOR_HIGH_TIER:
            return LikelihoodTier.HIGH
        return LikelihoodTier.MEDIUM  # high likelihood but low confidence -> capped down
    return LikelihoodTier.MEDIUM


class DecisionEngine:
    def __init__(self, diagnosis_engine=None):
        """
        diagnosis_engine: an optional DiagnosisEngine instance, used only when
        `decide()` is called without a pre-computed diagnosis. Injectable so
        tests can exercise decision logic with synthetic diagnosis dicts
        without needing a real trained model.
        """
        self.diagnosis_engine = diagnosis_engine

    def decide(self, case, diagnosis=None, actions=None) -> Decision:
        """
        case: dict or pandas.Series (pre-decision fields; see feature_config.py).
        diagnosis: optional pre-computed dict from DiagnosisEngine.diagnose(case).
                   If omitted, self.diagnosis_engine must be set.
        actions: optional pre-computed list[RecoveryAction] from
                 get_actions_for_case(case, diagnosis). If omitted, computed here.

        Returns a Decision — a RECOMMENDATION only, never an authorization.
        """
        case_id = self._get(case, "case_id")
        leakage_category = self._get(case, "leakage_category")
        if leakage_category is None:
            raise ValueError("case is missing required field: leakage_category")

        if diagnosis is None:
            if self.diagnosis_engine is None:
                raise ValueError(
                    "No diagnosis provided and no diagnosis_engine configured on this DecisionEngine."
                )
            diagnosis = self.diagnosis_engine.diagnose(case)

        if diagnosis.get("case_id") is not None and case_id is not None and diagnosis["case_id"] != case_id:
            raise ValueError(
                f"diagnosis case_id ({diagnosis.get('case_id')!r}) does not match case case_id "
                f"({case_id!r}); refusing to decide on mismatched diagnosis."
            )

        likelihood = diagnosis.get("predicted_recovery_likelihood")
        confidence = diagnosis.get("diagnosis_confidence")

        if leakage_category == "successful" or likelihood is None:
            return Decision(
                case_id=case_id,
                leakage_category=leakage_category,
                decision_status=DecisionStatus.NOT_APPLICABLE,
                recommended_action_type=None,
                recommended_action=None,
                likelihood_tier=LikelihoodTier.NOT_APPLICABLE,
                predicted_recovery_likelihood=None,
                diagnosis_confidence=None,
                recommendation_reason="No revenue at risk — nothing to recommend.",
                alternatives_considered=[],
            )

        if actions is None:
            actions = get_actions_for_case(case, diagnosis=diagnosis)

        tier = determine_likelihood_tier(likelihood, confidence)
        priority_list = PRIORITY_BY_CATEGORY_AND_TIER.get(leakage_category, {}).get(tier, [])

        applicable = [a for a in actions if a.technically_applicable]

        if len(applicable) == 0:
            alternatives = [
                AlternativeConsidered(
                    action_type=a.action_type.value,
                    technically_applicable=False,
                    priority_rank=None,
                    reason_not_chosen=f"not technically applicable: {a.applicability_reason}",
                )
                for a in actions
            ]
            return Decision(
                case_id=case_id,
                leakage_category=leakage_category,
                decision_status=DecisionStatus.NO_APPLICABLE_ACTIONS,
                recommended_action_type=None,
                recommended_action=None,
                likelihood_tier=tier,
                predicted_recovery_likelihood=likelihood,
                diagnosis_confidence=confidence,
                recommendation_reason=(
                    "No technically applicable action is available for this case "
                    "(all candidate actions were blocked — see alternatives_considered)."
                ),
                alternatives_considered=alternatives,
            )

        chosen, priority_rank = self._select_best(applicable, priority_list)

        alternatives = self._build_alternatives(actions, chosen, priority_list)

        reason = self._explain(chosen, tier, likelihood, confidence, priority_rank, leakage_category)

        return Decision(
            case_id=case_id,
            leakage_category=leakage_category,
            decision_status=DecisionStatus.RECOMMENDED,
            recommended_action_type=chosen.action_type.value,
            recommended_action=chosen.to_dict(),
            likelihood_tier=tier,
            predicted_recovery_likelihood=likelihood,
            diagnosis_confidence=confidence,
            recommendation_reason=reason,
            alternatives_considered=alternatives,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _get(case, field_name):
        if hasattr(case, "get"):
            return case.get(field_name)
        return getattr(case, field_name, None)

    @staticmethod
    def _select_best(applicable_actions, priority_list):
        """
        Picks the applicable action with the lowest index in priority_list.
        Actions not present in priority_list (shouldn't normally happen, since
        every catalog action for a category appears in every tier's list) are
        ranked after all listed ones, in catalog-definition order, as a safe
        deterministic fallback.
        """
        def rank(action):
            if action.action_type in priority_list:
                return (0, priority_list.index(action.action_type))
            return (1, 0)

        ranked = sorted(applicable_actions, key=lambda a: rank(a))
        best = ranked[0]
        best_rank = priority_list.index(best.action_type) if best.action_type in priority_list else None
        return best, best_rank

    @staticmethod
    def _build_alternatives(all_actions, chosen, priority_list):
        alternatives = []
        for a in all_actions:
            if a.action_type == chosen.action_type:
                continue
            if not a.technically_applicable:
                reason = f"not technically applicable: {a.applicability_reason}"
                rank = None
            else:
                rank = priority_list.index(a.action_type) if a.action_type in priority_list else None
                reason = f"technically applicable but ranked lower priority than {chosen.action_type.value} for this likelihood tier"
            alternatives.append(AlternativeConsidered(
                action_type=a.action_type.value,
                technically_applicable=a.technically_applicable,
                priority_rank=rank,
                reason_not_chosen=reason,
            ))
        return alternatives

    @staticmethod
    def _explain(chosen, tier, likelihood, confidence, priority_rank, leakage_category):
        parts = [
            f"Selected '{chosen.action_type.value}' for this {leakage_category} case.",
            f"Predicted recovery likelihood is {likelihood:.0%}, placing this case in the "
            f"'{tier.value}' tier (confidence {confidence:.0%})." if confidence is not None
            else f"Predicted recovery likelihood is {likelihood:.0%}, placing this case in the '{tier.value}' tier.",
        ]
        if tier == LikelihoodTier.HIGH:
            parts.append("High-tier cases prioritize the most direct recovery action available.")
        elif tier == LikelihoodTier.MEDIUM:
            parts.append("Medium-tier cases prioritize a lower-friction nudge over a forced direct retry.")
        elif tier == LikelihoodTier.LOW:
            parts.append("Low-tier cases prioritize escalation, since autonomous action is unlikely to help.")
        if priority_rank is not None:
            parts.append(f"This action ranked #{priority_rank + 1} in the priority order for this tier and category.")
        parts.append("This is a RECOMMENDATION only — it has not been authorized or executed.")
        return " ".join(parts)
