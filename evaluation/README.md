# RecoverAI — Evaluation & Metrics (Step 11)

## 1. Scope
Evaluates the **complete pipeline**, not just the Step 3 ML model — model,
decision, guardrail, execution, recovery observation, and measurement — in
four **structurally separate** categories that are never merged:

| Category | What it measures | Data source |
|---|---|---|
| **A — ML_MODEL** | Step 3's diagnosis model performance | `diagnosis/metrics_report.json` (referenced, never recomputed) |
| **B — SYNTHETIC_BACKTEST** | Agent-level (diagnose→decide→guardrail) performance | Step 2's held-out `test.csv` ground truth — **synthetic, not live** |
| **C — LIVE_EXECUTION** | What the real pipeline actually attempted | Step 10's `BatchMeasurement`, execution fields only |
| **D — OBSERVED_RECOVERY** | What Razorpay actually confirmed was paid | Step 10's `BatchMeasurement`, recovery fields only |

## 2. Files
```
recoverai/evaluation/
  evaluation_models.py     four category dataclasses + EvaluationReport
  model_evaluation.py         Category A — read-only reference to Step 3
  synthetic_backtest.py         Category B — real agent run against held-out ground truth
  evaluation_report.py           assembles A+B+C+D, never merges them
  test_evaluation.py               22 tests
  README.md                          this file
```

## 3. Category A — model (unchanged, referenced)
`load_model_evaluation_summary()` reads `diagnosis/metrics_report.json` —
Step 3's already-locked precision/recall/F1/ROC-AUC/PR-AUC/confusion matrix/
per-category breakdown. **No retraining, no recomputation, no modification**
of `model.joblib` — verified by checksum-before/after and a source scan for
any training call. If the file is missing, this raises rather than
regenerating it.

## 4. Category B — synthetic backtest (the only sanctioned ground-truth use)
`run_synthetic_backtest()` runs the **real** `DiagnosisEngine`,
`DecisionEngine`, and `GuardrailEngine` — no shortcuts — across Step 2's
held-out `test.csv`, then compares each case's guardrail outcome
(`AUTO_EXECUTE` = "agent would act autonomously") against
`ground_truth_recoverable`, purely for **scoring after the fact**.

**This is the only module in the entire project that reads
`ground_truth_recoverable`/`amount_recovered` outside of Step 2's own
generation/validation code**, and it does so exactly as Step 1's spec
permits: *"Use held-out data where appropriate"* for evaluation. Every
result carries an explicit disclaimer
(`SyntheticBacktestResult.disclaimer`), and the field name itself
(`backtest_amount_recoverable_if_ground_truth_trusted`) makes clear this is
a what-if number, never to be presented as live recovered revenue.

Ground truth is read **only after** `diagnose()`/`decide()`/`authorize()`
have already produced their outcome — never passed in as an input. Verified
by `test_ground_truth_only_read_after_agent_decision_not_passed_in`: running
the same case with tampered ground truth produces an identical agent
decision, proving it has zero influence on the agent itself, only on the
backtest's scoring afterward.

Metrics computed: confusion matrix (TP/FP/TN/FN) and precision/recall/F1 **at
the AUTO_EXECUTE decision level** — this is a genuinely different, harder
question than Step 3's model-only metrics, since it also reflects Step 6's
guardrail filtering (confidence threshold, monetary ceiling, attempt caps),
not just the raw model probability.

## 5. Categories C & D — live execution and observed recovery
Both are built directly from a Step 10 `BatchMeasurement` (optional — `None`
if no live run is supplied, never fabricated to fill the gap).
`build_live_execution_summary()` reads only action/execution fields;
`build_observed_recovery_summary()` reads only recovery fields — from the
*same* source object, but structurally separated into two different
dataclasses with no overlapping fields, so a consumer physically cannot
read a "recovered" number out of the execution summary.

`ObservedRecoveryEvaluationSummary.genuine_payment_verified` is `True` only
if `total_amount_recovered > 0`; when `False`, the `limitation_note`
explicitly states this could mean either "nothing paid yet" or "genuine
payment could not be verified in this environment" — it does not claim to
know which, and points to the underlying `RecoveryResult` records for that
detail.

## 6. Report assembly — never merged
`assemble_evaluation_report()` returns an `EvaluationReport` with four
separate fields (`model`, `synthetic_backtest`, `live_execution`,
`observed_recovery`) and an explicit `category_separation_notice`. No
function anywhere in this package adds a category-B dollar figure to a
category-D dollar figure, or vice versa — verified by
`test_report_with_live_run_populates_c_and_d_from_same_source_correctly`
checking that `total_amount_recovered` never appears inside the C-category
dict at all.

## 7. Tests (22/22 passing)
Covers: Category A loads and does not modify Step 3's artifact, and raises
rather than regenerating if missing; Category B runs the real agent
components, confusion matrix sums correctly, carries an explicit
disclaimer, is deterministic, and is proven not to influence the agent's
own decision (only its scoring); no forbidden ground-truth field appears in
categories A/C/D's actual code; Category C excludes all recovery fields;
Category D correctly reports `genuine_payment_verified` both when something
was paid and when nothing was; the assembled report keeps all four
categories separate with no fabricated C/D when no live run is given;
guardrail STOP/APPROVAL_REQUIRED counts are never silently reclassified as
AUTO_EXECUTE "positives" in the backtest; live Razorpay credentials remain
rejected; no secret leaks into report output; and the Step 2 dataset file
is proven byte-unchanged after a full backtest run.

## 8. Regression
Full suite from project root: **220/220** passing (198 prior baseline + 22
new). Step 2 dataset validation: 22/22. `model.joblib`, `metrics_report.json`,
and all four dataset CSVs verified byte-identical to their pre-Step-11
checksums. Only one Razorpay client class exists anywhere in the project
(confirmed by source scan) — Step 11 added no new client.

## 9. A real run (captured during implementation)
**Category A** (Step 3, unchanged): precision 0.806, recall 1.000, F1 0.892
(model-only, no guardrail filtering).

**Category B** (synthetic backtest, held-out test split, 74 leakage cases):
auto_execute 37 / approval_required 37 / stop 0; TP 32, FP 5, TN 11, FN 26;
precision 0.865, recall 0.552, F1 0.674 — **at the agent-decision level**,
recall drops sharply versus the model-only number, because Step 6's
confidence/monetary guardrails correctly hold back many true positives for
approval rather than auto-executing them. This is expected and honest — it
shows the guardrail doing its job, not a regression. Synthetic
`backtest_amount_recoverable_if_ground_truth_trusted`: ₹84,391.72 (0.2047
backtest rate) — **explicitly labeled synthetic, never live**.

**Categories C & D** (real pipeline run, 2 cases, dry-run mode): C shows 2
cases processed, 2 successful executions, ₹10,451.95 attempted; D shows
`total_amount_recovered: 0.0`, `genuine_payment_verified: False` — honestly
reflecting that dry-run mode makes no real payment claim, exactly as
designed.

## 10. Limitations
- Category B's precision/recall is only as good as Step 2's synthetic
  ground-truth generation process (documented in `data/README.md`) — it is
  an internally-consistent backtest, not validation against real merchant
  outcomes.
- Categories C/D depend entirely on whatever live run the caller supplies;
  in this sandbox, D will always show `total_amount_recovered: 0.0` because
  no genuine payment has ever been observed (network-blocked, per Step 10).
- No cross-category "business impact" figure (e.g. "backtest suggests we'd
  recover X, live shows we've recovered Y so far") is computed — deliberately,
  since presenting those side-by-side risks visual conflation even if the
  numbers are technically correctly labeled. Judges/readers should read
  sections B and D as answering different questions.

## 11. Ready for Step 12
`EvaluationReport.to_dict()` gives Step 12's demo/dashboard everything it
needs, pre-separated into four clearly labeled sections — no further
computation should be needed to present an honest evaluation story.
