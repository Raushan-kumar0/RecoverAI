"""
RecoverAI — Evaluation & Metrics: Schema (Step 11)

Defines four STRUCTURALLY SEPARATE metric categories. They are never merged
into one number, and the EvaluationReport dataclass keeps them as distinct
fields so no consumer can accidentally sum across categories:

    A. ML_MODEL          — Step 3's diagnosis model performance (referenced,
                             not recomputed; source of truth remains
                             diagnosis/metrics_report.json).
    B. SYNTHETIC_BACKTEST  — agent-level (diagnose->decide->guardrail)
                              evaluation against Step 2's held-out
                              ground_truth_recoverable / amount_recovered.
                              THIS IS NOT LIVE DATA. Explicitly labeled.
    C. LIVE_EXECUTION       — Step 7/9 execution outcomes from an actual
                                (test-mode, possibly network-blocked) pipeline
                                run. No recovered-revenue claim lives here.
    D. OBSERVED_RECOVERY      — Step 10's RECOVER-stage observations from that
                                 same real run. The ONLY category that may
                                 ever report a nonzero "recovered" figure.

No function in this package merges B into C/D, or vice versa. A dollar
figure computed in category B is a "what-if, if you trust synthetic ground
truth" number; a dollar figure in category D is "what Razorpay actually
told us happened." They must never be added together or presented as the
same kind of number.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List


class EvaluationCategory(str, Enum):
    ML_MODEL = "ml_model"
    SYNTHETIC_BACKTEST = "synthetic_backtest"
    LIVE_EXECUTION = "live_execution"
    OBSERVED_RECOVERY = "observed_recovery"


@dataclass
class ModelEvaluationSummary:
    """Category A. A read-only reference to Step 3's already-computed,
    already-locked metrics_report.json — never recomputed, never retrained."""
    source: str
    model_selected: str
    threshold: float
    threshold_selection_method: str
    test_precision: float
    test_recall: float
    test_f1: float
    test_roc_auc: Optional[float]
    test_pr_auc: Optional[float]
    test_confusion_matrix: Dict[str, int]
    false_positive_cost_exposure: float
    per_category_test_metrics: Dict[str, Any]
    category: EvaluationCategory = EvaluationCategory.ML_MODEL


@dataclass
class SyntheticBacktestResult:
    """Category B. Agent-level (diagnose -> decide -> guardrail) evaluation
    against Step 2's held-out ground truth. Explicitly NOT live data —
    ground_truth_recoverable/amount_recovered are read HERE ONLY, for
    backtest labeling, and never passed into any live pipeline call."""
    cases_evaluated: int
    auto_execute_count: int
    approval_required_count: int
    stop_count: int
    escalated_count: int

    # Confusion matrix: "predicted positive" = agent's guardrail outcome was
    # AUTO_EXECUTE; "actual positive" = ground_truth_recoverable == True.
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision_at_auto_execute: Optional[float]
    recall_at_auto_execute: Optional[float]
    f1_at_auto_execute: Optional[float]

    backtest_amount_at_risk: float
    backtest_amount_recoverable_if_ground_truth_trusted: float  # SYNTHETIC what-if figure only
    backtest_recovery_rate_if_ground_truth_trusted: float          # SYNTHETIC what-if figure only

    per_case: List[Dict[str, Any]] = field(default_factory=list)
    category: EvaluationCategory = EvaluationCategory.SYNTHETIC_BACKTEST
    disclaimer: str = (
        "SYNTHETIC BACKTEST — derived entirely from Step 2's synthetic ground truth "
        "(ground_truth_recoverable, amount_recovered), NOT from any live Razorpay "
        "observation. Must never be presented as, added to, or confused with live "
        "recovered revenue (category D, OBSERVED_RECOVERY)."
    )


@dataclass
class LiveExecutionEvaluationSummary:
    """Category C. Execution-outcome metrics from an actual pipeline run
    (Step 7/9), reusing Step 10's BatchMeasurement fields that describe
    ACTIONS, never recovered money."""
    cases_analyzed: int
    recovery_opportunities: int
    total_amount_at_risk: float
    total_amount_processed: float
    actions_attempted: int
    successful_executions: int
    failed_executions: int
    fallback_actions: int
    escalated_cases: int
    stopped_cases: int
    approval_required_cases: int
    category: EvaluationCategory = EvaluationCategory.LIVE_EXECUTION


@dataclass
class ObservedRecoveryEvaluationSummary:
    """Category D. The ONLY category that may report a nonzero recovered
    amount — and only if Step 10's RECOVER stage actually observed one."""
    total_amount_recovered: float
    recovery_rate: float
    unresolved_recovery_cases: int
    net_recovered_revenue: float
    recovery_cost: Optional[float]
    genuine_payment_verified: bool  # True only if at least one real "paid" observation occurred
    limitation_note: str
    category: EvaluationCategory = EvaluationCategory.OBSERVED_RECOVERY


@dataclass
class EvaluationReport:
    model: ModelEvaluationSummary
    synthetic_backtest: SyntheticBacktestResult
    live_execution: Optional[LiveExecutionEvaluationSummary]
    observed_recovery: Optional[ObservedRecoveryEvaluationSummary]
    generated_at: str
    category_separation_notice: str = (
        "Categories A (ml_model), B (synthetic_backtest), C (live_execution), and D "
        "(observed_recovery) are reported separately and are NEVER summed or merged. "
        "Only category D may report genuinely recovered revenue, and only when Step 10's "
        "RECOVER stage actually observed a paid status from Razorpay."
    )

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "generated_at": self.generated_at,
            "category_separation_notice": self.category_separation_notice,
            "A_ml_model": dict(self.model.__dict__) if self.model else None,
            "B_synthetic_backtest": dict(self.synthetic_backtest.__dict__) if self.synthetic_backtest else None,
            "C_live_execution": dict(self.live_execution.__dict__) if self.live_execution else None,
            "D_observed_recovery": dict(self.observed_recovery.__dict__) if self.observed_recovery else None,
        }
        for key in ("A_ml_model", "B_synthetic_backtest", "C_live_execution", "D_observed_recovery"):
            if d[key] and "category" in d[key]:
                d[key]["category"] = d[key]["category"].value if hasattr(d[key]["category"], "value") else d[key]["category"]
        return d
