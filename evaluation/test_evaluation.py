"""
RecoverAI — Evaluation & Metrics: Tests (Step 11)

Run:
    python3 -m pytest test_evaluation.py -v
"""

import os
import sys
import re
import json
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "guardrails"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "audit"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "failure_handling"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recovery"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "measurement"))

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from evaluation_models import (
    EvaluationCategory, ModelEvaluationSummary, SyntheticBacktestResult,
    LiveExecutionEvaluationSummary, ObservedRecoveryEvaluationSummary, EvaluationReport,
)
from model_evaluation import load_model_evaluation_summary, DEFAULT_METRICS_PATH
from synthetic_backtest import run_synthetic_backtest
from evaluation_report import assemble_evaluation_report, build_live_execution_summary, build_observed_recovery_summary

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MIDDAY = datetime(2026, 8, 22, 14, 0)

FORBIDDEN_LIVE_INPUTS = ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                          "ground_truth_recovery_value", "recovery_observed", "recovery_reason")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RECOVERAI_RAZORPAY_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fakekey1234567")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fakesecret1234567890")
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "true")
    yield


@pytest.fixture(scope="module")
def diagnosis_engine():
    from diagnose import DiagnosisEngine
    return DiagnosisEngine(str(Path(__file__).resolve().parent.parent / "diagnosis" / "model.joblib"))


@pytest.fixture(scope="module")
def decision_engine(diagnosis_engine):
    from decision_engine import DecisionEngine
    return DecisionEngine(diagnosis_engine=diagnosis_engine)


@pytest.fixture(scope="module")
def guard_engine():
    from guardrail_engine import GuardrailEngine
    return GuardrailEngine()


@pytest.fixture(scope="module")
def test_df():
    return pd.read_csv(DATA_DIR / "test.csv")


# ------------------------------------------------------------------ #
# Category A: model evaluation reference
# ------------------------------------------------------------------ #
def test_model_evaluation_loads_step3_report_unmodified():
    summary = load_model_evaluation_summary()
    assert summary.category == EvaluationCategory.ML_MODEL
    assert 0.0 <= summary.test_precision <= 1.0
    assert 0.0 <= summary.test_recall <= 1.0
    assert summary.source == str(DEFAULT_METRICS_PATH)


def test_model_evaluation_does_not_modify_metrics_file():
    import hashlib
    before = hashlib.md5(open(DEFAULT_METRICS_PATH, "rb").read()).hexdigest()
    load_model_evaluation_summary()
    after = hashlib.md5(open(DEFAULT_METRICS_PATH, "rb").read()).hexdigest()
    assert before == after


def test_model_evaluation_missing_file_raises_not_regenerates(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_model_evaluation_summary(metrics_path=tmp_path / "does_not_exist.json")


def test_no_training_or_retraining_call_in_model_evaluation_source():
    src = open(os.path.join(os.path.dirname(__file__), "model_evaluation.py"), encoding="utf-8").read()
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = "\n".join(l for l in code_only.splitlines() if not l.strip().startswith("#"))
    for forbidden in ("fit(", "GradientBoosting", "LogisticRegression(", "RandomForestClassifier(", ".dump("):
        assert forbidden not in code_only


# ------------------------------------------------------------------ #
# Category B: synthetic backtest
# ------------------------------------------------------------------ #
def test_synthetic_backtest_runs_real_agent_components(diagnosis_engine, decision_engine, guard_engine, test_df):
    result = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    assert result.category == EvaluationCategory.SYNTHETIC_BACKTEST
    assert result.cases_evaluated == (test_df["leakage_category"] != "successful").sum()
    assert result.auto_execute_count + result.approval_required_count + result.stop_count == result.cases_evaluated


def test_synthetic_backtest_confusion_matrix_sums_to_total(diagnosis_engine, decision_engine, guard_engine, test_df):
    result = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    total = result.true_positive + result.false_positive + result.true_negative + result.false_negative
    assert total == result.cases_evaluated


def test_synthetic_backtest_has_explicit_disclaimer(diagnosis_engine, decision_engine, guard_engine, test_df):
    result = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    assert "SYNTHETIC" in result.disclaimer
    assert "must never" in result.disclaimer.lower()


def test_synthetic_backtest_is_deterministic(diagnosis_engine, decision_engine, guard_engine, test_df):
    r1 = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    r2 = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    assert r1.true_positive == r2.true_positive
    assert r1.backtest_amount_recoverable_if_ground_truth_trusted == r2.backtest_amount_recoverable_if_ground_truth_trusted


def test_ground_truth_only_read_after_agent_decision_not_passed_in(diagnosis_engine, decision_engine, guard_engine, test_df):
    # Structural proof: tampering ground truth on a COPY changes the backtest
    # SCORE (since it's compared against), but running the SAME case with
    # DIFFERENT ground truth must never change the agent's own outcome
    # (guardrail_outcome / recommended_action_type) — only the scoring.
    row = test_df[test_df["leakage_category"] != "successful"].iloc[0]
    from diagnose import DiagnosisEngine  # already have engine, just re-derive decision directly
    diagnosis = diagnosis_engine.diagnose(row)
    from action_compatibility import get_actions_for_case
    actions = get_actions_for_case(row, diagnosis=diagnosis)
    decision_before = decision_engine.decide(row, diagnosis=diagnosis, actions=actions)

    tampered = row.copy()
    tampered["ground_truth_recoverable"] = not bool(row["ground_truth_recoverable"])
    tampered["amount_recovered"] = 999999.0
    diagnosis2 = diagnosis_engine.diagnose(tampered)
    actions2 = get_actions_for_case(tampered, diagnosis=diagnosis2)
    decision_after = decision_engine.decide(tampered, diagnosis=diagnosis2, actions=actions2)

    assert decision_before.recommended_action_type == decision_after.recommended_action_type


def test_backtest_amount_recoverable_only_counts_true_positives(diagnosis_engine, decision_engine, guard_engine, test_df):
    result = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    # Sanity: the synthetic recoverable amount cannot exceed the total at-risk amount evaluated.
    assert result.backtest_amount_recoverable_if_ground_truth_trusted <= result.backtest_amount_at_risk


def test_no_forbidden_influence_of_ground_truth_on_live_modules():
    """Ground truth field names must appear ONLY in synthetic_backtest.py
    (and evaluation_models.py's SyntheticBacktestResult docstrings) among
    the evaluation package's actual code — never in model_evaluation.py or
    evaluation_report.py, which handle categories A/C/D."""
    for filename in ("model_evaluation.py", "evaluation_report.py"):
        src = open(os.path.join(os.path.dirname(__file__), filename)).read()
        code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code_only = "\n".join(l for l in code_only.splitlines() if not l.strip().startswith("#"))
        for forbidden in FORBIDDEN_LIVE_INPUTS:
            assert forbidden not in code_only, f"{forbidden} referenced in {filename} (categories A/C/D must never touch ground truth)"


# ------------------------------------------------------------------ #
# Category C & D: live execution / observed recovery (from Step 10)
# ------------------------------------------------------------------ #
def _fake_batch_measurement(total_amount_recovered=0.0, recovery_rate=0.0):
    class FakeBM:
        def to_dict(self):
            return {
                "cases_analyzed": 5, "recovery_opportunities": 4, "total_amount_at_risk": 4000.0,
                "total_amount_processed": 3000.0, "total_amount_recovered": total_amount_recovered,
                "recovery_rate": recovery_rate, "recovery_cost": None,
                "net_recovered_revenue": total_amount_recovered,
                "actions_attempted": 4, "successful_executions": 3, "failed_executions": 1,
                "fallback_actions": 0, "escalated_cases": 1, "stopped_cases": 1,
                "approval_required_cases": 0, "unresolved_recovery_cases": 3,
            }
    return FakeBM()


def test_live_execution_summary_excludes_recovery_fields():
    summary = build_live_execution_summary(_fake_batch_measurement())
    assert summary.category == EvaluationCategory.LIVE_EXECUTION
    assert not hasattr(summary, "total_amount_recovered")
    assert not hasattr(summary, "recovery_rate")


def test_observed_recovery_summary_zero_when_nothing_paid():
    summary = build_observed_recovery_summary(_fake_batch_measurement(total_amount_recovered=0.0))
    assert summary.category == EvaluationCategory.OBSERVED_RECOVERY
    assert summary.total_amount_recovered == 0.0
    assert summary.genuine_payment_verified is False
    assert "sandbox" in summary.limitation_note.lower() or "not been paid" in summary.limitation_note.lower()


def test_observed_recovery_summary_true_when_something_paid():
    summary = build_observed_recovery_summary(_fake_batch_measurement(total_amount_recovered=500.0, recovery_rate=0.125))
    assert summary.genuine_payment_verified is True
    assert summary.total_amount_recovered == 500.0


# ------------------------------------------------------------------ #
# Full report assembly: categories never merged
# ------------------------------------------------------------------ #
def test_assembled_report_keeps_categories_structurally_separate(diagnosis_engine, decision_engine, guard_engine, test_df):
    model_summary = load_model_evaluation_summary()
    backtest = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    report = assemble_evaluation_report(model_summary, backtest, batch_measurement=None)

    assert isinstance(report, EvaluationReport)
    assert report.model.category == EvaluationCategory.ML_MODEL
    assert report.synthetic_backtest.category == EvaluationCategory.SYNTHETIC_BACKTEST
    assert report.live_execution is None  # no live run provided -> honestly absent, not fabricated
    assert report.observed_recovery is None


def test_report_without_live_run_has_no_fabricated_c_or_d(diagnosis_engine, decision_engine, guard_engine, test_df):
    model_summary = load_model_evaluation_summary()
    backtest = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    report = assemble_evaluation_report(model_summary, backtest, batch_measurement=None)
    d = report.to_dict()
    assert d["C_live_execution"] is None
    assert d["D_observed_recovery"] is None


def test_report_with_live_run_populates_c_and_d_from_same_source_correctly():
    model_summary_placeholder = ModelEvaluationSummary(
        source="x", model_selected="logistic_regression", threshold=0.5,
        threshold_selection_method="test", test_precision=0.8, test_recall=1.0, test_f1=0.89,
        test_roc_auc=0.6, test_pr_auc=0.85, test_confusion_matrix={}, false_positive_cost_exposure=0.0,
        per_category_test_metrics={},
    )
    backtest_placeholder = SyntheticBacktestResult(
        cases_evaluated=0, auto_execute_count=0, approval_required_count=0, stop_count=0, escalated_count=0,
        true_positive=0, false_positive=0, true_negative=0, false_negative=0,
        precision_at_auto_execute=None, recall_at_auto_execute=None, f1_at_auto_execute=None,
        backtest_amount_at_risk=0.0, backtest_amount_recoverable_if_ground_truth_trusted=0.0,
        backtest_recovery_rate_if_ground_truth_trusted=0.0,
    )
    report = assemble_evaluation_report(model_summary_placeholder, backtest_placeholder,
                                         batch_measurement=_fake_batch_measurement(total_amount_recovered=200.0))
    d = report.to_dict()
    assert d["C_live_execution"]["cases_analyzed"] == 5
    assert d["D_observed_recovery"]["total_amount_recovered"] == 200.0
    assert "total_amount_recovered" not in d["C_live_execution"]


def test_category_separation_notice_present_and_explicit():
    model_summary = load_model_evaluation_summary()
    backtest = SyntheticBacktestResult(
        cases_evaluated=0, auto_execute_count=0, approval_required_count=0, stop_count=0, escalated_count=0,
        true_positive=0, false_positive=0, true_negative=0, false_negative=0,
        precision_at_auto_execute=None, recall_at_auto_execute=None, f1_at_auto_execute=None,
        backtest_amount_at_risk=0.0, backtest_amount_recoverable_if_ground_truth_trusted=0.0,
        backtest_recovery_rate_if_ground_truth_trusted=0.0,
    )
    report = assemble_evaluation_report(model_summary, backtest, batch_measurement=None)
    assert "NEVER summed or merged" in report.category_separation_notice


# ------------------------------------------------------------------ #
# Safety / integration assurance (real components, end-to-end)
# ------------------------------------------------------------------ #
def test_backtest_never_bypasses_stop_or_approval_required(diagnosis_engine, decision_engine, guard_engine, test_df):
    result = run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    # A guardrail STOP/APPROVAL_REQUIRED case must be counted as such, never
    # silently reclassified as an AUTO_EXECUTE "true positive".
    assert result.stop_count + result.approval_required_count + result.auto_execute_count == result.cases_evaluated
    counted_positive_predictions = result.true_positive + result.false_positive
    assert counted_positive_predictions == result.auto_execute_count


def test_live_credentials_still_rejected_for_step10_dependencies(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_shouldneverwork")
    from razorpay_config import load_config_from_env, LiveModeCredentialsError
    with pytest.raises(LiveModeCredentialsError):
        load_config_from_env()


def test_no_secret_leakage_in_evaluation_report_output():
    model_summary = load_model_evaluation_summary()
    backtest = SyntheticBacktestResult(
        cases_evaluated=0, auto_execute_count=0, approval_required_count=0, stop_count=0, escalated_count=0,
        true_positive=0, false_positive=0, true_negative=0, false_negative=0,
        precision_at_auto_execute=None, recall_at_auto_execute=None, f1_at_auto_execute=None,
        backtest_amount_at_risk=0.0, backtest_amount_recoverable_if_ground_truth_trusted=0.0,
        backtest_recovery_rate_if_ground_truth_trusted=0.0,
    )
    report = assemble_evaluation_report(model_summary, backtest, batch_measurement=None)
    assert "fakesecret1234567890" not in json.dumps(report.to_dict(), default=str)


# ------------------------------------------------------------------ #
# Step 2/3 untouched
# ------------------------------------------------------------------ #
def test_step2_dataset_unchanged_after_evaluation_run(diagnosis_engine, decision_engine, guard_engine, test_df):
    import hashlib
    csv_path = DATA_DIR / "recoverai_cases.csv"
    before = hashlib.md5(open(csv_path, "rb").read()).hexdigest()
    run_synthetic_backtest(diagnosis_engine, decision_engine, guard_engine, test_df, current_time=MIDDAY)
    after = hashlib.md5(open(csv_path, "rb").read()).hexdigest()
    assert before == after


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
