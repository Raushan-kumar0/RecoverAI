"""
RecoverAI — Batch Revenue Recovery Measurement: Schema (Step 10)

Defines the structured input (one entry per case, assembled from what Steps
5/6/9/10 already produced) and output (the aggregate batch report). No
pipeline logic lives here — see batch_measurement.py.

THE ONE RULE THIS ENTIRE MODULE EXISTS TO ENFORCE:
    total_amount_recovered is computed ONLY by summing RecoveryResult.amount_recovered
    values where recovery_status is RECOVERED or PARTIALLY_RECOVERED — i.e.
    ONLY from Step 10's RECOVER-stage observations. It is never derived from
    predicted_recovery_likelihood, diagnosis_confidence, a recommendation,
    a successful EXECUTE status, or Step 2 synthetic ground truth. See
    batch_measurement.py's compute_batch_measurement() for the enforcement.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class BatchEntry:
    """
    One case's worth of already-observed pipeline facts, assembled by the
    caller from real Step 5/6/9/10 objects (or their .to_dict() forms).
    This dataclass does not run any stage itself — it is purely a carrier.
    """
    case_id: str
    leakage_category: str
    amount_at_risk: float

    # Step 6: the PRIMARY guardrail outcome for this case (before any Step 9
    # fallback/escalation re-authorization). One of "auto_execute" /
    # "approval_required" / "stop", or None for a "successful" (non-leakage) case.
    primary_guardrail_outcome: Optional[str] = None

    # Step 5: what the primary decision recommended (used to detect cases
    # where Step 5 recommended 'escalation' directly, e.g. LOW-tier cases,
    # which is a genuine escalation even though no EXECUTE failure occurred).
    primary_recommended_action_type: Optional[str] = None

    # Step 9: the full failure/fallback/escalation result for this case, if
    # any action was attempted. None for successful/non-leakage cases.
    failure_handling_result: Optional[Dict[str, Any]] = None

    # Step 10 (this addition): the RECOVER-stage observation for whichever
    # execution (primary or fallback) actually created a real Payment Link.
    # None if no observation was possible/performed.
    recovery_result: Optional[Dict[str, Any]] = None


@dataclass
class BatchMeasurement:
    """
    RECOVERY RATE DEFINITION (explicit, not silently invented):
        recovery_rate = total_amount_recovered / total_amount_at_risk

    Denominator is TOTAL AMOUNT AT RISK across the whole batch (all leakage
    cases' amount_at_risk, regardless of whether an action was ever
    attempted on them) — not total_amount_processed (amount actually
    attempted) and not recovery_opportunities (a case count). This was
    chosen because it answers the question a hackathon judge or merchant
    actually cares about: "of all the revenue that was at risk, how much
    did we get back?" — the headline "₹ at risk -> ₹ recovered" framing
    from the Step 1 spec (§9, §12). A narrower "recovery rate among
    attempted cases only" (total_amount_recovered / total_amount_processed)
    is also a valid, different question; it is NOT what this field reports,
    and callers who want that ratio can compute it themselves from
    total_amount_recovered and total_amount_processed, both of which are
    reported separately below.
    """
    cases_analyzed: int
    recovery_opportunities: int  # leakage cases only (excludes "successful")

    total_amount_at_risk: float
    total_amount_processed: float       # sum of amount_at_risk for cases where SOME execution attempt was made
    total_amount_recovered: float         # sum of amount_recovered from OBSERVED RECOVER results only
    recovery_rate: float                    # total_amount_recovered / total_amount_at_risk (0.0 if no risk)

    recovery_cost: Optional[float]            # None: no per-action cost model exists yet (documented limitation)
    net_recovered_revenue: float                # total_amount_recovered - (recovery_cost or 0)

    actions_attempted: int
    successful_executions: int    # EXECUTE succeeded (dry_run/simulated/executed) — NOT the same as recovered
    failed_executions: int          # EXECUTE genuinely failed (api_error/error)
    fallback_actions: int             # Step 9 FALLBACK_SUCCEEDED count
    escalated_cases: int                # Step 9 ESCALATED count + direct Step 5 escalation recommendations
    stopped_cases: int                    # primary guardrail outcome == STOP
    approval_required_cases: int            # primary guardrail outcome == APPROVAL_REQUIRED
    unresolved_recovery_cases: int            # leakage cases with an execution attempt but no RECOVERED/
                                                # PARTIALLY_RECOVERED observation yet (recovery_status is
                                                # NOT_OBSERVED, PENDING, or OBSERVATION_FAILED, or missing) —
                                                # distinct from "failed"; these are simply not yet confirmed.

    recovery_cost_note: str = (
        "recovery_cost is not modeled — no per-action cost data exists anywhere in this project "
        "(Step 3's false-positive-cost analysis reported exposure, not a computed cost, for the same "
        "reason). net_recovered_revenue therefore equals total_amount_recovered (cost treated as 0), "
        "which is a documented simplification, not a fabricated saving."
    )
    per_case: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self):
        return dict(self.__dict__)
