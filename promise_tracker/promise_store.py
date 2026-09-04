"""
RecoverAI — Promise-to-Pay Tracker: Storage

Thin SQLite wrapper, matching audit_store.py's own conventions exactly.
Promises need STATUS UPDATES over time (pending -> honored/broken), unlike
real_results_log.jsonl's append-only design — hence a real table with
UPDATE statements, not a JSON Lines log.
"""

import sqlite3
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional, Dict, Any, List

from promise_schema import DDL, PromiseStatus

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "promise_tracker.db"


class PromiseStore:
    def __init__(self, db_path: Optional[str] = None):
        """
        db_path: path to a SQLite file, or ":memory:" for an ephemeral store
                 (used by tests). Defaults to promise_tracker.db next to this
                 file, resolved relative to this file's own directory.
        """
        self.db_path = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(DDL)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def record_promise(self, case_id: str, leakage_category: Optional[str], promised_amount: float,
                        promise_date: str, payment_link_id: Optional[str] = None) -> int:
        """
        promise_date: ISO date string (YYYY-MM-DD) the customer committed to
        pay by. Always starts as PENDING — status is never set at creation
        time to anything else.
        """
        if not case_id:
            raise ValueError("case_id is required.")
        if promised_amount is None or promised_amount < 0:
            raise ValueError("promised_amount must be a non-negative number.")
        if not promise_date:
            raise ValueError("promise_date is required.")

        created_at = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO promises (case_id, leakage_category, promised_amount, promise_date, "
            "created_at, status, payment_link_id, checked_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, leakage_category, float(promised_amount), promise_date, created_at,
             PromiseStatus.PENDING.value, payment_link_id, None,
             "Promise recorded; awaiting promise_date and/or payment confirmation."),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_status(self, promise_id: int, status: PromiseStatus, reason: str,
                       payment_link_id: Optional[str] = None) -> None:
        checked_at = datetime.now(timezone.utc).isoformat()
        if payment_link_id is not None:
            self._conn.execute(
                "UPDATE promises SET status = ?, reason = ?, checked_at = ?, payment_link_id = ? WHERE promise_id = ?",
                (status.value, reason, checked_at, payment_link_id, promise_id),
            )
        else:
            self._conn.execute(
                "UPDATE promises SET status = ?, reason = ?, checked_at = ? WHERE promise_id = ?",
                (status.value, reason, checked_at, promise_id),
            )
        self._conn.commit()

    def get_promise(self, promise_id: int) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM promises WHERE promise_id = ?", (promise_id,))
        row = cur.fetchone()
        return self._row_to_dict(cur, row) if row else None

    def get_promises_for_case(self, case_id: str) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM promises WHERE case_id = ? ORDER BY created_at DESC", (case_id,)
        )
        rows = cur.fetchall()
        return [self._row_to_dict(cur, r) for r in rows]

    def get_all_promises(self, status: Optional[PromiseStatus] = None) -> List[Dict[str, Any]]:
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM promises WHERE status = ? ORDER BY promise_date ASC", (status.value,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM promises ORDER BY promise_date ASC")
        rows = cur.fetchall()
        return [self._row_to_dict(cur, r) for r in rows]

    def count_by_status(self) -> Dict[str, int]:
        cur = self._conn.execute("SELECT status, COUNT(*) FROM promises GROUP BY status")
        return {row[0]: row[1] for row in cur.fetchall()}

    @staticmethod
    def _row_to_dict(cur, row) -> Dict[str, Any]:
        columns = [d[0] for d in cur.description]
        return dict(zip(columns, row))