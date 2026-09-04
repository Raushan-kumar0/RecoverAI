"""
RecoverAI — Synthetic Merchant Dataset Generator (Step 2)

Generates ~1,000 realistic synthetic merchant revenue-lifecycle cases covering
four revenue-leakage categories plus a successful/non-leakage baseline.

CRITICAL DESIGN RULE (locked in Step 1, restated here):
    amount_at_risk           -> FACT, known before any decision.
    predicted_recovery_likelihood -> DOES NOT EXIST in this dataset. It is an
                                       AI output generated later in Step 3.
    amount_recovered / ground_truth_* -> OBSERVED OUTCOME, known only after
                                       the fact. Never usable as a pre-decision
                                       input feature.

Run:
    python3 generate_dataset.py

Reproducibility:
    Fixed SEED below. Re-running with the same SEED produces a byte-identical
    CSV. Changing SEED produces a different (but still valid) dataset.
"""

import hashlib
import os
import numpy as np
import pandas as pd
from datetime import timedelta

# ----------------------------------------------------------------------------
# CONFIG (kept as simple module-level constants for Step 2 — not over-built
# into a config layer yet; guardrail-style config comes later in Step 6 when
# there's an actual engine to configure).
# ----------------------------------------------------------------------------
SEED = 42
N_CUSTOMERS = 450
N_CASES = 1000
REFERENCE_DATE = pd.Timestamp("2026-08-20")  # fixed "today" for reproducibility
# Resolved relative to this file's own location (not the caller's cwd), so
# running this from the project root doesn't scatter output CSVs into the
# wrong directory. Path-resolution fix only — does not change SEED, logic,
# or output content in any way.
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

CUSTOMER_TYPES = ["new", "occasional", "reliable", "high_value", "risky"]
CUSTOMER_TYPE_PROBS = [0.25, 0.30, 0.25, 0.10, 0.10]
# Relative likelihood a customer of this type generates a case (repeat-customer effect)
CASE_SAMPLE_WEIGHT = {"new": 1, "occasional": 2, "reliable": 4, "high_value": 3, "risky": 3}

CATEGORY_PROBS_BY_TYPE = {
    "new":         {"successful": 0.35, "failed_payment": 0.25, "checkout_abandonment": 0.30, "failed_subscription": 0.05, "overdue_receivable": 0.05},
    "occasional":  {"successful": 0.35, "failed_payment": 0.25, "checkout_abandonment": 0.20, "failed_subscription": 0.12, "overdue_receivable": 0.08},
    "reliable":    {"successful": 0.55, "failed_payment": 0.12, "checkout_abandonment": 0.10, "failed_subscription": 0.13, "overdue_receivable": 0.10},
    "high_value":  {"successful": 0.45, "failed_payment": 0.10, "checkout_abandonment": 0.08, "failed_subscription": 0.22, "overdue_receivable": 0.15},
    "risky":       {"successful": 0.15, "failed_payment": 0.30, "checkout_abandonment": 0.15, "failed_subscription": 0.10, "overdue_receivable": 0.30},
}

PAYMENT_METHODS = ["UPI", "Card", "Netbanking", "Wallet", "EMI"]
PAYMENT_METHOD_PROBS = [0.42, 0.30, 0.15, 0.10, 0.03]

FAILURE_REASONS_BY_METHOD = {
    "UPI":        {"UPI timeout": 0.40, "insufficient funds": 0.25, "bank decline": 0.20, "network failure": 0.15},
    "Card":       {"bank decline": 0.35, "insufficient funds": 0.25, "authentication failure": 0.25, "network failure": 0.15},
    "Netbanking": {"network failure": 0.50, "bank decline": 0.30, "insufficient funds": 0.20},
    "Wallet":     {"insufficient funds": 0.50, "network failure": 0.30, "bank decline": 0.20},
    "EMI":        {"bank decline": 0.40, "authentication failure": 0.35, "insufficient funds": 0.25},
}

EVENT_TYPE_BY_CATEGORY = {
    "successful": "payment_success",
    "failed_payment": "payment_failed",
    "checkout_abandonment": "checkout_abandoned",
    "failed_subscription": "subscription_renewal_failed",
    "overdue_receivable": "invoice_overdue",
}
PAYMENT_STATUS_BY_CATEGORY = {
    "successful": "success",
    "failed_payment": "failed",
    "checkout_abandonment": "abandoned",
    "failed_subscription": "failed_recurring",
    "overdue_receivable": "overdue",
}


def make_rng(seed):
    return np.random.default_rng(seed)


def generate_customers(rng, n=N_CUSTOMERS):
    """Customer profiles drive every downstream correlation in the dataset."""
    types = rng.choice(CUSTOMER_TYPES, size=n, p=CUSTOMER_TYPE_PROBS)
    rows = []
    for i, ctype in enumerate(types):
        if ctype == "new":
            purchase_count = int(rng.poisson(0.5))
            success_rate = float(np.clip(rng.beta(2, 2), 0.05, 0.95))
            ltv = float(rng.uniform(500, 3000))
            opt_out_p, suspicious_p = 0.02, 0.02
            behavior = "new_customer"
        elif ctype == "occasional":
            purchase_count = int(rng.poisson(4) + 1)
            success_rate = float(np.clip(rng.beta(6, 2), 0.05, 0.98))
            ltv = float(rng.uniform(3000, 15000))
            opt_out_p, suspicious_p = 0.03, 0.02
            behavior = "usually_needs_reminder"
        elif ctype == "reliable":
            purchase_count = int(rng.poisson(15) + 3)
            success_rate = float(np.clip(rng.beta(18, 2), 0.05, 0.99))
            ltv = float(rng.uniform(15000, 60000))
            opt_out_p, suspicious_p = 0.01, 0.01
            behavior = "usually_recovers_via_retry"
        elif ctype == "high_value":
            purchase_count = int(rng.poisson(20) + 5)
            success_rate = float(np.clip(rng.beta(15, 3), 0.05, 0.99))
            ltv = float(rng.uniform(60000, 300000))
            opt_out_p, suspicious_p = 0.01, 0.01
            behavior = "usually_recovers_via_retry"
        else:  # risky
            purchase_count = int(rng.poisson(6) + 1)
            success_rate = float(np.clip(rng.beta(3, 5), 0.05, 0.90))
            ltv = float(rng.uniform(1000, 10000))
            opt_out_p, suspicious_p = 0.15, 0.12
            behavior = "rarely_recovers"

        rows.append({
            "customer_id": f"CUST{i+1:05d}",
            "customer_type": ctype,
            "customer_purchase_count": purchase_count,
            "customer_success_rate": round(success_rate, 3),
            "customer_lifetime_value": round(ltv, 2),
            "customer_opt_out": bool(rng.random() < opt_out_p),
            "suspicious_flag": bool(rng.random() < suspicious_p),
            "historical_recovery_behavior": behavior,
            "previous_payment_behavior": {
                "new": "new", "occasional": "occasional_failure", "reliable": "reliable",
                "high_value": "reliable", "risky": "frequent_failure",
            }[ctype],
        })
    return pd.DataFrame(rows)


def assign_case_customers(rng, customers, n_cases=N_CASES):
    weights = customers["customer_type"].map(CASE_SAMPLE_WEIGHT).values.astype(float)
    weights = weights / weights.sum()
    idx = rng.choice(len(customers), size=n_cases, p=weights, replace=True)
    return customers.iloc[idx].reset_index(drop=True)


def sample_category(rng, ctype):
    probs = CATEGORY_PROBS_BY_TYPE[ctype]
    cats = list(probs.keys())
    p = list(probs.values())
    return rng.choice(cats, p=p)


def sample_payment_method(rng):
    return rng.choice(PAYMENT_METHODS, p=PAYMENT_METHOD_PROBS)


def sample_failure_reason(rng, method):
    table = FAILURE_REASONS_BY_METHOD[method]
    reasons = list(table.keys())
    p = list(table.values())
    return rng.choice(reasons, p=p)


def sample_amount(rng, category, ctype):
    type_mult = {"new": 0.9, "occasional": 1.0, "reliable": 1.3, "high_value": 3.0, "risky": 0.8}[ctype]
    if category == "failed_subscription":
        base = rng.lognormal(mean=6.0, sigma=0.5)
    elif category == "overdue_receivable":
        base = rng.lognormal(mean=9.0, sigma=0.8)
    else:  # successful, failed_payment, checkout_abandonment
        base = rng.lognormal(mean=7.3, sigma=0.9)
    return round(float(base * type_mult), 2)


def sample_retry_count(rng, category, previous_payment_behavior):
    if category not in ("failed_payment", "failed_subscription"):
        return 0
    lam = 1.5 if previous_payment_behavior in ("frequent_failure",) else \
          0.8 if previous_payment_behavior == "occasional_failure" else 0.4
    return int(np.clip(rng.poisson(lam), 0, 4))


def sample_previous_attempt_count(rng, category, retry_count):
    """Broader count of any prior recovery-touch attempts (reminders/links/follow-ups),
    distinct from system-level payment retries."""
    if category == "successful":
        return 0
    base = retry_count + int(rng.poisson(0.6))
    return int(np.clip(base, 0, 5))


def simulate_recovery(rng, row):
    """
    Latent, noisy recoverability mechanism. Produces ground-truth outcome fields
    that are NEVER fed back into pre-decision features. This is intentionally
    imperfect (logistic score + Gaussian noise) so later evaluation isn't trivial.
    """
    if row["leakage_category"] == "successful":
        return dict(
            ground_truth_recoverable=False,
            ground_truth_recovery_outcome="not_applicable",
            amount_recovered=0.0,
            recovery_observed=False,
            recovery_reason="not_applicable",
        )

    score = 0.0
    score += 2.0 * (row["customer_success_rate"] - 0.5)
    score -= 0.35 * row["retry_count"]
    score += 0.8 if row["communication_allowed"] else -2.5
    score -= 2.0 if row["suspicious_flag"] else 0.0
    score += 0.15 * np.log1p(row["customer_lifetime_value"] / 1000.0)

    if row["leakage_category"] == "overdue_receivable" and pd.notna(row.get("days_overdue")):
        score -= 0.02 * row["days_overdue"]  # older overdue -> harder to recover
    if row["leakage_category"] == "failed_subscription":
        score -= 0.3  # mandate-related failures are structurally harder
    if row["leakage_category"] == "checkout_abandonment":
        score += 0.4  # customer showed intent, easier to win back

    score += rng.normal(0, 0.8)  # noise so the relationship isn't perfectly separable

    prob = 1.0 / (1.0 + np.exp(-score))

    # Opt-out customers cannot be contacted -> recovery mostly limited to organic payment
    if row["customer_opt_out"]:
        prob = min(prob, 0.08)

    recovered = bool(rng.random() < prob)

    if not recovered:
        if row["customer_opt_out"]:
            reason = "opted_out_no_contact_possible"
        elif row["retry_count"] >= 3:
            reason = "exceeded_retry_limit_unrecovered"
        elif row["suspicious_flag"]:
            reason = "high_risk_flag_unrecovered"
        else:
            reason = "no_recovery_within_window"
        return dict(
            ground_truth_recoverable=False,
            ground_truth_recovery_outcome="not_recovered",
            amount_recovered=0.0,
            recovery_observed=True,
            recovery_reason=reason,
        )

    # Recovered: full recovery for single transactions, partial possible for receivables
    if row["leakage_category"] == "overdue_receivable":
        frac = float(np.clip(rng.beta(6, 1.5), 0.5, 1.0))  # skewed toward full, some partial
    else:
        frac = 1.0
    amount_recovered = round(row["amount_at_risk"] * frac, 2)
    reason = "retry_succeeded" if row["leakage_category"] in ("failed_payment", "failed_subscription") \
        else "paid_after_reminder_or_link"

    return dict(
        ground_truth_recoverable=True,
        ground_truth_recovery_outcome="recovered",
        amount_recovered=amount_recovered,
        recovery_observed=True,
        recovery_reason=reason,
    )


def assign_split(customer_id, seed=SEED):
    """Deterministic, customer-aware split. A customer never spans two splits."""
    h = hashlib.md5(f"{customer_id}-split-seed-{seed}".encode()).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    if frac < 0.70:
        return "train"
    elif frac < 0.85:
        return "validation"
    else:
        return "test"


def generate_dataset(seed=SEED, n_cases=N_CASES, n_customers=N_CUSTOMERS):
    rng = make_rng(seed)
    customers = generate_customers(rng, n_customers)
    case_customers = assign_case_customers(rng, customers, n_cases)

    records = []
    for i in range(n_cases):
        cust = case_customers.iloc[i]
        category = sample_category(rng, cust["customer_type"])
        method = sample_payment_method(rng)
        amount = sample_amount(rng, category, cust["customer_type"])

        row = {
            "case_id": f"CASE{i+1:05d}",
            "transaction_id": f"TXN{i+1:06d}",
            "customer_id": cust["customer_id"],
            "leakage_category": category,
            "event_type": EVENT_TYPE_BY_CATEGORY[category],
            "amount": amount,
            "payment_method": method,
            "payment_status": PAYMENT_STATUS_BY_CATEGORY[category],
            "customer_purchase_count": cust["customer_purchase_count"],
            "customer_success_rate": cust["customer_success_rate"],
            "customer_lifetime_value": cust["customer_lifetime_value"],
            "previous_payment_behavior": cust["previous_payment_behavior"],
            "customer_opt_out": cust["customer_opt_out"],
            "suspicious_flag": cust["suspicious_flag"],
            "historical_recovery_behavior": cust["historical_recovery_behavior"],
        }

        # amount_at_risk: FACT. Zero for successful cases, else the exposed amount.
        row["amount_at_risk"] = amount if category != "successful" else 0.0

        # failure_reason only applies to actual payment failures
        if category in ("failed_payment", "failed_subscription"):
            row["failure_reason"] = sample_failure_reason(rng, method)
        else:
            row["failure_reason"] = np.nan

        # checkout fields only apply to checkout-flow events
        if category == "successful":
            row["checkout_started"] = True
            row["checkout_completed"] = True
        elif category == "failed_payment":
            row["checkout_started"] = True
            row["checkout_completed"] = True  # user submitted; gateway/bank declined
        elif category == "checkout_abandonment":
            row["checkout_started"] = True
            row["checkout_completed"] = False
        else:  # failed_subscription, overdue_receivable are not checkout events
            row["checkout_started"] = np.nan
            row["checkout_completed"] = np.nan

        # subscription/mandate context
        if category == "failed_subscription":
            row["subscription_status"] = "failed"
            row["mandate_status"] = rng.choice(["failed", "active_pending_retry"], p=[0.6, 0.4])
        else:
            row["subscription_status"] = np.nan
            row["mandate_status"] = np.nan

        # invoice/receivable context
        if category == "overdue_receivable":
            days_overdue = int(np.clip(rng.exponential(30), 1, 150))
            due_date = REFERENCE_DATE - timedelta(days=days_overdue)
            row["invoice_status"] = "overdue"
            row["invoice_due_date"] = due_date
            row["days_overdue"] = days_overdue
            timestamp = REFERENCE_DATE
        else:
            row["invoice_status"] = np.nan
            row["invoice_due_date"] = pd.NaT
            row["days_overdue"] = np.nan
            # spread other events over the past ~90 days
            timestamp = REFERENCE_DATE - timedelta(days=int(rng.integers(0, 90)),
                                                     hours=int(rng.integers(0, 24)))
        row["timestamp"] = timestamp

        # retry / attempt history
        row["retry_count"] = sample_retry_count(rng, category, cust["previous_payment_behavior"])
        row["previous_attempt_count"] = sample_previous_attempt_count(rng, category, row["retry_count"])

        # communication eligibility (consent-based; time-of-day contact window is
        # evaluated at guardrail runtime later, not baked into the dataset)
        row["communication_allowed"] = bool((not cust["customer_opt_out"]) and (not cust["suspicious_flag"]))

        records.append(row)

    df = pd.DataFrame(records)

    # Ground truth / post-action outcome (never a diagnosis input feature)
    outcomes = df.apply(lambda r: simulate_recovery(rng, r), axis=1, result_type="expand")
    df = pd.concat([df, outcomes], axis=1)

    # reproducible customer-aware split
    df["split"] = df["customer_id"].apply(lambda c: assign_split(c, seed))

    # column ordering: pre-decision features first, then ground-truth/outcome block, then split
    pre_decision_cols = [
        "case_id", "transaction_id", "customer_id", "event_type", "leakage_category",
        "amount", "amount_at_risk", "timestamp", "payment_method", "payment_status",
        "failure_reason", "checkout_started", "checkout_completed", "subscription_status",
        "mandate_status", "invoice_status", "invoice_due_date", "days_overdue",
        "retry_count", "previous_attempt_count", "customer_purchase_count",
        "customer_success_rate", "customer_lifetime_value", "previous_payment_behavior",
        "customer_opt_out", "suspicious_flag", "communication_allowed",
        "historical_recovery_behavior",
    ]
    ground_truth_cols = [
        "ground_truth_recoverable", "ground_truth_recovery_outcome",
        "amount_recovered", "recovery_observed", "recovery_reason",
    ]
    df = df[pre_decision_cols + ground_truth_cols + ["split"]]
    return df


def save_outputs(df, output_dir=OUTPUT_DIR):
    main_path = f"{output_dir}/recoverai_cases.csv"
    df.to_csv(main_path, index=False)

    for split_name, fname in [("train", "train.csv"), ("validation", "validation.csv"), ("test", "test.csv")]:
        df[df["split"] == split_name].drop(columns=["split"]).to_csv(f"{output_dir}/{fname}", index=False)

    return main_path


if __name__ == "__main__":
    dataset = generate_dataset()
    path = save_outputs(dataset)
    print(f"Generated {len(dataset)} cases -> {path}")
    print(dataset["leakage_category"].value_counts())
    print(dataset["split"].value_counts())
