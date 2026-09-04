"""
RecoverAI — Decision Engine: Tests (Step 5)

Run:
    python3 -m pytest test_decision_engine.py -v
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))

import pandas as pd
import pytest

from decision_models import DecisionStatus, LikelihoodTier
from decision_engine import DecisionEngine, determine_likelihood_tier
from action_models import ActionType

# Resolved relative to this file's own directory, not the current working
# directory, so `pytest` works from the project root (or anywhere else) on
# Windows or any OS.
DATA_CSV = str(Path(__file__).resolve().parent.parent / "data" / "recoverai_cases.csv")
LEAKAGE_CATEGORIES = ["failed_payment", "checkout_abandonment", "failed_subscription", "overdue_receivable"]

FORBIDDEN_POST_ACTION_FIELDS = [
    "ground_truth_recoverable", "ground_truth_recovery_outcome",
    "ground_truth_recovery_value", "amount_recovered", "recovery_observed", "recovery_reason",
]


@pytest.fixture(scope="module")
def df():
    return pd.read_csv(DATA_CSV)


@pytest.fixture(scope="module")
def diagnosis_engine():
    from diagnose import DiagnosisEngine
    return DiagnosisEngine(os.path.join(os.path.dirname(__file__), "..", "diagnosis", "model.joblib"))


@pytest.fixture(scope="module")
def engine(diagnosis_engine):
    return DecisionEngine(diagnosis_engine=diagnosis_engine)


def _row(df, category, comm_allowed=None):
    subset = df[df["leakage_category"] == category]
    if comm_allowed is not None:
        subset = subset[subset["communication_allowed"] == comm_allowed]
    if len(subset) == 0:
        pytest.skip(f"no matching row for category={category}, comm_allowed={comm_allowed}")
    return subset.iloc[0]


# 1. All four leakage categories produce a recommendation
@pytest.mark.parametrize("category", LEAKAGE_CATEGORIES)
def test_all_leakage_categories_produce_recommendation(engine, df, category):
    row = _row(df, category, comm_allowed=True)
    decision = engine.decide(row)
    assert decision.decision_status == DecisionStatus.RECOMMENDED
    assert decision.recommended_action_type is not None
    assert decision.leakage_category == category


# 2. Multiple compatible actions -> alternatives are populated and ranked
def test_multiple_compatible_actions_produce_alternatives(engine, df):
    row = _row(df, "failed_payment", comm_allowed=True)
    decision = engine.decide(row)
    assert len(decision.alternatives_considered) >= 1
    applicable_alts = [a for a in decision.alternatives_considered if a.technically_applicable]
    assert len(applicable_alts) >= 1
    for alt in applicable_alts:
        assert alt.priority_rank is not None


# 3. No compatible actions -> handled gracefully, not a crash
def test_no_compatible_actions_handled_gracefully():
    engine = DecisionEngine()
    case = {"case_id": "SYNTH_NOACTION", "leakage_category": "failed_payment", "amount_at_risk": 100}
    diagnosis = {"case_id": "SYNTH_NOACTION", "predicted_recovery_likelihood": 0.5, "diagnosis_confidence": 0.5}
    decision = engine.decide(case, diagnosis=diagnosis, actions=[])
    assert decision.decision_status == DecisionStatus.NO_APPLICABLE_ACTIONS
    assert decision.recommended_action_type is None
    assert decision.recommendation_reason != ""


# 4. Successful/non-leakage cases
def test_successful_case_not_applicable(engine, df):
    successful_rows = df[df["leakage_category"] == "successful"]
    if len(successful_rows) == 0:
        pytest.skip("no successful cases in dataset")
    row = successful_rows.iloc[0]
    decision = engine.decide(row)
    assert decision.decision_status == DecisionStatus.NOT_APPLICABLE
    assert decision.recommended_action_type is None
    assert decision.likelihood_tier == LikelihoodTier.NOT_APPLICABLE


# 5. Low vs high diagnosis likelihood produce different tiers/recommendations
def test_low_vs_high_likelihood_produce_different_tiers():
    engine = DecisionEngine()
    case = {"case_id": "SYNTH_TIER", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True}

    high = engine.decide(case, diagnosis={"case_id": "SYNTH_TIER", "predicted_recovery_likelihood": 0.85,
                                           "diagnosis_confidence": 0.7})
    low = engine.decide(case, diagnosis={"case_id": "SYNTH_TIER", "predicted_recovery_likelihood": 0.15,
                                          "diagnosis_confidence": 0.7})

    assert high.likelihood_tier == LikelihoodTier.HIGH
    assert low.likelihood_tier == LikelihoodTier.LOW
    assert high.recommended_action_type != low.recommended_action_type
    assert low.recommended_action_type == ActionType.ESCALATION.value


def test_high_likelihood_low_confidence_is_capped_to_medium():
    engine = DecisionEngine()
    case = {"case_id": "SYNTH_CAP", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True}
    decision = engine.decide(case, diagnosis={"case_id": "SYNTH_CAP", "predicted_recovery_likelihood": 0.9,
                                               "diagnosis_confidence": 0.05})
    assert decision.likelihood_tier == LikelihoodTier.MEDIUM


def test_determine_likelihood_tier_boundaries():
    assert determine_likelihood_tier(None, None) == LikelihoodTier.NOT_APPLICABLE
    assert determine_likelihood_tier(0.34, 0.9) == LikelihoodTier.LOW
    assert determine_likelihood_tier(0.35, 0.9) == LikelihoodTier.MEDIUM
    assert determine_likelihood_tier(0.6, 0.9) == LikelihoodTier.HIGH
    assert determine_likelihood_tier(0.6, 0.1) == LikelihoodTier.MEDIUM


# 6. Communication restrictions influence which action is recommended
def test_communication_disabled_avoids_communication_actions(engine, df):
    row = _row(df, "overdue_receivable", comm_allowed=False)
    decision = engine.decide(row)
    if decision.decision_status == DecisionStatus.RECOMMENDED:
        assert decision.recommended_action_type == ActionType.ESCALATION.value


def test_communication_disabled_only_escalation_applicable():
    engine = DecisionEngine()
    case = {"case_id": "SYNTH_NOCOMM", "leakage_category": "overdue_receivable", "amount_at_risk": 1000,
            "communication_allowed": False, "days_overdue": 10}
    diagnosis = {"case_id": "SYNTH_NOCOMM", "predicted_recovery_likelihood": 0.8, "diagnosis_confidence": 0.8}
    decision = engine.decide(case, diagnosis=diagnosis)
    assert decision.decision_status == DecisionStatus.RECOMMENDED
    assert decision.recommended_action_type == ActionType.ESCALATION.value
    for alt in decision.alternatives_considered:
        if not alt.technically_applicable:
            assert "communication" in alt.reason_not_chosen.lower()


# 7. Deterministic repeated decisions
def test_repeated_decisions_are_identical(engine, df):
    row = _row(df, "failed_subscription", comm_allowed=True)
    d1 = engine.decide(row)
    d2 = engine.decide(row)
    assert d1.recommended_action_type == d2.recommended_action_type
    assert d1.recommendation_reason == d2.recommendation_reason
    assert [a.action_type for a in d1.alternatives_considered] == [a.action_type for a in d2.alternatives_considered]


# 8. Forbidden post-action fields never influence the decision
def test_decision_invariant_to_forbidden_fields(engine, df):
    row = _row(df, "failed_payment", comm_allowed=True).to_dict()
    d1 = engine.decide(dict(row))

    tampered = dict(row)
    tampered["amount_recovered"] = 999999.0
    tampered["ground_truth_recovery_outcome"] = "recovered"
    tampered["ground_truth_recoverable"] = True
    tampered["recovery_observed"] = True
    tampered["recovery_reason"] = "retry_succeeded"
    d2 = engine.decide(tampered)

    assert d1.recommended_action_type == d2.recommended_action_type
    assert d1.recommendation_reason == d2.recommendation_reason


def test_no_forbidden_field_names_in_decision_output(engine, df):
    row = _row(df, "overdue_receivable", comm_allowed=True)
    decision = engine.decide(row)
    output_str = str(decision.to_dict())
    for forbidden in FORBIDDEN_POST_ACTION_FIELDS:
        assert forbidden not in output_str


def test_no_forbidden_import_in_decision_engine_source():
    src = open(os.path.join(os.path.dirname(__file__), "decision_engine.py"), encoding="utf-8").read()
    for forbidden in FORBIDDEN_POST_ACTION_FIELDS:
        assert forbidden not in src


# 9. Explainable recommendation reasons
def test_recommendation_reason_is_explanatory(engine, df):
    row = _row(df, "checkout_abandonment", comm_allowed=True)
    decision = engine.decide(row)
    assert len(decision.recommendation_reason) > 20
    assert "RECOMMENDATION only" in decision.recommendation_reason
    assert decision.recommended_action_type in decision.recommendation_reason


def test_mismatched_diagnosis_case_id_rejected():
    engine = DecisionEngine()
    case = {"case_id": "A", "leakage_category": "failed_payment", "amount_at_risk": 100}
    diagnosis = {"case_id": "B", "predicted_recovery_likelihood": 0.5, "diagnosis_confidence": 0.5}
    with pytest.raises(ValueError):
        engine.decide(case, diagnosis=diagnosis)


def test_missing_leakage_category_raises():
    engine = DecisionEngine()
    with pytest.raises(ValueError):
        engine.decide({"case_id": "X"}, diagnosis={"predicted_recovery_likelihood": 0.5, "diagnosis_confidence": 0.5})


def test_no_diagnosis_and_no_engine_raises():
    engine = DecisionEngine()  # no diagnosis_engine injected
    with pytest.raises(ValueError):
        engine.decide({"case_id": "X", "leakage_category": "failed_payment", "amount_at_risk": 100})


# Structural: decision never claims execution/authorization
def test_decision_status_vocabulary_never_overlaps_guardrail_vocabulary():
    forbidden_terms = {"AUTO_EXECUTE", "APPROVAL_REQUIRED", "STOP"}
    status_values = {s.value.upper() for s in DecisionStatus}
    assert status_values.isdisjoint(forbidden_terms)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
