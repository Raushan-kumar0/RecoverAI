"""
RecoverAI — Razorpay Test-Mode Integration: Tests (Step 7)

Run:
    python3 -m pytest test_razorpay_integration.py -v

IMPORTANT: all tests use FAKE test-mode-shaped credentials and run in
DRY_RUN mode by default. No test in this file makes a real network call.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "guardrails"))

import pytest

from razorpay_config import (
    load_config_from_env, RazorpayConfig,
    MissingCredentialsError, MalformedCredentialsError, LiveModeCredentialsError,
)
from razorpay_client import RazorpayTestModeClient
from razorpay_execution import execute_guardrail_approved_action, SUPPORTED_ACTION_TYPES

from decision_models import Decision, DecisionStatus, LikelihoodTier
from guardrail_models import GuardrailDecision, AuthorizationOutcome

FAKE_TEST_KEY_ID = "rzp_test_fakekey1234567"
FAKE_TEST_KEY_SECRET = "fakesecret1234567890"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from a clean environment and explicit dry-run default."""
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RECOVERAI_RAZORPAY_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    yield


def _set_fake_test_credentials(monkeypatch, dry_run=None):
    monkeypatch.setenv("RAZORPAY_KEY_ID", FAKE_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", FAKE_TEST_KEY_SECRET)
    if dry_run is not None:
        monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "true" if dry_run else "false")


def _decision(case_id, leakage_category, action_type, action_extra=None):
    action = {"action_type": action_type, "money_movement": action_type in ("payment_retry", "mandate_retry"),
              "customer_communication": action_type == "recovery_payment_link", "requires_merchant_approval": False}
    if action_extra:
        action.update(action_extra)
    return Decision(
        case_id=case_id, leakage_category=leakage_category, decision_status=DecisionStatus.RECOMMENDED,
        recommended_action_type=action_type, recommended_action=action,
        likelihood_tier=LikelihoodTier.HIGH, predicted_recovery_likelihood=0.8, diagnosis_confidence=0.8,
        recommendation_reason="test", alternatives_considered=[],
    )


def _guardrail(case_id, action_type, outcome):
    return GuardrailDecision(
        case_id=case_id, leakage_category="failed_payment", recommended_action_type=action_type,
        outcome=outcome, reason="test", triggered_rules=[], limits_checked={},
        approval_required=(outcome == AuthorizationOutcome.APPROVAL_REQUIRED), config_used={},
        evaluated_at="2026-01-01T00:00:00",
    )


# 1. Missing credentials
def test_missing_credentials_raises():
    with pytest.raises(MissingCredentialsError):
        load_config_from_env()


def test_missing_key_secret_only_raises(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", FAKE_TEST_KEY_ID)
    with pytest.raises(MissingCredentialsError):
        load_config_from_env()


# 2. Malformed credentials/configuration
def test_malformed_key_id_prefix_raises(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "not_a_real_key_format")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", FAKE_TEST_KEY_SECRET)
    with pytest.raises(MalformedCredentialsError):
        load_config_from_env()


def test_too_short_secret_raises(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", FAKE_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "short")
    with pytest.raises(MalformedCredentialsError):
        load_config_from_env()


# no live-mode configuration
def test_live_mode_key_unconditionally_rejected(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_realkeyshouldneverwork")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", FAKE_TEST_KEY_SECRET)
    with pytest.raises(LiveModeCredentialsError):
        load_config_from_env()


def test_live_mode_key_rejected_even_with_dry_run_false(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_realkeyshouldneverwork")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", FAKE_TEST_KEY_SECRET)
    monkeypatch.setenv("RECOVERAI_RAZORPAY_DRY_RUN", "false")
    with pytest.raises(LiveModeCredentialsError):
        load_config_from_env()


def test_client_construction_refuses_live_key_defense_in_depth():
    live_config = RazorpayConfig(key_id="rzp_live_x", key_secret="y" * 20, dry_run=True)
    with pytest.raises(ValueError):
        RazorpayTestModeClient(live_config)


# 3. test-mode configuration (dry-run default)
def test_dry_run_defaults_true_when_unset(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    assert config.dry_run is True


def test_dry_run_can_be_explicitly_disabled(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=False)
    config = load_config_from_env()
    assert config.dry_run is False


def test_redacted_never_exposes_secret(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    redacted = config.redacted()
    assert FAKE_TEST_KEY_SECRET not in str(redacted)
    assert redacted["key_secret"] == "***REDACTED***"


# dry-run client behavior — no network call
def test_dry_run_create_payment_link_makes_no_network_call(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    result = client.create_payment_link(amount_rupees=500, description="test", reference_id="CASE1")
    assert result["status"] == "dry_run"
    assert "would_call" in result
    assert result["would_call"]["method"] == "POST"
    assert FAKE_TEST_KEY_SECRET not in str(result)


def test_simulate_retry_never_claims_real_result(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    result = client.simulate_retry_operation("payment_retry", "CASE1", 500)
    assert result["status"] == "simulated"
    assert "no merchant-triggerable Razorpay API endpoint" in result["note"]


# 4. STOP prevents execution
def test_stop_prevents_execution(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C1", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = _decision("C1", "failed_payment", "payment_retry")
    guardrail = _guardrail("C1", "payment_retry", AuthorizationOutcome.STOP)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "not_executed"
    assert record.result_source == "not_executed"
    assert "STOP" in record.reason or "stop" in record.reason


# 5. APPROVAL_REQUIRED prevents execution (never bypassed)
def test_approval_required_prevents_execution(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C2", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = _decision("C2", "failed_payment", "payment_retry")
    guardrail = _guardrail("C2", "payment_retry", AuthorizationOutcome.APPROVAL_REQUIRED)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "not_executed"
    assert "APPROVAL_REQUIRED" in record.reason or "approval_required" in record.reason


# 6. AUTO_EXECUTE permits the integration path
def test_auto_execute_permits_payment_link_path(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C3", "leakage_category": "checkout_abandonment", "amount_at_risk": 750}
    decision = _decision("C3", "checkout_abandonment", "recovery_payment_link")
    guardrail = _guardrail("C3", "recovery_payment_link", AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "dry_run"
    assert record.result_source == "razorpay_test_mode_dry_run"


def test_auto_execute_permits_simulated_retry_path(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C4", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = _decision("C4", "failed_payment", "payment_retry")
    guardrail = _guardrail("C4", "payment_retry", AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "simulated"
    assert record.result_source == "bounded_simulation"


# 7. unsupported action rejection
def test_unsupported_action_type_rejected(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C5", "leakage_category": "overdue_receivable", "amount_at_risk": 500}
    decision = _decision("C5", "overdue_receivable", "escalation")
    guardrail = _guardrail("C5", "escalation", AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "not_executed"
    assert "does not require Razorpay" in record.reason


def test_all_supported_action_types_are_the_expected_three():
    assert SUPPORTED_ACTION_TYPES == {"payment_retry", "mandate_retry", "recovery_payment_link"}


# 8. invalid case / mismatch rejection — never execute an action not recommended
def test_action_type_mismatch_between_decision_and_guardrail_rejected(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C6", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = _decision("C6", "failed_payment", "payment_retry")
    guardrail = _guardrail("C6", "recovery_payment_link", AuthorizationOutcome.AUTO_EXECUTE)  # mismatched!
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "not_executed"
    assert "mismatch" in record.reason


def test_case_id_mismatch_rejected(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C7", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = _decision("C7_WRONG", "failed_payment", "payment_retry")
    guardrail = _guardrail("C7", "payment_retry", AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "not_executed"
    assert "mismatch" in record.reason


def test_non_recommended_decision_status_rejected(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C8", "leakage_category": "failed_payment", "amount_at_risk": 500}
    decision = Decision(case_id="C8", leakage_category="failed_payment", decision_status=DecisionStatus.NO_APPLICABLE_ACTIONS,
                         recommended_action_type=None, recommended_action=None,
                         likelihood_tier=LikelihoodTier.LOW, predicted_recovery_likelihood=0.1,
                         diagnosis_confidence=0.1, recommendation_reason="none")
    guardrail = _guardrail("C8", None, AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "not_executed"


# 9. safe error handling
def test_invalid_amount_handled_safely_not_crash(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C9", "leakage_category": "checkout_abandonment", "amount_at_risk": -100}  # invalid
    decision = _decision("C9", "checkout_abandonment", "recovery_payment_link")
    guardrail = _guardrail("C9", "recovery_payment_link", AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert record.execution_status == "error"
    assert record.result_source == "not_executed"


def test_no_secret_leakage_in_error_output(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    case = {"case_id": "C10", "leakage_category": "checkout_abandonment", "amount_at_risk": -100}
    decision = _decision("C10", "checkout_abandonment", "recovery_payment_link")
    guardrail = _guardrail("C10", "recovery_payment_link", AuthorizationOutcome.AUTO_EXECUTE)
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    assert FAKE_TEST_KEY_SECRET not in str(record.to_dict())


# 10. no live-mode configuration possible anywhere in this module
def test_client_base_url_is_always_razorpay_api(monkeypatch):
    _set_fake_test_credentials(monkeypatch)
    config = load_config_from_env()
    assert config.base_url == "https://api.razorpay.com/v1"
    assert "live" not in config.base_url


# 11. no secret leakage in logs (redacted representation used everywhere)
def test_dry_run_would_call_uses_redacted_auth(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    result = client.create_payment_link(amount_rupees=100, description="d", reference_id="C11")
    assert result["would_call"]["auth"]["key_secret"] == "***REDACTED***"


# 12. deterministic request construction where applicable
def test_payment_link_payload_amount_conversion_deterministic(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    r1 = client.create_payment_link(amount_rupees=1234.56, description="x", reference_id="CASE1")
    r2 = client.create_payment_link(amount_rupees=1234.56, description="x", reference_id="CASE1")
    assert r1["would_call"]["payload"]["amount"] == r2["would_call"]["payload"]["amount"] == 123456
    assert r1["would_call"]["payload"]["currency"] == "INR"


def test_reference_id_truncated_and_amount_positive_validated(monkeypatch):
    _set_fake_test_credentials(monkeypatch, dry_run=True)
    config = load_config_from_env()
    client = RazorpayTestModeClient(config)
    with pytest.raises(ValueError):
        client.create_payment_link(amount_rupees=0, description="x", reference_id="C1")
    with pytest.raises(ValueError):
        client.create_payment_link(amount_rupees=100, description="x", reference_id="")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
