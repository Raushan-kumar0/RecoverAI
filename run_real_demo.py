"""
RecoverAI — one real end-to-end run against Razorpay TEST MODE.

Run this from the PROJECT ROOT (same folder as RECOVERAI_PROJECT_HANDOFF.md).

Step A — create a real Payment Link:
    python run_real_demo.py
    (prints a short_url — open it, pay with a Razorpay test card)

Step B — after paying, confirm + measure:
    python run_real_demo.py --recover
"""
import os
import sys
import time
import json

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv_into_os_environ(env_path):
    """Minimal, stdlib-only .env loader. Only sets a variable if it isn't
    already present in the real OS environment, so an explicit `$env:X=...`
    /`export X=...` you set by hand always wins. This does NOT change
    razorpay_config.load_config_from_env(), which still reads only
    os.environ, per Step 7's locked design -- it just makes sure the
    values from .env actually land in os.environ before that call happens."""
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv_into_os_environ(os.path.join(ROOT, "integrations", "razorpay", ".env"))

# Match the project's own convention: each module wires its sibling dirs
# onto sys.path itself, so we only need to add the dirs whose top-level
# public class/function we import directly here.
for sub in ["diagnosis", "decision_engine", "guardrails",
            os.path.join("integrations", "razorpay"),
            "recovery", "measurement"]:
    sys.path.insert(0, os.path.join(ROOT, sub))

os.chdir(ROOT)  # so data/test.csv resolves relative to project root

import pandas as pd

from diagnose import DiagnosisEngine
from decision_engine import DecisionEngine
from guardrail_engine import GuardrailEngine
from razorpay_config import load_config_from_env
from razorpay_client import RazorpayTestModeClient
from razorpay_execution import execute_guardrail_approved_action
from recovery_checker import observe_recovery
from measurement_models import BatchEntry
from batch_measurement import compute_batch_measurement

STATE_FILE = os.path.join(ROOT, "last_run.json")
USED_CASES_FILE = os.path.join(ROOT, "used_cases.json")
RESULTS_LOG = os.path.join(ROOT, "real_results_log.jsonl")  # append-only — the dashboard's ONLY data source


def build_client():
    config = load_config_from_env()
    print("Using credentials:", config.redacted())
    return RazorpayTestModeClient(config)


def load_used_case_ids():
    if os.path.exists(USED_CASES_FILE):
        with open(USED_CASES_FILE) as f:
            return set(json.load(f))
    return set()


def mark_case_used(case_id):
    used = load_used_case_ids()
    used.add(case_id)
    with open(USED_CASES_FILE, "w") as f:
        json.dump(sorted(used), f, indent=2)


def find_case():
    df = pd.read_csv(os.path.join(ROOT, "data", "test.csv"))
    df = df[df["leakage_category"] != "successful"]
    used = load_used_case_ids()

    diag_engine = DiagnosisEngine()
    dec_engine = DecisionEngine(diagnosis_engine=diag_engine)
    grd_engine = GuardrailEngine()

    for _, row in df.iterrows():
        case = row.to_dict()
        if case["case_id"] in used:
            continue  # already has a real Payment Link — Razorpay rejects duplicate reference_id

        diagnosis = diag_engine.diagnose(case)
        decision = dec_engine.decide(case, diagnosis=diagnosis)
        guardrail = grd_engine.authorize(case, diagnosis, decision)

        rec_action = decision.recommended_action
        rec_action_type = rec_action.get("action_type") if isinstance(rec_action, dict) else rec_action

        if (
            getattr(decision.decision_status, "value", decision.decision_status) == "recommended"
            and rec_action_type == "recovery_payment_link"
            and getattr(guardrail.outcome, "value", guardrail.outcome) == "auto_execute"
        ):
            return case, diagnosis, decision, guardrail

    raise SystemExit(
        "No unused case in test.csv naturally resolves to "
        "recovery_payment_link + AUTO_EXECUTE. No suitable unused case found."
    )


def step_a_create_link():
    df_used_before_run = load_used_case_ids()  # snapshot; find_case() re-reads live inside loop below

    case, diagnosis, decision, guardrail = find_case()
    print(f"Selected case: {case['case_id']} "
          f"({case['leakage_category']}, Rs.{case['amount_at_risk']})")
    print("Decision action_type:", decision.recommended_action.get("action_type"),
          "| Guardrail:", guardrail.outcome)

    client = build_client()
    record = execute_guardrail_approved_action(case, decision, guardrail, client)
    record_dict = record.to_dict()
    print("\n--- ExecutionRecord ---")
    print(json.dumps(record_dict, indent=2, default=str))

    rzp_result = record_dict.get("razorpay_result") or {}
    link_id = rzp_result.get("razorpay_payment_link_id")
    short_url = rzp_result.get("razorpay_short_url")
    error_desc = (rzp_result.get("razorpay_error") or {}).get("description", "")

    if not link_id:
        if "already exists" in error_desc:
            print(f"\n{case['case_id']} already has a Payment Link from an earlier "
                  f"run outside this script's tracking. Marking it used and "
                  f"re-run this script to try the next case.")
            mark_case_used(case["case_id"])
        else:
            print("\nNo payment_link_id was returned — check the ExecutionRecord "
                  "above for why (dry_run still on? network/API error? bad creds?).")
        return

    print(f"\n>>> OPEN THIS URL AND PAY WITH A RAZORPAY TEST CARD: {short_url}")
    print(">>> Then re-run:  python run_real_demo.py --recover")

    mark_case_used(case["case_id"])

    with open(STATE_FILE, "w") as f:
        json.dump({
            "case_id": case["case_id"],
            "leakage_category": case["leakage_category"],
            "amount_at_risk": case["amount_at_risk"],
            "payment_link_id": link_id,
            "execution_record": record_dict,
        }, f, indent=2, default=str)


def step_b_recover():
    with open(STATE_FILE) as f:
        prior = json.load(f)

    client = build_client()
    result = observe_recovery(
        case_id=prior["case_id"],
        leakage_category=prior["leakage_category"],
        execution_record=prior["execution_record"],
        razorpay_client=client,
    )
    result_dict = dict(result.__dict__)
    result_dict["recovery_status"] = result.recovery_status.value  # enum -> str
    print("\n--- RecoveryResult ---")
    print(json.dumps(result_dict, indent=2, default=str))

    entry = BatchEntry(
        case_id=prior["case_id"],
        leakage_category=prior["leakage_category"],
        amount_at_risk=prior["amount_at_risk"],
        primary_guardrail_outcome="auto_execute",
        primary_recommended_action_type="recovery_payment_link",
        failure_handling_result={
            "outcome": "no_failure",
            "primary_execution": prior["execution_record"],
            "fallback_attempted": False,
            "fallback_execution": None,
            "escalated": False,
        },
        recovery_result=result_dict,
    )
    measurement = compute_batch_measurement([entry])
    measurement_dict = (
        measurement.to_dict() if hasattr(measurement, "to_dict") else measurement.__dict__
    )
    print("\n--- BatchMeasurement ---")
    print(json.dumps(measurement_dict, indent=2, default=str))

    # Append this real, observed result permanently. This is the ONLY place
    # that ever writes to RESULTS_LOG, and it only runs after a real
    # observe_recovery() call against the real Razorpay API. Nothing here
    # is typed in by hand — the dashboard will only ever read this file.
    log_entry = {
        "logged_at": time.time(),
        "case_id": prior["case_id"],
        "leakage_category": prior["leakage_category"],
        "amount_at_risk": prior["amount_at_risk"],
        "payment_link_id": prior["payment_link_id"],
        "execution_record": prior["execution_record"],
        "recovery_result": result_dict,
        "batch_measurement_for_this_case": measurement_dict,
    }
    with open(RESULTS_LOG, "a") as f:
        f.write(json.dumps(log_entry, default=str) + "\n")
    print(f"\n>>> Logged to {RESULTS_LOG} (case status: {result_dict['recovery_status']})")


if __name__ == "__main__":
    if "--recover" in sys.argv:
        step_b_recover()
    else:
        step_a_create_link()
