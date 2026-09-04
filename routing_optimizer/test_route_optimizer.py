"""
RecoverAI — Payment Routing Optimizer: Tests

Run:
    python3 -m pytest test_route_optimizer.py -v
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import math
import pytest

from route_catalog import PaymentRoute
from route_optimizer import select_optimal_route, RoutingDecision


def _case(case_id="CASE_TEST", payment_method="Card", failure_reason=None):
    return {"case_id": case_id, "payment_method": payment_method, "failure_reason": failure_reason}


# ---- Determinism ----

def test_same_input_always_produces_same_decision():
    case = _case(failure_reason="bank decline")
    d1 = select_optimal_route(case)
    d2 = select_optimal_route(case)
    assert d1.recommended_route == d2.recommended_route
    assert d1.recommended_route_score == d2.recommended_route_score


# ---- Each real failure_reason routes sensibly ----

def test_insufficient_funds_prefers_direct_link_over_same_method():
    d = select_optimal_route(_case(failure_reason="insufficient funds"))
    assert d.recommended_route == PaymentRoute.DIRECT_PAYMENT_LINK
    assert d.is_real_executable is True


def test_bank_decline_prefers_switching_to_upi():
    d = select_optimal_route(_case(payment_method="Card", failure_reason="bank decline"))
    assert d.recommended_route == PaymentRoute.SWITCH_TO_UPI
    assert d.is_real_executable is False


def test_network_failure_prefers_retry_same_method():
    d = select_optimal_route(_case(failure_reason="network failure"))
    assert d.recommended_route == PaymentRoute.RETRY_SAME_METHOD
    assert d.is_real_executable is False


def test_upi_timeout_prefers_switching_away_from_upi():
    d = select_optimal_route(_case(payment_method="UPI", failure_reason="UPI timeout"))
    assert d.recommended_route in (PaymentRoute.SWITCH_TO_CARD, PaymentRoute.SWITCH_TO_NETBANKING)
    assert d.recommended_route != PaymentRoute.RETRY_SAME_METHOD  # never re-recommend the method that just timed out


def test_authentication_failure_prefers_netbanking():
    d = select_optimal_route(_case(payment_method="Card", failure_reason="authentication failure"))
    assert d.recommended_route == PaymentRoute.SWITCH_TO_NETBANKING


# ---- Unknown / missing failure_reason ----

def test_missing_failure_reason_defaults_to_direct_link():
    d = select_optimal_route(_case(failure_reason=None))
    assert d.recommended_route == PaymentRoute.DIRECT_PAYMENT_LINK
    assert d.is_real_executable is True


def test_nan_failure_reason_handled_same_as_none():
    """pandas reads an empty CSV cell as float NaN, not None — must not crash."""
    d = select_optimal_route(_case(failure_reason=float("nan")))
    assert d.recommended_route == PaymentRoute.DIRECT_PAYMENT_LINK


def test_unrecognized_failure_reason_falls_back_to_default():
    d = select_optimal_route(_case(failure_reason="some_new_reason_not_in_catalog"))
    assert d.recommended_route == PaymentRoute.DIRECT_PAYMENT_LINK


# ---- is_real_executable is ONLY true for direct_payment_link ----

def test_is_real_executable_only_true_for_direct_payment_link():
    for reason in ("insufficient funds", "bank decline", "network failure", "UPI timeout",
                   "authentication failure", None):
        d = select_optimal_route(_case(failure_reason=reason))
        assert d.is_real_executable == (d.recommended_route == PaymentRoute.DIRECT_PAYMENT_LINK)


# ---- Alternatives / explainability ----

def test_decision_includes_rationale():
    d = select_optimal_route(_case(failure_reason="bank decline"))
    assert d.rationale and len(d.rationale) > 10


def test_decision_lists_alternatives_considered():
    d = select_optimal_route(_case(failure_reason="bank decline"))
    assert isinstance(d.alternatives_considered, list)
    assert all("route" in a and "score" in a and "rationale" in a for a in d.alternatives_considered)
    # the recommended route itself must not also appear in "alternatives"
    assert d.recommended_route.value not in [a["route"] for a in d.alternatives_considered]


def test_decision_records_original_payment_method():
    d = select_optimal_route(_case(payment_method="Wallet", failure_reason="network failure"))
    assert d.original_payment_method == "Wallet"


def test_decision_records_case_id():
    d = select_optimal_route(_case(case_id="CASE99999"))
    assert d.case_id == "CASE99999"


# ---- to_dict / JSON safety ----

def test_to_dict_is_json_safe():
    import json
    d = select_optimal_route(_case(failure_reason="UPI timeout"))
    json.dumps(d.to_dict())  # must not raise


def test_to_dict_route_is_plain_string_not_enum():
    d = select_optimal_route(_case(failure_reason="bank decline"))
    result = d.to_dict()
    assert isinstance(result["recommended_route"], str)
    assert result["recommended_route"] == "switch_to_upi"


# ---- Works against an object with .get(), matching real dataset row shape ----

def test_accepts_dict_like_case_row():
    class FakeRow(dict):
        pass
    row = FakeRow(case_id="CASE_ROW", payment_method="Card", failure_reason="bank decline")
    d = select_optimal_route(row)
    assert d.case_id == "CASE_ROW"
    assert d.recommended_route == PaymentRoute.SWITCH_TO_UPI


# ---- No ground-truth leakage ----

def test_routing_optimizer_never_reads_ground_truth_columns():
    for fname in ("route_optimizer.py", "route_catalog.py"):
        src = open(os.path.join(os.path.dirname(__file__), fname), encoding="utf-8").read()
        for forbidden in ("ground_truth_recoverable", "ground_truth_recovery_outcome",
                           "recovery_observed", "recovery_reason"):
            assert forbidden not in src, f"{forbidden} referenced in {fname}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))