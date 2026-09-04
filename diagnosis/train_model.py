"""
RecoverAI — Diagnosis Layer: Model Training (Step 3)

Loads the Step 2 dataset (train/validation/test, leakage cases only — see
rationale in README.md), builds features strictly from the PRE_DECISION_FEATURES
allowlist in feature_config.py, trains a recoverability classifier, selects an
operating threshold on the validation set, and evaluates ONCE on the held-out
test set.

Run:
    python3 train_model.py
"""

import json
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder, StandardScaler, FunctionTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    roc_auc_score, average_precision_score, precision_recall_curve,
    f1_score, precision_score, recall_score, confusion_matrix,
)

from feature_config import (
    PRE_DECISION_FEATURES, TARGET_COLUMN, select_pre_decision_features,
    assert_no_forbidden_columns,
)

DATA_DIR = "../data"
SEED = 42

NUMERIC_LOG_FEATURES = ["amount_at_risk", "customer_lifetime_value"]
NUMERIC_PLAIN_FEATURES = ["days_overdue", "retry_count", "previous_attempt_count",
                           "customer_purchase_count", "customer_success_rate"]
BOOLEAN_FEATURES = ["checkout_started", "checkout_completed", "customer_opt_out",
                     "suspicious_flag", "communication_allowed"]
CATEGORICAL_FEATURES = ["leakage_category", "payment_method", "failure_reason",
                         "subscription_status", "mandate_status", "invoice_status",
                         "previous_payment_behavior", "historical_recovery_behavior"]

assert set(NUMERIC_LOG_FEATURES + NUMERIC_PLAIN_FEATURES + BOOLEAN_FEATURES + CATEGORICAL_FEATURES) \
    == set(PRE_DECISION_FEATURES), "Feature grouping must exactly cover the allowlist"


def load_leakage_split(name):
    """Loads a split and restricts to leakage cases (successful cases have no
    recoverability target — see README.md 'Why leakage-only' for rationale)."""
    df = pd.read_csv(f"{DATA_DIR}/{name}.csv" if name != "full" else f"{DATA_DIR}/recoverai_cases.csv")
    df = df[df["leakage_category"] != "successful"].reset_index(drop=True)
    return df


def build_preprocessor():
    numeric_log_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("log", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
        ("scale", StandardScaler()),
    ])
    numeric_plain_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    boolean_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
    ])
    categorical_pipe = Pipeline([
        ("impute", SimpleImputer(strategy="constant", fill_value="missing")),
        ("onehot", OneHotEncoder(handle_unknown="ignore")),
    ])

    return ColumnTransformer([
        ("num_log", numeric_log_pipe, NUMERIC_LOG_FEATURES),
        ("num_plain", numeric_plain_pipe, NUMERIC_PLAIN_FEATURES),
        ("bool", boolean_pipe, BOOLEAN_FEATURES),
        ("cat", categorical_pipe, CATEGORICAL_FEATURES),
    ])


def prepare_xy(df):
    # select_pre_decision_features internally asserts the allowlist itself is
    # clean; the slice below is the actual leakage guard: forbidden columns
    # (including TARGET_COLUMN) are never selected into X regardless of what
    # exists in df.
    X = select_pre_decision_features(df)
    assert TARGET_COLUMN not in X.columns, "Target column leaked into feature set"
    y = df[TARGET_COLUMN].astype(int)
    return X, y


def select_threshold(y_val, p_val):
    """Threshold selected on validation only, maximizing F1. Test set stays untouched."""
    precisions, recalls, thresholds = precision_recall_curve(y_val, p_val)
    f1s = np.where((precisions + recalls) > 0,
                    2 * precisions * recalls / (precisions + recalls + 1e-12), 0)
    # precision_recall_curve returns thresholds of len n-1; align
    f1s = f1s[:-1]
    best_idx = int(np.argmax(f1s)) if len(f1s) > 0 else 0
    best_threshold = float(thresholds[best_idx]) if len(thresholds) > 0 else 0.5
    return best_threshold, float(f1s[best_idx]) if len(f1s) > 0 else 0.0


def evaluate(y_true, p, threshold):
    y_pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    metrics = {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, p)) if len(set(y_true)) > 1 else None,
        "pr_auc": float(average_precision_score(y_true, p)) if len(set(y_true)) > 1 else None,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "threshold": threshold,
        "n": int(len(y_true)),
        "positive_rate": float(np.mean(y_true)),
    }
    return metrics, y_pred


def false_positive_cost_analysis(df_test, y_true, y_pred):
    """False positives here = model predicted recoverable but ground truth says
    not recovered. Cost = the amount_at_risk chased for nothing."""
    fp_mask = (y_pred == 1) & (y_true.values == 0)
    fp_rows = df_test[fp_mask.values] if hasattr(fp_mask, "values") else df_test[fp_mask]
    n_fp = int(fp_mask.sum())
    total_fp_amount = float(fp_rows["amount_at_risk"].sum()) if n_fp > 0 else 0.0
    avg_fp_amount = float(fp_rows["amount_at_risk"].mean()) if n_fp > 0 else 0.0
    return {
        "n_false_positives": n_fp,
        "total_amount_at_risk_in_false_positives": round(total_fp_amount, 2),
        "average_false_positive_amount": round(avg_fp_amount, 2),
        "assumption": (
            "Cost estimate assumes each false positive triggers one low-cost "
            "autonomous action (reminder/payment link/retry) per Step 1 guardrail "
            "defaults, not the full amount_at_risk. A per-action cost isn't defined "
            "until Step 4/6, so this reports EXPOSURE (amount_at_risk chased) rather "
            "than a fabricated rupee cost figure."
        ),
    }


def per_category_metrics(df_split, y_true, p, threshold):
    out = {}
    for cat in sorted(df_split["leakage_category"].unique()):
        mask = (df_split["leakage_category"] == cat).values
        if mask.sum() == 0:
            continue
        y_cat = y_true[mask]
        p_cat = p[mask]
        if len(set(y_cat)) < 2:
            out[cat] = {"n": int(mask.sum()), "note": "single-class subset; ROC/PR-AUC undefined"}
            continue
        m, _ = evaluate(y_cat, p_cat, threshold)
        out[cat] = m
    return out


def main():
    train_df = load_leakage_split("train")
    val_df = load_leakage_split("validation")
    test_df = load_leakage_split("test")

    X_train, y_train = prepare_xy(train_df)
    X_val, y_val = prepare_xy(val_df)
    X_test, y_test = prepare_xy(test_df)

    preprocessor = build_preprocessor()

    candidates = {
        "logistic_regression": LogisticRegression(max_iter=1000, random_state=SEED),
        "random_forest": RandomForestClassifier(n_estimators=200, max_depth=6, random_state=SEED),
    }

    val_comparison = {}
    fitted = {}
    for name, clf in candidates.items():
        pipe = Pipeline([("prep", preprocessor), ("clf", clf)])
        pipe.fit(X_train, y_train)
        p_val = pipe.predict_proba(X_val)[:, 1]
        val_comparison[name] = {
            "roc_auc": float(roc_auc_score(y_val, p_val)),
            "pr_auc": float(average_precision_score(y_val, p_val)),
        }
        fitted[name] = pipe

    # Model selection: prefer logistic regression for explainability (coefficients
    # map directly to features) unless it is meaningfully worse on validation ROC-AUC.
    lr_auc = val_comparison["logistic_regression"]["roc_auc"]
    rf_auc = val_comparison["random_forest"]["roc_auc"]
    selected_name = "logistic_regression" if (rf_auc - lr_auc) < 0.03 else "random_forest"
    selected_pipe = fitted[selected_name]

    p_val_selected = selected_pipe.predict_proba(X_val)[:, 1]
    threshold, val_f1_at_threshold = select_threshold(y_val, p_val_selected)
    val_metrics, _ = evaluate(y_val, p_val_selected, threshold)
    # For the precision/recall trade-off discussion: also report the naive 0.5
    # threshold on validation, alongside the chosen F1-optimal threshold.
    val_metrics_at_naive_threshold, _ = evaluate(y_val, p_val_selected, 0.5)

    # Test set touched exactly once, here, after threshold is locked.
    p_test = selected_pipe.predict_proba(X_test)[:, 1]
    test_metrics, y_test_pred = evaluate(y_test, p_test, threshold)
    fp_cost = false_positive_cost_analysis(test_df, y_test, y_test_pred)
    per_category = per_category_metrics(test_df, y_test.values, p_test, threshold)

    # Save model + preprocessing pipeline together
    joblib.dump(selected_pipe, "model.joblib")

    report = {
        "model_selected": selected_name,
        "validation_model_comparison": val_comparison,
        "selection_rule": (
            "Prefer logistic_regression for explainability unless random_forest "
            "beats it by >0.03 validation ROC-AUC."
        ),
        "threshold_selection": {
            "method": "Maximize F1 on validation set (precision_recall_curve sweep)",
            "chosen_threshold": threshold,
            "validation_f1_at_threshold": val_f1_at_threshold,
        },
        "validation_metrics_at_threshold": val_metrics,
        "validation_metrics_at_naive_0.5_threshold": val_metrics_at_naive_threshold,
        "test_metrics_at_threshold": test_metrics,
        "false_positive_cost_analysis": fp_cost,
        "per_category_test_metrics": per_category,
        "train_size": len(train_df),
        "validation_size": len(val_df),
        "test_size": len(test_df),
        "target_column": TARGET_COLUMN,
        "seed": SEED,
    }

    with open("metrics_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
