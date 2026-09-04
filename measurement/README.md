# RecoverAI — Batch Revenue Recovery Measurement (Step 10)

## 1. Scope
Aggregates per-case results from Steps 5/6/9/10 into a single batch report.
**Pure aggregation** — runs no diagnosis, decision, guardrail, execution, or
recovery-observation logic; it only sums what already happened.

## 2. Files
```
recoverai/measurement/
  measurement_models.py     BatchEntry (per-case input) + BatchMeasurement (aggregate output)
  batch_measurement.py         compute_batch_measurement() — the aggregation logic
  test_measurement.py            19 tests
  README.md                        this file
```

## 3. The one rule this module exists to protect
`total_amount_recovered` is computed **exclusively** by summing
`recovery_result["amount_recovered"]` for entries whose
`recovery_result["recovery_status"]` is `"recovered"` or `"partially_recovered"`.
Nothing else — not a successful `execution_status`, not a guardrail outcome,
not `predicted_recovery_likelihood`, not Step 2 ground truth — can
contribute. This is enforced **structurally**: `BatchEntry` has no
`predicted_recovery_likelihood`, `diagnosis_confidence`, or `ground_truth_*`
field at all (verified by `test_batch_entry_has_no_likelihood_or_ground_truth_fields`),
so there is no code path by which they could leak in.

## 4. Recovery rate — explicit definition
```
recovery_rate = total_amount_recovered / total_amount_at_risk
```
**Denominator is total amount at risk across the whole batch** — every
leakage case's `amount_at_risk`, whether or not an action was ever
attempted on it — not `total_amount_processed` (amount actually attempted).
This answers the question the Step 1 spec's headline framing asks: *"₹ at
risk → ₹ recovered."* A narrower "recovery rate among attempted cases only"
(`total_amount_recovered / total_amount_processed`) is a different, also
valid question — it is **not** what `recovery_rate` reports here, and anyone
wanting that ratio can compute it themselves from the two numbers, both
reported separately. Verified explicitly by
`test_recovery_rate_denominator_is_total_amount_at_risk_not_processed`.

## 5. Metrics computed
| Field | Meaning |
|---|---|
| `cases_analyzed` | every case in the batch (including "successful"/non-leakage) |
| `recovery_opportunities` | leakage cases only |
| `total_amount_at_risk` | fact, sum of `amount_at_risk` |
| `total_amount_processed` | sum of `amount_at_risk` where SOME execution attempt was made |
| `total_amount_recovered` | sum from OBSERVED RECOVER results only — see §3 |
| `recovery_rate` | see §4 |
| `recovery_cost` | `None` — not modeled anywhere in this project (see §6) |
| `net_recovered_revenue` | `total_amount_recovered - (recovery_cost or 0)` |
| `actions_attempted` | count of individual attempts: primary + fallback (if made) + escalation (if reached) |
| `successful_executions` | EXECUTE succeeded (`dry_run`/`simulated`/`executed`) — **not** the same as recovered |
| `failed_executions` | EXECUTE genuinely failed (`api_error`/`error`) |
| `fallback_actions` | Step 9 `FALLBACK_SUCCEEDED` count |
| `escalated_cases` | Step 9 `ESCALATED` **plus** cases where Step 5 recommended `escalation` directly and Step 6 marked it `APPROVAL_REQUIRED` (no EXECUTE failure ever occurred — still a genuine escalation) |
| `stopped_cases` | primary guardrail outcome `STOP` |
| `approval_required_cases` | primary guardrail outcome `APPROVAL_REQUIRED` |
| `unresolved_recovery_cases` | leakage cases where an action WAS attempted but no `RECOVERED`/`PARTIALLY_RECOVERED` observation exists yet — distinct from "failed": the action may have worked, payment just isn't confirmed |

## 6. `recovery_cost` — honestly `None`, not fabricated
No per-action cost model exists anywhere in this project (Step 3's
false-positive-cost analysis reported *exposure*, not a computed cost, for
the identical reason — no pricing data has ever been introduced).
`net_recovered_revenue` therefore equals `total_amount_recovered` (cost
treated as 0), documented as a simplification in
`BatchMeasurement.recovery_cost_note`, not silently presented as a real
saving figure.

## 7. Escalation double-counting logic, explained
A case is "escalated" via two independent, legitimate paths:
1. Step 9 had to escalate after a genuine EXECUTE failure and an unsafe/
   failed fallback (`failure_handling_result.escalated == True`).
2. Step 5 recommended `'escalation'` **directly** (e.g. a LOW-likelihood-tier
   case) and Step 6 marked it `APPROVAL_REQUIRED` — no EXECUTE failure ever
   happened, since escalation never calls Razorpay at all.

Both are real escalations for batch-reporting purposes; conflating them
with "failures" would misrepresent path (2) as something having broken,
when the system worked exactly as designed. Verified by
`test_escalated_via_step9_counted_correctly` and
`test_escalated_via_direct_step5_recommendation_counted_correctly`.

## 8. Tests (19/19 passing)
Covers: recovered revenue sums only observed RECOVER results; a successful
execution with no recovery observation contributes zero to recovered
revenue (while still counting toward `successful_executions` and
`total_amount_processed`, correctly labeled and separated); dry-run/simulated
executions never count as recovered; `BatchEntry` structurally cannot carry a
likelihood/ground-truth field; tampered/extraneous dict keys on a
recovery_result are ignored; no forbidden field referenced in the module's
actual code; `FALLBACK_SUCCEEDED` and `ESCALATED` (both paths) represented
correctly; `NO_FAILURE` represented correctly; STOP and APPROVAL_REQUIRED
cases correctly show zero processed amount and zero attempted actions;
successful/non-leakage cases excluded from `recovery_opportunities` but
still counted in `cases_analyzed`; the full Step 1 §9 metric set is present;
recovery rate is zero (not a division error) with no risk in the batch; the
recovery-rate denominator is explicitly `total_amount_at_risk`, proven with
a batch where the two possible denominators would give different answers;
`recovery_cost` is `None` and documented, not fabricated; and
`unresolved_recovery_cases` is correctly counted only for genuinely-attempted-
but-unconfirmed cases, never for cases that were never attempted at all.

## 9. Regression
Full suite from project root: **198/198** passing (161 prior baseline + 18
new RECOVER tests + 19 new MEASURE tests). Step 2 dataset validation: 22/22.
`model.joblib`, `metrics_report.json`, and all four dataset CSVs verified
byte-identical to their pre-Step-10 checksums — nothing retrained or
regenerated. Only one Razorpay client class exists anywhere in the project
(confirmed by source scan) — no second client was created.

## 10. A real end-to-end example (run during implementation)
Three real dataset cases, real diagnosis/decision/guardrail/failure-handling,
real (network-blocked, honestly-failing) execution attempts:
```
CASE00010 | primary: recovery_payment_link | guardrail: auto_execute | step9: escalated | recovery: not_observed 0.0
CASE00012 | primary: recovery_payment_link | guardrail: auto_execute | step9: escalated | recovery: not_observed 0.0
CASE00014 | primary: recovery_payment_link | guardrail: auto_execute | step9: escalated | recovery: not_observed 0.0

{
  "cases_analyzed": 3, "recovery_opportunities": 3,
  "total_amount_at_risk": 9729.91, "total_amount_processed": 9729.91,
  "total_amount_recovered": 0.0, "recovery_rate": 0.0,
  "escalated_cases": 3, "unresolved_recovery_cases": 3
}
```
`total_amount_processed` (₹9,729.91) and `total_amount_recovered` (₹0.00)
are deliberately different numbers — the entire point of this module. Every
link-creation attempt genuinely failed (sandbox network block), Step 9
correctly escalated after an unsuccessful fallback, and RECOVER correctly
reports nothing was observed as paid — no number here is invented.

## 11. Limitations
- No live payment was ever confirmed (see `recovery/README.md` §9) —
  `total_amount_recovered` in every run performed in this environment is
  honestly `0.0`, not because the logic is wrong, but because no real
  payment has ever occurred to measure.
- `unresolved_recovery_cases` treats "checked once, not yet paid" and "never
  checked at all" as equally unresolved — a real deployment might want to
  distinguish these more granularly (e.g. retry-check scheduling), which is
  out of scope here.
- No time-windowed batching (e.g. "this week's cohort") — `compute_batch_measurement`
  takes whatever list of `BatchEntry` it's given; grouping/scheduling belongs
  to a future orchestrator, not this module.

## 12. Ready for Step 11
`BatchMeasurement.per_case` gives Step 11 (Evaluation & Metrics) a ready-made,
already-honest per-case dataset to compute precision/recall/business-impact
metrics against, without needing to re-derive anything from raw audit events.
