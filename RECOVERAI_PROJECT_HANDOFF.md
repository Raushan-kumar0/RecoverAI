# RecoverAI Project Handoff

_Last updated: end of Step 9. Read this alongside `recoverai_step1_specification.md`
(the canonical locked product spec) before doing anything else._

## 1. Hackathon
- Razorpay Hackathon
- Track 03 — AI Revenue Recovery: "Find revenue that's slipping away and win it back."

## 2. Product
**RecoverAI — Autonomous Revenue Recovery Agent for Razorpay Merchants.**
RecoverAI identifies revenue a merchant is at risk of losing (failed payments,
abandoned checkouts, failed subscriptions, overdue receivables), diagnoses why
using a trained model plus grounded rule-based reasoning, selects a bounded
recovery action, checks that action against deterministic policy guardrails,
executes only what's authorized through Razorpay test-mode APIs, and measures
actual — never fabricated — recovered revenue across a batch.

## 3. Locked workflow
```
DETECT → DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE → RECOVER → MEASURE
```
Steps 1–9 have implemented DETECT (dataset), DIAGNOSE (Step 3), the action
catalog (Step 4), DECIDE (Step 5), GUARDRAIL (Step 6), the EXECUTE
integration layer connecting to Razorpay Test Mode (Step 7), a full audit
trail (Step 8), and bounded failure/fallback/escalation handling around
EXECUTE (Step 9). RECOVER (confirming a customer actually paid) and MEASURE
(batch ₹ reporting) remain unbuilt. No live-mode Razorpay call has been made
or is possible anywhere in the project.

## 4. Locked Step 1 decisions (do not casually change)
- Four leakage categories: `failed_payment`, `checkout_abandonment`, `failed_subscription`, `overdue_receivable`, plus `successful` as baseline.
- Strict separation: deterministic facts / AI reasoning / AI recommendation / deterministic policy / actual Razorpay result — never blurred.
- **Detected-at-risk vs. actually-recovered is structurally separate**: `amount_at_risk` (fact) ≠ `predicted_recovery_likelihood` (AI prediction) ≠ `amount_recovered` (fact, only from an observed outcome). Predicted likelihood must never be counted as recovered revenue.
- Guardrails (`retry_limit`, `autonomous_attempt_cap`, `confidence_threshold`, `monetary_ceiling`, `contact_window`) are configurable defaults, not empirically validated — to be calibrated in Step 6.
- Tech stack: Python throughout; FastAPI only where genuinely useful; pandas/NumPy; SQLite for audit/state; Streamlit (not React) for the dashboard. Priority order: working agent > evaluation > Razorpay integration > reliability > dashboard polish.
- Full spec: `recoverai_step1_specification.md`.

## 5. Completed steps

### Step 1 — Product Definition & Scope — COMPLETE, LOCKED
- Files: `recoverai_step1_specification.md`
- Key decisions: see §4 above.
- Status: locked; only touch on a genuine contradiction/blocker.

### Step 2 — Synthetic Merchant Dataset — COMPLETE, LOCKED
- Files: `recoverai/data/{generate_dataset.py, validate_dataset.py, recoverai_cases.csv, train.csv, validation.csv, test.csv, README.md}`
- 1,000 synthetic cases (402 successful, 167 failed_payment, 158 checkout_abandonment, 141 failed_subscription, 132 overdue_receivable). Fixed `SEED=42`, verified byte-identical across reruns.
- Customer-aware train/validation/test split (no customer spans >1 split) — 719/148/133 rows.
- Ground truth (`ground_truth_recoverable`, `ground_truth_recovery_outcome`, `amount_recovered`, `recovery_observed`, `recovery_reason`) generated from a logistic function of legitimate pre-decision signals + noise — deliberately imperfect, not trivially separable.
- Tests/results: 22/22 validation checks pass (duplicates, missing values, invalid amounts/dates, status consistency, category logic, retry bounds, outcome consistency, category representation, split isolation, pre-decision/ground-truth column separation, forbidden-column absence).
- Status: dataset contract is final — do not regenerate or change the schema without a genuine blocker.

### Step 3 — AI Diagnosis Layer — COMPLETE, LOCKED
- Files: `recoverai/diagnosis/{feature_config.py, train_model.py, diagnose.py, test_diagnosis.py, model.joblib, metrics_report.json, README.md}`
- Model: Logistic Regression (selected over Random Forest; validation ROC-AUC 0.705 vs 0.692), trained on leakage cases only (434 train / 90 val / 74 test).
- Target: `ground_truth_recoverable`. Threshold 0.4357, chosen on validation (F1-max), applied once to test.
- Test metrics: Precision 0.806, Recall 1.000, F1 0.892, ROC-AUC 0.615, PR-AUC 0.856.
- Known weak spot (reported honestly, not hidden): failed_payment and checkout_abandonment show near-chance ROC-AUC (~0.53-0.54) on test; failed_subscription and overdue_receivable rank much better (0.79-0.91).
- Interface: `DiagnosisEngine.diagnose(case)` → structured dict with `predicted_recovery_likelihood`, `diagnosis_confidence` (margin-from-midpoint heuristic, not calibrated), `root_cause`, `risk_factors`, `positive_recovery_signals`, `reasoning_summary`, `evidence`.
- Tests/results: 17/17 pass, including structural leakage tests (forbidden fields never selectable, threshold applied to test matches validation-chosen threshold, evidence never references post-action fields).
- Status: model and tests preserved through Step 4 (verified via checksum — untouched).

### Step 4 — Recovery Action Toolbox — COMPLETE, LOCKED
- Files: `recoverai/actions/{action_models.py, action_catalog.py, action_compatibility.py, test_actions.py, README.md}`
- 7-action catalog: `payment_retry`, `mandate_retry`, `recovery_payment_link`, `payment_reminder`, `checkout_recovery_reminder`, `receivables_followup`, `escalation` — each with purpose, applicable categories, required fields, risk level, money-movement flag, communication flag, Razorpay-needed flag, and guardrail notes for Step 6.
- `get_actions_for_case(case, diagnosis=None)` — technical-applicability layer only (required fields present + communication-allowed check). Explicitly does NOT check retry limits, monetary ceilings, or contact hours — those are Step 6.
- Every generated `RecoveryAction.execution_status` = `NOT_EXECUTED`; no execute/send/charge method exists on the object; no Razorpay calls anywhere in the module.
- Tests/results: 29/29 pass. Regression: Step 2 validation still 22/22, Step 3 tests still 17/17, Step 3 model artifact checksum unchanged.
- Status: consumed by Step 5, unmodified.

### Step 5 — Decision Engine — COMPLETE, LOCKED
- Files: `recoverai/decision_engine/{decision_models.py, decision_engine.py, test_decision_engine.py, README.md}`
- Interface: `DecisionEngine(diagnosis_engine=...).decide(case, diagnosis=None, actions=None)` → `Decision`.
- Logic: buckets Step 3's `predicted_recovery_likelihood`/`diagnosis_confidence` into a LOW/MEDIUM/HIGH tier (high-likelihood-but-low-confidence is capped down to MEDIUM), then selects the highest-priority *technically-applicable* action per a documented, static per-category/per-tier priority table. Deterministic, rule-based — no second opaque model, keeping the Step 1 "AI reasoning vs. AI recommendation" separation intact.
- Output: `decision_status` (`recommended` / `no_applicable_actions` / `not_applicable` — vocabulary deliberately disjoint from Step 6's AUTO_EXECUTE/APPROVAL_REQUIRED/STOP), `recommended_action`, `recommendation_reason`, `alternatives_considered` (every action Step 4 returned, with rank or rejection reason).
- Does NOT authorize, does NOT enforce retry/monetary/contact-window limits, does NOT call Razorpay, does NOT execute or communicate.
- Tests/results: 21/21 pass, including a structural invariance test proving the decision is unchanged when post-action ground-truth fields are tampered with. Regression: Step 2 validation 22/22, Step 3 tests 17/17 (model checksum unchanged), Step 4 tests 29/29.
- Status: consumed by Step 6, unmodified.

### Step 6 — Guardrails + Human-in-the-loop — COMPLETE, LOCKED
- Files: `recoverai/guardrails/{guardrail_config.py, guardrail_models.py, guardrail_engine.py, test_guardrails.py, README.md}`
- Interface: `GuardrailEngine(config=None).authorize(case, diagnosis, decision, current_time=None)` → `GuardrailDecision` with exactly one `outcome` ∈ {`AUTO_EXECUTE`, `APPROVAL_REQUIRED`, `STOP`}.
- Configurable defaults (`GuardrailConfig`, restated from Step 1 §6, NOT empirically validated): `retry_limit=3`, `autonomous_attempt_cap=3` (7-day window), `confidence_threshold=0.6`, `monetary_ceiling=₹15,000` (chosen just above the Step 2 dataset's 90th-percentile `amount_at_risk`), `contact_window=09:00–20:00`.
- Rule order: STOP conditions checked first (suspicious flag; opt-out/comm-not-allowed/outside-contact-window blocking a communication action; retry-limit breach on a retry-type action; invalid/missing/not-applicable/no-actions decision) — then APPROVAL_REQUIRED conditions (attempt-cap reached; confidence below threshold; monetary ceiling breached on a money-movement action; action is definitionally human-gated per Step 4, e.g. `escalation`) — else AUTO_EXECUTE.
- Deliberate design choices documented in README: `retry_limit` breach → STOP but `autonomous_attempt_cap` breach → APPROVAL_REQUIRED (per Step 1's "mandatory escalation" wording, not abandonment); outside-contact-window → STOP rather than a "schedule later" state (this engine is strictly tri-state); attempt-history uses the case's own `retry_count`/`previous_attempt_count` fields since Step 8's audit trail doesn't exist yet.
- Does NOT call Razorpay, does NOT execute, does NOT send communications. Structurally verified (source-scan tests with docstrings/comments stripped).
- Tests/results: 31/31 pass, including forbidden-field invariance, full end-to-end pipeline tests (real Step 3→4→5→6 for all four categories), and a custom-config test proving thresholds are genuinely configurable. Regression: Step 2 validation 22/22, Step 3 tests 17/17 (model checksum unchanged), Step 4 tests 29/29, Step 5 tests 21/21.
- Status: consumed by Step 7, unmodified.

### Step 7 — Razorpay Test-Mode Integration — COMPLETE
- Files: `recoverai/integrations/razorpay/{razorpay_config.py, razorpay_client.py, razorpay_execution.py, test_razorpay_integration.py, .env.example, README.md}`; project-root `.gitignore` added.
- Interface: `execute_guardrail_approved_action(case, decision, guardrail_decision, client)` in `razorpay_execution.py` → `ExecutionRecord`. This is the ONLY module in the project allowed to touch Razorpay.
- Credentials: loaded exclusively from `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` env vars via `razorpay_config.load_config_from_env()`. `rzp_live_...` keys are rejected unconditionally at two independent points (config load + client construction) — no flag can override this. Secrets are never logged; `RazorpayConfig.redacted()` is used everywhere output is produced.
- `RECOVERAI_RAZORPAY_DRY_RUN` defaults to `true` (safe by default) even if unset — no network call happens unless explicitly disabled.
- Supported operations: only actions Step 4 marked `razorpay_integration_needed=True`.
  - `recovery_payment_link` → **real** Razorpay Test Mode API call (`POST /v1/payment_links`), verified against current Razorpay docs before implementation.
  - `payment_retry` / `mandate_retry` → **documented, labeled simulation** (`result_source="bounded_simulation"`) — Razorpay's public API has no merchant-triggerable "retry this failed charge now" endpoint (payments API is read/capture-only; subscription retries are automatic and server-scheduled). This is an honest bounded simulation per Step 1's rule, not a shortcut.
- Execution flow enforces, in order: guardrail outcome must be `AUTO_EXECUTE` (STOP and APPROVAL_REQUIRED both refused identically, never bypassed) → decision status must be `recommended` → case_id must match across case/decision/guardrail → action_type must match between decision and guardrail (no substitution) → action must be in the Razorpay-integration-needed set. Any failure returns a structured `not_executed` record rather than raising or silently proceeding.
- **Sandbox limitation (honest, verified):** this build environment's egress proxy blocks `api.razorpay.com` (`x-deny-reason: host_not_allowed`, confirmed via direct `curl`). With `dry_run=false` and fake test-shaped credentials, the client correctly attempted the real HTTP call and safely returned a structured `api_error` (HTTP 403 from the block) rather than crashing or claiming success. **No genuine Razorpay Test Mode transaction was completed in this environment** — the code path is proven correct, not proven against a live endpoint. See `recoverai/integrations/razorpay/README.md` §11–12 for exact local setup instructions to verify this with real credentials and real network access.
- Tests/results: 27/27 pass. Regression: Step 2 validation 22/22, Step 3 tests 17/17 (model checksum unchanged), Step 4 tests 29/29, Step 5 tests 21/21, Step 6 tests 31/31.
- Status: consumed by Step 8, unmodified.

### Step 8 — Audit Trail — COMPLETE
- Files: `recoverai/audit/{audit_schema.py, audit_store.py, audit_recorder.py, test_audit.py, README.md}`
- Storage: SQLite (per Step 1's tech direction), single append-only `audit_events` table — one event per pipeline stage per case, not one table per stage. `AuditStage` enum maps 1:1 onto the Step 1 §12 "Record:" list (detection, diagnosis, candidate_actions, decision, guardrail, execution — the last covers API action/result/failure/fallback/escalation/stop via existing `execution_status`/`result_source`/`guardrail_outcome` fields).
- Recording API: `record_detection`, `record_diagnosis`, `record_candidate_actions`, `record_decision`, `record_guardrail`, `record_execution` in `audit_recorder.py` — thin adapters that log the exact objects Steps 3–7 already produce; no new pipeline logic, no re-diagnosis, no re-decision.
- Leakage prevention: `record_detection()` builds its payload strictly from `feature_config.PRE_DECISION_FEATURES` + `case_id`, never `dict(case)` — verified by a tampered-ground-truth invariance test showing an identical recorded trail regardless of what's in the case's ground-truth columns.
- Secret redaction: `AuditStore.record_event()` recursively masks any payload key containing `secret`/`password`/`token`/`api_key`, as defense-in-depth on top of Step 7's own redaction.
- Every case's audit trail is human-readable (`summary` field) as well as fully structured (`payload`) — verified end-to-end against the real Step 3→4→5→6→7 pipeline for all four leakage categories plus the successful-case short-circuit.
- Tests/results: 20/20 pass. Regression: full suite from project root **145/145** (125 prior + 20 new). Step 2 validation 22/22. Model artifact and dataset checksums verified byte-identical — nothing retrained or regenerated.
- Status: ready for Step 9 to consume.

### Step 9 — Graceful Failure Handling — COMPLETE
- Files: `recoverai/failure_handling/{failure_models.py, failure_handler.py, test_failure_handling.py, README.md}`
- Entry point: `handle_execution_with_fallback(case, diagnosis, decision, guardrail_decision, razorpay_client, guardrail_engine, audit_store, current_time=None)` → `FailureHandlingResult` with `outcome` ∈ {`NO_FAILURE`, `FALLBACK_SUCCEEDED`, `ESCALATED`}.
- Pure orchestration — adds no new capability; only coordinates already-tested Steps 4 (candidate actions), 5 (Decision shape), 6 (re-authorization), 7 (the only function that ever touches Razorpay), and 8 (recording). No Step 1–8 file was modified.
- Flow: primary execution (Step 7) → if genuinely failed (`api_error`/`error` — NOT a guardrail STOP/APPROVAL_REQUIRED, which is correctly treated as `NO_FAILURE` and never routed around) → pick exactly one deterministic, catalog-order fallback candidate (never a retry of the same action) → re-authorize it via Step 6 from scratch → attempt via Step 7 → if that also isn't a success status, escalate (build the `escalation` recommendation, re-authorize — always `APPROVAL_REQUIRED` by Step 4's definition — attempt via Step 7, which correctly returns `not_executed` since escalation never calls Razorpay).
- Bounded: at most two Razorpay-client-touching calls per case, ever (primary + one fallback); escalation makes zero further calls. Verified via a call-counting test double.
- Every attempt (primary/fallback/escalation) is logged through Step 8's existing `record_decision`/`record_guardrail`/`record_execution` unmodified — the append-only audit schema already supported multiple events per stage per case, so no schema change was needed.
- Demonstrated with two real failure examples (not hidden, not faked): a `FakeFailingClient` test double for deterministic/network-independent tests, and a genuine environment-caused failure using the real `RazorpayTestModeClient` against this sandbox's blocked `api.razorpay.com` (real HTTP 403) — one run ended in `FALLBACK_SUCCEEDED` (payment_retry), another in `ESCALATED` (checkout_abandonment, where the only fallback candidate has no execution capability yet).
- Tests/results: 16/16 pass. Regression: full suite from project root **161/161** (145 prior + 16 new). Step 2 validation 22/22. Model/dataset checksums byte-identical — nothing retrained or regenerated.
- Status: ready for Step 10 to consume.

## 6. Current architecture (complete project tree)
```
recoverai/
  data/
    generate_dataset.py
    validate_dataset.py
    recoverai_cases.csv
    train.csv
    validation.csv
    test.csv
    README.md
  diagnosis/
    feature_config.py
    train_model.py
    diagnose.py
    test_diagnosis.py
    model.joblib
    metrics_report.json
    README.md
  actions/
    action_models.py
    action_catalog.py
    action_compatibility.py
    test_actions.py
    README.md
  decision_engine/
    decision_models.py
    decision_engine.py
    test_decision_engine.py
    README.md
  guardrails/
    guardrail_config.py
    guardrail_models.py
    guardrail_engine.py
    test_guardrails.py
    README.md
  integrations/
    razorpay/
      razorpay_config.py
      razorpay_client.py
      razorpay_execution.py
      test_razorpay_integration.py
      .env.example
      README.md
  audit/
    audit_schema.py
    audit_store.py
    audit_recorder.py
    test_audit.py
    README.md
  failure_handling/
    failure_models.py
    failure_handler.py
    test_failure_handling.py
    README.md
recoverai_step1_specification.md   (project root)
RECOVERAI_PROJECT_HANDOFF.md        (project root — this file)
.gitignore                           (project root — excludes .env, __pycache__, etc.)
requirements.txt                      (project root — pinned dependency versions)
```
Not yet created: `evaluation/`, `orchestrator/`, `dashboard/`, `config/`.

## 7. Data contract
- 1,000 synthetic cases, customer-aware split (train/validation/test), fixed seed 42, reproducible.
- **Pre-decision features** (usable as model/diagnosis inputs): `leakage_category`, `amount_at_risk`, `payment_method`, `failure_reason`, `checkout_started`, `checkout_completed`, `subscription_status`, `mandate_status`, `invoice_status`, `days_overdue`, `retry_count`, `previous_attempt_count`, `customer_purchase_count`, `customer_success_rate`, `customer_lifetime_value`, `previous_payment_behavior`, `customer_opt_out`, `suspicious_flag`, `communication_allowed`, `historical_recovery_behavior`.
- **Ground-truth/post-action fields** (NEVER usable as inputs): `ground_truth_recoverable`, `ground_truth_recovery_outcome`, `amount_recovered`, `recovery_observed`, `recovery_reason`.
- **Excluded but not forbidden** (identifiers/redundant): `case_id`, `transaction_id`, `customer_id`, `timestamp`, `invoice_due_date`, `event_type`, `payment_status`.
- Full schema and generation assumptions: `recoverai/data/README.md`.

## 8. Diagnosis contract
- Model: Logistic Regression pipeline (`recoverai/diagnosis/model.joblib`), reproducible under `SEED=42`.
- Feature allowlist enforced in code: `recoverai/diagnosis/feature_config.py` (`PRE_DECISION_FEATURES`, `ALL_FORBIDDEN_COLUMNS`, `assert_no_forbidden_columns`).
- `DiagnosisEngine.diagnose(case)` output schema: `case_id`, `leakage_category`, `root_cause`, `risk_factors`, `positive_recovery_signals`, `predicted_recovery_likelihood`, `diagnosis_confidence`, `confidence_method`, `reasoning_summary`, `evidence`.
- Successful/non-leakage cases return a `not_applicable` diagnosis without invoking the model.
- Current measured metrics: see §5 Step 3 summary and `recoverai/diagnosis/metrics_report.json` for full detail.
- Known limitations: weak ranking ability for failed_payment/checkout_abandonment categories specifically; confidence is a heuristic, not calibrated; small test set (74 cases) limits statistical stability.

## 9. Action contract
- Catalog: `recoverai/actions/action_catalog.py` (`ACTION_CATALOG` dict, 7 actions — see §5 Step 4 summary).
- Schema: `ActionDefinition` (static/type-level) and `RecoveryAction` (per-case instance) in `recoverai/actions/action_models.py`.
- Compatibility: `get_actions_for_case(case, diagnosis=None)` in `recoverai/actions/action_compatibility.py` — returns every action whose type applies to the case's category, each flagged `technically_applicable` + `applicability_reason`.
- What actions CAN do: exist as structured, auditable, non-executed proposals with full metadata for a future decision/guardrail layer to reason about.
- What actions CANNOT do (Step 4): select themselves as "the" action, check retry/monetary/time guardrails, get authorized, execute, call Razorpay, or claim any recovery occurred.
- Intentionally NOT implemented yet: action selection (Step 5), guardrail authorization (Step 6), any actual execution (Step 7).

## 9a. Decision contract (Step 5)
- Interface: `DecisionEngine(diagnosis_engine=None).decide(case, diagnosis=None, actions=None)` in `recoverai/decision_engine/decision_engine.py`.
- Tier thresholds: likelihood < 0.35 → LOW; 0.35–0.6 → MEDIUM; ≥ 0.6 with confidence ≥ 0.3 → HIGH; ≥ 0.6 with confidence < 0.3 → capped to MEDIUM. These are Step 5's own documented heuristic, distinct from Step 6's future `confidence_threshold` guardrail default.
- Priority tables: `PRIORITY_BY_CATEGORY_AND_TIER` in `decision_engine.py` — hand-authored, not learned, for auditability.
- Output schema: `Decision` / `AlternativeConsidered` dataclasses in `decision_models.py`.
- What it CAN do: recommend one action, explain why, list alternatives with rank/rejection reason.
- What it CANNOT do: authorize, enforce guardrail limits, execute, communicate, call Razorpay, or use any post-action ground-truth field (structurally verified by tests).
- Known limitation: does not weight by `amount_at_risk` magnitude — a ₹200,000 and a ₹500 case in the same tier/category get the same priority ordering. Left for Step 6/10 by design.

## 9b. Guardrail contract (Step 6)
- Interface: `GuardrailEngine(config=None).authorize(case, diagnosis, decision, current_time=None)` in `recoverai/guardrails/guardrail_engine.py`.
- Default config (`GuardrailConfig` in `guardrail_config.py`): `retry_limit=3`, `autonomous_attempt_cap=3`/7 days, `confidence_threshold=0.6`, `monetary_ceiling=₹15,000`, `contact_window=09:00–20:00`. All fields, all configurable, none hardcoded in rule logic.
- Output schema: `GuardrailDecision`/`TriggeredRule` dataclasses in `guardrail_models.py` — `outcome`, `reason`, `triggered_rules`, `limits_checked`, `approval_required`, `config_used`, `evaluated_at`.
- Full rule ordering and rationale: `recoverai/guardrails/README.md` §5–7.
- What it CAN do: authorize (AUTO_EXECUTE/APPROVAL_REQUIRED/STOP) with a fully audit-ready structured explanation.
- What it CANNOT do: execute, call Razorpay, communicate, or use any post-action ground-truth field (structurally verified by tests).
- Known limitation: attempt-history checks use the case's own static `retry_count`/`previous_attempt_count` fields rather than a live audit trail, since Step 8 doesn't exist yet — revisit when it does.

## 9c. Razorpay integration contract (Step 7)
- Entry point: `execute_guardrail_approved_action(case, decision, guardrail_decision, client)` in `recoverai/integrations/razorpay/razorpay_execution.py` — the ONLY module allowed to touch Razorpay.
- Credentials: `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` env vars only, never hardcoded. `rzp_live_...` rejected unconditionally, at two points. Secrets never appear in logs/errors (`RazorpayConfig.redacted()`).
- `RECOVERAI_RAZORPAY_DRY_RUN` defaults to `true` — no network call unless explicitly disabled.
- Supported operations: `recovery_payment_link` (real API call — `POST /v1/payment_links`); `payment_retry`/`mandate_retry` (bounded simulation — no real Razorpay endpoint exists for on-demand retry, verified against current docs).
- Enforces, refusing otherwise: guardrail outcome must be `AUTO_EXECUTE` (STOP/APPROVAL_REQUIRED never bypassed); decision status must be `recommended`; case_id and action_type must match exactly across case/decision/guardrail (never executes an unrecommended action).
- Result-source vocabulary kept structurally distinct: `razorpay_test_mode_dry_run` / `razorpay_test_mode_api` / `bounded_simulation` / `not_executed` — never conflated with Step 2's synthetic `ground_truth_recovery_outcome`, and live-mode results are architecturally impossible (§4 rejection).
- Known limitation: no genuine Razorpay Test Mode call has been completed yet (sandbox network restriction, not a code defect).

## 9d. Audit trail contract (Step 8)
- Storage: `AuditStore` in `recoverai/audit/audit_store.py` — SQLite, single append-only `audit_events` table (`case_id`, `leakage_category`, `stage`, `sequence`, `summary`, `payload_json`, `recorded_at`). No file is shipped by default — it's a runtime artifact created when recording functions are actually called.
- Recording API: `record_detection`, `record_diagnosis`, `record_candidate_actions`, `record_decision`, `record_guardrail`, `record_execution` in `recoverai/audit/audit_recorder.py` — pure logging adapters over the exact objects Steps 3–7 already produce. No new pipeline logic here.
- `AuditStage` enum (`detection`/`diagnosis`/`candidate_actions`/`decision`/`guardrail`/`execution`) maps 1:1 onto the original Step 1 §12 "Record:" list.
- Leakage prevention: `record_detection()` builds its payload strictly from `feature_config.PRE_DECISION_FEATURES` + `case_id`, never the raw case dict — structurally verified.
- Secret redaction: any payload key containing `secret`/`password`/`token`/`api_key` is masked before writing, as defense-in-depth on top of Step 7's own redaction.
- What it CAN do: give a complete, human-readable + structured trail of exactly what happened to a case and why, across all six stages.
- What it CANNOT do: run diagnosis/decision/guardrail/execution logic itself, aggregate across a batch (Step 10/12), or manage retention/rotation.
- Known limitation: the glue that calls all six `record_*` functions in sequence for a real pipeline run currently only exists inside the test suite — a reusable orchestration module is still deliberately deferred (`orchestrator/` remains unbuilt).

## 9e. Failure-handling contract (Step 9)
- Entry point: `handle_execution_with_fallback(...)` in `recoverai/failure_handling/failure_handler.py` — pure orchestration over Steps 4–8, adds no new Razorpay code.
- Outcomes: `NO_FAILURE` (success, or correctly guardrail-blocked — not a failure), `FALLBACK_SUCCEEDED`, `ESCALATED`.
- Critical distinction preserved: STOP/APPROVAL_REQUIRED are never reinterpreted as failures and never trigger fallback — only a genuine `api_error`/`error` execution status does.
- Bounded: at most two Razorpay-client calls per case ever (primary + one fallback); escalation makes zero further calls.
- Every fallback/escalation action is independently re-authorized via `GuardrailEngine.authorize()` — never inherits the primary's authorization.
- All attempts logged via Step 8's existing recorder functions, unmodified.
- Known limitation: only `recovery_payment_link` can genuinely fail (the only action with a real API call); `payment_retry`/`mandate_retry` always return `status="simulated"` by Step 7's locked design, so fallback/escalation paths are only reachable when the primary was `recovery_payment_link`.

## 10. Razorpay integration status
**IMPLEMENTED (Step 7), TEST MODE ONLY.** `recoverai/integrations/razorpay/`
connects Step 6's `AUTO_EXECUTE` outcomes to Razorpay. Only
`recovery_payment_link` makes a real API call (Payment Links); `payment_retry`/
`mandate_retry` are honest documented simulations (see §9c). **No real
network call could be completed from this build sandbox** — `api.razorpay.com`
is blocked by the environment's egress proxy (confirmed via `curl`,
`x-deny-reason: host_not_allowed`). The integration code is correct and was
proven to fail safely (structured `api_error`, no crash, no false success)
when the real call was attempted with `dry_run=false`. **No genuine Razorpay
Test Mode transaction has been completed anywhere in this project.** See §12
for exact local setup instructions to run this yourself with real network
access and real credentials.

## 11. Steps remaining
- Step 10 — Revenue Recovery Measurement (batch-level ₹ at risk → ₹ attempted → ₹ recovered, using Step 9's `FailureHandlingResult` outcomes to distinguish successful/fallback/escalated cases)
- Step 11 — Evaluation & Metrics
- Step 12 — Final Integration & Hackathon Demo

## 12. Critical constraints (must NOT be casually changed)
- The four leakage categories and the DETECT→DIAGNOSE→DECIDE→GUARDRAIL→EXECUTE→RECOVER→MEASURE workflow are locked.
- The five-way separation (facts / AI reasoning / AI recommendation / deterministic policy / actual API result) must never blur.
- `amount_at_risk`, `predicted_recovery_likelihood`, and `amount_recovered` must remain three structurally distinct concepts — predicted likelihood may never be counted as recovered revenue.
- The Step 2 dataset schema and split must not be regenerated or altered without a genuine blocker.
- The Step 3 model/metrics must not be silently retrained or changed.
- The Step 4 action toolbox's compatibility layer must never absorb guardrail logic (retry limits, monetary ceilings, contact windows, AUTO_EXECUTE/APPROVAL_REQUIRED/STOP) — that is exclusively Step 6's responsibility.
- The Step 5 decision engine's vocabulary (`recommended`/`no_applicable_actions`/`not_applicable`) must never be conflated with or renamed to Step 6's authorization vocabulary (`AUTO_EXECUTE`/`APPROVAL_REQUIRED`/`STOP`) — they are structurally separate layers, verified by test.
- Step 5 must remain a deterministic rule-based layer over Step 3's prediction — do not silently add a second ML/LLM call inside the decision engine.
- Step 6's `GuardrailEngine.authorize()` is the ONLY place AUTO_EXECUTE/APPROVAL_REQUIRED/STOP is decided. Step 7 must treat that outcome as final and non-negotiable — it must not re-derive or override authorization itself; it only acts on STOP=never, AUTO_EXECUTE=call Razorpay now, APPROVAL_REQUIRED=call Razorpay only after a real recorded approval.
- Step 6's guardrail thresholds are configurable defaults, not validated — do not present them as empirically proven in the demo narrative.
- Step 7's `razorpay_execution.execute_guardrail_approved_action()` is the ONLY module allowed to touch Razorpay. No other module (Step 8 audit, Step 9 failure handling, etc.) should call Razorpay directly — route everything through this function or its future extensions.
- Razorpay credentials must always come from environment variables, never hardcoded or committed. A `rzp_live_...` key must always be rejected unconditionally — this is a hard, non-configurable safety rule, not a default.
- `payment_retry`/`mandate_retry` remain honest simulations (not real API calls) unless a future step finds and verifies an actual Razorpay capability for it — do not silently start claiming these are real without re-verifying against current docs.
- No genuine Razorpay Test Mode transaction has been completed in this project yet (sandbox network restriction) — do not present demo numbers as if a real API call succeeded unless one actually has been run and its response captured.
- Step 8's `AuditStore`/`record_*` functions are the only sanctioned way pipeline events get persisted. Future steps should route logging through these rather than inventing a parallel logging mechanism.
- `record_detection()`'s strict use of `PRE_DECISION_FEATURES` (never the raw case dict) must be preserved — this is what keeps the audit trail itself leakage-safe for Step 2's synthetic rows.
- Step 9's `handle_execution_with_fallback()` is the sanctioned way to run a case through execution with failure handling. It must remain a thin orchestrator over Steps 4–8 — no new Razorpay code, no new guardrail logic, no bypassing re-authorization of fallback/escalation actions.
- The failure-vs-blocked distinction (only `api_error`/`error` counts as a failure; STOP/APPROVAL_REQUIRED never do) must be preserved — this is what keeps Step 9 from ever "routing around" a guardrail decision.
- The fallback bound (at most one alternate action, never a retry of the same action, never a sweep) must be preserved — this is the "no uncontrolled/repeated retry" guarantee.
- No step may fabricate metrics, recovered revenue, or Razorpay results.
- No step may jump ahead of its number without explicit instruction.
- Tech stack: Python/FastAPI-where-useful/SQLite/Streamlit — not React, unless a concrete future need is identified and explicitly approved.

## 13. What NOT to build yet
- Any real network-dependent audit/orchestration beyond persisting `ExecutionRecord`s — Step 8.
- A reusable multi-stage orchestrator module that calls all six audit `record_*` functions automatically — still deferred (currently only exercised inside Step 8's and Step 9's tests).
- Batch-level failure-handling policy (retry-the-whole-batch, circuit breakers, cross-case rate limiting) — deferred beyond Step 9's per-case bounded fallback.
- Batch-level ₹ measurement and aggregation across many cases — Step 10.
- Audit trail persistence (SQLite schema, logging) — Step 8.
- Failure-handling/fallback orchestration — Step 9.
- Batch-level ₹ measurement — Step 10.
- Held-out evaluation of the full agent (as opposed to just the Step 3 model) — Step 11.
- Dashboard/UI (Streamlit) — Step 12.

## 14. Exact next instruction

**NEXT STEP:**
Start Step 10 — Revenue Recovery Measurement.

Do not start Step 11 or later functionality until Step 10 is complete and reviewed.
