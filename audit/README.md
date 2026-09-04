# RecoverAI — Audit Trail (Step 8)

## 1. Scope
Persists what the agent did and why, at every stage of
DETECT → DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE, so "a judge should be able
to understand exactly what the agent did and why" (Step 1 §12) without
reading code. **Pure recording** — this module runs no diagnosis, decision,
guardrail, or execution logic itself; it only logs objects Steps 3–7 already
produced. It does not retrain, regenerate, or modify anything upstream.

## 2. Files
```
recoverai/audit/
  audit_schema.py     AuditStage enum + SQLite DDL
  audit_store.py         AuditStore — SQLite I/O, secret-redaction guard
  audit_recorder.py       per-stage recording functions (thin adapters over Step 3-7 objects)
  test_audit.py             20 tests
  README.md                  this file
```
No `audit_trail.db` file is shipped as a deliverable — it's a runtime
artifact, generated only when the recording functions are actually called
against real cases (e.g. in Step 10/12's batch runs).

## 3. Schema
One append-only table, not one table per stage — simpler, and a full case
trail is one ordered query:
```sql
CREATE TABLE audit_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    leakage_category TEXT,
    stage          TEXT NOT NULL,   -- detection|diagnosis|candidate_actions|decision|guardrail|execution
    sequence       INTEGER NOT NULL, -- per-case ordering
    summary        TEXT NOT NULL,     -- human-readable, judge-facing
    payload_json   TEXT NOT NULL,      -- full structured data
    recorded_at    TEXT NOT NULL
);
```
`AuditStage` maps 1:1 onto the Step 1 §12 "Record:" list — `DETECTION`
(case detection), `DIAGNOSIS` (Step 3), `CANDIDATE_ACTIONS` (Step 4),
`DECISION` (Step 5 — selected action + reasoning + alternatives),
`GUARDRAIL` (Step 6 — policy decision + approval requirement), `EXECUTION`
(Step 7 — API action, result, failure, fallback/simulation, and stop are all
represented via `execution_status`/`result_source`/`guardrail_outcome` on
this one event type; escalation is captured naturally since it's just
another `action_type`).

## 4. Recording API
```python
from audit_store import AuditStore
from audit_recorder import (record_detection, record_diagnosis, record_candidate_actions,
                             record_decision, record_guardrail, record_execution)

store = AuditStore("audit_trail.db")   # or ":memory:" for ephemeral use
record_detection(store, case)
record_diagnosis(store, case_id, leakage_category, diagnosis)
record_candidate_actions(store, case_id, leakage_category, actions)
record_decision(store, decision)
record_guardrail(store, guardrail_decision)
record_execution(store, execution_record)

trail = store.get_case_trail(case_id)   # ordered list of events
```
Each `record_*` function takes exactly the object the corresponding Step
already produces (`diagnosis` dict, `RecoveryAction` list, `Decision`,
`GuardrailDecision`, `ExecutionRecord`) — no new data is invented, and every
`.to_dict()` output is stored as-is (with the redaction guard in §6 applied).

## 5. Leakage prevention
`record_detection()` is the one function that touches a raw case object
(which, for Step 2 synthetic rows, also carries ground-truth columns). It
builds its payload **exclusively** from `feature_config.PRE_DECISION_FEATURES`
plus `case_id` — never `dict(case)` — so a synthetic ground-truth field can
never enter the audit trail via the detection event. Every other `record_*`
function only ever receives objects that are already leakage-safe by
construction (Steps 3–7 already enforce this). Verified by
`test_detection_payload_never_contains_forbidden_fields`,
`test_full_trail_invariant_to_tampered_ground_truth` (tampering
`amount_recovered`/`ground_truth_recovery_outcome` on the input case produces
an identical recorded trail), and `test_no_forbidden_field_names_anywhere_in_full_trail`.

## 6. Secret redaction (defense in depth)
`AuditStore.record_event()` recursively scans every payload before writing
and masks any dict key whose name contains `secret`, `password`, `token`, or
`api_key` (case-insensitive) as `"***REDACTED***"` — regardless of whether
the upstream object (e.g. Step 7's already-redacted `RazorpayConfig`) would
have leaked one anyway. Verified by `test_audit_store_redacts_secret_shaped_keys`
and `test_real_execution_record_never_leaks_fake_test_secret_into_audit` (a
real fake-test-credential run's full trail is scanned for the literal secret
string and confirmed absent).

## 7. Tests (20/20 passing)
Covers: schema creation, single-event round-trip, invalid stage/missing
case_id rejection, full 6-stage trail recorded correctly for all four
leakage categories via the real Step 3→4→5→6→7 pipeline, successful cases
correctly stopping after the diagnosis event (nothing to decide/authorize),
multi-case isolation, leakage prevention (both direct and via tampered
ground-truth invariance), non-empty human-readable summaries on every event,
SQLite file persistence across a close/reopen cycle, all three guardrail
outcomes and multiple execution statuses recorded distinctly, deterministic
payload content across repeated runs (net of timestamps), and secret
redaction both directly and through a real pipeline run.

## 8. Regression
Full suite from project root: **145/145** passing (125 prior + 20 new).
Step 2 dataset validation: 22/22. Model artifact (`model.joblib`,
`metrics_report.json`) and all four dataset CSVs verified byte-identical to
their pre-Step-8 checksums — nothing was retrained or regenerated.

## 9. Limitations
- No query/filter API beyond "all events for one case_id" and "all case
  ids" — batch-level aggregation (e.g. "all STOP outcomes this run") is a
  Step 10/12 concern, not built here.
- No log rotation/retention policy — out of scope for a hackathon demo.
- The orchestration glue that calls all six `record_*` functions in
  sequence (as exercised in this README's tests) is deliberately **not**
  packaged as a reusable `orchestrator/` module yet — that's still deferred,
  per the existing project roadmap (`orchestrator/` remains unbuilt).

## 10. Ready for Step 9
Every pipeline outcome — including STOP, APPROVAL_REQUIRED,
`not_executed`, `api_error`, and `error` — is already recorded with its full
reason and triggered rules. Step 9's graceful-failure-handling work has a
ready-made record of exactly what failed and why to build on.
