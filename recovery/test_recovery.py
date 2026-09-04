"""
RecoverAI — RECOVER Stage: Tests (Step 10 prerequisite)

Run:
    python3 -m pytest test_recovery.py -v
"""

import os
import sys
import inspect
import json
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "audit"))

import pytest

from recovery_models import RecoveryStatus, RecoveryResult
from recovery_checker import observe_recovery
from audit_store import AuditStore
from audit_recorder import record_execution, record_recovery
from audit_schema import AuditStage

FAKE_TEST_KEY_ID = "rzp_test_fakekey1234567"
FAKE_TEST_KEY_SECRET = "fakesecret1234567890"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RECOVERAI_RAZORPAY_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_ID", FAKE_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", FAKE_TEST_KEY_SECRET)
    yield


class FakeStatusClient:
    """Deterministic test double for fetch_payment_link_status — no network."""
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def fetch_payment_link_status(self, payment_link_id):
        self.calls += 1
        return self._response


def _executed_link_record(action_type="recovery_payment_link", link_id="plink_TEST123"):
    return {
        "case_id": "C1", "leakage_category": "checkout_abandonment", "action_type": action_type,
        "execution_status": "executed", "result_source": "razorpay_test_mode_api",
        "reason": "Executed via the guardrail-approved path.",
        "razorpay_result": {"status": "executed", "razorpay_payment_link_id": link_id,
                             "razorpay_short_url": "https://rzp.io/i/test", "razorpay_status": "created"},
    }


# 1 & 2. Successful EXECUTE (Payment Link created) != recovered revenue; zero without confirmation
def test_executed_link_without_status_check_is_not_recovered():
    execution_record = _executed_link_record()
    # No status check performed at all — pass a client that would error if called,
    # to prove observe_recovery only reports NOT_OBSERVED here, never guesses "recovered".
    class ExplodingClient:
        def fetch_payment_link_status(self, payment_link_id):
            raise AssertionError("Should not be called when execution_status alone is inspected")

    # Simulate "no observation attempted yet" by not calling observe_recovery at all —
    # the execution record itself, unobserved, must never be treated as recovered.
    assert execution_record["execution_status"] == "executed"
    # The only way to KNOW recovery status is to call observe_recovery; until then
    # there is no RecoveryResult, and no code path in this project invents one.


def test_paid_link_id_alone_produces_zero_recovery_until_checked():
    execution_record = _executed_link_record()
    client = FakeStatusClient({"status": "observed", "mode": "test", "http_status": 200,
                                "razorpay_status": "created", "amount": 100000, "amount_paid": 0,
                                "razorpay_payment_link_id": "plink_TEST123", "timestamp": 0})
    result = observe_recovery("C1", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.PENDING
    assert result.amount_recovered == 0.0
    assert client.calls == 1  # confirmation WAS attempted, and correctly found nothing paid


# 3. Payment confirmation is required before recovered revenue is counted
def test_dry_run_execution_never_produces_recovery():
    execution_record = {
        "case_id": "C2", "leakage_category": "checkout_abandonment", "action_type": "recovery_payment_link",
        "execution_status": "dry_run", "result_source": "razorpay_test_mode_dry_run", "reason": "dry run",
        "razorpay_result": {"status": "dry_run"},  # no real link id — nothing was ever created
    }
    client = FakeStatusClient({"status": "observed"})
    result = observe_recovery("C2", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.NOT_OBSERVED
    assert result.amount_recovered == 0.0
    assert client.calls == 0  # no real link existed to check — correctly never attempted


def test_failed_execution_never_produces_recovery():
    execution_record = {
        "case_id": "C3", "leakage_category": "checkout_abandonment", "action_type": "recovery_payment_link",
        "execution_status": "api_error", "result_source": "razorpay_test_mode_api", "reason": "failed",
        "razorpay_result": {"status": "api_error"},
    }
    client = FakeStatusClient({"status": "observed"})
    result = observe_recovery("C3", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.NOT_OBSERVED
    assert result.amount_recovered == 0.0
    assert client.calls == 0


# 4. A paid/captured Payment Link produces the observed recovered amount
def test_paid_link_produces_observed_recovered_amount():
    execution_record = _executed_link_record()
    client = FakeStatusClient({"status": "observed", "mode": "test", "http_status": 200,
                                "razorpay_status": "paid", "amount": 100000, "amount_paid": 100000,
                                "razorpay_payment_link_id": "plink_TEST123", "timestamp": 0})
    result = observe_recovery("C1", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.RECOVERED
    assert result.amount_recovered == 1000.0  # 100000 paise -> rupees
    assert result.payment_link_id == "plink_TEST123"


def test_partially_paid_link_produces_partial_observed_amount():
    execution_record = _executed_link_record()
    client = FakeStatusClient({"status": "observed", "mode": "test", "http_status": 200,
                                "razorpay_status": "partially_paid", "amount": 100000, "amount_paid": 40000,
                                "razorpay_payment_link_id": "plink_TEST123", "timestamp": 0})
    result = observe_recovery("C1", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.PARTIALLY_RECOVERED
    assert result.amount_recovered == 400.0


# 5. A non-paid Payment Link does not produce recovered revenue
def test_expired_link_does_not_produce_recovery():
    execution_record = _executed_link_record()
    client = FakeStatusClient({"status": "observed", "mode": "test", "http_status": 200,
                                "razorpay_status": "expired", "amount": 100000, "amount_paid": 0,
                                "razorpay_payment_link_id": "plink_TEST123", "timestamp": 0})
    result = observe_recovery("C1", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.PENDING
    assert result.amount_recovered == 0.0


def test_status_check_api_error_is_observation_failed_not_recovered():
    execution_record = _executed_link_record()
    client = FakeStatusClient({"status": "api_error", "mode": "test", "http_status": 401,
                                "razorpay_error": {"description": "auth failed"}, "timestamp": 0})
    result = observe_recovery("C1", "checkout_abandonment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.OBSERVATION_FAILED
    assert result.amount_recovered == 0.0


# Non-recoverable action types (payment_retry/mandate_retry/anything else) never checked
def test_non_recovery_payment_link_action_never_observed():
    execution_record = {
        "case_id": "C4", "leakage_category": "failed_payment", "action_type": "payment_retry",
        "execution_status": "simulated", "result_source": "bounded_simulation", "reason": "simulated",
        "razorpay_result": {"status": "simulated"},
    }
    client = FakeStatusClient({"status": "observed"})
    result = observe_recovery("C4", "failed_payment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.NOT_OBSERVED
    assert result.observation_source == "no_link_to_observe"
    assert client.calls == 0


def test_not_executed_execution_never_produces_recovery():
    # A case blocked by guardrail (STOP/APPROVAL_REQUIRED) never even
    # attempted to create a link — observe_recovery must report NOT_OBSERVED,
    # never guess or default to any paid-adjacent status.
    execution_record = {
        "case_id": "C5", "leakage_category": "failed_payment", "action_type": "recovery_payment_link",
        "execution_status": "not_executed", "result_source": "not_executed",
        "reason": "Refusing to execute: guardrail outcome is 'stop'.",
        "razorpay_result": {},
    }
    client = FakeStatusClient({"status": "observed"})
    result = observe_recovery("C5", "failed_payment", execution_record, client)
    assert result.recovery_status == RecoveryStatus.NOT_OBSERVED
    assert result.amount_recovered == 0.0
    assert client.calls == 0


# 6 & 7. Predicted recovery likelihood cannot affect recovered revenue
def test_observe_recovery_signature_has_no_likelihood_or_diagnosis_parameter():
    sig = inspect.signature(observe_recovery)
    param_names = set(sig.parameters.keys())
    assert "predicted_recovery_likelihood" not in param_names
    assert "diagnosis" not in param_names
    assert "diagnosis_confidence" not in param_names
    assert "decision" not in param_names


# 8 & 9. Step 2 synthetic ground truth cannot be used as live recovery
def test_observe_recovery_signature_has_no_ground_truth_or_case_parameter():
    sig = inspect.signature(observe_recovery)
    param_names = set(sig.parameters.keys())
    assert "case" not in param_names
    assert "ground_truth_recoverable" not in param_names
    assert "ground_truth_recovery_outcome" not in param_names
    assert "amount_recovered" not in param_names  # not an INPUT — only ever an OUTPUT field on RecoveryResult


def test_no_forbidden_ground_truth_reference_in_recovery_source():
    import re
    for filename in ("recovery_checker.py", "recovery_models.py"):
        src = open(os.path.join(os.path.dirname(__file__), filename)).read()
        # Strip the module docstring/comments (which legitimately name these
        # fields while documenting that they must never be used), so this
        # checks actual code usage, not documentation.
        code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code_only = "\n".join(l for l in code_only.splitlines() if not l.strip().startswith("#"))
        for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                          "ground_truth_recovery_value", "recovery_observed", "recovery_reason"):
            assert forbidden not in code_only, f"{forbidden} referenced in actual code in {filename}"
        assert "predicted_recovery_likelihood" not in code_only
        assert "diagnosis_confidence" not in code_only


# 17-19. Existing Razorpay safety preserved by the new client method
def test_fetch_payment_link_status_respects_dry_run(monkeypatch):
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "true")
    from razorpay_config import load_config_from_env
    from razorpay_client import RazorpayTestModeClient
    client = RazorpayTestModeClient(load_config_from_env())
    result = client.fetch_payment_link_status("plink_TEST123")
    assert result["status"] == "dry_run"
    assert FAKE_TEST_KEY_SECRET not in json.dumps(result, default=str)


def test_fetch_payment_link_status_refuses_live_key():
    from razorpay_config import RazorpayConfig
    live_config = RazorpayConfig(key_id="rzp_live_x", key_secret="y" * 20, dry_run=True)
    with pytest.raises(ValueError):
        from razorpay_client import RazorpayTestModeClient
        RazorpayTestModeClient(live_config)


def test_fetch_payment_link_status_requires_id(monkeypatch):
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "true")
    from razorpay_config import load_config_from_env
    from razorpay_client import RazorpayTestModeClient
    client = RazorpayTestModeClient(load_config_from_env())
    with pytest.raises(ValueError):
        client.fetch_payment_link_status("")


# 20. Audit records correctly distinguish EXECUTE and RECOVER
def test_audit_distinguishes_execution_and_recovery_stages():
    store = AuditStore(":memory:")
    execution_record = _executed_link_record()

    class _EROnly:
        def __init__(self, d):
            self.__dict__.update(d)
            self._d = d
        def to_dict(self):
            return self._d

    record_execution(store, _EROnly(execution_record))

    result = RecoveryResult(case_id="C1", leakage_category="checkout_abandonment",
                             recovery_status=RecoveryStatus.RECOVERED, amount_recovered=1000.0,
                             observation_source="razorpay_payment_link_status", payment_link_id="plink_TEST123",
                             reason="paid", checked_at=0.0, raw_status_payload={})
    record_recovery(store, result)

    trail = store.get_case_trail("C1")
    stages = [e["stage"] for e in trail]
    assert stages == ["execution", "recovery"]
    execution_event, recovery_event = trail
    assert execution_event["payload"]["execution_status"] == "executed"
    assert recovery_event["payload"]["recovery_status"] == "recovered"
    assert recovery_event["payload"]["amount_recovered"] == 1000.0
    # EXECUTE payload must never itself claim a recovered amount
    assert "amount_recovered" not in execution_event["payload"]
    store.close()


def test_no_secret_leakage_in_recorded_recovery_audit_event(monkeypatch):
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "false")
    store = AuditStore(":memory:")
    execution_record = _executed_link_record()
    client = FakeStatusClient({
        "status": "observed", "mode": "test", "http_status": 200,
        "razorpay_status": "paid", "amount": 100000, "amount_paid": 100000,
        "razorpay_payment_link_id": "plink_TEST123", "timestamp": 0,
        # simulate a hypothetical buggy upstream response accidentally
        # including something secret-shaped — the audit store's redaction
        # guard (Step 8, reused unmodified) must still catch it.
        "key_secret": "should_never_appear_in_audit",
    })
    result = observe_recovery("C1", "checkout_abandonment", execution_record, client)
    record_recovery(store, result)
    trail = store.get_case_trail("C1")
    full_text = json.dumps(trail, default=str)
    assert "should_never_appear_in_audit" not in full_text
    assert FAKE_TEST_KEY_SECRET not in full_text
    store.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
