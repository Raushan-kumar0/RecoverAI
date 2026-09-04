"""
RecoverAI — Diagnosis Layer: Feature Contract (Step 3)

Single source of truth for which columns from the Step 2 dataset the diagnosis
model is allowed to see. Every other module in this package imports from here
rather than hardcoding column names, so there is exactly one place that can
leak a forbidden field.
"""

# ----------------------------------------------------------------------------
# ALLOWED — pre-decision features. Known before any recovery decision is made.
# ----------------------------------------------------------------------------
PRE_DECISION_FEATURES = [
    "leakage_category",
    "amount_at_risk",
    "payment_method",
    "failure_reason",
    "checkout_started",
    "checkout_completed",
    "subscription_status",
    "mandate_status",
    "invoice_status",
    "days_overdue",
    "retry_count",
    "previous_attempt_count",
    "customer_purchase_count",
    "customer_success_rate",
    "customer_lifetime_value",
    "previous_payment_behavior",
    "customer_opt_out",
    "suspicious_flag",
    "communication_allowed",
    "historical_recovery_behavior",
]

# Columns that exist in the dataset but are deliberately excluded from the
# model — not because they leak the future, but because they are identifiers
# or redundant with an already-included feature. Excluded for model hygiene,
# not for leakage-safety, but tracked explicitly so the exclusion is a
# documented decision rather than an oversight.
EXCLUDED_NON_FEATURE_COLUMNS = [
    "case_id",              # identifier, not generalizable
    "transaction_id",       # identifier, not generalizable
    "customer_id",          # identifier, not generalizable
    "timestamp",            # raw event date; its useful signal (recency) isn't
                             # modeled in Step 3 — documented limitation
    "invoice_due_date",     # raw date; days_overdue already encodes this
    "event_type",           # deterministic 1:1 function of leakage_category
    "payment_status",       # deterministic 1:1 function of leakage_category
]

# ----------------------------------------------------------------------------
# FORBIDDEN — post-action / ground-truth outcome fields. NEVER usable as
# model inputs. This list is enforced in code (see assert_no_forbidden_columns
# below), not just documented.
# ----------------------------------------------------------------------------
POST_ACTION_FIELDS = [
    "ground_truth_recoverable",
    "ground_truth_recovery_outcome",
    "ground_truth_recovery_value",   # not a physical column in Step 2 data
                                      # (collapsed into amount_recovered — see
                                      # data/README.md §4) but blocked by name
                                      # in case a future dataset version adds it.
    "amount_recovered",
    "recovery_observed",
    "recovery_reason",
]

# `predicted_recovery_likelihood` is the OUTPUT of this layer, not an input.
# Forbidding it as a feature prevents any accidental circular dependency if
# this module is ever reused after the column has been appended to a dataframe.
FORBIDDEN_OUTPUT_AS_INPUT = ["predicted_recovery_likelihood"]

ALL_FORBIDDEN_COLUMNS = POST_ACTION_FIELDS + FORBIDDEN_OUTPUT_AS_INPUT

# The valid supervised-learning target. Sourced from ground truth — this is
# explicitly permitted because the target is what supervised learning trains
# against; it is never fed to the model as an input feature.
TARGET_COLUMN = "ground_truth_recoverable"


def assert_no_forbidden_columns(columns):
    """
    Fail-safe guard: raises if any forbidden post-action field or the model's
    own output column has leaked into a feature set. Call this immediately
    before fitting or predicting.
    """
    columns = set(columns)
    violations = columns & set(ALL_FORBIDDEN_COLUMNS)
    if violations:
        raise ValueError(
            f"Forbidden post-action/output field(s) found in feature set: "
            f"{sorted(violations)}. These fields are ground truth or model "
            f"output and must never be used as diagnosis model inputs."
        )


def select_pre_decision_features(df):
    """
    Returns a copy of df restricted to PRE_DECISION_FEATURES only, after
    asserting no forbidden columns are present in the requested feature list.
    This is the only sanctioned way other modules should slice features.
    """
    assert_no_forbidden_columns(PRE_DECISION_FEATURES)
    missing = [c for c in PRE_DECISION_FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"Expected pre-decision feature columns missing from input: {missing}")
    return df[PRE_DECISION_FEATURES].copy()
