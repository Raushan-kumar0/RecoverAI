"""
RecoverAI — Mandate Retry Sequencer: Tests

Run:
    python3 -m pytest test_mandate_sequencer.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "diagnosis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "decision_engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "guardrails"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "actions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "audit"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recovery"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))

import time
from datetime import datetime

import pytest

from sequencer_models import MandateRetrySequence, RetryAttempt, SequenceStatus
from sequencer_store import save_sequence, get_sequence, load_all_sequences, save_all_sequences
import mandate_sequencer as ms

from guardrail_engine import GuardrailEngine

MIDDAY_TS = datetime(2026, 8, 22, 14, 0).timestamp()
MIDDAY_DT = datetime(2026, 8, 22, 14, 0)
LATE_NIGHT_DT = datetime(2026, 8, 22, 23, 0)


class FakeClient:
    """Deterministic test double — never touches the network."""
    def __init__(self, create_result=None):
        self.simulate_calls = 0
        self.create_calls = 0
        self._create_result = create_result or {
            "status": "executed", "mode": "test", "http_status": 200,
            "razorpay_payment_link_id": "plink_FAKE123", "razorpay_short_url": "https://rzp.io/fake",
            "razorpay_status": "created", "timestamp": 0,
        }

    def simulate_retry_operation(self, action_type, case_id, amount_rupees):
        self.simulate_calls += 1
        return {"status": "simulated", "mode": "test", "action_type": action_type,
                "case_id": case_id, "amount_rupees": amount_rupees, "note": "test simulation", "timestamp": 0}

    def create_payment_link(self, amount_rupees, description, reference_id, customer=None):
        self.create_calls += 1
        return dict(self._create_result)

    def fetch_payment_link_status(self, payment_link_id):
        return {"status": "observed", "mode": "test", "http_status": 200,
                "razorpay_payment_link_id": payment_link_id, "razorpay_status": "paid",
                "amount": 100000, "amount_paid": 100000, "timestamp": 0}


SAMPLE_CASE = {
    "case_id": "CASE_SEQ_TEST", "leakage_category": "failed_subscription",
    "amount_at_risk": 1000.0, "communication_allowed": True,
}
SAMPLE_DIAGNOSIS = {"predicted_recovery_likelihood": 0.84, "diagnosis_confidence": 0.68}


# ---- start_sequence ----

def test_start_sequence_creates_correct_schedule():
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)
    assert seq.case_id == "CASE_SEQ_TEST"
    assert seq.max_attempts == 3
    assert seq.status == SequenceStatus.PENDING
    assert [a.scheduled_offset_days for a in seq.attempts] == [0, 3, 7]
    assert seq.attempts[0].scheduled_at == MIDDAY_TS
    assert seq.attempts[1].scheduled_at == MIDDAY_TS + 3 * 86400
    assert seq.attempts[2].scheduled_at == MIDDAY_TS + 7 * 86400


def test_start_sequence_custom_schedule():
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 1])
    assert seq.max_attempts == 2


# ---- get_due_attempt / run_due_attempt ----

def test_no_attempt_due_before_scheduled_time():
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)
    seq = ms.run_due_attempt(seq, FakeClient(), current_time=MIDDAY_TS)  # runs attempt 1 (offset 0)
    # attempt 2 is 3 days out — not due yet
    still_pending = ms.get_due_attempt(seq, current_time=MIDDAY_TS + 1)
    assert still_pending is None


def test_first_attempt_runs_immediately():
    client = FakeClient()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    assert client.simulate_calls == 1
    assert seq.attempts[0].executed_at == MIDDAY_TS
    assert seq.attempts[0].result_source == "bounded_simulation"
    assert seq.attempts[0].raw_result["status"] == "simulated"
    assert seq.status == SequenceStatus.ATTEMPT_SCHEDULED  # 2 attempts remain


def test_sequence_exhausts_after_all_attempts_run():
    client = FakeClient()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 1, 2])
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS + 1 * 86400)
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS + 2 * 86400)
    assert client.simulate_calls == 3
    assert seq.status == SequenceStatus.EXHAUSTED
    assert all(a.executed_at is not None for a in seq.attempts)


def test_run_due_attempt_is_noop_when_nothing_due():
    client = FakeClient()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 5])
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)  # runs attempt 1
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS + 1)  # attempt 2 not due yet
    assert client.simulate_calls == 1  # second call was a no-op
    assert seq.status == SequenceStatus.ATTEMPT_SCHEDULED


# ---- Attempts NEVER count as recovered ----

def test_exhausted_sequence_has_no_recovery_claim():
    client = FakeClient()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 0, 0])
    for _ in range(3):
        seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    assert seq.status == SequenceStatus.EXHAUSTED
    assert seq.fallback_recovery_result is None  # nothing to observe — no link ever existed


# ---- trigger_fallback ----

def test_fallback_only_triggers_when_exhausted():
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)  # still PENDING
    guardrail_engine = GuardrailEngine()
    result = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine,
                                  FakeClient(), current_time=MIDDAY_DT)
    assert result.status == SequenceStatus.PENDING  # unchanged — refused, not EXHAUSTED yet
    assert result.fallback_execution_record is None


def test_fallback_executes_real_payment_link_when_exhausted_and_authorized():
    client = FakeClient()
    guardrail_engine = GuardrailEngine()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 0, 0])
    for _ in range(3):
        seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    assert seq.status == SequenceStatus.EXHAUSTED

    seq = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine, client, current_time=MIDDAY_DT)

    assert client.create_calls == 1
    assert seq.status == SequenceStatus.FALLBACK_TRIGGERED
    assert seq.fallback_execution_record["execution_status"] == "executed"
    assert seq.fallback_execution_record["action_type"] == "recovery_payment_link"
    assert seq.fallback_execution_record["razorpay_result"]["razorpay_payment_link_id"] == "plink_FAKE123"


def test_fallback_re_authorizes_and_can_be_refused():
    """Fresh authorization is genuinely re-run — e.g. outside contact hours,
    the fallback must NOT execute just because the original mandate_retry did."""
    client = FakeClient()
    guardrail_engine = GuardrailEngine()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 0, 0])
    for _ in range(3):
        seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)

    seq = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine, client,
                               current_time=LATE_NIGHT_DT)  # outside 09:00-20:00 contact window

    assert client.create_calls == 0  # never even attempted
    assert seq.status == SequenceStatus.EXHAUSTED  # left as-is, not fabricated as triggered
    assert "not_executed_reason" in seq.fallback_execution_record


def test_fallback_is_idempotent_once_triggered():
    """Calling trigger_fallback again after it already succeeded must not
    create a second real Payment Link."""
    client = FakeClient()
    guardrail_engine = GuardrailEngine()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 0, 0])
    for _ in range(3):
        seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    seq = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine, client, current_time=MIDDAY_DT)
    assert client.create_calls == 1

    seq = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine, client, current_time=MIDDAY_DT)
    assert client.create_calls == 1  # unchanged — status is no longer EXHAUSTED, so it's a no-op


# ---- check_fallback_recovery ----

def test_check_fallback_recovery_noop_before_fallback_triggered():
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)
    seq = ms.check_fallback_recovery(seq, FakeClient())
    assert seq.fallback_recovery_result is None
    assert seq.status == SequenceStatus.PENDING


def test_check_fallback_recovery_reflects_real_paid_status():
    client = FakeClient()
    guardrail_engine = GuardrailEngine()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 0, 0])
    for _ in range(3):
        seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    seq = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine, client, current_time=MIDDAY_DT)
    assert seq.status == SequenceStatus.FALLBACK_TRIGGERED

    seq = ms.check_fallback_recovery(seq, client)  # FakeClient.fetch_payment_link_status returns "paid"
    assert seq.status == SequenceStatus.FALLBACK_RECOVERED
    assert seq.fallback_recovery_result["recovery_status"] == "recovered"
    assert seq.fallback_recovery_result["amount_recovered"] > 0


def test_check_fallback_recovery_stays_pending_when_unpaid():
    unpaid_client = FakeClient()
    unpaid_client.fetch_payment_link_status = lambda link_id: {
        "status": "observed", "mode": "test", "http_status": 200,
        "razorpay_payment_link_id": link_id, "razorpay_status": "created",
        "amount": 100000, "amount_paid": 0, "timestamp": 0,
    }
    guardrail_engine = GuardrailEngine()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS, schedule_offset_days=[0, 0, 0])
    for _ in range(3):
        seq = ms.run_due_attempt(seq, unpaid_client, current_time=MIDDAY_TS)
    seq = ms.trigger_fallback(seq, SAMPLE_CASE, SAMPLE_DIAGNOSIS, guardrail_engine, unpaid_client, current_time=MIDDAY_DT)

    seq = ms.check_fallback_recovery(seq, unpaid_client)
    assert seq.status == SequenceStatus.FALLBACK_TRIGGERED  # NOT flipped to recovered
    assert seq.fallback_recovery_result["recovery_status"] == "pending"
    assert seq.fallback_recovery_result["amount_recovered"] == 0.0


# ---- Persistence ----

def test_save_and_load_sequence_round_trips(tmp_path):
    path = tmp_path / "sequences.json"
    client = FakeClient()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)

    assert save_sequence(seq, path=path)
    loaded = get_sequence(seq.case_id, path=path)

    assert loaded is not None
    assert loaded.case_id == seq.case_id
    assert loaded.status == seq.status
    assert len(loaded.attempts) == len(seq.attempts)
    assert loaded.attempts[0].executed_at == seq.attempts[0].executed_at
    assert loaded.attempts[0].raw_result == seq.attempts[0].raw_result


def test_missing_store_file_returns_empty_dict(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert load_all_sequences(path=path) == {}
    assert get_sequence("ANY_CASE", path=path) is None


def test_corrupted_store_file_fails_safe_not_crash(tmp_path):
    path = tmp_path / "corrupted.json"
    path.write_text("{not valid json!!")
    assert load_all_sequences(path=path) == {}  # never crashes, never fabricates state


def test_store_holds_multiple_independent_sequences(tmp_path):
    path = tmp_path / "sequences.json"
    seq_a = ms.start_sequence({"case_id": "CASE_A", "leakage_category": "failed_subscription", "amount_at_risk": 500.0},
                               current_time=MIDDAY_TS)
    seq_b = ms.start_sequence({"case_id": "CASE_B", "leakage_category": "failed_subscription", "amount_at_risk": 700.0},
                               current_time=MIDDAY_TS)
    save_sequence(seq_a, path=path)
    save_sequence(seq_b, path=path)

    all_seqs = load_all_sequences(path=path)
    assert set(all_seqs.keys()) == {"CASE_A", "CASE_B"}
    assert all_seqs["CASE_A"].amount_at_risk == 500.0
    assert all_seqs["CASE_B"].amount_at_risk == 700.0


# ---- to_dict / from_dict fidelity ----

def test_sequence_to_dict_is_json_safe():
    import json
    client = FakeClient()
    seq = ms.start_sequence(SAMPLE_CASE, current_time=MIDDAY_TS)
    seq = ms.run_due_attempt(seq, client, current_time=MIDDAY_TS)
    json.dumps(seq.to_dict())  # must not raise


def test_attempt_to_dict_contains_all_fields():
    attempt = RetryAttempt(attempt_number=1, scheduled_offset_days=0, scheduled_at=MIDDAY_TS)
    d = attempt.to_dict()
    assert set(d.keys()) == {"attempt_number", "scheduled_offset_days", "scheduled_at",
                              "executed_at", "result_source", "raw_result"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))