"""
RecoverAI — Diagnosis Layer: Tests (Step 3)

Run:
    python3 -m pytest test_diagnosis.py -v
or:
    python3 test_diagnosis.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from feature_config import (
    PRE_DECISION_FEATURES, POST_ACTION_FIELDS, ALL_FORBIDDEN_COLUMNS,
    TARGET_COLUMN, assert_no_forbidden_columns, select_pre_decision_features,
)
from diagnose import DiagnosisEngine

# Resolved relative to this file's own directory, not the current working
# directory, so `pytest` works from the project root (or anywhere else) on
# Windows or any OS.
THIS_DIR = Path(__file__).resolve().parent
TEST_CSV = str(THIS_DIR.parent / "data" / "test.csv")
MODEL_PATH = str(THIS_DIR / "model.joblib")

REQUIRED_SCHEMA_KEYS = {
    "case_id", "leakage_category", "root_cause", "risk_factors",
    "positive_recovery_signals", "predicted_recovery_likelihood",
    "diagnosis_confidence", "reasoning_summary", "evidence",
}


@pytest.fixture(scope="module")
def engine():
    return DiagnosisEngine(MODEL_PATH)


@pytest.fixture(scope="module")
def test_df():
    return pd.read_csv(TEST_CSV)


# 1. Model trains successfully (artifact exists and loads without error)
def test_model_artifact_loads():
    import joblib
    pipe = joblib.load(MODEL_PATH)
    assert pipe is not None
    assert hasattr(pipe, "predict_proba")


# 2. Prediction returns a valid probability between 0 and 1
def test_prediction_is_valid_probability(engine, test_df):
    leakage_rows = test_df[test_df["leakage_category"] != "successful"]
    for _, row in leakage_rows.head(20).iterrows():
        d = engine.diagnose(row)
        p = d["predicted_recovery_likelihood"]
        assert p is not None
        assert 0.0 <= p <= 1.0


# 3. Diagnosis output follows the structured schema
def test_diagnosis_schema(engine, test_df):
    row = test_df[test_df["leakage_category"] == "failed_payment"].iloc[0]
    d = engine.diagnose(row)
    assert REQUIRED_SCHEMA_KEYS.issubset(set(d.keys()))
    assert isinstance(d["risk_factors"], list)
    assert isinstance(d["positive_recovery_signals"], list)
    assert isinstance(d["evidence"], list)
    # must be JSON-serializable (machine-readable requirement)
    json.dumps(d, default=str)


# 4. All four leakage categories can be diagnosed
@pytest.mark.parametrize("category", [
    "failed_payment", "checkout_abandonment", "failed_subscription", "overdue_receivable",
])
def test_all_leakage_categories_diagnosable(engine, test_df, category):
    row = test_df[test_df["leakage_category"] == category].iloc[0]
    d = engine.diagnose(row)
    assert d["leakage_category"] == category
    assert d["predicted_recovery_likelihood"] is not None
    assert d["root_cause"] != ""


# 5. Successful/non-leakage cases can be processed
def test_successful_case_processed_gracefully(engine, test_df):
    successful_rows = test_df[test_df["leakage_category"] == "successful"]
    if len(successful_rows) == 0:
        pytest.skip("no successful cases in test split")
    row = successful_rows.iloc[0]
    d = engine.diagnose(row)
    assert d["leakage_category"] == "successful"
    assert d["predicted_recovery_likelihood"] is None
    assert d["risk_factors"] == []


# 6. Forbidden post-action fields are never model inputs
def test_forbidden_fields_never_selected():
    assert set(PRE_DECISION_FEATURES).isdisjoint(set(ALL_FORBIDDEN_COLUMNS))
    with pytest.raises(ValueError):
        assert_no_forbidden_columns(["amount_recovered", "some_other_col"])
    with pytest.raises(ValueError):
        assert_no_forbidden_columns(["predicted_recovery_likelihood"])
    # sanity: allowlist itself must pass
    assert_no_forbidden_columns(PRE_DECISION_FEATURES)


def test_select_pre_decision_features_excludes_ground_truth(test_df):
    X = select_pre_decision_features(test_df)
    assert TARGET_COLUMN not in X.columns
    for col in POST_ACTION_FIELDS:
        assert col not in X.columns


# 7. Test set is not used for tuning — structural check: confirm the metrics
# report shows the threshold was selected on validation, and confirm test
# metrics exist as a separate, single reported evaluation.
def test_threshold_selected_on_validation_not_test():
    with open(THIS_DIR / "metrics_report.json") as f:
        report = json.load(f)
    assert "validation" in report["threshold_selection"]["method"].lower()
    assert "test_metrics_at_threshold" in report
    assert "validation_metrics_at_threshold" in report
    # threshold used for test must match the one chosen from validation
    assert report["validation_metrics_at_threshold"]["threshold"] == \
        report["test_metrics_at_threshold"]["threshold"]


# 8. Explanations only reference pre-decision information
def test_evidence_only_references_pre_decision_fields(engine, test_df):
    row = test_df[test_df["leakage_category"] == "overdue_receivable"].iloc[0]
    d = engine.diagnose(row)
    evidence_fields = {e["field"] for e in d["evidence"]}
    assert evidence_fields.isdisjoint(set(ALL_FORBIDDEN_COLUMNS))
    assert evidence_fields.issubset(set(PRE_DECISION_FEATURES) | {"checkout_started", "checkout_completed"})


# 9. Repeated inference is reproducible where expected
def test_repeated_inference_is_reproducible(engine, test_df):
    row = test_df[test_df["leakage_category"] == "failed_payment"].iloc[0]
    d1 = engine.diagnose(row)
    d2 = engine.diagnose(row)
    assert d1["predicted_recovery_likelihood"] == d2["predicted_recovery_likelihood"]
    assert d1["diagnosis_confidence"] == d2["diagnosis_confidence"]
    assert d1["root_cause"] == d2["root_cause"]
    assert d1["risk_factors"] == d2["risk_factors"]


# 10. Missing/invalid input is handled safely
def test_invalid_leakage_category_raises(engine):
    bad_case = {"leakage_category": "not_a_real_category", "amount_at_risk": 100}
    with pytest.raises(ValueError):
        engine.diagnose(bad_case)


def test_missing_amount_at_risk_raises(engine):
    bad_case = {"leakage_category": "failed_payment"}
    with pytest.raises(ValueError):
        engine.diagnose(bad_case)


def test_zero_amount_at_risk_on_leakage_case_raises(engine):
    bad_case = {"leakage_category": "failed_payment", "amount_at_risk": 0}
    with pytest.raises(ValueError):
        engine.diagnose(bad_case)


def test_missing_optional_fields_handled_via_imputation(engine, test_df):
    row = test_df[test_df["leakage_category"] == "failed_payment"].iloc[0].to_dict()
    # simulate a missing optional field (imputers should absorb this)
    row["customer_success_rate"] = np.nan
    d = engine.diagnose(row)
    assert 0.0 <= d["predicted_recovery_likelihood"] <= 1.0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
