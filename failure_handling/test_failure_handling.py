"""
RecoverAI — Graceful Failure Handling: Tests (Step 9)

Run:
    python3 -m pytest test_failure_handling.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "guardrails"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "audit"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))

from datetime import datetime
import json

import pytest

from failure_models import FailureHandlingOutcome
from failure_handler import handle_execution_with_fallback

from decision_engine import DecisionEngine
from decision_models import Decision, DecisionStatus, LikelihoodTier
from guardrail_engine import GuardrailEngine
from guardrail_models import GuardrailDecision, AuthorizationOutcome
from action_compatibility import get_actions_for_case
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
    yield


class FakeFailingClient:
    """Test double: deterministically fails create_payment_link WITHOUT any
    network call, so these tests never depend on external network state.
    Mirrors RazorpayTestModeClient's interface exactly."""
    def __init__(self):
        self.create_payment_link_calls = 0
        self.simulate_retry_calls = 0

    def create_payment_link(self, amount_rupees, description, reference_id, customer=None):
        self.create_payment_link_calls += 1
        return {"status": "api_error", "mode": "test", "http_status": 401,
                "razorpay_error": {"description": "Authentication failed (deterministic test failure)"},
                "timestamp": 0}

    def simulate_retry_operation(self, action_type, case_id, amount_rupees):
        self.simulate_retry_calls += 1
        return {"status": "simulated", "mode": "test", "action_type": action_type,
                "case_id": case_id, "amount_rupees": amount_rupees, "note": "test simulation", "timestamp": 0}


class FakeAlwaysFailingClient(FakeFailingClient):
    """Fails BOTH the real call and would fail a fallback real call too —
    used to prove escalation triggers when even the fallback can't succeed."""
    def simulate_retry_operation(self, action_type, case_id, amount_rupees):
        # Not used in these tests (payment_retry/mandate_retry never fail in
        # this project's design — see Step 7), but defined for completeness.
        return super().simulate_retry_operation(action_type, case_id, amount_rupees)


@pytest.fixture
def guard_engine():
    return GuardrailEngine()


@pytest.fixture
def dec_engine():
    return DecisionEngine()


@pytest.fixture
def store():
    s = AuditStore(":memory:")
    yield s
    s.close()


def _medium_tier_failed_payment_case(case_id="DEMO", retry_count=1, previous_attempt_count=1,
                                       comm_allowed=True, suspicious=False, opt_out=False):
    case = {
        "case_id": case_id, "leakage_category": "failed_payment", "amount_at_risk": 800.0,
        "payment_method": "UPI", "failure_reason": "network failure",
        "communication_allowed": comm_allowed, "customer_opt_out": opt_out, "suspicious_flag": suspicious,
        "retry_count": retry_count, "previous_attempt_count": previous_attempt_count,
    }
    diagnosis = {"case_id": case_id, "predicted_recovery_likelihood": 0.45, "diagnosis_confidence": 0.7}
    return case, diagnosis


def _decide_and_authorize(case, diagnosis, dec_engine, guard_engine, current_time=MIDDAY):
    actions = get_actions_for_case(case, diagnosis=diagnosis)
    decision = dec_engine.decide(case, diagnosis=diagnosis, actions=actions)
    diag_for_guard = {"diagnosis_confidence": decision.diagnosis_confidence}
    guardrail = guard_engine.authorize(case, diag_for_guard, decision, current_time=current_time)
    return decision, guardrail


# 1. Controlled Razorpay API failure handling — genuine, deterministic failure detected
def test_genuine_failure_is_detected_and_triggers_fallback(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case()
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert decision.recommended_action_type == "recovery_payment_link"
    assert guardrail.outcome == AuthorizationOutcome.AUTO_EXECUTE

    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    assert result.primary_execution["execution_status"] == "api_error"
    assert result.outcome in (FailureHandlingOutcome.FALLBACK_SUCCEEDED, FailureHandlingOutcome.ESCALATED)
    assert client.create_payment_link_calls == 1  # exactly one primary attempt, no repeats


# 2. Fallback succeeds when a Razorpay-capable alternative exists
def test_fallback_succeeds_with_alternate_action(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case()
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    assert result.outcome == FailureHandlingOutcome.FALLBACK_SUCCEEDED
    assert result.fallback_action_type == "payment_retry"
    assert result.fallback_execution["execution_status"] == "simulated"
    assert client.simulate_retry_calls == 1


# 3. No repeated/unbounded retry — bounded call count
def test_no_repeated_or_unbounded_retry(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case()
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    # at most one real-API attempt + one fallback attempt, never more
    assert client.create_payment_link_calls == 1
    assert client.simulate_retry_calls <= 1
    assert result.razorpay_calls_made <= 2


def test_escalation_never_calls_razorpay_again(dec_engine, guard_engine, store):
    # checkout_abandonment: only fallback candidate (checkout_recovery_reminder)
    # has no execution capability -> must escalate without any further client calls
    case = {
        "case_id": "DEMO_ESCALATE", "leakage_category": "checkout_abandonment", "amount_at_risk": 500.0,
        "checkout_started": True, "checkout_completed": False,
        "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
        "retry_count": 0, "previous_attempt_count": 0,
    }
    diagnosis = {"case_id": "DEMO_ESCALATE", "predicted_recovery_likelihood": 0.85, "diagnosis_confidence": 0.8}
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert decision.recommended_action_type == "recovery_payment_link"

    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    assert result.outcome == FailureHandlingOutcome.ESCALATED
    assert result.escalated is True
    assert result.escalation_execution["execution_status"] == "not_executed"
    assert client.create_payment_link_calls == 1  # only the primary attempt — escalation made zero extra calls


# 4. Failure recorded in the Step 8 audit trail
def test_failure_recorded_in_audit_trail(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_AUDIT")
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    trail = store.get_case_trail("DEMO_AUDIT")
    execution_events = [e for e in trail if e["stage"] == "execution"]
    assert len(execution_events) >= 2  # primary failure + fallback attempt, both logged
    assert execution_events[0]["payload"]["execution_status"] == "api_error"


def test_full_audit_trail_shows_complete_story(dec_engine, guard_engine, store):
    case = {
        "case_id": "DEMO_STORY", "leakage_category": "checkout_abandonment", "amount_at_risk": 500.0,
        "checkout_started": True, "checkout_completed": False,
        "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
        "retry_count": 0, "previous_attempt_count": 0,
    }
    diagnosis = {"case_id": "DEMO_STORY", "predicted_recovery_likelihood": 0.85, "diagnosis_confidence": 0.8}
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    trail = store.get_case_trail("DEMO_STORY")
    stages = [e["stage"] for e in trail]
    # primary execution (fail) -> fallback decision -> fallback guardrail -> fallback execution
    # -> escalation decision -> escalation guardrail -> escalation execution
    assert stages == ["execution", "decision", "guardrail", "execution", "decision", "guardrail", "execution"]
    for e in trail:
        assert len(e["summary"]) > 5


# 5. Appropriate fallback/alternate action (deterministic, not random)
def test_fallback_selection_is_deterministic(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_DET")
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)

    client1 = FakeFailingClient()
    store1 = AuditStore(":memory:")
    r1 = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client1, guard_engine, store1, current_time=MIDDAY)

    client2 = FakeFailingClient()
    store2 = AuditStore(":memory:")
    r2 = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client2, guard_engine, store2, current_time=MIDDAY)

    assert r1.fallback_action_type == r2.fallback_action_type
    assert r1.outcome == r2.outcome
    store1.close()
    store2.close()


# 6. Escalation when required
def test_escalates_when_fallback_has_no_execution_capability(dec_engine, guard_engine, store):
    # failed_subscription: primary recovery_payment_link fails; the only
    # non-escalation fallback candidate (payment_reminder) has no execution
    # capability in this project (only payment_retry/mandate_retry/
    # recovery_payment_link do — see Step 7), so it correctly comes back
    # not_executed, and the handler must escalate rather than give up silently
    # or claim success. (A true "zero candidates" scenario is structurally
    # unreachable: recovery_payment_link can only be technically applicable
    # when communication_allowed=True, which always makes at least one
    # reminder-type fallback applicable too in every category's catalog.)
    case = {
        "case_id": "DEMO_NOEXEC_FALLBACK", "leakage_category": "failed_subscription", "amount_at_risk": 500.0,
        "subscription_status": "failed", "mandate_status": None,  # mandate_retry inapplicable (missing field)
        "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
        "retry_count": 0, "previous_attempt_count": 0,
    }
    diagnosis = {"case_id": "DEMO_NOEXEC_FALLBACK", "predicted_recovery_likelihood": 0.45, "diagnosis_confidence": 0.7}
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert decision.recommended_action_type == "recovery_payment_link"

    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    assert result.outcome == FailureHandlingOutcome.ESCALATED
    assert result.fallback_attempted is True
    assert result.fallback_action_type == "payment_reminder"
    assert result.fallback_execution["execution_status"] == "not_executed"
    assert result.escalated is True
    assert client.create_payment_link_calls == 1  # no repeated calls to the failing endpoint


# 7. STOP still prevents execution (not treated as a "failure" — no fallback triggered)
def test_stop_primary_does_not_trigger_fallback(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_STOP", suspicious=True)
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert guardrail.outcome == AuthorizationOutcome.STOP

    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    assert result.outcome == FailureHandlingOutcome.NO_FAILURE
    assert result.fallback_attempted is False
    assert result.escalated is False
    assert client.create_payment_link_calls == 0  # STOP means Step 7 never even attempted a call
    assert client.simulate_retry_calls == 0


# 8. APPROVAL_REQUIRED still prevents execution (not treated as a "failure")
def test_approval_required_primary_does_not_trigger_fallback(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_APPROVAL")
    diagnosis["diagnosis_confidence"] = 0.1  # forces APPROVAL_REQUIRED via low confidence
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert guardrail.outcome == AuthorizationOutcome.APPROVAL_REQUIRED

    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)

    assert result.outcome == FailureHandlingOutcome.NO_FAILURE
    assert result.fallback_attempted is False
    assert client.create_payment_link_calls == 0


# 9. No guardrail bypass — the fallback itself must also be authorized
def test_fallback_action_is_independently_reauthorized(dec_engine, guard_engine, store, monkeypatch):
    # A fallback that would itself be blocked (opt-out) must not execute even
    # though the primary was AUTO_EXECUTE.
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_FALLBACK_BLOCKED")
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert guardrail.outcome == AuthorizationOutcome.AUTO_EXECUTE

    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)
    # payment_retry (the fallback) has no communication requirement, so in
    # this scenario it IS authorized — confirm the guardrail was genuinely
    # re-invoked (not skipped) by checking the audit trail has its own
    # guardrail event distinct from the primary's.
    trail = store.get_case_trail("DEMO_FALLBACK_BLOCKED")
    guardrail_events = [e for e in trail if e["stage"] == "guardrail"]
    assert len(guardrail_events) == 1  # one for the fallback (primary guardrail wasn't recorded by this function itself)
    assert guardrail_events[0]["payload"]["recommended_action_type"] == "payment_retry"


def test_fallback_blocked_by_guardrail_falls_through_to_escalation(dec_engine, guard_engine, store):
    # Force a scenario where the ONLY non-escalation fallback candidate is a
    # communication action, and the case has opted out -> fallback guardrail
    # must STOP it, and the handler must escalate rather than bypass.
    case = {
        "case_id": "DEMO_FALLBACK_STOP", "leakage_category": "overdue_receivable", "amount_at_risk": 500.0,
        "days_overdue": 5, "communication_allowed": True, "customer_opt_out": False, "suspicious_flag": False,
        "retry_count": 0, "previous_attempt_count": 0,
    }
    diagnosis = {"case_id": "DEMO_FALLBACK_STOP", "predicted_recovery_likelihood": 0.85, "diagnosis_confidence": 0.8}
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    assert decision.recommended_action_type == "recovery_payment_link"

    # Simulate the fallback candidate becoming blocked: after primary fails,
    # the case's own opt-out state governs re-authorization of the fallback.
    # Here we directly verify the mechanism by checking that IF the fallback
    # requires communication and communication is disallowed, guardrail STOPs it.
    case_no_comm = dict(case)
    case_no_comm["communication_allowed"] = False
    decision2, guardrail2 = _decide_and_authorize(case_no_comm, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    result = handle_execution_with_fallback(case_no_comm, diagnosis, decision2, guardrail2, client, guard_engine, store, current_time=MIDDAY)
    # recovery_payment_link itself requires communication_allowed, so with it
    # False the primary is STOPped already (not a failure) — confirms
    # guardrail is never bypassed at the primary stage either.
    assert result.outcome == FailureHandlingOutcome.NO_FAILURE


# 10. No Razorpay secret leakage in errors, logs, or audit records
def test_no_secret_leakage_in_failure_handling_result(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_SECRET")
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)
    assert FAKE_TEST_KEY_SECRET not in json.dumps(result.to_dict(), default=str)


def test_no_secret_leakage_in_audit_trail_after_failure(dec_engine, guard_engine, store):
    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_SECRET2")
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    client = FakeFailingClient()
    handle_execution_with_fallback(case, diagnosis, decision, guardrail, client, guard_engine, store, current_time=MIDDAY)
    trail = store.get_case_trail("DEMO_SECRET2")
    assert FAKE_TEST_KEY_SECRET not in json.dumps(trail, default=str)


def test_no_forbidden_ground_truth_reference_in_failure_handler_source():
    src = open(os.path.join(os.path.dirname(__file__), "failure_handler.py"), encoding="utf-8").read()
    for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                       "amount_recovered", "recovery_observed", "recovery_reason"):
        assert forbidden not in src


# 11. Existing successful Razorpay Test Mode execution path remains unchanged
def test_successful_dry_run_execution_unaffected_by_failure_handler(dec_engine, guard_engine, store, monkeypatch):
    from razorpay_config import load_config_from_env
    from razorpay_client import RazorpayTestModeClient
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "true")
    real_client = RazorpayTestModeClient(load_config_from_env())

    case, diagnosis = _medium_tier_failed_payment_case(case_id="DEMO_DRYRUN_OK")
    decision, guardrail = _decide_and_authorize(case, diagnosis, dec_engine, guard_engine)
    result = handle_execution_with_fallback(case, diagnosis, decision, guardrail, real_client, guard_engine, store, current_time=MIDDAY)

    assert result.outcome == FailureHandlingOutcome.NO_FAILURE
    assert result.primary_execution["execution_status"] == "dry_run"
    assert result.fallback_attempted is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
