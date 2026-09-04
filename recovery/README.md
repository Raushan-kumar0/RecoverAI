# RecoverAI — RECOVER Stage (Step 10 prerequisite)

## 1. Scope
Observes whether a customer **actually paid**, structurally separate from
EXECUTE (which only proves an action/API call was attempted). This is the
missing stage the locked workflow always specified:
```
DETECT → DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE → RECOVER → MEASURE
```

## 2. Files
```
recoverai/recovery/
  recovery_models.py     RecoveryStatus enum + RecoveryResult dataclass
  recovery_checker.py       observe_recovery() — the only function that produces a RecoveryResult
  test_recovery.py            18 tests
  README.md                     this file
```
Reuses the **existing** `RazorpayTestModeClient` (Step 7) — extended with one
new read-only method, `fetch_payment_link_status()`. No second Razorpay
client was created anywhere in the project.

## 3. Why this endpoint is legitimate
`GET /v1/payment_links/:id` is a real, documented Razorpay endpoint
(https://razorpay.com/docs/api/payments/payment-links/fetch-id-standard/)
that returns the link's current `status` (`created`/`paid`/`partially_paid`/
`expired`/`cancelled`) and `amount_paid`. It's read-only — it cannot create,
modify, or cancel anything — and is available in Test Mode exactly like the
Payment Links creation call already used in Step 7.

## 4. The critical distinction, enforced structurally
`execution_status == "executed"` (Step 7) only means Razorpay **accepted the
request to create a link** — it says nothing about payment.
`observe_recovery()` is the **only** function that can produce a
`RecoveryResult`, and its signature takes exactly `case_id`,
`leakage_category`, an `ExecutionRecord`, and the Razorpay client — **no**
`diagnosis`, `predicted_recovery_likelihood`, `decision`, or raw `case`
parameter exists at all. This is verified by
`test_observe_recovery_signature_has_no_likelihood_or_diagnosis_parameter`
and `test_observe_recovery_signature_has_no_ground_truth_or_case_parameter` —
there is no code path by which a prediction or Step 2 ground truth could
ever influence a recovery observation, because the function literally cannot
see them.

## 5. Status mapping (from Razorpay's own response only)
| Condition | `RecoveryStatus` | `amount_recovered` |
|---|---|---|
| No real link was ever created (dry-run, simulated, failed execution, not_executed, or a non-`recovery_payment_link` action) | `NOT_OBSERVED` | 0.0 |
| Status-check call itself errored (network/API failure) | `OBSERVATION_FAILED` | 0.0 |
| Razorpay `status == "paid"` | `RECOVERED` | `amount_paid` (paise→rupees) |
| Razorpay `amount_paid > 0` but status isn't `"paid"` | `PARTIALLY_RECOVERED` | the genuinely-paid partial amount |
| Razorpay `amount_paid == 0` and any other status | `PENDING` | 0.0 |

`OBSERVATION_FAILED` is deliberately distinct from `PENDING` — a failed
check means "we don't know," not "not paid."

## 6. Audit integration
`AuditStage` gained one new member, `RECOVERY` — the smallest compatible
schema change possible: `stage` is a plain TEXT column, so existing rows and
queries are completely unaffected. `record_recovery()` follows the exact
pattern of every other Step 8 recorder function. A case's audit trail now
distinguishes an `execution` event (`execution_status='executed'`, no
`amount_recovered` field at all) from a separate `recovery` event
(`recovery_status`, `amount_recovered`) — verified by
`test_audit_distinguishes_execution_and_recovery_stages`.

## 7. Safety preserved unmodified
`fetch_payment_link_status()` reuses the same `RazorpayConfig` (dry-run
default, unconditional live-key rejection, secret redaction) as every other
client method — no new safety logic was written, none was needed.

## 8. Tests (18/18 passing)
Covers: a genuinely executed link with no status check yet is `NOT_OBSERVED`
(not fabricated as recovered); dry-run, `api_error`, `error`, and
`not_executed` executions never produce recovery and never even attempt a
status check; a `"paid"` response produces the exact observed `amount_paid`;
a `"partially_paid"` response produces the genuinely-paid partial amount; an
`"expired"`/unpaid response produces zero; a failed status-check call is
`OBSERVATION_FAILED`, not silently treated as unpaid; non-`recovery_payment_link`
actions are never checked at all; the function signature structurally cannot
accept a likelihood/diagnosis/ground-truth input; no forbidden field name
appears in the module's actual code (docstring-stripped source scan); the
new client method respects dry-run and live-key rejection identically to
the existing one; and no secret leaks into a recorded RECOVER audit event
even when a (simulated) buggy upstream response contains one.

## 9. Honest verification status
**No genuine Razorpay Test Mode payment was observed by this project.** This
sandbox's egress proxy blocks `api.razorpay.com` (confirmed in Step 7 and
reconfirmed here), so no real Payment Link has ever actually been created,
which means there has never been a real `payment_link_id` to check a status
for. Everything in §8 is proven with deterministic test doubles
(`FakeStatusClient`), not a live call. This is stated plainly, not blurred:
**"the RECOVER code path is implemented and unit-tested" is not the same
claim as "a genuine Test Mode payment was verified."** The latter has not
happened anywhere in this project.

## 10. Limitations
- No webhook support — this is a pull (poll-on-demand) model, not push.
- No retry/backoff on the status-check call itself (a single attempt,
  consistent with Step 9's "no uncontrolled retry" principle).
- Partial-payment handling reports whatever `amount_paid` Razorpay returns,
  but doesn't track incremental changes between multiple checks over time
  (each `observe_recovery()` call is a fresh, independent snapshot).

## 11. Ready for Step 10 measurement
Every `RecoveryResult` (`recovery_status`, `amount_recovered`) is exactly
what `measurement/batch_measurement.py` sums — nothing else.
