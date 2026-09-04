"""
RecoverAI — Guardrail Engine: Tests (Step 6)

Run:
    python3 -m pytest test_guardrails.py -v
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))

from datetime import datetime
import pandas as pd
import pytest

from guardrail_config import GuardrailConfig, DEFAULT_GUARDRAIL_CONFIG
from guardrail_models import AuthorizationOutcome
from guardrail_engine import GuardrailEngine
from decision_engine import DecisionEngine
from decision_models import Decision, DecisionStatus, LikelihoodTier

# Resolved relative to this file's own directory, not the current working
# directory, so `pytest` works from the project root (or anywhere else) on
# Windows or any OS.
DATA_CSV = str(Path(__file__).resolve().parent.parent / "data" / "recoverai_cases.csv")
MIDDAY = datetime(2026, 8, 22, 14, 0)
MIDNIGHT = datetime(2026, 8, 22, 2, 0)

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
def decision_engine(diagnosis_engine):
    return DecisionEngine(diagnosis_engine=diagnosis_engine)


@pytest.fixture(scope="module")
def guard_engine():
    return GuardrailEngine()


def _row(df, category, comm_allowed=None):
    subset = df[df["leakage_category"] == category]
    if comm_allowed is not None:
        subset = subset[subset["communication_allowed"] == comm_allowed]
    if len(subset) == 0:
        pytest.skip(f"no matching row for category={category}, comm_allowed={comm_allowed}")
    return subset.iloc[0]


def _make_recommended_decision(case_id, leakage_category, action_dict):
    """Build a synthetic RECOMMENDED Decision without going through Step 5, for
    isolated guardrail testing."""
    return Decision(
        case_id=case_id,
        leakage_category=leakage_category,
        decision_status=DecisionStatus.RECOMMENDED,
        recommended_action_type=action_dict["action_type"],
        recommended_action=action_dict,
        likelihood_tier=LikelihoodTier.HIGH,
        predicted_recovery_likelihood=0.8,
        diagnosis_confidence=0.8,
        recommendation_reason="synthetic test decision",
        alternatives_considered=[],
    )


def _action(action_type, money_movement=False, customer_communication=False, requires_merchant_approval=False):
    return {
        "action_type": action_type,
        "money_movement": money_movement,
        "customer_communication": customer_communication,
        "requires_merchant_approval": requires_merchant_approval,
        "risk_level": "low",
    }


# 1. AUTO_EXECUTE — everything within limits
def test_auto_execute_when_all_checks_pass(guard_engine):
    case = {"case_id": "C1", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C1", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.AUTO_EXECUTE
    assert g.approval_required is False


# 2. APPROVAL_REQUIRED — low confidence
def test_approval_required_low_confidence(guard_engine):
    case = {"case_id": "C2", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.2}
    decision = _make_recommended_decision("C2", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.APPROVAL_REQUIRED
    assert g.approval_required is True
    assert any(r.rule == "confidence_below_threshold" for r in g.triggered_rules)


# APPROVAL_REQUIRED — action requires merchant approval by definition (escalation)
def test_approval_required_escalation_by_definition(guard_engine):
    case = {"case_id": "C3", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C3", "failed_payment", _action("escalation", requires_merchant_approval=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.APPROVAL_REQUIRED
    assert any(r.rule == "action_requires_merchant_approval_by_definition" for r in g.triggered_rules)


# 3. STOP — suspicious flag
def test_stop_suspicious_flag(guard_engine):
    case = {"case_id": "C4", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": True,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C4", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "suspicious_flag" for r in g.triggered_rules)


# retry limits
def test_retry_limit_exceeded_stops(guard_engine):
    case = {"case_id": "C5", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 3, "previous_attempt_count": 3}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C5", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "retry_limit_exceeded" for r in g.triggered_rules)


def test_retry_within_limit_does_not_stop_on_retry_rule(guard_engine):
    case = {"case_id": "C5b", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 2, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C5b", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert not any(r.rule == "retry_limit_exceeded" for r in g.triggered_rules)


def test_autonomous_attempt_cap_reached_requires_approval(guard_engine):
    case = {"case_id": "C6", "leakage_category": "overdue_receivable", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 3, "days_overdue": 10}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C6", "overdue_receivable", _action("payment_reminder", customer_communication=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.APPROVAL_REQUIRED
    assert any(r.rule == "autonomous_attempt_cap_reached" for r in g.triggered_rules)


# monetary limits
def test_monetary_ceiling_exceeded_requires_approval(guard_engine):
    case = {"case_id": "C7", "leakage_category": "failed_payment", "amount_at_risk": 50000,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C7", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.APPROVAL_REQUIRED
    assert any(r.rule == "monetary_ceiling_exceeded" for r in g.triggered_rules)


def test_monetary_ceiling_not_checked_for_non_money_movement_action(guard_engine):
    case = {"case_id": "C7b", "leakage_category": "overdue_receivable", "amount_at_risk": 200000,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0, "days_overdue": 5}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C7b", "overdue_receivable", _action("payment_reminder", customer_communication=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert not any(r.rule == "monetary_ceiling_exceeded" for r in g.triggered_rules)
    assert g.outcome == AuthorizationOutcome.AUTO_EXECUTE


# confidence threshold (also covered above; explicit boundary check)
def test_confidence_exactly_at_threshold_passes(guard_engine):
    case = {"case_id": "C8", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": DEFAULT_GUARDRAIL_CONFIG.confidence_threshold}
    decision = _make_recommended_decision("C8", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert not any(r.rule == "confidence_below_threshold" for r in g.triggered_rules)


# communication restrictions
def test_communication_action_blocked_when_not_allowed(guard_engine):
    case = {"case_id": "C9", "leakage_category": "overdue_receivable", "amount_at_risk": 500,
            "communication_allowed": False, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0, "days_overdue": 5}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C9", "overdue_receivable", _action("payment_reminder", customer_communication=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "communication_not_allowed" for r in g.triggered_rules)


def test_non_communication_action_unaffected_by_communication_restriction(guard_engine):
    case = {"case_id": "C9b", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": False, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C9b", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.AUTO_EXECUTE


def test_outside_contact_window_stops_communication_action(guard_engine):
    case = {"case_id": "C10", "leakage_category": "checkout_abandonment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C10", "checkout_abandonment", _action("recovery_payment_link", customer_communication=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDNIGHT)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "outside_contact_window" for r in g.triggered_rules)


# 4. suspicious cases (also above); opt-out cases
def test_stop_customer_opt_out_blocks_communication(guard_engine):
    case = {"case_id": "C11", "leakage_category": "overdue_receivable", "amount_at_risk": 500,
            "communication_allowed": False, "customer_opt_out": True, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0, "days_overdue": 5}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C11", "overdue_receivable", _action("payment_reminder", customer_communication=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "customer_opt_out_blocks_communication" for r in g.triggered_rules)


def test_opt_out_does_not_block_non_communication_action(guard_engine):
    case = {"case_id": "C11b", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": False, "customer_opt_out": True, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C11b", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.AUTO_EXECUTE


# invalid/missing recommendation
def test_stop_when_decision_is_none(guard_engine):
    case = {"case_id": "C12", "leakage_category": "failed_payment", "amount_at_risk": 500}
    g = guard_engine.authorize(case, {"diagnosis_confidence": 0.9}, None, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "invalid_or_missing_recommendation" for r in g.triggered_rules)


def test_stop_when_decision_not_applicable(guard_engine):
    case = {"case_id": "C13", "leakage_category": "successful", "amount_at_risk": 0}
    decision = Decision(case_id="C13", leakage_category="successful", decision_status=DecisionStatus.NOT_APPLICABLE,
                         recommended_action_type=None, recommended_action=None,
                         likelihood_tier=LikelihoodTier.NOT_APPLICABLE, predicted_recovery_likelihood=None,
                         diagnosis_confidence=None, recommendation_reason="nothing at risk")
    g = guard_engine.authorize(case, {}, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert "not_applicable" in g.reason


def test_stop_when_decision_no_applicable_actions(guard_engine):
    case = {"case_id": "C14", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = Decision(case_id="C14", leakage_category="failed_payment", decision_status=DecisionStatus.NO_APPLICABLE_ACTIONS,
                         recommended_action_type=None, recommended_action=None,
                         likelihood_tier=LikelihoodTier.LOW, predicted_recovery_likelihood=0.1,
                         diagnosis_confidence=0.1, recommendation_reason="no actions applicable")
    g = guard_engine.authorize(case, {"diagnosis_confidence": 0.1}, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP
    assert any(r.rule == "invalid_or_missing_recommendation" for r in g.triggered_rules)


# 5. deterministic repeated decisions
def test_repeated_authorization_is_deterministic(guard_engine):
    case = {"case_id": "C15", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 1, "previous_attempt_count": 1}
    diagnosis = {"diagnosis_confidence": 0.75}
    decision = _make_recommended_decision("C15", "failed_payment", _action("payment_retry", money_movement=True))
    g1 = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    g2 = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g1.outcome == g2.outcome
    assert g1.reason == g2.reason
    assert [r.rule for r in g1.triggered_rules] == [r.rule for r in g2.triggered_rules]


# 6. forbidden post-action fields never influence authorization
def test_authorization_invariant_to_forbidden_fields(guard_engine):
    case = {"case_id": "C16", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 1, "previous_attempt_count": 1}
    diagnosis = {"diagnosis_confidence": 0.75}
    decision = _make_recommended_decision("C16", "failed_payment", _action("payment_retry", money_movement=True))
    g1 = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)

    tampered_case = dict(case)
    tampered_case["amount_recovered"] = 999999
    tampered_case["ground_truth_recovery_outcome"] = "recovered"
    tampered_case["ground_truth_recoverable"] = True
    tampered_case["recovery_observed"] = True
    tampered_case["recovery_reason"] = "retry_succeeded"
    g2 = guard_engine.authorize(tampered_case, diagnosis, decision, current_time=MIDDAY)

    assert g1.outcome == g2.outcome
    assert g1.reason == g2.reason


def test_no_forbidden_field_names_in_guardrail_output(guard_engine):
    case = {"case_id": "C17", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C17", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    output_str = str(g.to_dict())
    for forbidden in FORBIDDEN_POST_ACTION_FIELDS:
        assert forbidden not in output_str


def _strip_comments_and_docstrings(src: str) -> str:
    """Removes triple-quoted docstrings and '#' comment lines so a source-scan
    test checks actual code usage, not documentation that legitimately names
    a forbidden field/term while explaining that it must never be used."""
    import re
    no_docstrings = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    no_comments = "\n".join(line for line in no_docstrings.splitlines() if not line.strip().startswith("#"))
    return no_comments


def test_no_forbidden_reference_in_guardrail_engine_source():
    src = open(os.path.join(os.path.dirname(__file__), "guardrail_engine.py"), encoding="utf-8").read()
    code_only = _strip_comments_and_docstrings(src)
    for forbidden in FORBIDDEN_POST_ACTION_FIELDS:
        assert forbidden not in code_only, f"{forbidden} referenced in actual code, not just documentation"


def test_no_razorpay_or_execution_calls_in_guardrail_modules():
    import guardrail_config, guardrail_models, guardrail_engine
    for module in (guardrail_config, guardrail_models, guardrail_engine):
        src = open(module.__file__).read()
        code_only = _strip_comments_and_docstrings(src).lower()
        assert "razorpay" not in code_only
        assert "requests.post" not in code_only
        assert "requests.get" not in code_only


# 7. human approval requirement — approval_required flag matches outcome
def test_approval_required_flag_matches_outcome(guard_engine):
    case = {"case_id": "C18", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.1}
    decision = _make_recommended_decision("C18", "failed_payment", _action("payment_retry", money_movement=True))
    g = guard_engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.approval_required == (g.outcome == AuthorizationOutcome.APPROVAL_REQUIRED)


# 8. configurable defaults — custom config actually changes behavior
def test_custom_config_changes_outcome():
    strict_config = GuardrailConfig(monetary_ceiling=100.0)  # much lower than default
    engine = GuardrailEngine(config=strict_config)
    case = {"case_id": "C19", "leakage_category": "failed_payment", "amount_at_risk": 500,
            "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
            "retry_count": 0, "previous_attempt_count": 0}
    diagnosis = {"diagnosis_confidence": 0.9}
    decision = _make_recommended_decision("C19", "failed_payment", _action("payment_retry", money_movement=True))
    g = engine.authorize(case, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.APPROVAL_REQUIRED
    assert g.config_used["monetary_ceiling"] == 100.0


def test_default_config_values_match_step1_spec():
    assert DEFAULT_GUARDRAIL_CONFIG.retry_limit == 3
    assert DEFAULT_GUARDRAIL_CONFIG.autonomous_attempt_cap == 3
    assert DEFAULT_GUARDRAIL_CONFIG.confidence_threshold == 0.6


# End-to-end integration across the full pipeline with real Step 3/4/5 output
@pytest.mark.parametrize("category", ["failed_payment", "checkout_abandonment", "failed_subscription", "overdue_receivable"])
def test_end_to_end_pipeline_produces_valid_outcome(df, decision_engine, guard_engine, category):
    row = _row(df, category, comm_allowed=True)
    decision = decision_engine.decide(row)
    diagnosis = {"diagnosis_confidence": decision.diagnosis_confidence,
                 "predicted_recovery_likelihood": decision.predicted_recovery_likelihood}
    g = guard_engine.authorize(row, diagnosis, decision, current_time=MIDDAY)
    assert g.outcome in (AuthorizationOutcome.AUTO_EXECUTE, AuthorizationOutcome.APPROVAL_REQUIRED, AuthorizationOutcome.STOP)
    assert g.recommended_action_type == decision.recommended_action_type


def test_end_to_end_successful_case_stops(df, decision_engine, guard_engine):
    successful_rows = df[df["leakage_category"] == "successful"]
    if len(successful_rows) == 0:
        pytest.skip("no successful cases")
    row = successful_rows.iloc[0]
    decision = decision_engine.decide(row)
    g = guard_engine.authorize(row, {}, decision, current_time=MIDDAY)
    assert g.outcome == AuthorizationOutcome.STOP


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
