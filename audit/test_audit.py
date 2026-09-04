"""
RecoverAI — Audit Trail: Tests (Step 8)

Run:
    python3 -m pytest test_audit.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "guardrails"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))

from datetime import datetime
from pathlib import Path
import json

import pandas as pd
import pytest

from audit_schema import AuditStage
from audit_store import AuditStore
from audit_recorder import (
    record_detection, record_diagnosis, record_candidate_actions,
    record_decision, record_guardrail, record_execution,
)

from decision_models import Decision, DecisionStatus, LikelihoodTier
from guardrail_models import GuardrailDecision, AuthorizationOutcome
from action_compatibility import get_actions_for_case

DATA_CSV = str(Path(__file__).resolve().parent.parent / "data" / "recoverai_cases.csv")
FORBIDDEN_POST_ACTION_FIELDS = [
    "ground_truth_recoverable", "ground_truth_recovery_outcome",
    "ground_truth_recovery_value", "amount_recovered", "recovery_observed", "recovery_reason",
]


@pytest.fixture(scope="module")
def df():
    return pd.read_csv(DATA_CSV)


@pytest.fixture(scope="module")
def diagnosis_engine():
    from diagnose import DiagnosisEngine
    return DiagnosisEngine(os.path.join(os.path.dirname(__file__), "..", "diagnosis", "model.joblib"))


@pytest.fixture(scope="module")
def decision_engine(diagnosis_engine):
    from decision_engine import DecisionEngine
    return DecisionEngine(diagnosis_engine=diagnosis_engine)


@pytest.fixture(scope="module")
def guard_engine():
    from guardrail_engine import GuardrailEngine
    return GuardrailEngine()


@pytest.fixture(scope="module")
def razorpay_client(monkeypatch_module_env):
    from razorpay_config import load_config_from_env
    from razorpay_client import RazorpayTestModeClient
    return RazorpayTestModeClient(load_config_from_env())


@pytest.fixture(scope="module", autouse=True)
def monkeypatch_module_env():
    os.environ["RAZORPAY_KEY_ID"] = "rzp_test_fakekey1234567"
    os.environ["RAZORPAY_KEY_SECRET"] = "fakesecret1234567890"
    os.environ["RECOVERAI_RAZORPAY_DRY_RUN"] = "true"
    yield


@pytest.fixture
def store():
    s = AuditStore(":memory:")
    yield s
    s.close()


def _row(df, category, comm_allowed=None):
    subset = df[df["leakage_category"] == category]
    if comm_allowed is not None:
        subset = subset[subset["communication_allowed"] == comm_allowed]
    if len(subset) == 0:
        pytest.skip(f"no matching row for category={category}, comm_allowed={comm_allowed}")
    return subset.iloc[0]


def _run_full_pipeline_and_record(store, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client,
                                   current_time=None):
    current_time = current_time or datetime(2026, 8, 22, 14, 0)
    record_detection(store, row)
    if row["leakage_category"] == "successful":
        diagnosis = diagnosis_engine.diagnose(row)
        record_diagnosis(store, row["case_id"], row["leakage_category"], diagnosis)
        return
    diagnosis = diagnosis_engine.diagnose(row)
    record_diagnosis(store, row["case_id"], row["leakage_category"], diagnosis)
    actions = get_actions_for_case(row, diagnosis=diagnosis)
    record_candidate_actions(store, row["case_id"], row["leakage_category"], actions)
    decision = decision_engine.decide(row, diagnosis=diagnosis, actions=actions)
    record_decision(store, decision)
    diag_for_guard = {"diagnosis_confidence": decision.diagnosis_confidence}
    guardrail = guard_engine.authorize(row, diag_for_guard, decision, current_time=current_time)
    record_guardrail(store, guardrail)
    execution = execute_guardrail_approved_action_helper(row, decision, guardrail, razorpay_client)
    record_execution(store, execution)
    return diagnosis, actions, decision, guardrail, execution


def execute_guardrail_approved_action_helper(row, decision, guardrail, razorpay_client):
    from razorpay_execution import execute_guardrail_approved_action
    return execute_guardrail_approved_action(row, decision, guardrail, razorpay_client)


# 1. Schema / basic store behavior
def test_schema_creates_table(store):
    assert store.count_events() == 0


def test_record_and_retrieve_single_event(store):
    event_id = store.record_event("C1", "failed_payment", AuditStage.DETECTION, "test summary", {"foo": "bar"})
    assert event_id is not None
    trail = store.get_case_trail("C1")
    assert len(trail) == 1
    assert trail[0]["summary"] == "test summary"
    assert trail[0]["payload"] == {"foo": "bar"}
    assert trail[0]["stage"] == "detection"


def test_invalid_stage_rejected(store):
    with pytest.raises(ValueError):
        store.record_event("C1", "failed_payment", "not_a_real_stage", "x", {})


def test_missing_case_id_rejected(store):
    with pytest.raises(ValueError):
        store.record_event(None, "failed_payment", AuditStage.DETECTION, "x", {})


# 2. Full multi-stage trail for one case, real pipeline
@pytest.mark.parametrize("category", ["failed_payment", "checkout_abandonment", "failed_subscription", "overdue_receivable"])
def test_full_trail_recorded_for_leakage_categories(store, df, diagnosis_engine, decision_engine, guard_engine,
                                                       razorpay_client, category):
    row = _row(df, category, comm_allowed=True)
    _run_full_pipeline_and_record(store, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail = store.get_case_trail(row["case_id"])
    stages = [e["stage"] for e in trail]
    assert stages == ["detection", "diagnosis", "candidate_actions", "decision", "guardrail", "execution"]
    # sequence must be strictly increasing
    sequences = [e["sequence"] for e in trail]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_successful_case_trail_stops_after_diagnosis(store, df, diagnosis_engine, decision_engine, guard_engine, razorpay_client):
    successful_rows = df[df["leakage_category"] == "successful"]
    if len(successful_rows) == 0:
        pytest.skip("no successful cases")
    row = successful_rows.iloc[0]
    _run_full_pipeline_and_record(store, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail = store.get_case_trail(row["case_id"])
    stages = [e["stage"] for e in trail]
    assert stages == ["detection", "diagnosis"]


# 3. Multiple cases don't cross-contaminate
def test_multiple_cases_isolated(store, df, diagnosis_engine, decision_engine, guard_engine, razorpay_client):
    row1 = _row(df, "failed_payment", comm_allowed=True)
    row2 = _row(df, "overdue_receivable", comm_allowed=True)
    _run_full_pipeline_and_record(store, row1, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    _run_full_pipeline_and_record(store, row2, diagnosis_engine, decision_engine, guard_engine, razorpay_client)

    trail1 = store.get_case_trail(row1["case_id"])
    trail2 = store.get_case_trail(row2["case_id"])
    assert all(e["case_id"] == row1["case_id"] for e in trail1)
    assert all(e["case_id"] == row2["case_id"] for e in trail2)
    assert set(store.get_all_case_ids()) == {row1["case_id"], row2["case_id"]}


# 4. Forbidden post-action fields never stored
def test_detection_payload_never_contains_forbidden_fields(store, df):
    row = _row(df, "failed_payment")
    record_detection(store, row)
    trail = store.get_case_trail(row["case_id"])
    payload = trail[0]["payload"]
    for forbidden in FORBIDDEN_POST_ACTION_FIELDS:
        assert forbidden not in payload


def test_full_trail_invariant_to_tampered_ground_truth(store, df, diagnosis_engine, decision_engine, guard_engine, razorpay_client):
    row = _row(df, "failed_payment", comm_allowed=True)
    store_a = AuditStore(":memory:")
    _run_full_pipeline_and_record(store_a, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail_a = store_a.get_case_trail(row["case_id"])

    tampered = row.copy()
    tampered["amount_recovered"] = 999999
    tampered["ground_truth_recovery_outcome"] = "recovered"
    store_b = AuditStore(":memory:")
    _run_full_pipeline_and_record(store_b, tampered, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail_b = store_b.get_case_trail(row["case_id"])

    summaries_a = [e["summary"] for e in trail_a]
    summaries_b = [e["summary"] for e in trail_b]
    assert summaries_a == summaries_b
    store_a.close()
    store_b.close()


def test_no_forbidden_field_names_anywhere_in_full_trail(store, df, diagnosis_engine, decision_engine, guard_engine, razorpay_client):
    row = _row(df, "overdue_receivable", comm_allowed=True)
    _run_full_pipeline_and_record(store, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail = store.get_case_trail(row["case_id"])
    full_text = json.dumps(trail, default=str)
    for forbidden in FORBIDDEN_POST_ACTION_FIELDS:
        assert forbidden not in full_text


# 5. Human-readable summaries present
def test_every_event_has_nonempty_summary(store, df, diagnosis_engine, decision_engine, guard_engine, razorpay_client):
    row = _row(df, "checkout_abandonment", comm_allowed=True)
    _run_full_pipeline_and_record(store, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail = store.get_case_trail(row["case_id"])
    for e in trail:
        assert isinstance(e["summary"], str) and len(e["summary"]) > 10


# 6. Persistence across a real file (not just :memory:)
def test_persistence_across_reopen(tmp_path):
    db_path = tmp_path / "test_audit.db"
    store1 = AuditStore(str(db_path))
    store1.record_event("C_PERSIST", "failed_payment", AuditStage.DETECTION, "persisted event", {"x": 1})
    store1.close()

    store2 = AuditStore(str(db_path))
    trail = store2.get_case_trail("C_PERSIST")
    assert len(trail) == 1
    assert trail[0]["summary"] == "persisted event"
    store2.close()


# 7. AUTO_EXECUTE / APPROVAL_REQUIRED / STOP all recorded distinctly
def test_guardrail_outcomes_recorded_distinctly(store):
    for outcome in [AuthorizationOutcome.AUTO_EXECUTE, AuthorizationOutcome.APPROVAL_REQUIRED, AuthorizationOutcome.STOP]:
        gd = GuardrailDecision(
            case_id=f"C_{outcome.value}", leakage_category="failed_payment", recommended_action_type="payment_retry",
            outcome=outcome, reason="test", triggered_rules=[], limits_checked={},
            approval_required=(outcome == AuthorizationOutcome.APPROVAL_REQUIRED), config_used={},
            evaluated_at="2026-01-01T00:00:00",
        )
        record_guardrail(store, gd)
    for outcome in [AuthorizationOutcome.AUTO_EXECUTE, AuthorizationOutcome.APPROVAL_REQUIRED, AuthorizationOutcome.STOP]:
        trail = store.get_case_trail(f"C_{outcome.value}")
        assert trail[0]["payload"]["outcome"] == outcome.value


# 8. execution statuses (executed/simulated/dry_run/not_executed) recorded distinctly
def test_execution_statuses_recorded_distinctly(store, df, decision_engine, diagnosis_engine, guard_engine, razorpay_client):
    # not_executed via STOP
    decision = Decision(case_id="C_STOP", leakage_category="failed_payment", decision_status=DecisionStatus.RECOMMENDED,
                         recommended_action_type="payment_retry",
                         recommended_action={"action_type": "payment_retry", "money_movement": True,
                                              "customer_communication": False, "requires_merchant_approval": False},
                         likelihood_tier=LikelihoodTier.HIGH, predicted_recovery_likelihood=0.8,
                         diagnosis_confidence=0.8, recommendation_reason="test")
    guardrail = GuardrailDecision(case_id="C_STOP", leakage_category="failed_payment", recommended_action_type="payment_retry",
                                   outcome=AuthorizationOutcome.STOP, reason="test stop", triggered_rules=[],
                                   limits_checked={}, approval_required=False, config_used={}, evaluated_at="x")
    from razorpay_execution import execute_guardrail_approved_action
    execution = execute_guardrail_approved_action({"case_id": "C_STOP"}, decision, guardrail, razorpay_client)
    record_execution(store, execution)
    trail = store.get_case_trail("C_STOP")
    assert trail[0]["payload"]["execution_status"] == "not_executed"


# 9. Deterministic payload content (excluding timestamps/ids)
def test_recorded_payload_content_deterministic(store, df, diagnosis_engine, decision_engine, guard_engine, razorpay_client):
    row = _row(df, "failed_subscription", comm_allowed=True)
    store_a = AuditStore(":memory:")
    _run_full_pipeline_and_record(store_a, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    store_b = AuditStore(":memory:")
    _run_full_pipeline_and_record(store_b, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)

    trail_a = store_a.get_case_trail(row["case_id"])
    trail_b = store_b.get_case_trail(row["case_id"])
    for ea, eb in zip(trail_a, trail_b):
        pa = dict(ea["payload"])
        pb = dict(eb["payload"])
        pa.pop("executed_at", None)
        pb.pop("executed_at", None)
        if isinstance(pa.get("razorpay_result"), dict):
            pa["razorpay_result"] = dict(pa["razorpay_result"])
            pa["razorpay_result"].pop("timestamp", None)
        if isinstance(pb.get("razorpay_result"), dict):
            pb["razorpay_result"] = dict(pb["razorpay_result"])
            pb["razorpay_result"].pop("timestamp", None)
        assert pa == pb
    store_a.close()
    store_b.close()


# 10. No secret leakage in audit records — defense in depth
def test_audit_store_redacts_secret_shaped_keys(store):
    payload = {"key_secret": "should_never_appear", "razorpay_key_secret": "also_hidden", "amount": 100}
    store.record_event("C_SECRET", "failed_payment", AuditStage.EXECUTION, "test", payload)
    trail = store.get_case_trail("C_SECRET")
    stored_payload = trail[0]["payload"]
    assert stored_payload["key_secret"] == "***REDACTED***"
    assert stored_payload["razorpay_key_secret"] == "***REDACTED***"
    assert stored_payload["amount"] == 100


def test_real_execution_record_never_leaks_fake_test_secret_into_audit(store, df, diagnosis_engine, decision_engine,
                                                                        guard_engine, razorpay_client):
    row = _row(df, "checkout_abandonment", comm_allowed=True)
    _run_full_pipeline_and_record(store, row, diagnosis_engine, decision_engine, guard_engine, razorpay_client)
    trail = store.get_case_trail(row["case_id"])
    full_text = json.dumps(trail, default=str)
    assert "fakesecret1234567890" not in full_text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
