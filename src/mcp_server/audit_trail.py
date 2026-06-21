"""
audit_trail.py
───────────────
Durable, queryable audit trail for every reconciliation decision.

Why SQLite instead of a JSON log?
  A JSON file gets overwritten or grows unboundedly awkward to query.
  SQLite gives us:
    - Durability across runs (the file persists; new runs APPEND, not overwrite)
    - Queryability (e.g. "show me every escalation from the last 7 days")
    - A real audit trail shape — timestamped, append-only, with an actor
      field — which is what a compliance team would actually expect to
      see, not console output that vanishes when the terminal closes.

This is the difference between a demo and something that resembles
production infrastructure: the system's decisions are provable and
reviewable after the fact, not just visible in a log scroll.

Schema:
  audit_log table
    id              INTEGER PRIMARY KEY
    run_id          TEXT     — groups entries from the same reconciliation run
    timestamp       TEXT     — ISO 8601, when the entry was written
    txn_id          TEXT     — the transaction this entry concerns
    exception_type  TEXT     — TIMING / ROUNDING / DUPLICATE / MISSING / REFID / FX / null
    action          TEXT     — AUTO_RESOLVED / ESCALATED / APPROVED / REJECTED
    actor           TEXT     — "agent" or "human:<approver note>"
    confidence      REAL     — agent's confidence score (null for human actions)
    reasoning       TEXT     — agent's explanation for the decision
    amount          REAL     — transaction amount (for quick filtering by value)
"""

import sqlite3
import os
from datetime import datetime


class AuditTrail:
    """
    Durable audit trail backed by SQLite.

    Each ReconcileAgent run appends to the SAME database file rather than
    starting fresh — so the audit trail accumulates real history across
    multiple runs, which is what makes it meaningful to query later.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        """Create the audit_log table if it doesn't already exist."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                txn_id          TEXT NOT NULL,
                exception_type  TEXT,
                action          TEXT NOT NULL,
                actor           TEXT NOT NULL,
                confidence      REAL,
                reasoning       TEXT,
                amount          REAL
            )
        """)
        conn.commit()
        conn.close()

    def log(self, run_id: str, txn_id: str, action: str, actor: str,
            exception_type: str = None, confidence: float = None,
            reasoning: str = "", amount: float = None) -> int:
        """
        Append a new entry to the audit trail. Never overwrites — every
        call is a new row, preserving full history.

        Returns:
            The row id of the newly inserted entry.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            INSERT INTO audit_log
                (run_id, timestamp, txn_id, exception_type, action,
                 actor, confidence, reasoning, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, datetime.now().isoformat(), txn_id, exception_type,
            action, actor, confidence, reasoning, amount
        ))
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def get_run_history(self, run_id: str) -> list[dict]:
        """Retrieve every audit entry for a specific reconciliation run."""
        return self._query(
            "SELECT * FROM audit_log WHERE run_id = ? ORDER BY timestamp",
            (run_id,)
        )

    def get_transaction_history(self, txn_id: str) -> list[dict]:
        """Retrieve every audit entry ever logged for a specific transaction —
        across ALL runs. This is what lets the agent (and a human) see if a
        transaction has been flagged before, was previously escalated, etc."""
        return self._query(
            "SELECT * FROM audit_log WHERE txn_id = ? ORDER BY timestamp",
            (txn_id,)
        )

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Retrieve the most recent audit entries across all runs."""
        return self._query(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )

    def get_escalations(self, run_id: str = None) -> list[dict]:
        """Retrieve all ESCALATED entries, optionally filtered to one run."""
        if run_id:
            return self._query(
                "SELECT * FROM audit_log WHERE action = 'ESCALATED' AND run_id = ? ORDER BY timestamp",
                (run_id,)
            )
        return self._query(
            "SELECT * FROM audit_log WHERE action = 'ESCALATED' ORDER BY timestamp"
        )

    def get_summary_stats(self) -> dict:
        """Aggregate stats across all runs ever logged — total durable history."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        total_runs = conn.execute(
            "SELECT COUNT(DISTINCT run_id) as c FROM audit_log"
        ).fetchone()["c"]

        total_entries = conn.execute(
            "SELECT COUNT(*) as c FROM audit_log"
        ).fetchone()["c"]

        action_counts = conn.execute("""
            SELECT action, COUNT(*) as count
            FROM audit_log GROUP BY action
        """).fetchall()

        conn.close()

        return {
            "total_runs": total_runs,
            "total_entries": total_entries,
            "action_breakdown": {row["action"]: row["count"] for row in action_counts},
        }

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(row) for row in rows]
