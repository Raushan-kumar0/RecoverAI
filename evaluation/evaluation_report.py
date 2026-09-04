"""
RecoverAI — Evaluation & Metrics: Report Assembly (Step 11)

Combines categories A (model), B (synthetic backtest), C (live execution),
and D (observed recovery) into one EvaluationReport — as four distinct
fields, never merged into a single number. C and D are optional: they
require an actual pipeline run to have happened (via Step 10's
BatchMeasurement); A and B are always available (A from Step 3's locked
artifact, B from running the real agent against the held-out split).
"""

from datetime import datetime, timezone
from typing import Optional

from evaluation_models import (
    EvaluationReport, LiveExecutionEvaluationSummary, ObservedRecoveryEvaluationSummary,
)


def build_live_execution_summary(batch_measurement) -> LiveExecutionEvaluationSummary:
    """batch_measurement: a Step 10 BatchMeasurement (object or dict). Only
    execution/action fields are read — never anything recovery-related."""
    bm = batch_measurement.to_dict() if hasattr(batch_measurement, "to_dict") else dict(batch_measurement)
    return LiveExecutionEvaluationSummary(
        cases_analyzed=bm["cases_analyzed"],
        recovery_opportunities=bm["recovery_opportunities"],
        total_amount_at_risk=bm["total_amount_at_risk"],
        total_amount_processed=bm["total_amount_processed"],
        actions_attempted=bm["actions_attempted"],
        successful_executions=bm["successful_executions"],
        failed_executions=bm["failed_executions"],
        fallback_actions=bm["fallback_actions"],
        escalated_cases=bm["escalated_cases"],
        stopped_cases=bm["stopped_cases"],
        approval_required_cases=bm["approval_required_cases"],
    )


def build_observed_recovery_summary(batch_measurement) -> ObservedRecoveryEvaluationSummary:
    """batch_measurement: the SAME Step 10 BatchMeasurement — only the
    recovery-related fields are read here, structurally separated from
    build_live_execution_summary() above even though the source object
    is shared."""
    bm = batch_measurement.to_dict() if hasattr(batch_measurement, "to_dict") else dict(batch_measurement)
    genuine_payment_verified = bm["total_amount_recovered"] > 0
    if genuine_payment_verified:
        note = "At least one genuine 'paid' observation contributed to total_amount_recovered."
    else:
        note = (
            "total_amount_recovered is 0.0. This may mean nothing has been paid yet, OR that no "
            "genuine Razorpay Test Mode payment could be verified in this environment (see "
            "recovery/README.md and measurement/README.md for the sandbox network limitation). "
            "This field alone cannot distinguish those two cases — check individual RecoveryResult "
            "records' recovery_status for that detail."
        )
    return ObservedRecoveryEvaluationSummary(
        total_amount_recovered=bm["total_amount_recovered"],
        recovery_rate=bm["recovery_rate"],
        unresolved_recovery_cases=bm["unresolved_recovery_cases"],
        net_recovered_revenue=bm["net_recovered_revenue"],
        recovery_cost=bm["recovery_cost"],
        genuine_payment_verified=genuine_payment_verified,
        limitation_note=note,
    )


def assemble_evaluation_report(model_summary, synthetic_backtest_result,
                                batch_measurement: Optional[object] = None) -> EvaluationReport:
    live_execution = build_live_execution_summary(batch_measurement) if batch_measurement is not None else None
    observed_recovery = build_observed_recovery_summary(batch_measurement) if batch_measurement is not None else None

    return EvaluationReport(
        model=model_summary,
        synthetic_backtest=synthetic_backtest_result,
        live_execution=live_execution,
        observed_recovery=observed_recovery,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
