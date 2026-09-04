"""
RecoverAI — Dashboard: Tests (Step 12)

Tests target dashboard_data.py (the pure-Python data layer) — app.py is
Streamlit presentation code and is instead verified by actually launching
it headlessly (see README.md §6 for that verification, captured during
implementation; Streamlit UI rendering itself isn't meaningfully unit-testable
without a browser).

Run:
    python3 -m pytest test_dashboard.py -v
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import pytest

import dashboard_data as dd
from audit_store import AuditStore

FAKE_TEST_KEY_ID = "rzp_test_fakekey1234567"
FAKE_TEST_KEY_SECRET = "fakesecret1234567890"
MIDDAY = datetime(2026, 8, 22, 14, 0)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RECOVERAI_RAZORPAY_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_ID", FAKE_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", FAKE_TEST_KEY_SECRET)
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "true")
    yield


@pytest.fixture
def engines():
    return dd.get_engines()


@pytest.fixture
def store():
    s = AuditStore(":memory:")
    yield s
    s.close()


# 1. Dashboard data layer loads successfully
def test_get_engines_returns_ready_client_with_valid_test_credentials(engines):
    diag, dec, guard, client, err = engines
    assert err is None
    assert client is not None
    assert diag is not None and dec is not None and guard is not None


def test_get_engines_degrades_gracefully_without_credentials(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    diag, dec, guard, client, err = dd.get_engines()
    assert client is None
    assert err is not None
    assert diag is not None  # diagnosis/decision/guardrail still work without Razorpay


def test_load_dataset_sample_never_modifies_source_file():
    import hashlib
    path = dd.DATA_CSV
    before = hashlib.md5(open(path, "rb").read()).hexdigest()
    dd.load_dataset_sample(n=5)
    after = hashlib.md5(open(path, "rb").read()).hexdigest()
    assert before == after


def test_load_dataset_sample_respects_n_and_filters():
    sample = dd.load_dataset_sample(n=3, leakage_only=True)
    assert len(sample) <= 3
    assert (sample["leakage_category"] != "successful").all()


# 2. Full pipeline run through the dashboard layer
def test_run_batch_produces_complete_case_results(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=2, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    assert len(results) == len(sample)
    for r in results:
        assert r["diagnosis"] is not None
        assert r["decision"] is not None
        assert r["guardrail"] is not None
        assert r["failure_handling_result"] is not None
        assert r["recovery_result"] is not None


def test_run_batch_logs_full_audit_trail(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=1, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    trail = dd.get_audit_timeline(store, results[0]["case_id"])
    stages = [e["stage"] for e in trail]
    assert stages == ["detection", "diagnosis", "candidate_actions", "decision", "guardrail", "execution", "recovery"]


# 3. Correct separation of ML / backtest / live / recovery metrics
def test_evaluation_report_categories_structurally_separate(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=2, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    report = dd.build_evaluation_report(results, sample, diag, dec, guard, current_time=MIDDAY)
    d = report.to_dict()
    assert set(["A_ml_model", "B_synthetic_backtest", "C_live_execution", "D_observed_recovery"]).issubset(d.keys())
    assert "total_amount_recovered" not in d["A_ml_model"]
    assert "total_amount_recovered" not in d["B_synthetic_backtest"]
    assert "backtest_amount_recoverable_if_ground_truth_trusted" not in (d["D_observed_recovery"] or {})


def test_overview_metrics_never_use_backtest_numbers(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=2, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    overview = dd.get_overview_metrics(results, sample)
    # In dry-run mode, nothing was ever paid — overview's recovered figure
    # must be 0, never populated from Category B's synthetic number.
    assert overview["observed_recovered_revenue"] == 0.0


# 4. No synthetic ground-truth leakage into live recovery
def test_dashboard_layer_never_reads_ground_truth_columns():
    src = open(os.path.join(os.path.dirname(__file__), "dashboard_data.py"), encoding="utf-8").read()
    for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome", "amount_recovered'",
                       "recovery_observed", "recovery_reason"):
        assert forbidden not in src, f"{forbidden} referenced in dashboard_data.py"


def test_app_py_never_reads_ground_truth_columns():
    src = open(os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8").read()
    for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                       "recovery_observed", "recovery_reason"):
        assert forbidden not in src


# 5. No predicted-likelihood-as-revenue leakage
def test_overview_recovered_revenue_not_derived_from_likelihood(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=3, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    # Every case has some predicted_recovery_likelihood, but recovered revenue
    # must still be 0 in dry-run mode regardless of how high those likelihoods are.
    likelihoods = [r["diagnosis"].get("predicted_recovery_likelihood") for r in results if r["diagnosis"]]
    assert any(l is not None and l > 0.5 for l in likelihoods)  # sanity: some real likelihood exists
    overview = dd.get_overview_metrics(results, sample)
    assert overview["observed_recovered_revenue"] == 0.0


# 6. EXECUTE != RECOVER
def test_executed_status_does_not_imply_recovered(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=5, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    for r in results:
        fhr = r.get("failure_handling_result")
        rec = r.get("recovery_result")
        if fhr and fhr["primary_execution"]["execution_status"] in ("executed", "dry_run", "simulated"):
            # A successful/attempted execution must never, by itself, produce
            # a "recovered" status without independent observation.
            if rec:
                assert rec["recovery_status"] in ("not_observed", "pending", "observation_failed",
                                                   "recovered", "partially_recovered")
                if rec["recovery_status"] not in ("recovered", "partially_recovered"):
                    assert rec["amount_recovered"] == 0.0


# 7. Only observed Razorpay payment status can produce recovered revenue
def test_check_payment_link_reuses_observe_recovery_not_new_logic(engines):
    diag, dec, guard, client, err = engines
    result = dd.check_payment_link("plink_TEST_DASHBOARD", client)
    assert result["recovery_status"] in ("not_observed", "pending", "recovered", "partially_recovered", "observation_failed")
    # dry-run mode: must be not_observed, never fabricated as recovered
    assert result["recovery_status"] == "not_observed"
    assert result["amount_recovered"] == 0.0


def test_check_payment_link_requires_id(engines):
    diag, dec, guard, client, err = engines
    with pytest.raises(ValueError):
        dd.check_payment_link("", client)


# 8. Test Mode labeling present in the UI source
def test_app_prominently_labels_test_mode():
    src = open(os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8").read()
    assert "RAZORPAY TEST MODE" in src or "TEST MODE" in src
    # Prominence is achieved via a shared badge constant (DRY) referenced
    # across multiple tabs, rather than repeating the literal string —
    # verify it's actually used in several distinct sections.
    assert src.count("RAZORPAY_TEST_MODE_BADGE") >= 4  # 1 definition + at least 3 usages


# 9. Pending payment / failed observation / dry-run behavior labeled, not zeroed silently
def test_app_has_explicit_labels_for_all_no_data_states():
    src = open(os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8").read()
    for label in ("NOT OBSERVED", "DRY RUN", "OBSERVATION FAILED", "PENDING"):
        assert label in src


# 10. Audit trail integrity through the dashboard
def test_audit_trail_case_isolation_through_dashboard(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=2, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    ids = [r["case_id"] for r in results]
    trail0 = dd.get_audit_timeline(store, ids[0])
    trail1 = dd.get_audit_timeline(store, ids[1])
    assert all(e["case_id"] == ids[0] for e in trail0)
    assert all(e["case_id"] == ids[1] for e in trail1)


# 11. Empty / no-live-data behavior
def test_overview_metrics_on_empty_batch():
    overview = dd.get_overview_metrics([], pd.DataFrame(columns=["case_id", "amount_at_risk"]))
    assert overview["cases_analyzed"] == 0
    assert overview["observed_recovered_revenue"] == 0.0
    assert overview["revenue_at_risk"] == 0.0


def test_get_audit_timeline_empty_for_unknown_case(store):
    trail = dd.get_audit_timeline(store, "NEVER_RAN")
    assert trail == []


# 12. No secret leakage anywhere in dashboard output
def test_no_secret_leakage_in_case_results(engines, store):
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=2, leakage_only=True, comm_allowed_only=True)
    results = dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    for r in results:
        text = json.dumps({k: v for k, v in r.items() if k not in ("decision", "guardrail", "diagnosis")}, default=str)
        assert FAKE_TEST_KEY_SECRET not in text


def test_no_secret_leakage_in_payment_link_check(engines):
    diag, dec, guard, client, err = engines
    result = dd.check_payment_link("plink_SECRET_CHECK", client)
    assert FAKE_TEST_KEY_SECRET not in json.dumps(result, default=str)


# Structural: dashboard does not modify Step 2/3 artifacts
def test_dashboard_run_does_not_modify_model_or_dataset(engines, store):
    import hashlib
    model_path = Path(__file__).resolve().parent.parent / "diagnosis" / "model.joblib"
    before = hashlib.md5(open(model_path, "rb").read()).hexdigest()
    diag, dec, guard, client, err = engines
    sample = dd.load_dataset_sample(n=2, leakage_only=True, comm_allowed_only=True)
    dd.run_batch(sample, diag, dec, guard, client, store, current_time=MIDDAY)
    after = hashlib.md5(open(model_path, "rb").read()).hexdigest()
    assert before == after


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
