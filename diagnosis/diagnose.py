"""
RecoverAI — Diagnosis Layer: Diagnosis Engine (Step 3)

Produces a structured diagnosis for a single case:
    FACTS (from the case) + root cause + risk factors + positive signals
    + predicted_recovery_likelihood (model output) + diagnosis_confidence
    + reasoning_summary + evidence

Root cause / risk factors / positive signals are rule-based and grounded
strictly in fields actually present on the case — never invented, never an
LLM guess. The only ML-derived number is predicted_recovery_likelihood.

This module does NOT decide an action, does NOT check guardrails, and does
NOT call any external API. It only diagnoses.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from feature_config import select_pre_decision_features, TARGET_COLUMN

KNOWN_LEAKAGE_CATEGORIES = {
    "successful", "failed_payment", "checkout_abandonment",
    "failed_subscription", "overdue_receivable",
}

# Thresholds used by the rule-based extractors below. Kept as named constants
# (not magic numbers) so the logic is auditable and easy to recalibrate later.
HIGH_RETRY_COUNT = 2
LOW_SUCCESS_RATE = 0.5
HIGH_SUCCESS_RATE = 0.8
REPEAT_CUSTOMER_PURCHASES = 5
HIGH_LTV = 20000
LONG_OVERDUE_DAYS = 60


class DiagnosisEngine:
    def __init__(self, model_path=None):
        # Default resolves relative to this file's own directory (not the
        # current working directory), so DiagnosisEngine() works regardless
        # of where the caller/pytest was launched from (Windows or any OS).
        # An explicitly-provided model_path is used exactly as given, as before.
        if model_path is None:
            model_path = Path(__file__).resolve().parent / "model.joblib"
        self.pipeline = joblib.load(model_path)
        self._known_categories = self._extract_training_categories()

    def _extract_training_categories(self):
        """Pulls the categorical vocabulary the OneHotEncoder was fit on, used
        later to flag out-of-distribution values for the confidence heuristic."""
        ohe = self.pipeline.named_steps["prep"].named_transformers_["cat"].named_steps["onehot"]
        cat_cols = self.pipeline.named_steps["prep"].transformers_[3][2]
        return {col: set(cats) for col, cats in zip(cat_cols, ohe.categories_)}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def diagnose(self, case):
        """
        case: dict or pandas.Series with (at minimum) the pre-decision
              feature columns from feature_config.PRE_DECISION_FEATURES.
        Returns a structured diagnosis dict. Raises ValueError on invalid input.
        """
        case = self._validate_and_normalize(case)

        if case["leakage_category"] == "successful":
            return self._not_applicable_diagnosis(case)

        X = select_pre_decision_features(pd.DataFrame([case]))
        p = float(self.pipeline.predict_proba(X)[:, 1][0])

        root_cause, root_evidence = self._root_cause(case)
        risk_factors, risk_evidence = self._risk_factors(case)
        positive_signals, positive_evidence = self._positive_signals(case)
        confidence, confidence_note = self._confidence(p, case)

        evidence = root_evidence + risk_evidence + positive_evidence

        reasoning_summary = self._reasoning_summary(
            case, root_cause, risk_factors, positive_signals, p, confidence
        )

        return {
            "case_id": case.get("case_id", None),
            "leakage_category": case["leakage_category"],
            "root_cause": root_cause,
            "risk_factors": risk_factors,
            "positive_recovery_signals": positive_signals,
            "predicted_recovery_likelihood": round(p, 4),
            "diagnosis_confidence": round(confidence, 4),
            "confidence_method": confidence_note,
            "reasoning_summary": reasoning_summary,
            "evidence": evidence,
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate_and_normalize(self, case):
        if isinstance(case, pd.Series):
            case = case.to_dict()
        if not isinstance(case, dict):
            raise ValueError(f"Case must be a dict or pandas.Series, got {type(case)}")

        category = case.get("leakage_category")
        if category not in KNOWN_LEAKAGE_CATEGORIES:
            raise ValueError(
                f"Invalid or missing leakage_category: {category!r}. "
                f"Must be one of {sorted(KNOWN_LEAKAGE_CATEGORIES)}."
            )

        amount_at_risk = case.get("amount_at_risk")
        if amount_at_risk is None or (isinstance(amount_at_risk, float) and np.isnan(amount_at_risk)):
            raise ValueError("amount_at_risk is required and cannot be missing.")
        if category != "successful" and amount_at_risk <= 0:
            raise ValueError(
                f"amount_at_risk must be > 0 for a leakage case (got {amount_at_risk})."
            )

        return case

    def _not_applicable_diagnosis(self, case):
        """Successful/non-leakage cases have nothing at risk — diagnosed
        without invoking the recoverability model, since the model's target
        (recoverability) is undefined when there is no revenue at risk."""
        return {
            "case_id": case.get("case_id", None),
            "leakage_category": "successful",
            "root_cause": "No revenue at risk — this case completed successfully.",
            "risk_factors": [],
            "positive_recovery_signals": [],
            "predicted_recovery_likelihood": None,
            "diagnosis_confidence": None,
            "confidence_method": "not_applicable",
            "reasoning_summary": (
                "This case represents a successful transaction with amount_at_risk = 0. "
                "There is no revenue leakage to diagnose or recover."
            ),
            "evidence": [{"field": "amount_at_risk", "value": case.get("amount_at_risk")}],
        }

    # ------------------------------------------------------------------ #
    # Root cause (grounded in actual case fields only)
    # ------------------------------------------------------------------ #
    def _root_cause(self, case):
        cat = case["leakage_category"]
        ev = []
        if cat == "failed_payment":
            reason = case.get("failure_reason")
            method = case.get("payment_method")
            ev = [{"field": "failure_reason", "value": reason}, {"field": "payment_method", "value": method}]
            return f"Payment failed via {method} due to: {reason}.", ev

        if cat == "checkout_abandonment":
            ev = [
                {"field": "checkout_started", "value": case.get("checkout_started")},
                {"field": "checkout_completed", "value": case.get("checkout_completed")},
            ]
            return "Checkout was initiated but never completed; no payment attempt occurred.", ev

        if cat == "failed_subscription":
            sub_status = case.get("subscription_status")
            mandate = case.get("mandate_status")
            reason = case.get("failure_reason")
            ev = [
                {"field": "subscription_status", "value": sub_status},
                {"field": "mandate_status", "value": mandate},
                {"field": "failure_reason", "value": reason},
            ]
            return (f"Recurring subscription payment failed (status: {sub_status}, "
                    f"mandate: {mandate}); underlying reason: {reason}."), ev

        if cat == "overdue_receivable":
            days = case.get("days_overdue")
            ev = [{"field": "days_overdue", "value": days}, {"field": "invoice_status", "value": case.get("invoice_status")}]
            return f"Invoice is overdue by {int(days) if pd.notna(days) else 'an unknown number of'} days.", ev

        return "Unrecognized leakage category.", ev

    # ------------------------------------------------------------------ #
    # Risk factors (only included when actually supported by the case)
    # ------------------------------------------------------------------ #
    def _risk_factors(self, case):
        factors, ev = [], []

        retry_count = case.get("retry_count", 0) or 0
        if retry_count >= HIGH_RETRY_COUNT:
            factors.append(f"Multiple prior payment retries already failed (retry_count={retry_count}).")
            ev.append({"field": "retry_count", "value": retry_count})

        success_rate = case.get("customer_success_rate")
        if success_rate is not None and not pd.isna(success_rate) and success_rate < LOW_SUCCESS_RATE:
            factors.append(f"Low historical payment success rate ({success_rate:.2f}).")
            ev.append({"field": "customer_success_rate", "value": success_rate})

        if case.get("suspicious_flag"):
            factors.append("Case is flagged as suspicious/high-risk.")
            ev.append({"field": "suspicious_flag", "value": True})

        if case.get("customer_opt_out"):
            factors.append("Customer has opted out of communication.")
            ev.append({"field": "customer_opt_out", "value": True})

        if case.get("communication_allowed") is False:
            factors.append("Communication is not currently permitted for this customer.")
            ev.append({"field": "communication_allowed", "value": False})

        days_overdue = case.get("days_overdue")
        if case["leakage_category"] == "overdue_receivable" and days_overdue is not None \
                and not pd.isna(days_overdue) and days_overdue > LONG_OVERDUE_DAYS:
            factors.append(f"Invoice significantly overdue ({int(days_overdue)} days).")
            ev.append({"field": "days_overdue", "value": days_overdue})

        if case.get("historical_recovery_behavior") == "rarely_recovers":
            factors.append("Customer has historically rarely recovered from payment issues.")
            ev.append({"field": "historical_recovery_behavior", "value": "rarely_recovers"})

        if case.get("previous_payment_behavior") == "frequent_failure":
            factors.append("Customer has a pattern of frequent payment failures.")
            ev.append({"field": "previous_payment_behavior", "value": "frequent_failure"})

        if case.get("failure_reason") == "authentication failure":
            factors.append("Authentication failures often recur without a payment method change.")
            ev.append({"field": "failure_reason", "value": "authentication failure"})

        return factors, ev

    # ------------------------------------------------------------------ #
    # Positive recovery signals (only included when actually supported)
    # ------------------------------------------------------------------ #
    def _positive_signals(self, case):
        signals, ev = [], []

        success_rate = case.get("customer_success_rate")
        if success_rate is not None and not pd.isna(success_rate) and success_rate >= HIGH_SUCCESS_RATE:
            signals.append(f"Strong historical payment success rate ({success_rate:.2f}).")
            ev.append({"field": "customer_success_rate", "value": success_rate})

        purchase_count = case.get("customer_purchase_count", 0) or 0
        if purchase_count >= REPEAT_CUSTOMER_PURCHASES:
            signals.append(f"Repeat customer with {purchase_count} prior purchases.")
            ev.append({"field": "customer_purchase_count", "value": purchase_count})

        ltv = case.get("customer_lifetime_value")
        if ltv is not None and not pd.isna(ltv) and ltv >= HIGH_LTV:
            signals.append(f"High customer lifetime value (₹{ltv:,.0f}).")
            ev.append({"field": "customer_lifetime_value", "value": ltv})

        if (case.get("retry_count", 0) or 0) == 0:
            signals.append("No prior failed retries on this case.")
            ev.append({"field": "retry_count", "value": 0})

        if case.get("communication_allowed") is True:
            signals.append("Customer can be contacted through standard channels.")
            ev.append({"field": "communication_allowed", "value": True})

        if case.get("historical_recovery_behavior") == "usually_recovers_via_retry":
            signals.append("Customer has historically recovered well from payment issues.")
            ev.append({"field": "historical_recovery_behavior", "value": "usually_recovers_via_retry"})

        if case.get("previous_payment_behavior") == "reliable":
            signals.append("Customer has a reliable payment history.")
            ev.append({"field": "previous_payment_behavior", "value": "reliable"})

        return signals, ev

    # ------------------------------------------------------------------ #
    # Confidence — deliberately distinct from predicted_recovery_likelihood.
    # ------------------------------------------------------------------ #
    def _confidence(self, p, case):
        """
        ASSUMPTION (documented, not a calibrated statistical confidence):
        base confidence = 2*|p - 0.5|, i.e. how far the model's probability
        sits from the maximally-uncertain midpoint. This measures decision
        margin, not epistemic/statistical certainty.

        Adjustment: if any categorical feature value on this case was never
        seen during training, confidence is reduced (the model is
        extrapolating), and this is flagged in the confidence_method note.
        """
        base = float(np.clip(2 * abs(p - 0.5), 0.0, 1.0))

        unseen_fields = []
        for col, known_values in self._known_categories.items():
            val = case.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                val = "missing"
            if val not in known_values:
                unseen_fields.append(col)

        if unseen_fields:
            confidence = base * 0.7
            note = (f"margin-from-midpoint heuristic (2*|p-0.5|), reduced 30% due to "
                    f"out-of-distribution value(s) in: {unseen_fields}")
        else:
            confidence = base
            note = "margin-from-midpoint heuristic (2*|p-0.5|); not a calibrated statistical confidence interval"

        return confidence, note

    # ------------------------------------------------------------------ #
    def _reasoning_summary(self, case, root_cause, risk_factors, positive_signals, p, confidence):
        parts = [root_cause]
        if risk_factors:
            parts.append("Risk factors: " + " ".join(risk_factors))
        if positive_signals:
            parts.append("Positive signals: " + " ".join(positive_signals))
        parts.append(
            f"Predicted recovery likelihood: {p:.0%} (model estimate, not a guarantee). "
            f"Diagnosis confidence: {confidence:.0%}."
        )
        return " ".join(parts)
