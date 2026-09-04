"""
RecoverAI — Recovery Action Toolbox: Tests (Step 4)

Run:
    python3 -m pytest test_actions.py -v
"""

import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))

import numpy as np
import pandas as pd
import pytest

from action_models import ActionType, RiskLevel, ExecutionStatus, RecoveryAction
from action_catalog import ACTION_CATALOG, get_actions_for_category, get_action_definition
from action_compatibility import get_actions_for_case, get_applicable_actions

# Resolved relative to this file's own directory, not the current working
# directory, so `pytest` works from the project root (or anywhere else) on
# Windows or any OS.
TEST_CSV = str(Path(__file__).resolve().parent.parent / "data" / "recoverai_cases.csv")
# Note: Step 4 tests exercise deterministic action-toolbox logic, not the
# Step 3 model's predictive performance, so there is no "held-out test set"
# concern here — using the full dataset gives broader coverage (e.g. finding
# opt-out cases in every category) without touching any ML evaluation split.

LEAKAGE_CATEGORIES = ["failed_payment", "checkout_abandonment", "failed_subscription", "overdue_receivable"]


@pytest.fixture(scope="module")
def test_df():
    return pd.read_csv(TEST_CSV)


@pytest.fixture(scope="module")
def diagnosis_engine():
    from diagnose import DiagnosisEngine
    return DiagnosisEngine(os.path.join(os.path.dirname(__file__), "..", "diagnosis", "model.joblib"))


def _row(df, category, comm_allowed=None):
    subset = df[df["leakage_category"] == category]
    if comm_allowed is not None:
        subset = subset[subset["communication_allowed"] == comm_allowed]
    if len(subset) == 0:
        pytest.skip(f"no matching row for category={category}, comm_allowed={comm_allowed}")
    return subset.iloc[0]


# 1. Every action conforms to the action schema
def test_every_catalog_entry_has_required_fields():
    for action_type, definition in ACTION_CATALOG.items():
        assert isinstance(action_type, ActionType)
        assert isinstance(definition.purpose, str) and len(definition.purpose) > 0
        assert isinstance(definition.applicable_categories, list) and len(definition.applicable_categories) > 0
        assert isinstance(definition.risk_level, RiskLevel)
        assert isinstance(definition.money_movement, bool)
        assert isinstance(definition.customer_communication, bool)
        assert isinstance(definition.requires_customer_consent, bool)
        assert isinstance(definition.requires_merchant_approval, bool)
        assert isinstance(definition.razorpay_integration_needed, bool)
        assert isinstance(definition.guardrail_considerations, str) and len(definition.guardrail_considerations) > 0


def test_generated_action_conforms_to_schema(test_df):
    row = _row(test_df, "failed_payment")
    actions = get_actions_for_case(row)
    assert len(actions) > 0
    for a in actions:
        assert isinstance(a, RecoveryAction)
        assert a.action_id
        assert a.case_id == row["case_id"]
        assert a.leakage_category == "failed_payment"
        assert isinstance(a.technically_applicable, bool)
        assert isinstance(a.applicability_reason, str)
        assert a.execution_status == ExecutionStatus.NOT_EXECUTED
        d = a.to_dict()
        assert isinstance(d["action_type"], str)
        assert isinstance(d["risk_level"], str)


# 2. Every leakage category has compatible actions
@pytest.mark.parametrize("category", LEAKAGE_CATEGORIES)
def test_every_leakage_category_has_actions_defined(category):
    definitions = get_actions_for_category(category)
    assert len(definitions) > 0, f"{category} has no action definitions in the catalog"


@pytest.mark.parametrize("category", LEAKAGE_CATEGORIES)
def test_every_leakage_category_has_at_least_one_technically_applicable_action_when_comm_allowed(test_df, category):
    row = _row(test_df, category, comm_allowed=True)
    applicable = get_applicable_actions(row)
    assert len(applicable) > 0, f"{category} produced zero technically-applicable actions even with communication allowed"


def test_successful_category_has_no_actions(test_df):
    successful_rows = test_df[test_df["leakage_category"] == "successful"]
    if len(successful_rows) == 0:
        pytest.skip("no successful cases in test split")
    row = successful_rows.iloc[0]
    actions = get_actions_for_case(row)
    assert actions == []


# 3. Incompatible actions are rejected (marked technically_applicable=False, not silently dropped)
def test_missing_required_field_marks_action_inapplicable():
    case = {
        "case_id": "SYNTH001", "leakage_category": "failed_subscription",
        "amount_at_risk": 500.0, "communication_allowed": True,
        "mandate_status": None,  # missing -> mandate_retry required field
        "subscription_status": "failed",
    }
    actions = get_actions_for_case(case)
    mandate_action = next(a for a in actions if a.action_type == ActionType.MANDATE_RETRY)
    assert mandate_action.technically_applicable is False
    assert "missing required case field" in mandate_action.applicability_reason


# 4. Communication actions reject opted-out/communication-disabled cases
def test_communication_disabled_rejects_communication_actions(test_df):
    row = _row(test_df, "overdue_receivable", comm_allowed=False)
    actions = get_actions_for_case(row)
    comm_actions = [a for a in actions if a.customer_communication]
    assert len(comm_actions) > 0, "expected at least one communication-type action defined for overdue_receivable"
    for a in comm_actions:
        assert a.technically_applicable is False
        assert "communication" in a.applicability_reason.lower()


def test_communication_disabled_does_not_block_non_communication_actions(test_df):
    row = _row(test_df, "failed_payment", comm_allowed=False)
    actions = get_actions_for_case(row)
    retry = next(a for a in actions if a.action_type == ActionType.PAYMENT_RETRY)
    escalate = next(a for a in actions if a.action_type == ActionType.ESCALATION)
    assert retry.technically_applicable is True
    assert escalate.technically_applicable is True


# 5. Money-moving actions are correctly marked
def test_money_movement_flags():
    assert get_action_definition(ActionType.PAYMENT_RETRY).money_movement is True
    assert get_action_definition(ActionType.MANDATE_RETRY).money_movement is True


# 6. Non-money actions are correctly marked
def test_non_money_movement_flags():
    for action_type in [ActionType.RECOVERY_PAYMENT_LINK, ActionType.PAYMENT_REMINDER,
                         ActionType.CHECKOUT_RECOVERY_REMINDER, ActionType.RECEIVABLES_FOLLOWUP,
                         ActionType.ESCALATION]:
        assert get_action_definition(action_type).money_movement is False


# 7. No action executes
def test_no_action_ever_executes(test_df):
    for category in LEAKAGE_CATEGORIES:
        row = _row(test_df, category)
        for a in get_actions_for_case(row):
            assert a.execution_status == ExecutionStatus.NOT_EXECUTED


def test_recovery_action_has_no_execute_method():
    # structural guarantee: RecoveryAction is a plain data object with no
    # method that could perform an action.
    forbidden_method_names = {"execute", "run", "send", "call_razorpay", "charge", "retry"}
    action_methods = {m for m in dir(RecoveryAction) if not m.startswith("_")}
    assert forbidden_method_names.isdisjoint(action_methods)


# 8. No Razorpay API is called
def test_no_razorpay_imports_in_step4_modules():
    import action_models, action_catalog, action_compatibility
    for module in (action_models, action_catalog, action_compatibility):
        src = open(module.__file__).read().lower()
        assert "razorpay" not in src or "razorpay_integration_needed" in src or "will need a razorpay" in src \
            or "not yet implemented" in src or "step 7" in src
        # stronger check: no actual API call patterns
        assert "requests.post" not in src
        assert "requests.get" not in src
        assert "api.razorpay.com" not in src
        assert ".create(" not in src


# 9. Unknown action types fail safely
def test_unknown_action_type_raises():
    with pytest.raises(ValueError):
        get_action_definition("not_a_real_action_type")


def test_unknown_leakage_category_raises():
    case = {"case_id": "X", "leakage_category": "not_a_real_category", "amount_at_risk": 100}
    with pytest.raises(ValueError):
        get_actions_for_case(case)


def test_missing_leakage_category_raises():
    case = {"case_id": "X", "amount_at_risk": 100}
    with pytest.raises(ValueError):
        get_actions_for_case(case)


# 10. Diagnosis output can be consumed by the compatibility layer
def test_diagnosis_output_consumed(test_df, diagnosis_engine):
    row = _row(test_df, "checkout_abandonment", comm_allowed=True)
    diagnosis = diagnosis_engine.diagnose(row)
    actions = get_actions_for_case(row, diagnosis=diagnosis)
    assert len(actions) > 0
    for a in actions:
        assert a.diagnosis_context is not None
        assert a.diagnosis_context["predicted_recovery_likelihood"] == diagnosis["predicted_recovery_likelihood"]


def test_diagnosis_context_does_not_affect_technical_applicability(test_df, diagnosis_engine):
    row = _row(test_df, "checkout_abandonment", comm_allowed=True)
    diagnosis = diagnosis_engine.diagnose(row)
    actions_with_diag = get_actions_for_case(row, diagnosis=diagnosis)
    actions_without_diag = get_actions_for_case(row, diagnosis=None)
    flags_with = {a.action_type: a.technically_applicable for a in actions_with_diag}
    flags_without = {a.action_type: a.technically_applicable for a in actions_without_diag}
    assert flags_with == flags_without


def test_mismatched_diagnosis_case_id_rejected(test_df, diagnosis_engine):
    row = _row(test_df, "failed_payment")
    other_row = _row(test_df, "checkout_abandonment")
    diagnosis = diagnosis_engine.diagnose(other_row)  # deliberately wrong case's diagnosis
    with pytest.raises(ValueError):
        get_actions_for_case(row, diagnosis=diagnosis)


# 11. Actions are deterministic/reproducible
def test_action_ids_deterministic(test_df):
    row = _row(test_df, "failed_payment")
    actions1 = get_actions_for_case(row)
    actions2 = get_actions_for_case(row)
    ids1 = sorted(a.action_id for a in actions1)
    ids2 = sorted(a.action_id for a in actions2)
    assert ids1 == ids2


def test_repeated_calls_produce_identical_applicability(test_df):
    row = _row(test_df, "overdue_receivable")
    r1 = [(a.action_type, a.technically_applicable, a.applicability_reason) for a in get_actions_for_case(row)]
    r2 = [(a.action_type, a.technically_applicable, a.applicability_reason) for a in get_actions_for_case(row)]
    assert r1 == r2


# 12. Invalid cases are handled safely
def test_case_missing_amount_at_risk_still_flags_missing_field():
    case = {"case_id": "X", "leakage_category": "checkout_abandonment", "communication_allowed": True}
    actions = get_actions_for_case(case)
    link = next(a for a in actions if a.action_type == ActionType.RECOVERY_PAYMENT_LINK)
    assert link.technically_applicable is False
    assert "amount_at_risk" in link.applicability_reason


def test_case_as_pandas_series_and_dict_equivalent(test_df):
    row = _row(test_df, "failed_payment", comm_allowed=True)
    as_series_actions = get_actions_for_case(row)
    as_dict_actions = get_actions_for_case(row.to_dict())
    flags_series = {a.action_type: a.technically_applicable for a in as_series_actions}
    flags_dict = {a.action_type: a.technically_applicable for a in as_dict_actions}
    assert flags_series == flags_dict


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
