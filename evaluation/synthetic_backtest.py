"""
RecoverAI — Evaluation & Metrics: Synthetic Backtest (Step 11, Category B)

Runs the REAL agent (DIAGNOSE -> DECIDE -> GUARDRAIL, using the actual Step
3/5/6 components — no shortcuts, no re-implementation) across Step 2's
held-out test split, and compares the agent's guardrail outcome against
Step 2's synthetic ground truth.

THIS IS THE ONLY MODULE IN THE ENTIRE PROJECT THAT READS
`ground_truth_recoverable` / `amount_recovered` FOR ANYTHING OTHER THAN
DATASET VALIDATION (Step 2). This is intentional and sanctioned — Step 1's
own spec says "Use held-out data where appropriate" for evaluation, and
backtesting the agent's decisions against synthetic ground truth is exactly
that. What is NOT sanctioned, and does not happen here or anywhere else in
this module: ground truth is never passed into diagnose(), decide(), or
authorize() as an INPUT — it is only compared AFTER the agent has already
produced its outcome, purely for scoring.

Every result from this module carries an explicit disclaimer
(SyntheticBacktestResult.disclaimer) and must never be merged with Step 10's
live execution/recovery numbers (categories C/D).
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))

from typing import Optional
from datetime import datetime

from action_compatibility import get_actions_for_case  # noqa: E402

from evaluation_models import SyntheticBacktestResult


def run_synthetic_backtest(diagnosis_engine, decision_engine, guardrail_engine, test_df,
                            current_time: Optional[datetime] = None) -> SyntheticBacktestResult:
    """
    test_df: the Step 2 held-out test split (data/test.csv), loaded by the
             caller. This function does not load or regenerate the dataset.
    """
    current_time = current_time or datetime.now()

    leakage_rows = test_df[test_df["leakage_category"] != "successful"]

    auto_execute_count = 0
    approval_required_count = 0
    stop_count = 0
    escalated_count = 0
    tp = fp = tn = fn = 0
    backtest_amount_at_risk = 0.0
    backtest_amount_recoverable = 0.0
    per_case = []

    for _, row in leakage_rows.iterrows():
        diagnosis = diagnosis_engine.diagnose(row)
        actions = get_actions_for_case(row, diagnosis=diagnosis)
        decision = decision_engine.decide(row, diagnosis=diagnosis, actions=actions)
        diag_for_guard = {"diagnosis_confidence": decision.diagnosis_confidence}
        guardrail = guardrail_engine.authorize(row, diag_for_guard, decision, current_time=current_time)
        outcome = guardrail.outcome.value

        # Ground truth read HERE ONLY, for scoring after the fact — never fed
        # into diagnose()/decide()/authorize() above.
        ground_truth_recoverable = bool(row["ground_truth_recoverable"])
        ground_truth_amount_recovered = float(row["amount_recovered"])
        amount_at_risk = float(row["amount_at_risk"])

        if outcome == "stop":
            stop_count += 1
        elif outcome == "approval_required":
            approval_required_count += 1
            if decision.recommended_action_type == "escalation":
                escalated_count += 1
        else:
            auto_execute_count += 1

        predicted_positive = (outcome == "auto_execute")
        actual_positive = ground_truth_recoverable

        if predicted_positive and actual_positive:
            tp += 1
        elif predicted_positive and not actual_positive:
            fp += 1
        elif not predicted_positive and actual_positive:
            fn += 1
        else:
            tn += 1

        backtest_amount_at_risk += amount_at_risk
        if predicted_positive and actual_positive:
            backtest_amount_recoverable += ground_truth_amount_recovered

        per_case.append({
            "case_id": row.get("case_id"),
            "leakage_category": row.get("leakage_category"),
            "guardrail_outcome": outcome,
            "recommended_action_type": decision.recommended_action_type,
            "ground_truth_recoverable": ground_truth_recoverable,
        })

    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision is not None and recall is not None and (precision + recall) > 0) else None

    backtest_rate = (backtest_amount_recoverable / backtest_amount_at_risk) if backtest_amount_at_risk > 0 else 0.0

    return SyntheticBacktestResult(
        cases_evaluated=len(leakage_rows),
        auto_execute_count=auto_execute_count,
        approval_required_count=approval_required_count,
        stop_count=stop_count,
        escalated_count=escalated_count,
        true_positive=tp, false_positive=fp, true_negative=tn, false_negative=fn,
        precision_at_auto_execute=precision, recall_at_auto_execute=recall, f1_at_auto_execute=f1,
        backtest_amount_at_risk=round(backtest_amount_at_risk, 2),
        backtest_amount_recoverable_if_ground_truth_trusted=round(backtest_amount_recoverable, 2),
        backtest_recovery_rate_if_ground_truth_trusted=round(backtest_rate, 4),
        per_case=per_case,
    )
