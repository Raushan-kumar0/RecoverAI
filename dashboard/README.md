# RecoverAI — Dashboard (Step 12)

## 1. Scope
The final presentation layer over the existing 11-step pipeline. **Zero new
pipeline logic** — every number displayed comes from an already-existing,
already-tested Step 3–11 function, called exactly as it already is
elsewhere in the project.

## 2. Files
```
recoverai/dashboard/
  dashboard_data.py     pure-Python data access layer — NO Streamlit import, fully unit-testable
  app.py                  Streamlit presentation layer — calls dashboard_data.py only, no logic of its own
  test_dashboard.py         22 tests
  README.md                   this file
```

## 3. Framework
**Streamlit** — per Step 1's locked tech decision ("Streamlit ... for the
hackathon dashboard"), not React. Installed and verified working
(`streamlit==1.62.0`, added to `requirements.txt`).

## 4. Launch command
```bash
# from the project root:
streamlit run dashboard/app.py

# or from inside dashboard/:
streamlit run app.py
```
**Verified during implementation**: launched headlessly
(`streamlit run app.py --server.headless true`), confirmed the process
stayed running and the server responded `HTTP 200` on its port, then shut
down cleanly. This is a genuine launch verification, not an assumption.

## 5. Architecture — why two files
`dashboard_data.py` contains **zero** Streamlit imports. Every function in
it wraps one existing pipeline call (`DiagnosisEngine.diagnose`,
`DecisionEngine.decide`, `GuardrailEngine.authorize`,
`handle_execution_with_fallback`, `observe_recovery`,
`compute_batch_measurement`, `assemble_evaluation_report`,
`AuditStore.get_case_trail`) and returns the real object. This makes the
data layer fully unit-testable with `pytest` — no browser, no running
server needed — and guarantees `app.py` cannot accidentally duplicate or
diverge from any Step 3–11 logic, because it has none of its own to
diverge with.

## 6. Section-by-section data sources
| Section | Data source |
|---|---|
| A. Overview | `dashboard_data.get_overview_metrics()` → Step 10 `compute_batch_measurement()` |
| B. AI Diagnosis | `DiagnosisEngine.diagnose()` output, as-is |
| C. Decision + Guardrails | `DecisionEngine.decide()` + `GuardrailEngine.authorize()` output, as-is |
| D. Execution | `handle_execution_with_fallback()`'s `ExecutionRecord`s, as-is |
| E. Recovery | `observe_recovery()`'s `RecoveryResult`, as-is — plus a manual "check any Payment Link ID" tool using the **same** function |
| F. Measurement | `assemble_evaluation_report()` → Step 11's four categories (A/B/C/D), unmerged |
| G. Audit Trail | `AuditStore.get_case_trail()`, as-is |

## 7. Live vs. synthetic — how the UI keeps them apart
Section F renders Step 11's four categories in four visually separate
columns, each with its own caption:
- **A (ML/Evaluation)**: Step 3's locked model metrics.
- **B (Synthetic/Backtest)**: labeled "⚠️ SYNTHETIC" in the UI, held-out
  ground-truth backtest — its `backtest_amount_recoverable_if_ground_truth_trusted`
  figure never appears anywhere near category D's number.
- **C (Live/Test Mode Execution)**: what was actually attempted.
- **D (Observed Recovery)**: labeled with the Test Mode badge, only field
  that can ever show nonzero recovered revenue, with an explicit
  `genuine_payment_verified` flag and limitation note.

No code path sums or displays B and D side-by-side as if they were the same
kind of number — verified by `test_evaluation_report_categories_structurally_separate`.

## 8. The verified ₹10.00 Test Mode payment
This dashboard was built to **display** genuine observations, not to
hardcode one. The sidebar's "Check a real Payment Link" tool calls
`dashboard_data.check_payment_link(payment_link_id, razorpay_client)`,
which constructs a minimal execution-record shell and passes it straight
into `observe_recovery()` — the exact same sanctioned function Step 10 uses
for every other observation. Enter `plink_TTYhxhZtExs5C0` there, with real
Test Mode credentials and `RECOVERAI_RAZORPAY_DRY_RUN=false`, in an
environment with real network access, and the dashboard will re-observe and
display it genuinely. **This sandbox cannot do that** — `api.razorpay.com`
remains blocked here (unchanged since Step 7), so every live check performed
during this implementation correctly returned `not_observed` in dry-run mode,
never a fabricated `recovered`.

## 9. Honest no-data / error states (section I)
Every tab checks `has_results` before rendering and shows an explicit
`st.info("No live run yet...")` rather than blank/zeroed metrics that could
be misread as "zero recovered." Execution and recovery statuses are shown
as explicit badges (`✅ EXECUTED`, `🧪 DRY RUN`, `❌ API ERROR`,
`⛔ NOT EXECUTED`, `✅ RECOVERED`, `⏳ PENDING`, `❔ NOT OBSERVED`,
`⚠️ OBSERVATION FAILED`) — never silently collapsed to a bare `0`. Verified
by `test_app_has_explicit_labels_for_all_no_data_states`.

## 10. Tests (22/22 passing)
Covers: engines load correctly with valid credentials and degrade
gracefully (not crash) without them; the dataset file is never modified by
loading a sample; a full batch run produces complete per-case results and a
correctly-ordered 7-stage audit trail; the four evaluation categories stay
structurally separate (no recovered-revenue field leaks into A/B, no
backtest field leaks into C/D); overview metrics never substitute
synthetic/backtest numbers for observed recovery; predicted likelihood
never influences recovered revenue even when likelihoods are high;
`dashboard_data.py` and `app.py` never reference forbidden ground-truth
field names; a successful/attempted execution never implies recovery
without independent observation; the manual Payment Link checker reuses
`observe_recovery()` (proven by its dry-run output matching the same
interpretation rules) rather than inventing new logic; Test Mode labeling
is prominent across multiple sections; all required no-data labels are
present; audit trail case isolation holds through the dashboard layer;
empty-batch and unknown-case calls degrade gracefully; no secret leaks into
any case result or manual check output; and a full dashboard run leaves
`model.joblib` byte-identical.

## 11. Regression
Full suite from project root: **242/242** passing (220 prior baseline + 22
new). Step 2 dataset validation: 22/22. `model.joblib`, `metrics_report.json`,
and all four dataset CSVs verified byte-identical to their pre-Step-12
checksums. Only one Razorpay client class exists anywhere in the project.

## 12. Limitations
- Genuine live payment observation cannot be demonstrated *from this
  sandbox* (network block, unchanged since Step 7/10/11) — the manual
  Payment Link checker is real, tested, and ready, but has only ever
  returned `dry_run`/`not_observed` here.
- Streamlit's own UI rendering isn't unit-tested by `pytest` (not
  practically possible without a browser) — instead it was verified by an
  actual headless launch during implementation, documented in §4.
- No authentication/multi-user support — appropriate for a hackathon demo,
  not a production deployment.
- The audit store used by the dashboard is in-memory per session
  (`AuditStore(":memory:")`) — restarting the app clears prior runs; this
  is intentional for a clean demo state, not a persistence bug.

## 13. This is the final step
Step 12 completes the originally-planned 12-step roadmap. No Step 13 or
further feature work was started.
