"""
RecoverAI — Audit Trail: Schema (Step 8)

A single append-only event log, not one table per stage. Every stage of the
DETECT -> DIAGNOSE -> DECIDE -> GUARDRAIL -> EXECUTE pipeline writes one
structured event here. This keeps the schema simple and lets a full case
trail be reconstructed with one ordered query, which is what a judge needs
("understand exactly what the agent did and why" — Step 1 §12).

This module defines the schema only. No pipeline logic lives here — see
audit_recorder.py for how each stage's existing Step 3-7 output objects are
turned into an event, and audit_store.py for the actual SQLite I/O.
"""

from enum import Enum

DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    leakage_category TEXT,
    stage          TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    summary        TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    recorded_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_events_case_id ON audit_events (case_id);
"""


class AuditStage(str, Enum):
    """Maps 1:1 onto the Step 1 §12 "Record:" list, extended for Step 10's
    RECOVER stage:
    - DETECTION          -> case detection (the fact snapshot)
    - DIAGNOSIS           -> Step 3 diagnosis
    - CANDIDATE_ACTIONS    -> Step 4 candidate actions considered
    - DECISION              -> Step 5 selected action + reasoning + alternatives
    - GUARDRAIL              -> Step 6 policy decision + approval requirement
    - EXECUTION               -> Step 7 API action / result / failure / fallback /
                                   escalation / stop are all represented via
                                   execution_status + guardrail_outcome on this event
    - RECOVERY                 -> RECOVER-stage observation (did the customer
                                   actually pay?) — structurally separate from
                                   EXECUTION; added for Step 10. Backward
                                   compatible: `stage` is a plain TEXT column,
                                   so existing rows/queries are unaffected.
    """
    DETECTION = "detection"
    DIAGNOSIS = "diagnosis"
    CANDIDATE_ACTIONS = "candidate_actions"
    DECISION = "decision"
    GUARDRAIL = "guardrail"
    EXECUTION = "execution"
    RECOVERY = "recovery"


STAGE_ORDER = {
    AuditStage.DETECTION: 0,
    AuditStage.DIAGNOSIS: 1,
    AuditStage.CANDIDATE_ACTIONS: 2,
    AuditStage.DECISION: 3,
    AuditStage.GUARDRAIL: 4,
    AuditStage.EXECUTION: 5,
    AuditStage.RECOVERY: 6,
}
