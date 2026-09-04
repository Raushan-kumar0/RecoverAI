# RecoverAI — Promise-to-Pay Tracker (Extension, post-Step 12)

## 1. Scope

Tracks a customer's stated commitment to pay a specific amount by a
specific future date, and independently verifies whether they honored it —
without ever inferring, guessing, or assuming the outcome.

## 2. Files
```
promise_tracker/
  promise_schema.py       DDL + PromiseStatus enum
  promise_store.py        SQLite wrapper (matches audit_store.py conventions)
  promise_checker.py       record_promise / link_payment_link / check_promise / escalate_promise
  test_promise_tracker.py  24 tests
  README.md                this file
```

State lives in `promise_tracker.db`, a real SQLite table — unlike
`real_results_log.jsonl` (append-only), promises need genuine status
UPDATES over time (pending → honored/broken/escalated).

## 3. The state machine

```
record_promise()
  -> PENDING

check_promise()   [call any time; safe to call repeatedly]
  -> if a Payment Link is attached AND Razorpay confirms 'paid'
     (via the SAME observe_recovery() Step 10 uses everywhere else):
       -> HONORED   (terminal — never re-evaluated again)
  -> else if promise_date has genuinely passed (as of an explicitly
     passed current_time, never the implicit real clock):
       -> BROKEN
  -> else:
       -> stays PENDING, unchanged

escalate_promise()   [only valid from BROKEN; a human/merchant decision]
  -> ESCALATED   (terminal — never re-evaluated again)
```

## 4. Why HONORED requires a real Payment Link

A promise with no linked Payment Link at all has nothing to independently
verify — it can only ever sit `PENDING` (before the date) or become
`BROKEN` (after the date passes with nothing confirmed). It can never
reach `HONORED` without a real, checkable Payment Link reporting `paid`,
same rule as every other "recovered" claim in this project.

## 5. Why a late payment still counts as HONORED, not BROKEN

`check_promise()` checks payment status BEFORE checking whether the date
has passed. A customer who pays a few days late is still honoring their
commitment in substance — the promise tracker reflects that, rather than
mechanically marking anything past its date as broken regardless of
outcome. (Covered explicitly by
`test_check_promise_paid_after_due_date_is_still_honored_not_broken`.)

## 6. What this does NOT do

- Does not modify anything in Steps 1–12 or the mandate sequencer.
- Does not send any real message/reminder to a customer — recording a
  promise is a manual/internal action (there's no real customer-facing
  promise-capture form in this project); linking a real Payment Link to a
  promise still goes through the exact same `execute_guardrail_approved_action()`
  as everywhere else when one is created.
- Does not automatically escalate — that's a deliberate human decision,
  never inferred by the checker itself.