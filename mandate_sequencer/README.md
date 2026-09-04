# RecoverAI — Mandate Retry Sequencer (Extension, post-Step 12)

## 0. Why this matters for the brief

Razorpay's mandate infrastructure covers BOTH recurring subscriptions and
EMI (Equated Monthly Installment) schedules identically underneath — both
are "a pre-authorized recurring charge that failed and needs retrying."
This module is therefore not subscription-specific:

- **Involuntary churn** — a subscriber didn't choose to leave; their card
  expired or a bank declined a routine charge. This sequencer is a direct
  answer to reducing that: instead of silently losing the customer after
  one failed charge, it retries on a schedule, then falls back to a real,
  payable link before giving up.
- **Failed EMI mandates** — a customer's EMI installment failing has the
  exact same structure: a recurring mandate charge that didn't go through.
  The same retry-then-fallback sequence applies without any code change —
  only the leakage_category differs (this project models it via
  `failed_subscription`, but the mechanism generalizes to any failed
  recurring/EMI mandate).

## 1. Scope

Extends `mandate_retry` from a single simulated attempt (Step 7) into a
scheduled, multi-attempt sequence, with an automatic real fallback once
attempts are exhausted. Answers the Track 03 "Mandate retry sequencer"
example direction honestly, without pretending Razorpay has an endpoint it
doesn't have.

**Adds no new Razorpay capability.** Every individual retry attempt is
still exactly `client.simulate_retry_operation("mandate_retry", ...)` —
the same already-tested Step 7 call. This module only adds scheduling and
tracking on top, plus one genuinely new real action: the fallback.

## 2. Files
```
mandate_sequencer/
  sequencer_models.py      RetryAttempt, MandateRetrySequence, SequenceStatus
  sequencer_store.py       JSON persistence, keyed by case_id (mutable — NOT append-only)
  mandate_sequencer.py      start_sequence / run_due_attempt / trigger_fallback / check_fallback_recovery
  test_mandate_sequencer.py 20 tests
  README.md                 this file
```

Sequence state lives in `mandate_retry_sequences.json` at the project root —
deliberately **separate** from `real_results_log.jsonl`, so retry-attempt
bookkeeping (which can never itself be "recovered") is structurally
incapable of being summed into genuine recovered revenue.

## 3. The sequence lifecycle

```
start_sequence()
  -> PENDING (3 attempts scheduled: day 0, day 3, day 7 by default)

run_due_attempt()   [call repeatedly; no-ops if nothing is due yet]
  -> ATTEMPT_SCHEDULED (some attempts remain)
  -> EXHAUSTED (all attempts run — mandate_retry attempts can NEVER
     self-report success; there is nothing to observe)

trigger_fallback()   [only acts when status == EXHAUSTED]
  -> re-authorizes a NEW recovery_payment_link decision from scratch via
     GuardrailEngine.authorize() (never reuses the original mandate_retry
     approval — a different action type earns its own authorization,
     same principle Step 9's fallback handling already follows)
  -> if AUTO_EXECUTE: FALLBACK_TRIGGERED (a REAL Payment Link now exists,
     created via the same execute_guardrail_approved_action() everyone
     else uses)
  -> if not AUTO_EXECUTE (e.g. outside contact hours right now): sequence
     stays EXHAUSTED, nothing fabricated, safe to call again later

check_fallback_recovery()   [only acts once FALLBACK_TRIGGERED]
  -> calls the SAME observe_recovery() (Step 10) used everywhere else
  -> FALLBACK_RECOVERED only if Razorpay itself reports 'paid'
```

## 4. Why re-authorization matters

The fallback is a genuinely different action (`recovery_payment_link`,
`money_movement=False`, real Razorpay call) from the original
(`mandate_retry`, simulated). It is never assumed safe just because the
original attempt sequence was approved — `trigger_fallback()` builds a
fresh `Decision` using the REAL, locked Step 4 catalog metadata for
`recovery_payment_link` (via `action_catalog.ACTION_CATALOG`, not a
hand-approximated dict) and runs it through `GuardrailEngine.authorize()`
again, at the CURRENT time. A case that would have failed contact-hours or
confidence checks right now correctly stays `EXHAUSTED`, not silently
executed.

## 5. What this does NOT do

- Does not modify, retrain, or bypass anything in Steps 1–11.
- Does not add a new Razorpay endpoint or pretend one exists.
- Does not let a sequence reach RECOVERED without a real, independently
  observed 'paid' status — same rule as every other part of this project.
- Does not run on a real clock/scheduler — `current_time` is always passed
  in explicitly (matching the rest of the project's testing convention),
  so advancing a sequence in a demo means passing a later `current_time`,
  not literally waiting days.