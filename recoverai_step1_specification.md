# RecoverAI — Step 1 Product Specification
**Track:** Razorpay Hackathon — Track 03: AI Revenue Recovery
**Status:** LOCKED — Step 1 COMPLETE. This is the canonical product specification for the rest of the build. No application code implemented yet.

---

## 1. Users
- **Merchant operations / finance team** — primary user. Wants recovered revenue with minimal manual effort and enough trust in the agent to let it act autonomously within limits.
- **Approver (merchant admin/finance lead)** — reviews and approves/rejects APPROVAL_REQUIRED cases.
- **Hackathon judge** — needs to see reasoning and results transparently in a short live demo.
- **Customer (indirect)** — receiver of recovery communications; several guardrails exist specifically to protect them (opt-out, contact hours, attempt caps).

## 2. Revenue Leakage Categories
1. **Failed Payments** — UPI timeout, bank decline, insufficient funds, network failure, authentication failure. Interventions: retry, payment link, alternate payment method, reminder, escalate, stop.
2. **Checkout Abandonment** — Interventions: reminder, payment link, personalized message, bounded incentive (where permitted), escalate, stop.
3. **Failed Subscriptions** — Interventions: mandate retry, payment link, reminder, reschedule retry, escalate, stop.
4. **Overdue Receivables** — Interventions: reminder, payment link, follow-up, escalate, stop.

## 3. Agent Workflow — Stage Contracts

| Stage | Input | Output |
|---|---|---|
| DETECT | Raw transaction/event record | Flagged case with category, assigned deterministically |
| DIAGNOSE | Case + customer/transaction history (facts) | Structured AI diagnosis: root cause, risk factors, recovery likelihood — labeled as prediction |
| DECIDE | Diagnosis + action toolbox | AI-recommended action + confidence + explanation — labeled as recommendation, not authorization |
| GUARDRAIL | Recommended action + deterministic rules | AUTO_EXECUTE / APPROVAL_REQUIRED / STOP + the rule(s) that fired |
| EXECUTE | Authorized action only | Actual Razorpay test-mode API call, or controlled no-op if stopped |
| RECOVER | API result | Real-world observed outcome |
| MEASURE | Outcomes across batch | Computed ₹ metrics, recovery rate, escalations, stops |

Every stage's output is logged before the next stage executes — the audit trail is a structural byproduct, not an afterthought.

## 4. Recovery Action Toolbox

| Action | Intent | Risk Level |
|---|---|---|
| Retry payment | Re-attempt failed charge | Low |
| Generate payment link | Give a fresh payment path | Low |
| Reminder | Nudge customer | Low |
| Suggest alternate payment method | Route around method-specific failure | Low–Medium |
| Bounded incentive/discount | Increase conversion likelihood | Medium–High |
| Mandate retry (subscriptions) | Re-attempt subscription charge | Low |
| Reschedule retry | Delay and retry later | Low |
| Follow-up (receivables) | Escalating reminder cadence | Low–Medium |
| Escalate to human | Hand off to merchant staff | N/A |
| Stop | Take no further action | N/A |

## 5. AI Responsibility — Strict Separation [LOCKED]
- **Deterministic facts**: dataset fields as-is. Never generated or altered by the LLM.
- **AI/model reasoning (diagnosis)**: interpretation of facts — cause, risk factors, recovery likelihood. Always labeled as prediction, always shown with confidence.
- **AI recommendation (decision)**: proposed action + explanation. Labeled as recommendation only — not an authorization to act.
- **Deterministic policy decision (guardrail)**: rule-engine output (code, not a model call) that actually authorizes or blocks money-adjacent actions.
- **Actual API result**: ground truth from Razorpay test mode. Only this may ever be reported as "recovered." The LLM never asserts a payment succeeded.

### 5a. Detected-at-risk vs. actually-recovered [LOCKED — critical correction]
The architecture must keep these two concepts structurally separate, not just semantically:
- **Revenue detected as at risk** — a DETECT-stage output. This is a fact derived from the dataset (a failed/abandoned/overdue amount), not a prediction.
- **Recovery likelihood** — a DIAGNOSE-stage AI prediction. This is a probability estimate used for prioritization and decision-making. It is never money.
- **Revenue actually recovered** — only ever derived from an observed, real RECOVER-stage outcome (a successful Razorpay test-mode API result, or an equivalent verifiable confirmation). Predicted recovery probability must never be multiplied into, summed into, or otherwise allowed to contribute to the "₹ recovered" metric.

Practically, this means the data model must store `amount_at_risk` (fact), `predicted_recovery_likelihood` (AI output, kept separate), and `amount_recovered` (fact, populated only after a real observed outcome) as distinct fields that are never collapsed into one number until the MEASURE stage explicitly reports them side by side.

## 6. Guardrails (Configurable Defaults — NOT empirically validated)
These values are placeholder design defaults, not numbers derived from data or experiments. They exist so the guardrail *engine* has a sane starting configuration to test against, and they will be recalibrated once the synthetic dataset (Step 2) gives us real distributions. They must live in configuration (e.g. a single config file/object read by the guardrail engine), never hardcoded inline across the codebase, so they can be tuned without touching logic.

Configurable parameters (all adjustable, all logged with whatever value was active at decision time):
- `retry_limit` — max automated payment retries per case (default: 3)
- `autonomous_attempt_cap` — max total autonomous recovery touches per case within a lookback window (default: 3 within 7 days)
- `confidence_threshold` — minimum AI confidence for AUTO_EXECUTE eligibility (default: 0.6)
- `monetary_ceiling` — cap on incentive/discount, as the lower of a fixed ₹ amount or % of transaction value (defaults TBD once real amount distributions exist)
- `contact_window` — allowed local time range for customer-facing messages (default: a standard daytime window, exact hours TBD)

Rule logic using these parameters:
- **AUTO_EXECUTE**: low-risk action (retry/link/reminder/reschedule); customer not opted out; within `contact_window`; attempts so far < `autonomous_attempt_cap`; no fraud/suspicious flag; AI confidence ≥ `confidence_threshold`.
- **APPROVAL_REQUIRED**: any action with direct monetary cost above `monetary_ceiling`; aggressive actions (beyond reminder/link) on high-value transactions; AI confidence below `confidence_threshold`; escalating receivables follow-ups.
- **STOP**: customer opted out; suspicious/fraud-flagged; `retry_limit` or `autonomous_attempt_cap` reached; outside `contact_window` with no valid reschedule slot; hard API failure after one retry.
- **API failure handling**: one automatic retry with backoff, then STOP + logged failure — never silent.

## 7. Human-in-the-Loop
Merchant approval required when: money is directly offered to the customer; the transaction is high-value and the action is more aggressive than a reminder/payment link; the case is flagged suspicious; or AI confidence is below threshold.

## 8. Success Criteria (Final Proof)
Revenue processed, revenue at risk, cases analyzed, recovery opportunities, actions attempted, successful recoveries, revenue recovered, recovery rate, net recovered revenue, recovery cost, escalated cases, stopped cases, failed actions, fallback actions. Where applicable and computed on held-out data: precision, recall, F1, false-positive rate, false-positive cost. No metric reported unless actually calculated from a real run.

## 9. Final Demo Scenario (~3 minutes)
1. Batch overview: total revenue processed, revenue at risk
2. One case walked live end-to-end: DETECT → DIAGNOSE → DECIDE → GUARDRAIL → EXECUTE → RECOVER
3. One APPROVAL_REQUIRED or STOP case (guardrails are real, not decorative)
4. One graceful failure (handled safely)
5. Audit trail for the case walked through
6. Batch dashboard: ₹ at risk → ₹ attempted → ₹ recovered, recovery rate, escalations/stops

## 10. Architecture Direction [LOCKED — lightweight, no premature scaffolding]

```
recoverai/
  data/             synthetic dataset (Step 2)
  diagnosis/        AI diagnosis layer (Step 3)
  actions/          recovery action toolbox definitions (Step 4)
  decision_engine/  AI recovery decision engine (Step 5)
  guardrails/        deterministic policy engine (Step 6)
  integrations/
    razorpay/         test-mode API client (Step 7)
  audit/              audit trail storage + retrieval (Step 8)
  evaluation/          metrics computation (Step 10-11)
  orchestrator/         ties DETECT→MEASURE together
  dashboard/             Streamlit demo UI (Step 12)
  docs/                  spec + architecture notes
  config/                guardrail thresholds, limits (config, not hardcoded)
```

**Finalized tech direction:**
- Language: Python throughout
- API layer: FastAPI, introduced only where an actual API layer is useful (e.g. if the orchestrator needs to be called from a separate process) — not added speculatively
- AI reasoning: Anthropic API (Claude), structured/JSON outputs for diagnosis and decision stages
- Data: pandas/NumPy for the synthetic dataset and evaluation
- Storage: SQLite for audit trail/state
- Frontend: Streamlit (or another minimal Python frontend) for the hackathon dashboard — no React unless a concrete need emerges later
- No frontend infrastructure is built merely for the sake of having it

**Build priority order:** working agent > evaluation > Razorpay integration > reliability > dashboard polish.

---

## Open Risk (flagged, not blocking)
This project's file state currently lives in a conversation-scoped sandbox, not a durable repo. For a 12-step multi-session build, recommend either staying in one continuous conversation with periodic downloadable snapshots, or initializing a real Git repository early.
