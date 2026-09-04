"""
RecoverAI — Batch Revenue Recovery Measurement: Tests (Step 10)

Run:
    python3 -m pytest test_measurement.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from measurement_models import BatchEntry, BatchMeasurement
from batch_measurement import compute_batch_measurement


def _entry(case_id, leakage_category="failed_payment", amount_at_risk=1000.0,
           guardrail_outcome="auto_execute", recommended_action_type="payment_retry",
           fhr=None, recovery_result=None):
    return BatchEntry(
        case_id=case_id, leakage_category=leakage_category, amount_at_risk=amount_at_risk,
        primary_guardrail_outcome=guardrail_outcome, primary_recommended_action_type=recommended_action_type,
        failure_handling_result=fhr, recovery_result=recovery_result,
    )


def _fhr(outcome="no_failure", primary_status="executed", fallback_attempted=False,
         fallback_status=None, escalated=False):
    d = {
        "outcome": outcome,
        "primary_execution": {"execution_status": primary_status},
        "fallback_attempted": fallback_attempted,
        "escalated": escalated,
    }
    if fallback_attempted:
        d["fallback_execution"] = {"execution_status": fallback_status}
    return d


def _recovery(status, amount):
    return {"recovery_status": status, "amount_recovered": amount}


# 10. Batch recovered revenue is the sum of observed RECOVER results only
def test_batch_recovered_revenue_sums_only_recover_results():
    entries = [
        _entry("C1", amount_at_risk=1000.0, fhr=_fhr(), recovery_result=_recovery("recovered", 1000.0)),
        _entry("C2", amount_at_risk=500.0, fhr=_fhr(), recovery_result=_recovery("pending", 0.0)),
        _entry("C3", amount_at_risk=800.0, fhr=_fhr(), recovery_result=_recovery("partially_recovered", 300.0)),
    ]
    m = compute_batch_measurement(entries)
    assert m.total_amount_recovered == 1300.0  # 1000 + 0 + 300, nothing else contributes


# 11. Execution success with no RECOVER confirmation does not increase recovered revenue
def test_successful_execution_without_recovery_result_contributes_zero():
    entries = [_entry("C1", amount_at_risk=5000.0, fhr=_fhr(primary_status="executed"), recovery_result=None)]
    m = compute_batch_measurement(entries)
    assert m.total_amount_recovered == 0.0
    assert m.successful_executions == 1  # execution success IS counted, separately, correctly labeled
    assert m.total_amount_processed == 5000.0  # attempted, not recovered — two different numbers


def test_dry_run_and_simulated_never_count_as_recovered():
    entries = [
        _entry("C1", fhr=_fhr(primary_status="dry_run"), recovery_result=_recovery("not_observed", 0.0)),
        _entry("C2", fhr=_fhr(primary_status="simulated"), recovery_result=_recovery("not_observed", 0.0)),
    ]
    m = compute_batch_measurement(entries)
    assert m.total_amount_recovered == 0.0
    assert m.successful_executions == 2


# 6-9 (batch level): predictions/ground-truth cannot influence the aggregate,
# structurally — BatchEntry has no such fields at all.
def test_batch_entry_has_no_likelihood_or_ground_truth_fields():
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(BatchEntry)}
    forbidden = {"predicted_recovery_likelihood", "diagnosis_confidence",
                 "ground_truth_recoverable", "ground_truth_recovery_outcome",
                 "ground_truth_recovery_value", "recovery_observed", "recovery_reason"}
    assert field_names.isdisjoint(forbidden)


def test_tampering_unrelated_dict_does_not_change_measurement():
    # Even if a caller's recovery_result dict had extra unexpected keys
    # (e.g. leftover ground-truth-shaped keys from a careless caller), only
    # recovery_status/amount_recovered are ever read.
    tampered = {"recovery_status": "pending", "amount_recovered": 0.0,
                "ground_truth_recovery_outcome": "recovered", "amount_recovered_fake": 999999}
    entries = [_entry("C1", amount_at_risk=1000.0, fhr=_fhr(), recovery_result=tampered)]
    m = compute_batch_measurement(entries)
    assert m.total_amount_recovered == 0.0  # status is "pending", not recovered — tampered extra keys ignored


def test_no_forbidden_field_reference_in_batch_measurement_source():
    import re
    for filename in ("batch_measurement.py", "measurement_models.py"):
        src = open(os.path.join(os.path.dirname(__file__), filename)).read()
        code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code_only = "\n".join(l for l in code_only.splitlines() if not l.strip().startswith("#"))
        for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                          "ground_truth_recovery_value", "predicted_recovery_likelihood", "diagnosis_confidence"):
            assert forbidden not in code_only, f"{forbidden} referenced in actual code in {filename}"


# 12. Step 9 FALLBACK_SUCCEEDED is represented correctly
def test_fallback_succeeded_counted_correctly():
    entries = [_entry("C1", fhr=_fhr(outcome="fallback_succeeded", primary_status="api_error",
                                      fallback_attempted=True, fallback_status="simulated"))]
    m = compute_batch_measurement(entries)
    assert m.fallback_actions == 1
    assert m.failed_executions == 1   # the primary failure is still counted honestly
    assert m.successful_executions == 1  # the fallback success is counted separately
    assert m.escalated_cases == 0


# 13. Step 9 ESCALATED is represented correctly
def test_escalated_via_step9_counted_correctly():
    entries = [_entry("C1", fhr=_fhr(outcome="escalated", primary_status="api_error", escalated=True))]
    m = compute_batch_measurement(entries)
    assert m.escalated_cases == 1
    assert m.failed_executions == 1


def test_escalated_via_direct_step5_recommendation_counted_correctly():
    # LOW-tier case: Step 5 recommends 'escalation' directly, no EXECUTE
    # failure ever occurs (nothing to fail — escalation never calls Razorpay).
    entries = [_entry("C1", guardrail_outcome="approval_required", recommended_action_type="escalation",
                       fhr=_fhr(outcome="no_failure", primary_status="not_executed"))]
    m = compute_batch_measurement(entries)
    assert m.escalated_cases == 1
    assert m.approval_required_cases == 1
    assert m.failed_executions == 0  # correctly NOT counted as a failure


# 14. Step 9 NO_FAILURE is represented correctly
def test_no_failure_counted_correctly():
    entries = [_entry("C1", fhr=_fhr(outcome="no_failure", primary_status="dry_run"))]
    m = compute_batch_measurement(entries)
    assert m.successful_executions == 1
    assert m.failed_executions == 0
    assert m.escalated_cases == 0
    assert m.fallback_actions == 0


# 15. Guardrail STOP cannot execute (reflected in batch counts)
def test_stopped_case_produces_zero_processed_amount():
    entries = [_entry("C1", amount_at_risk=1000.0, guardrail_outcome="stop",
                       fhr=_fhr(outcome="no_failure", primary_status="not_executed"))]
    m = compute_batch_measurement(entries)
    assert m.stopped_cases == 1
    assert m.total_amount_processed == 0.0
    assert m.actions_attempted == 0
    assert m.total_amount_recovered == 0.0


# 16. APPROVAL_REQUIRED cannot bypass approval (reflected in batch counts)
def test_approval_required_case_produces_zero_processed_amount_unless_escalation():
    entries = [_entry("C1", amount_at_risk=1000.0, guardrail_outcome="approval_required",
                       recommended_action_type="payment_retry",
                       fhr=_fhr(outcome="no_failure", primary_status="not_executed"))]
    m = compute_batch_measurement(entries)
    assert m.approval_required_cases == 1
    assert m.total_amount_processed == 0.0
    assert m.actions_attempted == 0


# Successful (non-leakage) cases excluded from recovery_opportunities but
# still counted in cases_analyzed
def test_successful_case_excluded_from_recovery_opportunities():
    entries = [
        _entry("C1", leakage_category="successful", amount_at_risk=0.0, guardrail_outcome=None,
               recommended_action_type=None, fhr=None, recovery_result=None),
        _entry("C2", leakage_category="failed_payment", amount_at_risk=500.0),
    ]
    m = compute_batch_measurement(entries)
    assert m.cases_analyzed == 2
    assert m.recovery_opportunities == 1


# Full required-final-proof metric set sanity check
def test_batch_measurement_reports_full_metric_set():
    m = compute_batch_measurement([_entry("C1")])
    d = m.to_dict()
    for key in ("cases_analyzed", "recovery_opportunities", "total_amount_at_risk",
                "total_amount_processed", "total_amount_recovered", "recovery_rate",
                "net_recovered_revenue", "actions_attempted", "successful_executions",
                "failed_executions", "fallback_actions", "escalated_cases", "stopped_cases",
                "approval_required_cases"):
        assert key in d


def test_recovery_rate_zero_when_no_amount_at_risk():
    m = compute_batch_measurement([])
    assert m.recovery_rate == 0.0
    assert m.total_amount_at_risk == 0.0


def test_recovery_cost_is_none_and_documented_not_fabricated():
    m = compute_batch_measurement([_entry("C1")])
    assert m.recovery_cost is None
    assert "not modeled" in m.recovery_cost_note


# Explicit recovery_rate denominator (documented, not silently invented)
def test_recovery_rate_denominator_is_total_amount_at_risk_not_processed():
    # amount_at_risk 1000 (attempted+recovered) + 4000 (never attempted, STOPped) = 5000 total at risk.
    # recovered = 1000. If the denominator were total_amount_processed (1000),
    # rate would be 1.0 — but it must be against total_amount_at_risk (5000) => 0.2.
    entries = [
        _entry("C1", amount_at_risk=1000.0, fhr=_fhr(primary_status="executed"),
               recovery_result=_recovery("recovered", 1000.0)),
        _entry("C2", amount_at_risk=4000.0, guardrail_outcome="stop",
               fhr=_fhr(outcome="no_failure", primary_status="not_executed")),
    ]
    m = compute_batch_measurement(entries)
    assert m.total_amount_at_risk == 5000.0
    assert m.total_amount_processed == 1000.0
    assert m.total_amount_recovered == 1000.0
    assert m.recovery_rate == 0.2  # 1000/5000, NOT 1000/1000


# Unresolved recovery cases: attempted but not yet confirmed paid
def test_unresolved_recovery_cases_counted_when_execution_succeeded_but_unconfirmed():
    entries = [
        _entry("C1", fhr=_fhr(primary_status="executed"), recovery_result=_recovery("pending", 0.0)),
        _entry("C2", fhr=_fhr(primary_status="executed"), recovery_result=_recovery("recovered", 1000.0)),
        _entry("C3", fhr=_fhr(primary_status="executed"), recovery_result=None),  # never checked at all
    ]
    m = compute_batch_measurement(entries)
    assert m.unresolved_recovery_cases == 2  # C1 (pending) and C3 (never observed); C2 is resolved (recovered)


def test_unresolved_not_counted_for_stopped_or_unprocessed_cases():
    entries = [_entry("C1", guardrail_outcome="stop", fhr=_fhr(outcome="no_failure", primary_status="not_executed"))]
    m = compute_batch_measurement(entries)
    assert m.unresolved_recovery_cases == 0  # never attempted at all — not "unresolved", just never tried


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
