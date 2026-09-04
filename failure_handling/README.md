# RecoverAI — Graceful Failure Handling (Step 9)

## 1. Scope
Implements: primary action fails → detected → no uncontrolled retry →
failure recorded in the Step 8 audit trail → bounded fallback attempted
where appropriate → escalation when fallback isn't safe/permitted/possible.
**Pure orchestration** — adds no new capability of its own; only coordinates
already-tested Steps 4–8. Does not modify, retrain, or regenerate anything
from Steps 1–8.

## 2. Files
```
recoverai/failure_handling/
  failure_models.py       FailureHandlingOutcome enum + FailureHandlingResult dataclass
  failure_handler.py         handle_execution_with_fallback() — the orchestration logic
  test_failure_handling.py    16 tests
  README.md                    this file
```

## 3. Why this needs no new Razorpay code
Step 7's `execute_guardrail_approved_action()` remains the *only* function
that ever touches the Razorpay client — this module calls it up to twice
(primary, then one fallback) and never duplicates or bypasses its guardrail
check. Step 6's `GuardrailEngine.authorize()` is called again for the
fallback/escalation action, so **the fallback is re-authorized from
scratch** — it is never assumed safe just because the primary was.

## 4. The failure/fallback/escalation flow
```
execute_guardrail_approved_action(primary)  -> record_execution (Step 8)
        |
        v
execution_status in {api_error, error}?  --no--> NO_FAILURE (done — this
        |                                          includes STOP/APPROVAL_REQUIRED,
        |yes                                       which are NOT failures)
        v
get_actions_for_case() -> pick first technically-applicable, non-escalation,
non-primary-action-type candidate (deterministic, catalog order — never a
retry of the same action, never a sweep through every alternative)
        |
        v
found a candidate?  --no--> skip to escalation
        |yes
        v
_build_recommendation() -> record_decision (Step 8)
guardrail_engine.authorize() -> record_guardrail (Step 8)   [re-authorized, not inherited]
execute_guardrail_approved_action(fallback) -> record_execution (Step 8)
        |
        v
fallback execution_status in {dry_run, simulated, executed}?
        |                                              |
       no                                             yes
        v                                               v
   [fall through]                              FALLBACK_SUCCEEDED (done)
        |
        v
escalation candidate (always available per Step 4) -> _build_recommendation
-> record_decision -> guardrail_engine.authorize() -> record_guardrail
-> execute_guardrail_approved_action() -> record_execution
        |
        v
   ESCALATED (done — escalation never calls Razorpay; it's not_executed
              by design since its guardrail outcome is always APPROVAL_REQUIRED)
```

## 5. Bounded — never uncontrolled/repeated
At most **one** fallback action is ever tried (not the same failed action
retried, not a sweep across every alternative), and escalation itself never
touches Razorpay. A case can reach the Razorpay client **at most twice**
here, ever — verified by `razorpay_calls_made` on the result and by
`test_no_repeated_or_unbounded_retry` / `test_escalation_never_calls_razorpay_again`
counting actual client-method invocations on a test double.

## 6. Critical distinction: failure vs. correctly-blocked
`execution_status == "not_executed"` (guardrail said STOP or
APPROVAL_REQUIRED) is **not** a failure — it's a correct policy decision,
and this module does not try to route around it. Only a genuine attempted-
and-broke Razorpay call (`api_error`/`error`) triggers fallback/escalation.
Verified explicitly by `test_stop_primary_does_not_trigger_fallback` and
`test_approval_required_primary_does_not_trigger_fallback` — both assert
zero client calls and `NO_FAILURE`, proving STOP/APPROVAL_REQUIRED still
prevent execution and are never reinterpreted as something to "fix."

## 7. A real, demonstrated failure (not hidden, not faked)
Two honest ways this failure is demonstrated:

**(a) Deterministic unit tests** use a `FakeFailingClient` test double that
returns `{"status": "api_error", ...}` without any network dependency — so
tests are fast and don't depend on external network state.

**(b) A genuine environment-caused failure**, run against the *real*
`RazorpayTestModeClient` with `dry_run=false` and syntactically-valid-but-
fake test credentials (`test_successful_dry_run_execution_unaffected...`
confirms dry-run mode is untouched; the live-attempt case was run manually
during implementation — see the transcript). This sandbox's egress proxy
blocks `api.razorpay.com` (confirmed in Step 7), so the real client
genuinely attempts the call and gets a real HTTP 403 back — a real,
non-fabricated failure. Example captured during implementation
(`recovery_payment_link` primary → fails → `payment_retry` fallback →
succeeds):
```
[0] execution   execution_status='api_error', result_source='razorpay_test_mode_api'
[1] decision    Decision Engine recommended 'payment_retry'. Fallback after 'recovery_payment_link' execution failure...
[2] guardrail   Guardrail outcome = auto_execute (approval_required=False)...
[3] execution   execution_status='simulated', result_source='bounded_simulation'
outcome: FailureHandlingOutcome.FALLBACK_SUCCEEDED | fallback: payment_retry | calls_made: 2
```
And a full escalation example (checkout_abandonment, where the only
fallback candidate has no execution capability in this project yet):
```
[0] execution   execution_status='api_error' (primary: recovery_payment_link)
[1] decision    recommended 'checkout_recovery_reminder' (fallback)
[2] guardrail   outcome = auto_execute
[3] execution   execution_status='not_executed' (no Razorpay integration needed — correctly refused, not a bug)
[4] decision    recommended 'escalation'
[5] guardrail   outcome = approval_required (escalation is definitionally human-gated)
[6] execution   execution_status='not_executed' (escalation never calls Razorpay)
outcome: FailureHandlingOutcome.ESCALATED
```

## 8. Failure recorded in the audit trail
Every attempt — primary, fallback, escalation — is logged via Step 8's
existing `record_execution`/`record_decision`/`record_guardrail` unmodified.
No new audit stage or schema change was needed: the append-only
`audit_events` table already supports multiple events of the same stage per
case (via its `sequence` column), so a full multi-attempt story is just a
longer, still-ordered trail. Verified by `test_full_audit_trail_shows_complete_story`.

## 9. No guardrail bypass
Every fallback and every escalation action is passed through
`GuardrailEngine.authorize()` again before any execution attempt — never
assumed authorized because the primary was. If a fallback itself would be
blocked (e.g. it requires communication and the case is opted out), Step 7
safely refuses it exactly as it would for any other case, and the handler
falls through to escalation rather than forcing it. Verified by
`test_fallback_action_is_independently_reauthorized` and
`test_fallback_blocked_by_guardrail_falls_through_to_escalation`.

## 10. Secret safety
`FAKE_TEST_KEY_SECRET` is asserted absent from both the `FailureHandlingResult`
and the full audit trail after a real failure/fallback/escalation run — this
holds by construction, since Step 9 never constructs its own Razorpay
payloads; it only ever calls the already-redaction-safe Step 7 function.

## 11. Tests (16/16 passing)
Covers: genuine failure detection triggering fallback; fallback succeeding
with a Razorpay-capable alternative; bounded call counts (never repeated/
unbounded); escalation making zero extra Razorpay calls; failure correctly
recorded in the audit trail; a full multi-stage audit story verified stage-
by-stage; deterministic (non-random) fallback selection across repeated
runs; escalation when the only available fallback has no execution
capability in this project; STOP and APPROVAL_REQUIRED both correctly
*not* triggering fallback (proving they still prevent execution and aren't
reinterpreted as failures); independent guardrail re-authorization of the
fallback action; a blocked fallback correctly falling through to escalation
rather than being forced; no secret leakage in the result object or the
audit trail; no forbidden ground-truth field referenced in the module's
source; and the pre-existing successful dry-run execution path proven
completely unaffected by this module's presence.

## 12. Regression
Full suite from project root: **161/161** passing (145 prior + 16 new).
Step 2 dataset validation: 22/22. `model.joblib`, `metrics_report.json`, and
all four dataset CSVs verified byte-identical to their pre-Step-9
checksums — nothing retrained or regenerated.

## 13. Limitations
- Fallback selection is a simple deterministic "first applicable candidate
  in catalog order" rule, not a re-run of Step 5's tiered decision logic —
  intentionally simple for auditability; a more sophisticated re-ranking
  could be a future refinement.
- Only `recovery_payment_link` can genuinely fail in this project (it's the
  only action with a real API call); `payment_retry`/`mandate_retry` always
  return `status="simulated"` by Step 7's locked design, so this module's
  fallback/escalation paths are only reachable when the primary action was
  `recovery_payment_link`. This is a property of Step 7, not a Step 9 gap.
- No fallback exists for communication-type actions (`payment_reminder`,
  `checkout_recovery_reminder`, `receivables_followup`) because no execution
  engine for them exists anywhere in the project yet — correctly surfaced as
  `not_executed` and escalated, not silently dropped.
- No cooldown/backoff timing between primary and fallback attempts — both
  happen immediately in sequence, appropriate for a synchronous demo flow.

## 14. Ready for Step 10
Every `FailureHandlingResult` (`outcome`, `primary_execution`,
`fallback_execution`, `escalation_execution`, `razorpay_calls_made`) is
already structured for batch-level aggregation — Step 10's ₹ measurement
work can count `FALLBACK_SUCCEEDED` vs. `ESCALATED` vs. `NO_FAILURE` outcomes
directly from these records without needing new instrumentation.
