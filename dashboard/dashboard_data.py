"""
RecoverAI — Dashboard: Data Access Layer (Step 12)

Pure Python, NO Streamlit import here — this module is fully unit-testable
without a running UI. app.py (the Streamlit presentation layer) calls into
this module and nothing else for data; it contains no pipeline logic of its
own.

Every function here is a thin wrapper around an ALREADY-EXISTING, ALREADY-
TESTED Step 3-11 component. This module does not:
    - run diagnosis logic itself (calls DiagnosisEngine.diagnose)
    - run decision logic itself (calls DecisionEngine.decide)
    - run guardrail logic itself (calls GuardrailEngine.authorize)
    - run execution logic itself (calls handle_execution_with_fallback)
    - run recovery-observation logic itself (calls observe_recovery)
    - run measurement logic itself (calls compute_batch_measurement)
    - run evaluation logic itself (calls run_synthetic_backtest / load_model_evaluation_summary)
    - infer recovery from diagnosis, execution, or ground truth — it has no
      code path capable of doing so, by construction (see build_case_view()).
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
for sub in ("diagnosis", "decision_engine", "guardrails", "actions",
            "integrations/razorpay", "audit", "failure_handling",
            "recovery", "measurement", "evaluation", "mandate_sequencer", "promise_tracker",
            "routing_optimizer", "hinglish_recovery"):
    sys.path.insert(0, str(_PROJECT_ROOT / sub))

import pandas as pd  # noqa: E402

# ------------------------------------------------------------------ #
# Load Razorpay Test-Mode credentials from integrations/razorpay/.env
# if present, BEFORE anything reads os.environ for them. This is the
# minimal Step 12 addition: python-dotenv's load_dotenv() only fills in
# variables that aren't already set in the environment (it never
# overrides a real shell-exported value), and it never prints or logs
# the values it loads. razorpay_config.load_config_from_env() (Step 7,
# unmodified) is still the only place that actually reads/validates them.
try:
    from dotenv import load_dotenv
    _RAZORPAY_ENV_PATH = _PROJECT_ROOT / "integrations" / "razorpay" / ".env"
    if _RAZORPAY_ENV_PATH.exists():
        load_dotenv(_RAZORPAY_ENV_PATH)
except ImportError:
    pass  # python-dotenv not installed — falls back to whatever is already in the environment

from diagnose import DiagnosisEngine  # noqa: E402
from decision_engine import DecisionEngine  # noqa: E402
from guardrail_engine import GuardrailEngine  # noqa: E402
from action_compatibility import get_actions_for_case  # noqa: E402
from razorpay_config import load_config_from_env, CredentialError  # noqa: E402
from razorpay_client import RazorpayTestModeClient  # noqa: E402
from audit_store import AuditStore  # noqa: E402
from audit_recorder import (record_detection, record_diagnosis, record_candidate_actions,  # noqa: E402
                             record_decision, record_guardrail, record_execution, record_recovery)
from failure_handler import handle_execution_with_fallback  # noqa: E402
from recovery_checker import observe_recovery  # noqa: E402
from recovery_models import RecoveryStatus  # noqa: E402
from measurement_models import BatchEntry  # noqa: E402
from batch_measurement import compute_batch_measurement  # noqa: E402
from model_evaluation import load_model_evaluation_summary  # noqa: E402
from synthetic_backtest import run_synthetic_backtest  # noqa: E402
from evaluation_report import assemble_evaluation_report  # noqa: E402
import mandate_sequencer as _ms  # noqa: E402
from sequencer_models import SequenceStatus  # noqa: E402
from sequencer_store import get_sequence, save_sequence  # noqa: E402
import promise_checker as _pc  # noqa: E402
from promise_schema import PromiseStatus  # noqa: E402
from promise_store import PromiseStore  # noqa: E402
import route_optimizer as _ro  # noqa: E402
from route_catalog import PaymentRoute  # noqa: E402
import script_generator as _hg  # noqa: E402

DATA_CSV = _PROJECT_ROOT / "data" / "recoverai_cases.csv"
TEST_CSV = _PROJECT_ROOT / "data" / "test.csv"
MODEL_PATH = _PROJECT_ROOT / "diagnosis" / "model.joblib"
RESULTS_LOG = _PROJECT_ROOT / "real_results_log.jsonl"

# ------------------------------------------------------------------ #
# Engine / credential setup
# ------------------------------------------------------------------ #
def credentials_configured() -> bool:
    return bool(os.environ.get("RAZORPAY_KEY_ID")) and bool(os.environ.get("RAZORPAY_KEY_SECRET"))


def get_engines():
    """
    Returns (diagnosis_engine, decision_engine, guardrail_engine, razorpay_client_or_None, credential_error_or_None).
    Never raises — the dashboard must degrade gracefully (section I) rather
    than crash when credentials are missing or malformed.
    """
    diagnosis_engine = DiagnosisEngine(str(MODEL_PATH))
    decision_engine = DecisionEngine(diagnosis_engine=diagnosis_engine)
    guardrail_engine = GuardrailEngine()

    razorpay_client = None
    credential_error = None
    try:
        config = load_config_from_env()
        razorpay_client = RazorpayTestModeClient(config)
    except CredentialError as e:
        credential_error = str(e)

    return diagnosis_engine, decision_engine, guardrail_engine, razorpay_client, credential_error


def is_dry_run() -> bool:
    raw = os.environ.get("RECOVERAI_RAZORPAY_DRY_RUN")
    return True if raw is None else raw.strip().lower() != "false"


# ------------------------------------------------------------------ #
# Load genuinely observed real recovery results (ANY status — used both
# to decide "does this case already have a real link" and to compute
# the historical verified-recovered total).
# ------------------------------------------------------------------ #
def load_real_recovery_results() -> Dict[str, Dict[str, Any]]:
    """
    Reads real_results_log.jsonl — written by run_real_demo.py AND (as of
    this fix) by the dashboard itself via _persist_real_recovery(). Returns
    {case_id: latest_entry}, keeping only the most recent logged_at per
    case_id (a case can be checked more than once — e.g. pending, then
    later recovered — and only the latest observation should count).
    Includes entries of ANY recovery_status (not just "recovered"), because
    callers need to know "does this case already have a real Payment Link
    at all" (to avoid a duplicate-reference_id error), not just which ones
    ended up paid. Missing file / unreadable lines => {} (fail safe, never
    fabricate).
    """
    latest: Dict[str, Dict[str, Any]] = {}
    if not RESULTS_LOG.exists():
        return latest
    try:
        with open(RESULTS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                case_id = entry.get("case_id")
                if not case_id:
                    continue
                existing = latest.get(case_id)
                if existing is None or entry.get("logged_at", 0) > existing.get("logged_at", 0):
                    latest[case_id] = entry
    except OSError:
        return {}
    return latest


def _persist_real_recovery(case_id, leakage_category, amount_at_risk, execution_record, recovery_result):
    """
    Appends a genuine, real Razorpay Test Mode execution to real_results_log.jsonl
    — the SAME file and SAME schema run_real_demo.py writes. Before this fix, only
    run_real_demo.py ever wrote here, so any case executed through the dashboard
    left no record anywhere, causing repeated 'reference_id already exists' errors
    on re-run. Only called after a real (non-dry-run, non-simulated) Payment Link
    was actually created — never fabricates anything, purely records what already
    genuinely happened.
    """
    entry = BatchEntry(
        case_id=case_id, leakage_category=leakage_category, amount_at_risk=float(amount_at_risk),
        primary_guardrail_outcome="auto_execute", primary_recommended_action_type="recovery_payment_link",
        failure_handling_result={
            "outcome": "no_failure", "primary_execution": execution_record,
            "fallback_attempted": False, "fallback_execution": None, "escalated": False,
        },
        recovery_result=recovery_result,
    )
    measurement = compute_batch_measurement([entry])
    log_entry = {
        "logged_at": time.time(),
        "case_id": case_id,
        "leakage_category": leakage_category,
        "amount_at_risk": float(amount_at_risk),
        "payment_link_id": (execution_record.get("razorpay_result") or {}).get("razorpay_payment_link_id"),
        "execution_record": execution_record,
        "recovery_result": recovery_result,
        "batch_measurement_for_this_case": measurement.to_dict(),
    }
    try:
        with open(RESULTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, default=str) + "\n")
    except OSError:
        pass  # never let a logging failure break the dashboard run


# ------------------------------------------------------------------ #
# Loading the dataset (never modified, never regenerated)
# ------------------------------------------------------------------ #
def load_dataset_sample(n: int = 10, leakage_only: bool = True, comm_allowed_only: bool = False,
                         skip_already_executed: bool = True, category: str = None,
                         stratify_by_category: bool = True) -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV)
    if category:
        df = df[df["leakage_category"] == category]
    if leakage_only:
        df = df[df["leakage_category"] != "successful"]
    if comm_allowed_only:
        df = df[df["communication_allowed"] == True]  # noqa: E712
    if skip_already_executed:
        # Cases that already have a real Payment Link (from any past dashboard
        # or run_real_demo.py run) will only ever produce a duplicate-reference
        # API error if re-executed fresh. Skip them so "Run batch" always
        # surfaces cases that can actually be freshly executed. (Cases that
        # already have a link are still handled correctly if selected some
        # other way — see the reuse-prior-execution path below — this is
        # purely about what the batch sampler offers by default.)
        already_used = set(load_real_recovery_results().keys())
        if already_used:
            df = df[~df["case_id"].isin(already_used)]

    n = min(n, len(df))
    if n == 0:
        return df.head(0)

    if not stratify_by_category or category is not None or df["leakage_category"].nunique() <= 1:
        # No stratification possible/requested — plain random draw.
        return df.sample(n=n).reset_index(drop=True)

    # Stratified draw: spread n as evenly as possible across whichever
    # leakage_category values are actually present in the eligible pool right
    # now, so a small batch doesn't clump into one or two categories purely
    # by sampling variance. If a category has fewer available cases than its
    # even share, take what's there and top up from the remaining pool.
    groups = list(df.groupby("leakage_category"))
    base_quota = n // len(groups)
    remainder = n % len(groups)

    picked_frames = []
    leftover_frames = []
    for i, (_, group_df) in enumerate(groups):
        quota = base_quota + (1 if i < remainder else 0)
        take = min(quota, len(group_df))
        if take > 0:
            picked = group_df.sample(n=take)
            picked_frames.append(picked)
            leftover_frames.append(group_df.drop(picked.index))
        else:
            leftover_frames.append(group_df)

    picked = pd.concat(picked_frames) if picked_frames else df.head(0)
    still_needed = n - len(picked)
    if still_needed > 0:
        leftover_pool = pd.concat(leftover_frames) if leftover_frames else df.head(0)
        top_up = leftover_pool.sample(n=min(still_needed, len(leftover_pool)))
        picked = pd.concat([picked, top_up])

    # Shuffle final order so same-category cases aren't visually grouped together.
    return picked.sample(frac=1).reset_index(drop=True)


# ------------------------------------------------------------------ #
# Running one case through the full pipeline (reuses every stage as-is)
# ------------------------------------------------------------------ #
def run_case_through_pipeline(row, diagnosis_engine, decision_engine, guardrail_engine,
                               razorpay_client, audit_store: AuditStore,
                               current_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Runs DETECT -> DIAGNOSE -> DECIDE -> GUARDRAIL -> EXECUTE(+fallback/escalate)
    -> RECOVER for one case, logging every stage to the audit trail exactly
    as Steps 8/9/10 already do. Returns a dict bundling everything the UI
    needs to render sections B-E for this case — no new interpretation, just
    the real objects each stage produced.
    """
    current_time = current_time or datetime.now()
    case_id = row["case_id"]
    leakage_category = row["leakage_category"]

    record_detection(audit_store, row)

    diagnosis = diagnosis_engine.diagnose(row)
    record_diagnosis(audit_store, case_id, leakage_category, diagnosis)

    if leakage_category == "successful" or diagnosis.get("predicted_recovery_likelihood") is None:
        return {
            "case_id": case_id, "leakage_category": leakage_category, "diagnosis": diagnosis,
            "decision": None, "guardrail": None, "failure_handling_result": None, "recovery_result": None,
        }

    actions = get_actions_for_case(row, diagnosis=diagnosis)
    record_candidate_actions(audit_store, case_id, leakage_category, actions)

    decision = decision_engine.decide(row, diagnosis=diagnosis, actions=actions)
    record_decision(audit_store, decision)

    diag_for_guard = {"diagnosis_confidence": decision.diagnosis_confidence}
    guardrail = guardrail_engine.authorize(row, diag_for_guard, decision, current_time=current_time)
    record_guardrail(audit_store, guardrail)

    if razorpay_client is None:
        return {
            "case_id": case_id, "leakage_category": leakage_category, "diagnosis": diagnosis,
            "decision": decision, "guardrail": guardrail,
            "failure_handling_result": None, "recovery_result": None,
            "no_client_reason": "Razorpay credentials not configured — execution skipped.",
        }

    # If this case already has a real Payment Link (from THIS session, a past
    # dashboard session, or run_real_demo.py), re-executing would just hit
    # Razorpay's own duplicate-reference_id rejection. Reuse the existing
    # link and re-observe its current status instead of attempting a doomed
    # second CREATE. This is still a live observe_recovery() call to
    # Razorpay — nothing here is fabricated.
    prior = load_real_recovery_results().get(case_id)
    if prior is not None:
        prior_execution = prior.get("execution_record") or {}
        recovery_result = observe_recovery(case_id, leakage_category, prior_execution, razorpay_client)
        record_recovery(audit_store, recovery_result)
        return {
            "case_id": case_id, "leakage_category": leakage_category, "diagnosis": diagnosis,
            "decision": decision, "guardrail": guardrail,
            "failure_handling_result": {
                "primary_execution": prior_execution, "fallback_execution": None,
                "fallback_attempted": False, "escalated": False, "outcome": "reused_prior_execution",
            },
            "recovery_result": recovery_result.to_dict(),
            "reused_prior_execution": True,
        }

    fhr = handle_execution_with_fallback(row, diagnosis, decision, guardrail, razorpay_client,
                                          guardrail_engine, audit_store, current_time=current_time)

    exec_to_check = fhr.primary_execution
    if fhr.fallback_execution and fhr.fallback_execution.get("action_type") == "recovery_payment_link":
        exec_to_check = fhr.fallback_execution
    recovery_result = observe_recovery(case_id, leakage_category, exec_to_check, razorpay_client)
    record_recovery(audit_store, recovery_result)

    # Persist genuinely successful real executions so future runs (from
    # either the dashboard or run_real_demo.py) know this case already
    # has a real Payment Link, instead of hitting a duplicate-reference error.
    if (exec_to_check.get("execution_status") == "executed"
            and exec_to_check.get("result_source") == "razorpay_test_mode_api"):
        amount_at_risk = float(row.get("amount_at_risk", 0.0))
        _persist_real_recovery(case_id, leakage_category, amount_at_risk, exec_to_check, recovery_result.to_dict())

    return {
        "case_id": case_id, "leakage_category": leakage_category, "diagnosis": diagnosis,
        "decision": decision, "guardrail": guardrail,
        "failure_handling_result": fhr.to_dict(), "recovery_result": recovery_result.to_dict(),
    }


def run_batch(sample_df: pd.DataFrame, diagnosis_engine, decision_engine, guardrail_engine,
              razorpay_client, audit_store: AuditStore, current_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
    return [
        run_case_through_pipeline(row, diagnosis_engine, decision_engine, guardrail_engine,
                                   razorpay_client, audit_store, current_time)
        for _, row in sample_df.iterrows()
    ]


# ------------------------------------------------------------------ #
# Section A/F: batch measurement (Category C/D) — reuses Step 10 exactly
# ------------------------------------------------------------------ #
def build_batch_entries(case_results: List[Dict[str, Any]], sample_df: pd.DataFrame) -> List[BatchEntry]:
    amount_lookup = dict(zip(sample_df["case_id"], sample_df["amount_at_risk"]))
    entries = []
    for r in case_results:
        guardrail = r.get("guardrail")
        decision = r.get("decision")
        entries.append(BatchEntry(
            case_id=r["case_id"], leakage_category=r["leakage_category"],
            amount_at_risk=float(amount_lookup.get(r["case_id"], 0.0)),
            primary_guardrail_outcome=(guardrail.outcome.value if guardrail else None),
            primary_recommended_action_type=(decision.recommended_action_type if decision else None),
            failure_handling_result=r.get("failure_handling_result"),
            recovery_result=r.get("recovery_result"),
        ))
    return entries


def get_overview_metrics(case_results: List[Dict[str, Any]], sample_df: pd.DataFrame) -> Dict[str, Any]:
    entries = build_batch_entries(case_results, sample_df)
    bm = compute_batch_measurement(entries)

    recommended = sum(
        1 for r in case_results
        if r.get("decision")
        and r["decision"].decision_status.value == "recommended"
    )

    guardrail_approved = sum(
        1 for r in case_results
        if r.get("guardrail")
        and r["guardrail"].outcome.value == "auto_execute"
    )

    # Historical, cross-session, genuinely Razorpay-verified recoveries —
    # kept as its OWN field below, never blended into observed_recovered_revenue.
    # observed_recovered_revenue must stay scoped to THIS batch only (see
    # test_dashboard.py: test_overview_metrics_never_use_backtest_numbers,
    # test_overview_recovered_revenue_not_derived_from_likelihood,
    # test_overview_metrics_on_empty_batch — all three assert it's 0.0 when
    # nothing in the current batch was actually paid).
    real_recoveries = load_real_recovery_results()
    recovered_entries = [
        e for e in real_recoveries.values()
        if (e.get("recovery_result") or {}).get("recovery_status") in ("recovered", "partially_recovered")
    ]
    historical_verified_recovered_revenue = sum(
        float((e.get("recovery_result") or {}).get("amount_recovered") or 0.0)
        for e in recovered_entries
    )

    return {
        "revenue_at_risk": bm.total_amount_at_risk,
        "cases_analyzed": bm.cases_analyzed,
        "recommended_actions": recommended,
        "guardrail_approved_actions": guardrail_approved,
        "actions_executed": bm.actions_attempted,
        "approval_required_cases": bm.approval_required_cases,
        "stopped_cases": bm.stopped_cases,

        "observed_recovered_revenue": bm.total_amount_recovered,  # THIS batch only
        "recovery_rate": bm.recovery_rate,

        # NEW — all-time, cross-session total, kept separate on purpose
        "historical_verified_recovered_revenue": round(historical_verified_recovered_revenue, 2),
        "historical_verified_case_count": len(recovered_entries),

        "batch_measurement": bm,
    }


# ------------------------------------------------------------------ #
# Section F: full 4-category evaluation report — reuses Step 11 exactly
# ------------------------------------------------------------------ #
# Mandate Retry Sequencer — opt-in, separate from run_case_through_pipeline.
# Does NOT change what happens to mandate_retry cases in a normal batch run
# (those stay exactly as tested: one simulated attempt, NOT_OBSERVED). This
# is a deliberately separate path you point at a specific case_id.
# ------------------------------------------------------------------ #
def get_case_row(case_id: str) -> Optional[Dict[str, Any]]:
    df = pd.read_csv(DATA_CSV)
    match = df[df["case_id"] == case_id]
    if match.empty:
        return None
    return match.iloc[0].to_dict()


def advance_mandate_sequence(case_id: str, diagnosis_engine, guardrail_engine, razorpay_client,
                              simulated_current_time: datetime):
    """
    Loads (or starts) case_id's retry sequence, runs every attempt that is
    due as of simulated_current_time, triggers the real fallback once
    exhausted, and checks the fallback's real recovery status. Persists
    after every change. A genuinely recovered fallback also gets written to
    real_results_log.jsonl (same _persist_real_recovery every other real
    execution uses), so it counts toward Historical Verified Recoveries too.

    simulated_current_time exists because real 3/7-day waits aren't
    feasible in a live demo — same principle as the rest of this project's
    testing convention (current_time is always passed explicitly, never
    read from the real clock inside core logic).
    """
    case = get_case_row(case_id)
    if case is None:
        raise ValueError(f"Unknown case_id: {case_id!r} — not found in {DATA_CSV.name}.")

    sim_ts = simulated_current_time.timestamp()

    sequence = get_sequence(case_id)
    if sequence is None:
        sequence = _ms.start_sequence(case, current_time=sim_ts)

    while True:
        due = _ms.get_due_attempt(sequence, current_time=sim_ts)
        if due is None:
            break
        sequence = _ms.run_due_attempt(sequence, razorpay_client, current_time=sim_ts)

    if sequence.status == SequenceStatus.EXHAUSTED:
        diagnosis = diagnosis_engine.diagnose(case)
        sequence = _ms.trigger_fallback(sequence, case, diagnosis, guardrail_engine, razorpay_client,
                                         current_time=simulated_current_time)

    if sequence.status in (SequenceStatus.FALLBACK_TRIGGERED, SequenceStatus.FALLBACK_RECOVERED):
        sequence = _ms.check_fallback_recovery(sequence, razorpay_client)
        if sequence.status == SequenceStatus.FALLBACK_RECOVERED:
            _persist_real_recovery(case_id, sequence.leakage_category, sequence.amount_at_risk,
                                    sequence.fallback_execution_record, sequence.fallback_recovery_result)

    save_sequence(sequence)
    return sequence


def get_mandate_sequence(case_id: str):
    """Read-only lookup — for displaying a sequence's current state without advancing it."""
    return get_sequence(case_id)


# ------------------------------------------------------------------ #
# Promise-to-Pay Tracker — also opt-in, also separate from the normal
# pipeline. A promise is recorded manually (there's no real customer-facing
# capture form in this project) and only ever independently verified via
# the SAME observe_recovery() every other real check uses.
# ------------------------------------------------------------------ #
def record_new_promise(store: PromiseStore, case_id: str, leakage_category: Optional[str],
                        promised_amount: float, promise_date: str, payment_link_id: Optional[str] = None) -> int:
    return _pc.record_promise(store, case_id, leakage_category, promised_amount, promise_date, payment_link_id)


def check_promise_status(store: PromiseStore, promise_id: int, razorpay_client,
                          current_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Re-evaluates one promise. If it genuinely becomes HONORED, also
    persists to real_results_log.jsonl (same _persist_real_recovery every
    other real execution uses) so an honored promise counts toward
    Historical Verified Recoveries too — it IS a genuine Razorpay-confirmed
    payment, same as any other.
    """
    result = _pc.check_promise(store, promise_id, razorpay_client, current_time)
    if result["status"] == PromiseStatus.HONORED.value and result.get("payment_link_id"):
        synthetic_execution_record = {
            "case_id": result["case_id"], "leakage_category": result["leakage_category"],
            "action_type": "recovery_payment_link", "execution_status": "executed",
            "result_source": "razorpay_test_mode_api", "reason": "Promise-to-pay verification.",
            "razorpay_result": {"razorpay_payment_link_id": result["payment_link_id"]},
        }
        recovery_result_dict = {
            "case_id": result["case_id"], "leakage_category": result["leakage_category"],
            "recovery_status": "recovered", "amount_recovered": result["promised_amount"],
            "observation_source": "razorpay_payment_link_status", "payment_link_id": result["payment_link_id"],
            "reason": result["reason"],
        }
        _persist_real_recovery(result["case_id"], result["leakage_category"], result["promised_amount"],
                                synthetic_execution_record, recovery_result_dict)
    return result


def attach_link_to_promise(store: PromiseStore, promise_id: int, payment_link_id: str) -> Dict[str, Any]:
    """
    Attaches a real Payment Link to a promise that was recorded without one —
    the common real-world flow: a customer verbally commits to pay, THEN a
    Payment Link gets created. Does not change status by itself; the next
    check_promise_status() call is what actually verifies payment.
    """
    _pc.link_payment_link(store, promise_id, payment_link_id)
    return store.get_promise(promise_id)


def escalate_promise_status(store: PromiseStore, promise_id: int, reason: str) -> Dict[str, Any]:
    _pc.escalate_promise(store, promise_id, reason)
    return store.get_promise(promise_id)


def get_all_promises(store: PromiseStore, status: Optional[str] = None):
    status_enum = PromiseStatus(status) if status else None
    return store.get_all_promises(status_enum)


def get_promise_counts(store: PromiseStore) -> Dict[str, int]:
    return store.count_by_status()


# ------------------------------------------------------------------ #
# Payment Routing Optimizer — also opt-in, also separate from the normal
# pipeline. See routing_optimizer/README.md's honesty statement: only the
# direct_payment_link route is genuinely real+executable; every other
# recommended route is honestly SIMULATED when "executed" here.
# ------------------------------------------------------------------ #
def get_routing_decision(case_id: str):
    case = get_case_row(case_id)
    if case is None:
        raise ValueError(f"Unknown case_id: {case_id!r} — not found in {DATA_CSV.name}.")
    return _ro.select_optimal_route(case)


def execute_routing_decision(decision, diagnosis_engine, guardrail_engine, razorpay_client,
                              current_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Executes a RoutingDecision:
      - direct_payment_link: builds a fresh Decision using the REAL, locked
        Step 4 catalog metadata for recovery_payment_link, re-authorizes
        from scratch via GuardrailEngine (never assumed safe), and if
        approved, executes for REAL via execute_guardrail_approved_action()
        — genuinely checkable afterward via observe_recovery().
      - any other route: returns an honestly SIMULATED result (same
        client.simulate_retry_operation() every other simulated action
        uses), tagged with the chosen route for transparency. Never marked
        recovered by itself.
    Returns a dict shaped like an ExecutionRecord.to_dict(), so the same
    dashboard display logic used elsewhere can render it.
    """
    case = get_case_row(decision.case_id)
    if case is None:
        raise ValueError(f"Unknown case_id: {decision.case_id!r}")

    if not decision.is_real_executable:
        raw = razorpay_client.simulate_retry_operation(
            decision.recommended_route.value, decision.case_id, float(case.get("amount_at_risk", 0.0))
        )
        raw["chosen_route"] = decision.recommended_route.value
        raw["routing_rationale"] = decision.rationale
        return {
            "case_id": decision.case_id, "leakage_category": case.get("leakage_category"),
            "action_type": decision.recommended_route.value, "execution_status": "simulated",
            "result_source": "bounded_simulation",
            "reason": f"Routing optimizer recommended '{decision.recommended_route.value}': {decision.rationale}",
            "razorpay_result": raw,
        }

    from decision_models import Decision, DecisionStatus, LikelihoodTier
    from razorpay_execution import execute_guardrail_approved_action
    from action_catalog import ACTION_CATALOG
    from action_models import ActionType

    catalog_def = ACTION_CATALOG[ActionType.RECOVERY_PAYMENT_LINK]
    recommended_action = {
        "action_type": catalog_def.action_type.value,
        "money_movement": catalog_def.money_movement,
        "customer_communication": catalog_def.customer_communication,
        "requires_merchant_approval": catalog_def.requires_merchant_approval,
    }
    diagnosis = diagnosis_engine.diagnose(case)
    routing_decision_obj = Decision(
        case_id=decision.case_id, leakage_category=case.get("leakage_category"),
        decision_status=DecisionStatus.RECOMMENDED,
        recommended_action_type="recovery_payment_link", recommended_action=recommended_action,
        likelihood_tier=LikelihoodTier.HIGH,
        predicted_recovery_likelihood=diagnosis.get("predicted_recovery_likelihood"),
        diagnosis_confidence=diagnosis.get("diagnosis_confidence"),
        recommendation_reason=(
            f"Routing optimizer recommended a direct Payment Link for case {decision.case_id} "
            f"(failure_reason={decision.failure_reason!r}): {decision.rationale}"
        ),
    )
    diag_for_guard = {"diagnosis_confidence": diagnosis.get("diagnosis_confidence")}
    guardrail = guardrail_engine.authorize(case, diag_for_guard, routing_decision_obj, current_time=current_time)
    outcome = guardrail.outcome.value if hasattr(guardrail.outcome, "value") else guardrail.outcome

    if outcome != "auto_execute":
        return {
            "case_id": decision.case_id, "leakage_category": case.get("leakage_category"),
            "action_type": "recovery_payment_link", "execution_status": "not_executed",
            "result_source": "not_executed",
            "reason": f"Fresh guardrail re-authorization for the routed action returned {outcome!r}, not auto_execute.",
            "razorpay_result": {},
        }

    execution_record = execute_guardrail_approved_action(case, routing_decision_obj, guardrail, razorpay_client)
    result_dict = execution_record.to_dict()

    if (result_dict.get("execution_status") == "executed"
            and result_dict.get("result_source") == "razorpay_test_mode_api"):
        recovery_result_placeholder = None
        try:
            from recovery_checker import observe_recovery
            rr = observe_recovery(decision.case_id, case.get("leakage_category"), result_dict, razorpay_client)
            recovery_result_placeholder = rr.to_dict()
        except Exception:
            pass
        if recovery_result_placeholder:
            _persist_real_recovery(decision.case_id, case.get("leakage_category"),
                                    float(case.get("amount_at_risk", 0.0)), result_dict, recovery_result_placeholder)
            result_dict["_initial_recovery_check"] = recovery_result_placeholder

    return result_dict


# ------------------------------------------------------------------ #
# Hinglish Recovery Script Generator — content-only, always simulated.
# See hinglish_recovery/README.md's honesty statement: no real telephony
# or messaging integration exists in this project.
# ------------------------------------------------------------------ #
def generate_hinglish_script(case_id: str, channel: str = "whatsapp", payment_link_url: Optional[str] = None):
    case = get_case_row(case_id)
    if case is None:
        raise ValueError(f"Unknown case_id: {case_id!r} — not found in {DATA_CSV.name}.")
    return _hg.generate_script(case, channel=channel, payment_link_url=payment_link_url)


# ------------------------------------------------------------------ #
def build_evaluation_report(case_results: List[Dict[str, Any]], sample_df: pd.DataFrame,
                             diagnosis_engine, decision_engine, guardrail_engine,
                             current_time: Optional[datetime] = None):
    model_summary = load_model_evaluation_summary()
    test_df = pd.read_csv(TEST_CSV)
    backtest = run_synthetic_backtest(diagnosis_engine, decision_engine, guardrail_engine,
                                       test_df, current_time=current_time)
    entries = build_batch_entries(case_results, sample_df)
    bm = compute_batch_measurement(entries) if entries else None
    return assemble_evaluation_report(model_summary, backtest, batch_measurement=bm)


# ------------------------------------------------------------------ #
# Section E (ad hoc): check one Payment Link by ID — reuses observe_recovery
# exactly (no duplicated interpretation logic). This is how a genuinely
# verified Test Mode payment (e.g. from your own environment) gets displayed
# — never hardcoded here.
# ------------------------------------------------------------------ #
def check_payment_link(payment_link_id: str, razorpay_client) -> Dict[str, Any]:
    if not payment_link_id:
        raise ValueError("payment_link_id is required")
    synthetic_execution_record = {
        "case_id": "manual-check", "leakage_category": None, "action_type": "recovery_payment_link",
        "execution_status": "executed", "result_source": "razorpay_test_mode_api",
        "reason": "Manual dashboard status check.",
        "razorpay_result": {"razorpay_payment_link_id": payment_link_id},
    }
    result = observe_recovery("manual-check", None, synthetic_execution_record, razorpay_client)
    return result.to_dict()


# ------------------------------------------------------------------ #
# Section G: audit timeline — reuses AuditStore.get_case_trail exactly
# ------------------------------------------------------------------ #
def get_audit_timeline(audit_store: AuditStore, case_id: str) -> List[Dict[str, Any]]:
    return audit_store.get_case_trail(case_id)