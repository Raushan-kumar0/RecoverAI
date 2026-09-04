# RecoverAI — Payment Routing Optimizer (Extension, post-Step 12)

## 1. Scope

Given a failed payment, recommends which route to try next — grounded in
the case's REAL `failure_reason` and `payment_method` (existing, non-
ground-truth dataset columns already used as model features in
`diagnosis/feature_config.py`). Answers the Track 03 "optimize payment
routing" requirement as a decision-quality improvement.

## 2. Files
```
routing_optimizer/
  route_catalog.py        PaymentRoute enum + failure_reason -> route heuristics (documented assumptions)
  route_optimizer.py       select_optimal_route() — pure, deterministic decision function
  test_route_optimizer.py  18 tests
  README.md                 this file
```

## 3. Read this before assuming more than is actually here

Razorpay's Test Mode has a real, documented limitation: **UPI Payment Links
are not supported in Test Mode** ("UPI Payment Links will work only in Live
Mode"). Standard Payment Links are method-agnostic — the customer picks
their method on Razorpay's own checkout page; nothing on the merchant/API
side can force or verify which method they used.

Given that constraint, this module is honest about what it can and cannot
prove:

- **The routing DECISION is real** — deterministic, explainable, grounded
  in the case's actual `failure_reason` (verified against
  `data/recoverai_cases.csv`'s real distribution: insufficient funds, bank
  decline, network failure, UPI timeout, authentication failure).
- **The scoring weights are a stated, documented ASSUMPTION** (a common
  domain heuristic — e.g. a UPI timeout doesn't mean a card would fail the
  same way), NOT measured from real historical route-switch outcomes,
  because this project has no such data. Same honesty precedent as
  `measurement_models.py`'s own documented assumptions (e.g. `recovery_cost`
  being explicitly noted as unmodeled rather than silently treated as $0).
- **Only ONE recommended route is ever genuinely executable and
  verifiable**: `direct_payment_link` — because that's a real
  `recovery_payment_link` execution, checked the same way as everywhere
  else in this project (`observe_recovery()`). Every other route
  (`retry_same_method`, `switch_to_upi`, `switch_to_card`,
  `switch_to_netbanking`) is honestly SIMULATED when executed — same
  pattern as `mandate_retry`/`payment_retry` — because there is no real,
  verifiable API this project can call to force or observe a specific
  payment method's outcome differently in Test Mode.

`RoutingDecision.is_real_executable` makes this explicit and checkable in
code — `True` only for `direct_payment_link`, `False` for everything else.

## 4. Example (against real dataset rows)

```
CASE00002  UPI         fail=UPI timeout            -> switch_to_card       (real=False)
CASE00038  Card        fail=authentication failure -> switch_to_netbanking (real=False)
CASE00008  UPI         fail=bank decline           -> switch_to_upi        (real=False)
CASE00039  UPI         fail=insufficient funds     -> direct_payment_link  (real=True)
CASE00035  Netbanking  fail=network failure        -> retry_same_method    (real=False)
```

## 5. What this does NOT do

- Does not modify anything in Steps 1–12, the mandate sequencer, or the
  promise tracker.
- Does not claim Razorpay's API behaves differently per payment method in
  this project's Test Mode environment — it cannot, and this README says
  so explicitly rather than implying otherwise.
- Does not read any ground-truth field.