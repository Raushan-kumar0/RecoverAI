"""
RecoverAI — Evaluation & Metrics: Model Evaluation Reference (Step 11, Category A)

Reads Step 3's already-computed, already-locked `diagnosis/metrics_report.json`
and wraps it as a ModelEvaluationSummary. This module NEVER retrains the
model, NEVER recomputes metrics, and NEVER touches diagnosis/model.joblib.
It is a read-only reference so Step 11 can report model performance as part
of a full-pipeline evaluation without duplicating Step 3's work.
"""

import json
from pathlib import Path

from evaluation_models import ModelEvaluationSummary

DEFAULT_METRICS_PATH = Path(__file__).resolve().parent.parent / "diagnosis" / "metrics_report.json"


def load_model_evaluation_summary(metrics_path=None) -> ModelEvaluationSummary:
    path = Path(metrics_path) if metrics_path else DEFAULT_METRICS_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Step 3 metrics report not found at {path}. Step 11 reads this file — it does not "
            f"regenerate it. Re-run Step 3's train_model.py if this file is genuinely missing "
            f"(this should never be necessary as part of Step 11)."
        )

    with open(path) as f:
        report = json.load(f)

    test_metrics = report["test_metrics_at_threshold"]
    fp_cost = report.get("false_positive_cost_analysis", {})

    return ModelEvaluationSummary(
        source=str(path),
        model_selected=report.get("model_selected", "unknown"),
        threshold=test_metrics.get("threshold"),
        threshold_selection_method=report.get("threshold_selection", {}).get("method", "unknown"),
        test_precision=test_metrics["precision"],
        test_recall=test_metrics["recall"],
        test_f1=test_metrics["f1"],
        test_roc_auc=test_metrics.get("roc_auc"),
        test_pr_auc=test_metrics.get("pr_auc"),
        test_confusion_matrix=test_metrics.get("confusion_matrix", {}),
        false_positive_cost_exposure=fp_cost.get("total_amount_at_risk_in_false_positives", 0.0),
        per_category_test_metrics=report.get("per_category_test_metrics", {}),
    )
