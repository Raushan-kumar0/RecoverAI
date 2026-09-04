"""
RecoverAI — Promise-to-Pay Tracker: Schema (Extension, post-Step 12)

DDL + status enum for tracking a customer's stated commitment to pay by a
future date. Pure schema — no logic about how/when a promise gets marked
honored/broken lives here (see promise_checker.py for that).
"""

from enum import Enum

DDL = """
CREATE TABLE IF NOT EXISTS promises (
    promise_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    leakage_category TEXT,
    promised_amount REAL NOT NULL,
    promise_date TEXT NOT NULL,       -- ISO date the customer committed to pay by
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,             -- pending / honored / broken / escalated
    payment_link_id TEXT,             -- linked real Payment Link, if one exists
    checked_at TEXT,                  -- last time status was independently verified
    reason TEXT                       -- honest explanation of the current status
);
CREATE INDEX IF NOT EXISTS idx_promises_case_id ON promises(case_id);
CREATE INDEX IF NOT EXISTS idx_promises_status ON promises(status);
"""


class PromiseStatus(str, Enum):
    PENDING = "pending"        # promise_date hasn't passed yet; no payment observed
    HONORED = "honored"        # ONLY set via observe_recovery() against payment_link_id reporting 'paid'
    BROKEN = "broken"          # ONLY set once promise_date has genuinely passed with no payment confirmed
    ESCALATED = "escalated"    # broken + manually flagged for follow-up (merchant action, not automatic)