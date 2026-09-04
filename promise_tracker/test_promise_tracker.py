"""
RecoverAI — Promise-to-Pay Tracker: Tests

Run:
    python3 -m pytest test_promise_tracker.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recovery"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations", "razorpay"))

from datetime import datetime, date

import pytest

from promise_schema import PromiseStatus
from promise_store import PromiseStore
import promise_checker as pc


class FakeClient:
    """Deterministic test double — never touches the network."""
    def __init__(self, paid=False, amount_paid=0, raises=False):
        self._paid = paid
        self._amount_paid = amount_paid
        self._raises = raises
        self.fetch_calls = 0

    def fetch_payment_link_status(self, payment_link_id):
        self.fetch_calls += 1
        if self._raises:
            raise ConnectionError("simulated network failure")
        return {
            "status": "observed", "mode": "test", "http_status": 200,
            "razorpay_payment_link_id": payment_link_id,
            "razorpay_status": "paid" if self._paid else "created",
            "amount": 100000, "amount_paid": self._amount_paid if self._paid else 0,
            "timestamp": 0,
        }


@pytest.fixture
def store():
    s = PromiseStore(":memory:")
    yield s
    s.close()


# ---- record_promise ----

def test_record_promise_starts_pending(store):
    pid = pc.record_promise(store, "CASE001", "overdue_receivable", 500.0, "2026-09-05")
    promise = store.get_promise(pid)
    assert promise["status"] == PromiseStatus.PENDING.value
    assert promise["promised_amount"] == 500.0
    assert promise["promise_date"] == "2026-09-05"
    assert promise["payment_link_id"] is None


def test_record_promise_can_include_payment_link_immediately(store):
    pid = pc.record_promise(store, "CASE002", "checkout_abandonment", 300.0, "2026-09-01", payment_link_id="plink_ABC")
    promise = store.get_promise(pid)
    assert promise["payment_link_id"] == "plink_ABC"


def test_record_promise_rejects_missing_case_id(store):
    with pytest.raises(ValueError):
        store.record_promise("", "overdue_receivable", 500.0, "2026-09-05")


def test_record_promise_rejects_negative_amount(store):
    with pytest.raises(ValueError):
        store.record_promise("CASE003", "overdue_receivable", -10.0, "2026-09-05")


# ---- link_payment_link ----

def test_link_payment_link_attaches_without_changing_status(store):
    pid = pc.record_promise(store, "CASE004", "overdue_receivable", 500.0, "2026-09-05")
    pc.link_payment_link(store, pid, "plink_XYZ")
    promise = store.get_promise(pid)
    assert promise["payment_link_id"] == "plink_XYZ"
    assert promise["status"] == PromiseStatus.PENDING.value


def test_link_payment_link_raises_for_unknown_promise(store):
    with pytest.raises(ValueError):
        pc.link_payment_link(store, 9999, "plink_XYZ")


# ---- check_promise: HONORED only via real observation ----

def test_check_promise_honored_when_razorpay_confirms_paid(store):
    pid = pc.record_promise(store, "CASE005", "overdue_receivable", 500.0, "2026-09-05", payment_link_id="plink_PAID")
    client = FakeClient(paid=True, amount_paid=50000)
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 1))
    assert result["status"] == PromiseStatus.HONORED.value
    assert client.fetch_calls == 1

def test_check_promise_not_honored_when_paid_amount_is_less_than_promised(store):
    """A real payment for LESS than what was promised must NOT be marked
    honored — the commitment wasn't fully met."""
    pid = pc.record_promise(store, "CASE005B", "overdue_receivable", 988.80, "2026-09-05", payment_link_id="plink_UNDERPAID")
    client = FakeClient(paid=True, amount_paid=5000)  # ₹50.00 paid, ₹988.80 promised
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 1))
    assert result["status"] == PromiseStatus.PENDING.value  # NOT honored
    assert "988.80" in result["reason"] and "50.00" in result["reason"]


def test_check_promise_honored_when_paid_amount_exceeds_promised(store):
    """Overpayment should still count as honored — the commitment was more than met."""
    pid = pc.record_promise(store, "CASE005C", "overdue_receivable", 100.0, "2026-09-05", payment_link_id="plink_OVERPAID")
    client = FakeClient(paid=True, amount_paid=15000)  # ₹150.00 paid, ₹100.00 promised
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 1))
    assert result["status"] == PromiseStatus.HONORED.value


def test_check_promise_stays_pending_before_due_date_if_unpaid(store):
    pid = pc.record_promise(store, "CASE006", "overdue_receivable", 500.0, "2026-09-05", payment_link_id="plink_UNPAID")
    client = FakeClient(paid=False)
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 1))
    assert result["status"] == PromiseStatus.PENDING.value


def test_check_promise_never_honored_without_a_payment_link(store):
    """No link at all -> nothing to observe -> can never become HONORED, no matter what."""
    pid = pc.record_promise(store, "CASE007", "overdue_receivable", 500.0, "2026-09-05")
    result = pc.check_promise(store, pid, razorpay_client=None, current_time=datetime(2026, 9, 1))
    assert result["status"] == PromiseStatus.PENDING.value


# ---- check_promise: BROKEN only after genuine date passage ----

def test_check_promise_broken_after_due_date_with_no_payment(store):
    pid = pc.record_promise(store, "CASE008", "overdue_receivable", 500.0, "2026-09-05", payment_link_id="plink_UNPAID")
    client = FakeClient(paid=False)
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 10))  # after promise_date
    assert result["status"] == PromiseStatus.BROKEN.value
    assert "promise_date" in result["reason"] and "passed" in result["reason"]


def test_check_promise_broken_after_due_date_with_no_link_at_all(store):
    pid = pc.record_promise(store, "CASE009", "overdue_receivable", 500.0, "2026-09-05")
    result = pc.check_promise(store, pid, razorpay_client=None, current_time=datetime(2026, 9, 10))
    assert result["status"] == PromiseStatus.BROKEN.value


def test_check_promise_exactly_on_due_date_is_not_yet_broken(store):
    """promise_date itself is still within the promise — only the day AFTER counts as passed."""
    pid = pc.record_promise(store, "CASE010", "overdue_receivable", 500.0, "2026-09-05", payment_link_id="plink_UNPAID")
    client = FakeClient(paid=False)
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 5))
    assert result["status"] == PromiseStatus.PENDING.value


def test_check_promise_paid_after_due_date_is_still_honored_not_broken():
    """Payment confirmed check must take priority over date-passed check —
    a late-but-genuine payment should never be reported as broken."""
    store = PromiseStore(":memory:")
    pid = pc.record_promise(store, "CASE011", "overdue_receivable", 500.0, "2026-09-05", payment_link_id="plink_LATE_PAID")
    client = FakeClient(paid=True, amount_paid=50000)
    result = pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 10))  # after due date, but paid
    assert result["status"] == PromiseStatus.HONORED.value
    store.close()


# ---- Terminal statuses are stable ----

def test_check_promise_does_not_re_evaluate_honored(store):
    pid = pc.record_promise(store, "CASE012", "overdue_receivable", 500.0, "2026-09-05", payment_link_id="plink_PAID")
    client = FakeClient(paid=True, amount_paid=50000)
    pc.check_promise(store, pid, client, current_time=datetime(2026, 9, 1))
    assert store.get_promise(pid)["status"] == PromiseStatus.HONORED.value

    # Re-check with a client that would now report unpaid — should be ignored
    unpaid_client = FakeClient(paid=False)
    result = pc.check_promise(store, pid, unpaid_client, current_time=datetime(2026, 9, 20))
    assert result["status"] == PromiseStatus.HONORED.value  # unchanged
    assert unpaid_client.fetch_calls == 0  # never even called — terminal, not re-evaluated


def test_check_promise_does_not_re_evaluate_escalated(store):
    pid = pc.record_promise(store, "CASE013", "overdue_receivable", 500.0, "2026-09-05")
    pc.check_promise(store, pid, None, current_time=datetime(2026, 9, 10))  # -> BROKEN
    pc.escalate_promise(store, pid, "Merchant flagged for phone follow-up.")
    assert store.get_promise(pid)["status"] == PromiseStatus.ESCALATED.value

    result = pc.check_promise(store, pid, None, current_time=datetime(2026, 9, 20))
    assert result["status"] == PromiseStatus.ESCALATED.value  # unchanged, still terminal


# ---- escalate_promise ----

def test_escalate_only_valid_from_broken(store):
    pid = pc.record_promise(store, "CASE014", "overdue_receivable", 500.0, "2026-09-05")
    with pytest.raises(ValueError):
        pc.escalate_promise(store, pid, "too early")  # still PENDING, not BROKEN


def test_escalate_sets_reason(store):
    pid = pc.record_promise(store, "CASE015", "overdue_receivable", 500.0, "2026-09-05")
    pc.check_promise(store, pid, None, current_time=datetime(2026, 9, 10))
    pc.escalate_promise(store, pid, "Calling customer directly.")
    promise = store.get_promise(pid)
    assert promise["status"] == PromiseStatus.ESCALATED.value
    assert promise["reason"] == "Calling customer directly."


def test_escalate_raises_for_unknown_promise(store):
    with pytest.raises(ValueError):
        pc.escalate_promise(store, 9999, "reason")


# ---- Store: querying ----

def test_get_promises_for_case_returns_only_that_case(store):
    pc.record_promise(store, "CASE_A", "overdue_receivable", 100.0, "2026-09-05")
    pc.record_promise(store, "CASE_A", "overdue_receivable", 200.0, "2026-10-01")
    pc.record_promise(store, "CASE_B", "overdue_receivable", 300.0, "2026-09-05")

    a_promises = store.get_promises_for_case("CASE_A")
    assert len(a_promises) == 2
    assert all(p["case_id"] == "CASE_A" for p in a_promises)


def test_get_all_promises_filters_by_status(store):
    p1 = pc.record_promise(store, "CASE_X", "overdue_receivable", 100.0, "2026-09-05")
    p2 = pc.record_promise(store, "CASE_Y", "overdue_receivable", 200.0, "2026-09-01")
    pc.check_promise(store, p2, None, current_time=datetime(2026, 9, 10))  # -> BROKEN

    pending = store.get_all_promises(status=PromiseStatus.PENDING)
    broken = store.get_all_promises(status=PromiseStatus.BROKEN)
    assert len(pending) == 1 and pending[0]["case_id"] == "CASE_X"
    assert len(broken) == 1 and broken[0]["case_id"] == "CASE_Y"


def test_count_by_status(store):
    pc.record_promise(store, "CASE_1", "overdue_receivable", 100.0, "2026-09-05")
    pc.record_promise(store, "CASE_2", "overdue_receivable", 100.0, "2026-09-05")
    p3 = pc.record_promise(store, "CASE_3", "overdue_receivable", 100.0, "2026-09-01")
    pc.check_promise(store, p3, None, current_time=datetime(2026, 9, 10))

    counts = store.count_by_status()
    assert counts.get("pending") == 2
    assert counts.get("broken") == 1


def test_get_promise_returns_none_for_unknown_id(store):
    assert store.get_promise(9999) is None


# ---- Persistence round-trip (real file, not :memory:) ----

def test_promises_persist_across_store_reconnection(tmp_path):
    path = str(tmp_path / "promises_test.db")
    store1 = PromiseStore(path)
    pid = pc.record_promise(store1, "CASE_PERSIST", "overdue_receivable", 750.0, "2026-09-05")
    store1.close()

    store2 = PromiseStore(path)
    promise = store2.get_promise(pid)
    assert promise is not None
    assert promise["case_id"] == "CASE_PERSIST"
    assert promise["promised_amount"] == 750.0
    store2.close()


# ---- No ground-truth leakage ----

def test_promise_tracker_never_reads_ground_truth_columns():
    for fname in ("promise_checker.py", "promise_store.py", "promise_schema.py"):
        src = open(os.path.join(os.path.dirname(__file__), fname), encoding="utf-8").read()
        for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome"):
            assert forbidden not in src, f"{forbidden} referenced in {fname}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))