"""
RecoverAI — Synthetic Dataset Validation (Step 2)

Runs integrity, consistency, and leakage checks against recoverai_cases.csv.
Distinguishes EXPECTED category-specific nulls from UNEXPECTED missing data.

Run:
    python3 validate_dataset.py
"""

import os
import sys
import pandas as pd
import numpy as np

# Resolved relative to this file's own location (not the caller's cwd), so
# `python data/validate_dataset.py` from the project root and
# `python3 validate_dataset.py` from inside data/ both work identically.
# Purely a path-resolution fix — the CSV itself is never touched here.
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recoverai_cases.csv")

# Fields legitimately null depending on category — anything else null is an error.
EXPECTED_NULL_BY_CATEGORY = {
    "failure_reason": {"successful", "checkout_abandonment", "overdue_receivable"},
    "checkout_started": {"failed_subscription", "overdue_receivable"},
    "checkout_completed": {"failed_subscription", "overdue_receivable"},
    "subscription_status": {"successful", "failed_payment", "checkout_abandonment", "overdue_receivable"},
    "mandate_status": {"successful", "failed_payment", "checkout_abandonment", "overdue_receivable"},
    "invoice_status": {"successful", "failed_payment", "checkout_abandonment", "failed_subscription"},
    "invoice_due_date": {"successful", "failed_payment", "checkout_abandonment", "failed_subscription"},
    "days_overdue": {"successful", "failed_payment", "checkout_abandonment", "failed_subscription"},
}

PRE_DECISION_COLUMNS = [
    "case_id", "transaction_id", "customer_id", "event_type", "leakage_category",
    "amount", "amount_at_risk", "timestamp", "payment_method", "payment_status",
    "failure_reason", "checkout_started", "checkout_completed", "subscription_status",
    "mandate_status", "invoice_status", "invoice_due_date", "days_overdue",
    "retry_count", "previous_attempt_count", "customer_purchase_count",
    "customer_success_rate", "customer_lifetime_value", "previous_payment_behavior",
    "customer_opt_out", "suspicious_flag", "communication_allowed",
    "historical_recovery_behavior",
]
GROUND_TRUTH_COLUMNS = [
    "ground_truth_recoverable", "ground_truth_recovery_outcome",
    "amount_recovered", "recovery_observed", "recovery_reason",
]
FORBIDDEN_COLUMNS = ["predicted_recovery_likelihood"]


def check(results, name, passed, detail=""):
    results.append({"check": name, "passed": bool(passed), "detail": detail})


def run_validation(df):
    results = []

    # 1. Duplicate IDs
    check(results, "no_duplicate_case_id", df["case_id"].is_unique,
          f"{df['case_id'].duplicated().sum()} duplicate case_id rows")
    check(results, "no_duplicate_transaction_id", df["transaction_id"].is_unique,
          f"{df['transaction_id'].duplicated().sum()} duplicate transaction_id rows")

    # 2. Missing values — distinguish expected category-specific nulls from unexpected
    unexpected_missing = {}
    for col in df.columns:
        if col not in EXPECTED_NULL_BY_CATEGORY:
            n_missing = df[col].isna().sum()
            if n_missing > 0:
                unexpected_missing[col] = int(n_missing)
        else:
            expected_categories = EXPECTED_NULL_BY_CATEGORY[col]
            is_null = df[col].isna()
            bad = df[is_null & ~df["leakage_category"].isin(expected_categories)]
            if len(bad) > 0:
                unexpected_missing[col] = int(len(bad))
    check(results, "no_unexpected_missing_values", len(unexpected_missing) == 0,
          f"unexpected nulls by column: {unexpected_missing}" if unexpected_missing else "none")

    # 3. Invalid amounts
    bad_amount = df[(df["amount"] <= 0) | (df["amount"].isna())]
    check(results, "all_amounts_positive", len(bad_amount) == 0,
          f"{len(bad_amount)} rows with amount <= 0 or missing")
    bad_risk = df[(df["leakage_category"] != "successful") & (df["amount_at_risk"] <= 0)]
    check(results, "amount_at_risk_positive_for_leakage_cases", len(bad_risk) == 0,
          f"{len(bad_risk)} leakage rows with amount_at_risk <= 0")
    zero_risk_success = df[(df["leakage_category"] == "successful") & (df["amount_at_risk"] != 0)]
    check(results, "amount_at_risk_zero_for_successful_cases", len(zero_risk_success) == 0,
          f"{len(zero_risk_success)} successful rows with nonzero amount_at_risk")

    # 4. Impossible dates
    receivables = df[df["leakage_category"] == "overdue_receivable"]
    bad_dates = receivables[receivables["invoice_due_date"] > receivables["timestamp"]]
    check(results, "invoice_due_date_not_after_event_date", len(bad_dates) == 0,
          f"{len(bad_dates)} receivable rows with due_date after event timestamp")
    bad_overdue = receivables[(receivables["days_overdue"] <= 0)]
    check(results, "days_overdue_positive_for_receivables", len(bad_overdue) == 0,
          f"{len(bad_overdue)} receivable rows with non-positive days_overdue")

    # 5. Inconsistent statuses / categories
    expected_event = {
        "successful": "payment_success", "failed_payment": "payment_failed",
        "checkout_abandonment": "checkout_abandoned", "failed_subscription": "subscription_renewal_failed",
        "overdue_receivable": "invoice_overdue",
    }
    bad_event = df[df.apply(lambda r: expected_event[r["leakage_category"]] != r["event_type"], axis=1)]
    check(results, "event_type_matches_leakage_category", len(bad_event) == 0,
          f"{len(bad_event)} rows with mismatched event_type/leakage_category")

    expected_status = {
        "successful": "success", "failed_payment": "failed", "checkout_abandonment": "abandoned",
        "failed_subscription": "failed_recurring", "overdue_receivable": "overdue",
    }
    bad_status = df[df.apply(lambda r: expected_status[r["leakage_category"]] != r["payment_status"], axis=1)]
    check(results, "payment_status_matches_leakage_category", len(bad_status) == 0,
          f"{len(bad_status)} rows with mismatched payment_status/leakage_category")

    # 6. Checkout abandonment logic
    abandon = df[df["leakage_category"] == "checkout_abandonment"]
    bad_abandon = abandon[~((abandon["checkout_started"] == True) & (abandon["checkout_completed"] == False))]
    check(results, "checkout_abandonment_started_true_completed_false", len(bad_abandon) == 0,
          f"{len(bad_abandon)} abandonment rows violating started=True/completed=False")

    # 7. Subscription logic
    sub = df[df["leakage_category"] == "failed_subscription"]
    bad_sub = sub[sub["subscription_status"].isna() | sub["mandate_status"].isna()]
    check(results, "subscription_context_present_for_failed_subscription", len(bad_sub) == 0,
          f"{len(bad_sub)} failed_subscription rows missing subscription/mandate context")

    # 8. Invalid retry counts
    bad_retry = df[(df["retry_count"] < 0) | (df["retry_count"] > 4)]
    check(results, "retry_count_in_valid_range", len(bad_retry) == 0,
          f"{len(bad_retry)} rows with retry_count outside [0,4]")

    # 9. Impossible customer histories
    bad_success_rate = df[(df["customer_success_rate"] < 0) | (df["customer_success_rate"] > 1)]
    check(results, "customer_success_rate_in_0_1", len(bad_success_rate) == 0,
          f"{len(bad_success_rate)} rows with success_rate outside [0,1]")
    bad_purchase_count = df[df["customer_purchase_count"] < 0]
    check(results, "customer_purchase_count_non_negative", len(bad_purchase_count) == 0,
          f"{len(bad_purchase_count)} rows with negative purchase_count")
    bad_ltv = df[df["customer_lifetime_value"] <= 0]
    check(results, "customer_lifetime_value_positive", len(bad_ltv) == 0,
          f"{len(bad_ltv)} rows with non-positive lifetime value")

    # 10. Impossible recovery outcomes
    bad_recovered_amount = df[df["amount_recovered"] > df["amount_at_risk"]]
    check(results, "amount_recovered_not_exceeding_amount_at_risk", len(bad_recovered_amount) == 0,
          f"{len(bad_recovered_amount)} rows with amount_recovered > amount_at_risk")
    inconsistent_outcome = df[
        ((df["ground_truth_recovery_outcome"] == "recovered") & (df["amount_recovered"] <= 0)) |
        ((df["ground_truth_recovery_outcome"] == "not_recovered") & (df["amount_recovered"] != 0)) |
        ((df["ground_truth_recovery_outcome"] == "not_applicable") & (df["amount_recovered"] != 0))
    ]
    check(results, "recovery_outcome_consistent_with_amount_recovered", len(inconsistent_outcome) == 0,
          f"{len(inconsistent_outcome)} rows with outcome/amount_recovered mismatch")

    # 11. Category representation (need enough of each for later evaluation)
    counts = df["leakage_category"].value_counts()
    under_represented = counts[counts < 50]
    check(results, "all_categories_sufficiently_represented", len(under_represented) == 0,
          f"category counts: {counts.to_dict()}")

    # 12. Customer split leakage — a customer must not appear in more than one split
    split_map = df.groupby("customer_id")["split"].nunique()
    leaking_customers = split_map[split_map > 1]
    check(results, "no_customer_spans_multiple_splits", len(leaking_customers) == 0,
          f"{len(leaking_customers)} customers appear in >1 split")

    # 13. Future-outcome leakage: ground-truth columns must not silently duplicate
    # a pre-decision column, and vice versa.
    overlap = set(PRE_DECISION_COLUMNS) & set(GROUND_TRUTH_COLUMNS)
    check(results, "no_column_overlap_between_pre_decision_and_ground_truth", len(overlap) == 0,
          f"overlapping columns: {overlap}" if overlap else "none")

    # 14. Forbidden columns (predicted_recovery_likelihood belongs to Step 3, not Step 2)
    present_forbidden = [c for c in FORBIDDEN_COLUMNS if c in df.columns]
    check(results, "no_forbidden_step3_columns_present", len(present_forbidden) == 0,
          f"forbidden columns found: {present_forbidden}" if present_forbidden else "none")

    return results


def main():
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp", "invoice_due_date"])
    results = run_validation(df)

    n_failed = sum(1 for r in results if not r["passed"])
    print(f"Validation checks: {len(results)} total, {len(results) - n_failed} passed, {n_failed} failed\n")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {r['check']}: {r['detail']}")

    if n_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
