"""
RecoverAI — Payment Routing Optimizer: Route Catalog (Extension, post-Step 12)

Defines the candidate payment routes a failed payment could be retried
through, and the heuristic mapping from a case's REAL `failure_reason`
(an existing, non-ground-truth dataset column — already used as a model
feature in diagnosis/feature_config.py) to which route(s) are worth trying.

IMPORTANT HONESTY NOTE: Razorpay's Payment Links product does not expose a
way to force a genuinely DIFFERENT observable outcome per payment method in
Test Mode (UPI Payment Links specifically are documented as unsupported in
Test Mode: "UPI Payment Links will work only in Live Mode"). This module's
routing DECISION is real, deterministic, and explainable — grounded in the
dataset's actual failure_reason distribution — but it is a decision-quality
improvement, not a claim that Razorpay's API behaves differently per route
in this environment. See route_optimizer.py's module docstring for the
full honesty statement.

The scoring weights below are a documented, stated ASSUMPTION (a common
domain heuristic: e.g. a UPI-specific timeout is unlikely to recur if the
customer is prompted to pay via a different rail instead) — NOT derived
from any real historical route-switch success data, because this project
has none. This mirrors measurement_models.py's own precedent of stating
assumptions explicitly rather than presenting them as measured fact.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List


class PaymentRoute(str, Enum):
    RETRY_SAME_METHOD = "retry_same_method"
    SWITCH_TO_UPI = "switch_to_upi"
    SWITCH_TO_CARD = "switch_to_card"
    SWITCH_TO_NETBANKING = "switch_to_netbanking"
    DIRECT_PAYMENT_LINK = "direct_payment_link"  # method-agnostic — let the customer pick on Razorpay's real checkout


@dataclass
class RouteOption:
    route: PaymentRoute
    score: float               # 0.0-1.0, relative — see route_optimizer.py for how this is combined with method history
    rationale: str


# failure_reason -> ordered list of (route, base_score, rationale), grounded in
# the dataset's actual failure_reason categories (verified against
# data/recoverai_cases.csv: insufficient funds, bank decline, network failure,
# UPI timeout, authentication failure).
ROUTING_HEURISTICS = {
    "insufficient funds": [
        RouteOption(PaymentRoute.RETRY_SAME_METHOD, 0.3,
                    "Insufficient funds is a balance problem, not a method problem — "
                    "switching payment method alone is unlikely to help immediately."),
        RouteOption(PaymentRoute.DIRECT_PAYMENT_LINK, 0.6,
                    "A fresh, direct link gives the customer time and flexibility to pay "
                    "once funds are available, via whichever method suits them then."),
    ],
    "bank decline": [
        RouteOption(PaymentRoute.SWITCH_TO_UPI, 0.7,
                    "A bank/issuer-side decline on card is often specific to that card's "
                    "issuer or network — UPI routes through a different rail entirely."),
        RouteOption(PaymentRoute.RETRY_SAME_METHOD, 0.2,
                    "Retrying the same card against the same declining issuer has low "
                    "expected uplift without a method change."),
    ],
    "network failure": [
        RouteOption(PaymentRoute.RETRY_SAME_METHOD, 0.6,
                    "Network failures are typically transient (customer's connection, "
                    "gateway timeout) — a same-method retry is often sufficient."),
        RouteOption(PaymentRoute.DIRECT_PAYMENT_LINK, 0.4,
                    "If retries continue failing, a direct link removes any dependency "
                    "on the original session/connection state entirely."),
    ],
    "UPI timeout": [
        RouteOption(PaymentRoute.SWITCH_TO_CARD, 0.6,
                    "A UPI-specific timeout (app not responding, PSP delay) doesn't "
                    "indicate a card would fail the same way."),
        RouteOption(PaymentRoute.SWITCH_TO_NETBANKING, 0.5,
                    "Netbanking is a reasonable alternate rail, avoiding the UPI app "
                    "dependency entirely."),
    ],
    "authentication failure": [
        RouteOption(PaymentRoute.SWITCH_TO_NETBANKING, 0.5,
                    "Netbanking's authentication flow (bank login) differs from card "
                    "3D-Secure/OTP — may avoid whatever caused the original auth failure."),
        RouteOption(PaymentRoute.RETRY_SAME_METHOD, 0.4,
                    "A one-off OTP/authentication hiccup may simply succeed on retry."),
    ],
}

DEFAULT_ROUTE_OPTIONS: List[RouteOption] = [
    RouteOption(PaymentRoute.DIRECT_PAYMENT_LINK, 0.5,
                "No specific failure_reason recorded for this case — a direct, "
                "method-agnostic Payment Link is the safest default: it lets the "
                "customer choose whichever method works for them, on Razorpay's "
                "real checkout page."),
]