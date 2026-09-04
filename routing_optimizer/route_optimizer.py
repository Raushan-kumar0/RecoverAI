"""
RecoverAI — Payment Routing Optimizer: Decision Logic (Extension, post-Step 12)

Given a failed payment case, recommends which payment route to try next —
grounded in the case's REAL `failure_reason` and `payment_method` columns
(existing, non-ground-truth dataset fields, already used as model features
elsewhere in this project — see diagnosis/feature_config.py).

===================================================================
HONESTY STATEMENT (read this before wiring into anything real):
===================================================================
This module answers the track brief's "optimize payment routing" concept
as a DECISION-QUALITY improvement, not as a claim that Razorpay's Test Mode
API produces different, verifiable outcomes per payment method:

  - Razorpay's own documentation states UPI Payment Links are NOT supported
    in Test Mode ("UPI Payment Links will work only in Live Mode").
  - Standard Payment Links are method-agnostic on Razorpay's real checkout
    page — the customer picks their method there; this project cannot
    force or verify a specific method was used from the merchant side via
    the API surface it has access to.
  - Therefore: the ROUTING DECISION here is real, deterministic, and
    explainable (see route_catalog.py's heuristics, grounded in actual
    dataset failure_reason distributions). What happens AFTER the decision
    depends on which route is chosen:
      * DIRECT_PAYMENT_LINK -> executes a REAL recovery_payment_link,
        through the SAME execute_guardrail_approved_action() (Step 7) used
        everywhere else, checked via the SAME observe_recovery() (Step 10).
        This route can genuinely reach RECOVERED.
      * Any other route (RETRY_SAME_METHOD, SWITCH_TO_UPI/CARD/NETBANKING)
        -> honestly SIMULATED, same as mandate_retry/payment_retry, because
        there is no real API this project has access to that can force or
        verify a specific payment method's outcome differently in Test
        Mode. These routes are never marked recovered by themselves.

No ground-truth field is ever read here.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from route_catalog import PaymentRoute, RouteOption, ROUTING_HEURISTICS, DEFAULT_ROUTE_OPTIONS


@dataclass
class RoutingDecision:
    case_id: str
    original_payment_method: Optional[str]
    failure_reason: Optional[str]
    recommended_route: PaymentRoute
    recommended_route_score: float
    rationale: str
    alternatives_considered: List[Dict[str, Any]]
    is_real_executable: bool  # True only for DIRECT_PAYMENT_LINK — see honesty statement above

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "original_payment_method": self.original_payment_method,
            "failure_reason": self.failure_reason,
            "recommended_route": self.recommended_route.value,
            "recommended_route_score": self.recommended_route_score,
            "rationale": self.rationale,
            "alternatives_considered": self.alternatives_considered,
            "is_real_executable": self.is_real_executable,
        }


def _get(case, field_name, default=None):
    if hasattr(case, "get"):
        return case.get(field_name, default)
    return getattr(case, field_name, default)


def select_optimal_route(case) -> RoutingDecision:
    """
    Pure, deterministic decision function — same case in, same decision out,
    every time (no randomness, no hidden state). Reads ONLY failure_reason
    and payment_method from the case, both real dataset columns.
    """
    case_id = _get(case, "case_id")
    payment_method = _get(case, "payment_method")
    failure_reason = _get(case, "failure_reason")

    # pandas reads a genuinely empty CSV cell as float NaN, not None/""; treat
    # both consistently as "no failure_reason recorded" rather than crash on it.
    if failure_reason is None or (isinstance(failure_reason, float) and failure_reason != failure_reason):
        failure_reason = None

    options: List[RouteOption] = ROUTING_HEURISTICS.get(failure_reason, DEFAULT_ROUTE_OPTIONS)
    best = max(options, key=lambda o: o.score)
    alternatives = [
        {"route": o.route.value, "score": o.score, "rationale": o.rationale}
        for o in options if o.route != best.route
    ]

    return RoutingDecision(
        case_id=case_id,
        original_payment_method=payment_method,
        failure_reason=failure_reason,
        recommended_route=best.route,
        recommended_route_score=best.score,
        rationale=best.rationale,
        alternatives_considered=alternatives,
        is_real_executable=(best.route == PaymentRoute.DIRECT_PAYMENT_LINK),
    )