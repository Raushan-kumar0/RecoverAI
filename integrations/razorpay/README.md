# RecoverAI — Razorpay Test-Mode Integration (Step 7)

## 1. Scope
The first and only step permitted to touch Razorpay. Connects a
Guardrail-authorized (Step 6) recommendation to safe sandbox execution.
**Test mode only. Never live money. Never bypasses authorization.**

## 2. Files
```
recoverai/integrations/razorpay/
  razorpay_config.py         env-var credential loading + validation, unconditional live-key rejection
  razorpay_client.py           HTTP client: 1 real operation (Payment Links) + 1 documented simulation (retries)
  razorpay_execution.py         orchestration: enforces guardrail outcome, identity matching, action scope
  test_razorpay_integration.py   27 tests
  .env.example                    credential name template — NO real values
  README.md                        this file
../../.gitignore                   updated at project root to exclude .env files
```

## 3. Why only ONE real API operation
Before writing any code, Razorpay's public documentation was checked (per
Step 1 rule 8 — verify capability before implementing):
- **Payment Links API** (`POST /v1/payment_links`) is genuinely supported,
  documented, and safe in test mode (up to 30 links per business in test
  mode, no live consequences). ✅ Implemented as a real call.
- **Payments API** explicitly states it can only *retrieve* payment details
  or move a payment from `authorized`→`captured` — it has no "retry this
  failed charge" endpoint. ✅ Confirmed no such capability exists.
- **Subscription retries** are automatic and server-scheduled by Razorpay
  (T+1, T+2, T+3 days) — not something a merchant triggers on demand via API.
  Test mode does let you simulate a *manual charge attempt result*
  (success/failure) through the dashboard's "test charge" tool, but this is
  a dashboard testing aid, not a programmatic "retry now" endpoint a
  production integration would call. ✅ Confirmed no such capability exists.

Per the Step 1 rule ("if a real-world money action cannot safely or
realistically be performed in test mode, design an honest bounded simulation
around the actual API capability rather than pretending it happened"):
- `recovery_payment_link` → **real** Razorpay Test Mode API call.
- `payment_retry` / `mandate_retry` → **documented, clearly labeled
  simulation** (`status="simulated"`, `result_source="bounded_simulation"`),
  never a real network call, never claimed as a genuine Razorpay result.

## 4. Credential handling
- Loaded exclusively from `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`
  environment variables — never hardcoded, never read from a committed file.
- `key_id` must start with `rzp_test_`. A `rzp_live_` key is **rejected
  unconditionally**, at two independent points (`load_config_from_env` and
  again defensively in `RazorpayTestModeClient.__init__`) — not overridable
  by any flag.
- `key_secret` must be present and at least 8 characters.
- Every loggable/error representation uses `RazorpayConfig.redacted()`,
  which shows only the first 12 characters of `key_id` and never the secret.
  Verified by `test_redacted_never_exposes_secret` and
  `test_no_secret_leakage_in_error_output`.
- `.env.example` documents the required variable *names* only — see §9 for
  exact local setup instructions. `.gitignore` at the project root excludes
  `.env` and any `*.env` file (with `.env.example` explicitly un-ignored).

## 5. Safety: DRY_RUN by default
`RECOVERAI_RAZORPAY_DRY_RUN` defaults to `true` even if unset. In dry-run
mode, `create_payment_link` makes **no network call** and instead returns
the exact request that *would* be sent (`method`, `url`, `payload`, redacted
auth) with `status="dry_run"`. A real call only happens if this is
explicitly set to `"false"` — and even then, a live key is still refused
(§4). This means the integration can be fully tested, demoed, and inspected
without any risk of an accidental real call.

## 6. Execution flow (enforced in `razorpay_execution.py`)
```
Step 5 Decision + Step 6 GuardrailDecision + case
        |
        v
1. guardrail_decision.outcome == AUTO_EXECUTE ?  --no--> REFUSE (not_executed)
        |yes
        v
2. decision.decision_status == "recommended" ?    --no--> REFUSE
        |yes
        v
3. case_id matches across case/decision/guardrail? --no--> REFUSE
        |yes
        v
4. decision's action_type == guardrail's action_type? --no--> REFUSE
        |yes
        v
5. action_type in {payment_retry, mandate_retry,
   recovery_payment_link}?                          --no--> REFUSE (out of scope)
        |yes
        v
6. Dispatch: recovery_payment_link -> real API call (or dry-run)
             payment_retry/mandate_retry -> bounded_simulation
        |
        v
   ExecutionRecord (structured, always returned, never raises uncaught)
```
`APPROVAL_REQUIRED` and `STOP` are refused **identically** at step 1 — this
integration never distinguishes "not yet approved" from "explicitly
stopped" in terms of whether it acts; both mean "do not execute." A real
merchant-approval-then-execute flow would need to re-run Step 6 to produce a
fresh `AUTO_EXECUTE` outcome before reaching this function; that
re-authorization workflow is not built in Step 7.

## 7. Result-source labeling (never misrepresented)
| `result_source` | Meaning |
|---|---|
| `razorpay_test_mode_dry_run` | No network call made (default safety) |
| `razorpay_test_mode_api` | A real Razorpay **TEST MODE** API call was attempted |
| `bounded_simulation` | `payment_retry`/`mandate_retry` — no real endpoint exists (§3) |
| `not_executed` | Refused before any call — STOP, APPROVAL_REQUIRED, mismatch, or unsupported action |

This is structurally separate from the Step 2 dataset's
`ground_truth_recovery_outcome` (a synthetic label for model evaluation) and
from any future live-mode result (which this module can never produce —
§4). No code path in this project ever conflates these three concepts.

## 8. Leakage prevention
`razorpay_execution.py` reads only `case_id`, `leakage_category`, and
`amount_at_risk` from `case`, plus `action_type`/`money_movement`/etc. from
the already-authorized `decision`/`guardrail_decision` objects. It never
reads `amount_recovered`, `ground_truth_recoverable`,
`ground_truth_recovery_outcome`, `recovery_observed`, or `recovery_reason` —
by construction (those fields are never referenced anywhere in the module).

## 9. Tests (27/27 passing)
Covers: missing credentials, malformed key format, too-short secret,
unconditional live-key rejection (including when dry-run is explicitly
disabled, and again at client construction as defense-in-depth), dry-run
default behavior and explicit override, redacted-secret guarantees, no
network call in dry-run, honest simulation labeling for retry actions, STOP
and APPROVAL_REQUIRED both preventing execution, AUTO_EXECUTE permitting
both the real-call path and the simulated path, unsupported-action
rejection, action-type and case_id mismatch rejection (never executing an
action other than the one recommended/authorized), non-`recommended`
decision status rejection, safe handling of an invalid amount (no crash),
no secret leakage anywhere in output, base URL always pointing at the
Razorpay API host, and deterministic request-payload construction (amount
paise conversion, currency).

## 10. Regression
Step 2 dataset validation: 22/22. Step 3 tests: 17/17 (model artifact
checksum unchanged — not retrained). Step 4 tests: 29/29. Step 5 tests:
21/21. Step 6 tests: 31/31.

## 11. Limitations
- **No real network call could be completed in this build environment.**
  The sandbox's egress proxy blocks `api.razorpay.com` entirely
  (`x-deny-reason: host_not_allowed`, confirmed via direct `curl` test) —
  this is unrelated to credentials or code correctness. With `dry_run=false`
  and fake test-shaped credentials, the client correctly attempted the real
  HTTP call, received the proxy's block as an HTTP 403, and returned a safe
  structured `api_error` result rather than crashing or claiming success.
  This proves the code path is correct; it has not been proven against a
  live Razorpay test-mode endpoint. See §9 (task report) for exact
  instructions to verify this yourself in an environment with real network
  access and real credentials.
- `payment_retry`/`mandate_retry` are honest simulations, not real API
  calls, for the documented reason in §3 — this is a deliberate design
  choice, not a shortcut.
- No idempotency-key replay/dedup logic beyond generating one per call —
  a production integration would need to persist and check these (Step 8's
  concern).
- No webhook handling (e.g. `payment_link.paid`) — this integration only
  covers the *creation* half of the payment-link flow; confirming a customer
  actually paid is out of scope for Step 7 and belongs to Step 9/10.

## 12. Ready for Step 8
Every `ExecutionRecord` (`execution_status`, `result_source`,
`razorpay_result`, `executed_at`) is already structured for logging — Step 8
just needs to persist these records, not redesign them.
