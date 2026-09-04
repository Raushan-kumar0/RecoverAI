"""
RecoverAI — Audit Trail: Storage (Step 8)

Thin SQLite wrapper. Pure storage/retrieval — no knowledge of Step 3-7
object shapes lives here (see audit_recorder.py for that).

Defense-in-depth secret redaction: even though every upstream module
(Step 7's RazorpayConfig.redacted() in particular) already avoids ever
producing a secret in its output, record_event() additionally scans the
payload recursively before writing and masks any key whose name suggests it
could hold a credential. This means an audit record can never leak a secret
even if a future caller's payload construction has a bug.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from audit_schema import DDL, AuditStage

SENSITIVE_KEY_MARKERS = ("secret", "password", "token", "api_key")

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "audit_trail.db"


def _redact_payload(obj):
    """Recursively masks any dict value whose key looks credential-shaped."""
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if isinstance(k, str) and any(marker in k.lower() for marker in SENSITIVE_KEY_MARKERS):
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = _redact_payload(v)
        return redacted
    if isinstance(obj, list):
        return [_redact_payload(v) for v in obj]
    return obj


class AuditStore:
    def __init__(self, db_path: Optional[str] = None):
        """
        db_path: path to a SQLite file, or ":memory:" for an ephemeral store
                 (used by tests). Defaults to audit_trail.db next to this
                 file — resolved relative to this file's own directory, not
                 the caller's working directory (see Step 7 portability fix
                 precedent).
        """
        self.db_path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
        # check_same_thread=False: Streamlit can rerun a script (e.g. on a
        # button click) in a different worker thread than the one that
        # created this connection, while st.session_state keeps the SAME
        # AuditStore instance alive across those reruns. Streamlit's
        # execution model is sequential per session — reruns never touch
        # this connection concurrently — so relaxing sqlite3's default
        # same-thread check is safe here and doesn't change any query logic.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(DDL)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def record_event(self, case_id: str, leakage_category: Optional[str], stage: AuditStage,
                      summary: str, payload: Dict[str, Any]) -> int:
        if not case_id:
            raise ValueError("case_id is required to record an audit event.")
        if not isinstance(stage, AuditStage):
            raise ValueError(f"stage must be an AuditStage member, got {stage!r}")

        safe_payload = _redact_payload(payload)
        payload_json = json.dumps(safe_payload, default=str)

        cur = self._conn.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 FROM audit_events WHERE case_id = ?",
            (case_id,),
        )
        next_sequence = cur.fetchone()[0]

        recorded_at = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO audit_events (case_id, leakage_category, stage, sequence, summary, payload_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, leakage_category, stage.value, next_sequence, summary, payload_json, recorded_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_case_trail(self, case_id: str) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT event_id, case_id, leakage_category, stage, sequence, summary, payload_json, recorded_at "
            "FROM audit_events WHERE case_id = ? ORDER BY sequence ASC, event_id ASC",
            (case_id,),
        )
        rows = cur.fetchall()
        return [
            {
                "event_id": r[0], "case_id": r[1], "leakage_category": r[2], "stage": r[3],
                "sequence": r[4], "summary": r[5], "payload": json.loads(r[6]), "recorded_at": r[7],
            }
            for r in rows
        ]

    def get_all_case_ids(self) -> List[str]:
        cur = self._conn.execute("SELECT DISTINCT case_id FROM audit_events ORDER BY case_id")
        return [r[0] for r in cur.fetchall()]

    def count_events(self, case_id: Optional[str] = None) -> int:
        if case_id is not None:
            cur = self._conn.execute("SELECT COUNT(*) FROM audit_events WHERE case_id = ?", (case_id,))
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM audit_events")
        return cur.fetchone()[0]
