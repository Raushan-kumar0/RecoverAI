# RecoverAI — Synthetic Merchant Dataset (Step 2)

## 1. Purpose
Provides ~1,000 realistic, reproducible synthetic merchant revenue-lifecycle cases
covering the four revenue-leakage categories locked in the Step 1 specification,
plus a successful/non-leakage baseline. Used as the foundation for diagnosis
(Step 3), decision-making (Step 5), guardrail testing (Step 6), simulated
recovery execution (Step 7+), and evaluation (Step 11).

## 2. Files
- `generate_dataset.py` — the generator. Deterministic given `SEED` (default 42).
- `validate_dataset.py` — runs 22 integrity/consistency/leakage checks against the output.
- `recoverai_cases.csv` — full dataset (1,000 rows, includes `split` column).
- `train.csv` / `validation.csv` / `test.csv` — the same data pre-split, `split` column dropped.
- `README.md` — this file.

## 3. Leakage categories
`failed_payment`, `checkout_abandonment`, `failed_subscription`, `overdue_receivable`,
plus `successful` as the non-leakage baseline (so the dataset isn't just a failure
collection). Category assignment is **not** forced to fixed quotas — it's sampled
conditionally on a customer's behavioral profile (see §5), so category mix is
realistic rather than artificially balanced. Actual generated distribution (seed 42):

| Category | Count |
|---|---|
| successful | 402 |
| failed_payment | 167 |
| checkout_abandonment | 158 |
| failed_subscription | 141 |
| overdue_receivable | 132 |

## 4. Ground truth definitions [LOCKED per Step 1 §5a]
- **`amount_at_risk`** — FACT. The exposed amount for a leakage case; `0` for successful cases. Known at DETECT time, before any decision.
- **`predicted_recovery_likelihood`** — does **not** exist in this dataset. It is an AI output generated later in Step 3 from the pre-decision features below. Never fabricated here.
- **`amount_recovered`**, **`ground_truth_recoverable`**, **`ground_truth_recovery_outcome`**, **`recovery_observed`**, **`recovery_reason`** — OBSERVED / GROUND-TRUTH OUTCOME fields. Known only after the fact. Never usable as input features for diagnosis or decisioning.

## 5. Final schema

### Pre-decision features (available before any AI decision)
| Field | Type | Notes |
|---|---|---|
| `case_id` | string | unique |
| `transaction_id` | string | unique |
| `customer_id` | string | repeat customers appear across multiple cases |
| `event_type` | categorical | granular event label, 1:1 with `leakage_category` |
| `leakage_category` | categorical | `successful` / `failed_payment` / `checkout_abandonment` / `failed_subscription` / `overdue_receivable` |
| `amount` | float | transaction/invoice amount |
| `amount_at_risk` | float | FACT — see §4 |
| `timestamp` | datetime | event time |
| `payment_method` | categorical | UPI / Card / Netbanking / Wallet / EMI |
| `payment_status` | categorical | success / failed / abandoned / failed_recurring / overdue |
| `failure_reason` | categorical | populated only for `failed_payment`/`failed_subscription`; correlated with `payment_method` |
| `checkout_started`, `checkout_completed` | bool | populated only for checkout-flow categories (`successful`, `failed_payment`, `checkout_abandonment`) |
| `subscription_status`, `mandate_status` | categorical | populated only for `failed_subscription` |
| `invoice_status`, `invoice_due_date`, `days_overdue` | mixed | populated only for `overdue_receivable` |
| `retry_count` | int (0-4) | automated payment/mandate retries already attempted; applies to `failed_payment`/`failed_subscription` only |
| `previous_attempt_count` | int (0-5) | broader prior recovery-touch count (reminders/links/follow-ups already tried), any leakage category |
| `customer_purchase_count`, `customer_success_rate`, `customer_lifetime_value` | numeric | customer history, generated from a latent `customer_type` |
| `previous_payment_behavior` | categorical | new / occasional_failure / reliable / frequent_failure |
| `customer_opt_out`, `suspicious_flag` | bool | guardrail-relevant customer flags |
| `communication_allowed` | bool | derived: `not opt_out and not suspicious`. Time-of-day contact-window eligibility is a runtime guardrail concern (Step 6), not baked into the dataset |
| `historical_recovery_behavior` | categorical | descriptive summary of the customer's past pattern |

### Ground-truth / post-action outcome fields (never input features)
| Field | Type | Notes |
|---|---|---|
| `ground_truth_recoverable` | bool | `True` iff outcome == `recovered` |
| `ground_truth_recovery_outcome` | categorical | `recovered` / `not_recovered` / `not_applicable` (successful cases) |
| `amount_recovered` | float | actual simulated recovered amount; `0` unless outcome == `recovered` |
| `recovery_observed` | bool | whether a recovery process/outcome was observed at all (`False` for successful cases — nothing to recover) |
| `recovery_reason` | categorical | short descriptive label for why the outcome occurred (e.g. `retry_succeeded`, `opted_out_no_contact_possible`) — descriptive metadata, not a numeric label |

### Metadata
| Field | Type | Notes |
|---|---|---|
| `split` | categorical | `train` / `validation` / `test` — present in `recoverai_cases.csv` only; the three split CSVs already have this column removed since the filename encodes it |

## 6. Generation assumptions
- Each case is linked to one of 450 synthetic customers, sampled with replacement so that repeat customers (weighted toward `reliable`/`high_value`/`risky` types) build up realistic purchase/failure histories.
- `customer_type` (new / occasional / reliable / high_value / risky) is a **latent** variable used only to drive correlated distributions — it is not exposed as a raw column; its effects surface through `customer_purchase_count`, `customer_success_rate`, `customer_lifetime_value`, `previous_payment_behavior`, and category likelihood.
- `failure_reason` is sampled conditionally on `payment_method` (e.g. UPI timeout is more likely for UPI than for Netbanking).
- `retry_count` is sampled conditionally on the customer's failure history tendency, not independently.
- Ground-truth recovery outcome is generated from a logistic function of pre-decision signals (customer success rate, retry count, communication eligibility, suspicious flag, category, days overdue, customer value) plus Gaussian noise — deliberately imperfect so downstream evaluation isn't trivially separable. See `simulate_recovery()` in `generate_dataset.py` for the exact formula.
- Opted-out customers are capped at a low residual recovery probability (organic payment only, since they cannot be contacted) rather than forced to zero, reflecting reality more honestly than an absolute rule.
- Overdue receivables can recover **partially** (recovered fraction sampled Beta-skewed toward but not always 100%); all other categories recover fully or not at all, since they represent single transactions rather than negotiable invoices.

## 7. Data-leakage prevention
- Ground-truth columns (§4) are generated strictly *after* all pre-decision features and are structurally separate (`GROUND_TRUTH_COLUMNS` vs `PRE_DECISION_COLUMNS` in code).
- `validate_dataset.py` explicitly checks (a) no column overlap between the two groups, and (b) `predicted_recovery_likelihood` — a Step 3 artifact — is absent from this dataset.
- The `split` assignment is computed from a hash of `customer_id` only (independent of any outcome field), so it cannot leak outcome information either.

## 8. Random seed
`SEED = 42` in `generate_dataset.py`. Verified: two runs with `SEED=42` produce a byte-identical CSV (same MD5 hash). Running with `SEED=7` produces a different, still-valid dataset — confirming the generator is genuinely seed-driven, not accidentally deterministic regardless of input.

## 9. Validation checks
`validate_dataset.py` runs 22 checks: duplicate IDs, unexpected missing values (vs. legitimate category-specific nulls), invalid/non-positive amounts, `amount_at_risk` consistency, impossible invoice dates, event/status-category consistency, checkout-abandonment logic, subscription-context completeness, retry-count bounds, customer-history plausibility, recovered-amount-not-exceeding-at-risk, outcome/amount consistency, category representation (≥50 cases each), customer-level split isolation, pre-decision/ground-truth column separation, and absence of the forbidden `predicted_recovery_likelihood` column. **All 22 currently pass.**

## 10. Split strategy
Customer-aware, deterministic, hash-based (`assign_split()` in `generate_dataset.py`): each `customer_id` is hashed with the seed to a value in [0,1), thresholded at 0.70/0.85 into train/validation/test. This guarantees **no customer appears in more than one split**, preventing a trivial form of leakage in later ML evaluation. Because splitting happens at the customer level, case-level proportions land close to but not exactly 70/15/15 (this run: 71.9% / 14.8% / 13.3%).

## 11. Limitations
- The ~74% overall recovery rate among leakage cases is a **modeling assumption** baked into `simulate_recovery()`, not an empirical benchmark from real merchant data. It should be treated as a starting point for evaluation, not a claimed real-world recovery rate.
- `customer_type` is latent/synthetic — there's no attempt to model a specific real merchant's actual customer base.
- Contact-hour eligibility is deliberately *not* encoded per-record (it's a runtime guardrail concern), so guardrail testing in Step 6 will need to simulate time-of-day separately.
- Partial recovery is only modeled for `overdue_receivable`; other categories are all-or-nothing, which is a simplification (e.g. partial subscription credit isn't modeled).
- This is Step 2 output only — no diagnosis, decision, guardrail, or Razorpay logic has been implemented or is implied by this dataset.
