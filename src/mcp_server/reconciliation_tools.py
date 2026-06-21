"""
reconciliation_tools.py
─────────────────────────
MCP server exposing reconciliation tools to the agent.

Five tools, each mapping to a real step in how a human analyst would
investigate and resolve a reconciliation exception:

  1. search_counterpart   — look for a plausible matching transaction
                             on the other side, using looser criteria
                             than the deterministic matcher
  2. get_transaction_history — check the durable audit trail for any
                             prior history on this transaction (has it
                             been flagged before? resolved before?)
  3. resolve_exception    — take the action: mark as resolved, with a
                             stated exception_type and reasoning. This
                             WRITES to the audit trail — a real action,
                             not a suggestion.
  4. escalate_exception    — flag for human review when confidence is
                             low or the amount exceeds the auto-resolve
                             threshold. Also writes to the audit trail.
  5. log_investigation_note — lets the agent record intermediate
                             reasoning even before a final decision,
                             useful for traceability on multi-step
                             investigations.

Design note — why separate resolve_exception and escalate_exception
instead of one "decide" tool?
  Making them distinct tools means the audit trail can never be
  ambiguous about what happened — a resolve_exception call and an
  escalate_exception call are unmistakably different actions in the
  database, which matters when this audit trail might be reviewed by
  a compliance officer later. It also lets us enforce the approval
  gate policy AT THE TOOL LEVEL (see resolve_exception below) rather
  than trusting the agent's own judgment alone.
"""

import json
import uuid
from config import AUTO_RESOLVE_CONFIDENCE, AUTO_RESOLVE_MAX_AMOUNT
from src.mcp_server.audit_trail import AuditTrail


TOOL_SCHEMAS = [
    {
        "name": "search_counterpart",
        "description": (
            "Search for a plausible matching transaction on the other system "
            "(ledger or bank) using loose criteria — similar amount, nearby date, "
            "or similar description. Use this to investigate why a transaction "
            "didn't match automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search_in": {
                    "type": "string",
                    "enum": ["ledger", "bank"],
                    "description": "Which dataset to search in"
                },
                "amount": {"type": "number", "description": "Approximate amount to search for"},
                "description": {"type": "string", "description": "Counterparty/description to search for"},
                "date": {"type": "string", "description": "Approximate date (YYYY-MM-DD) to search near"},
            },
            "required": ["search_in"]
        }
    },
    {
        "name": "get_transaction_history",
        "description": (
            "Check the durable audit trail for any prior history on a specific "
            "transaction ID — has it been investigated before, in a previous run? "
            "Use this before making a decision to avoid re-litigating settled cases."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string", "description": "The transaction ID to check"},
            },
            "required": ["txn_id"]
        }
    },
    {
        "name": "resolve_exception",
        "description": (
            "Mark an exception as resolved with a stated root cause and reasoning. "
            "This WRITES a permanent audit trail entry. "
            "IMPORTANT: resolution is only allowed automatically when confidence >= "
            f"{AUTO_RESOLVE_CONFIDENCE} AND amount <= ${AUTO_RESOLVE_MAX_AMOUNT}. "
            "If either condition isn't met, this tool will reject the resolution and "
            "tell you to use escalate_exception instead — this is a hard policy "
            "enforced by the system, not a suggestion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string"},
                "exception_type": {
                    "type": "string",
                    "enum": ["TIMING", "ROUNDING", "DUPLICATE", "MISSING", "REFID", "FX", "OTHER"],
                },
                "confidence": {"type": "number", "description": "Confidence 0.0-1.0"},
                "amount": {"type": "number", "description": "Transaction amount"},
                "reasoning": {"type": "string", "description": "Why this is the root cause and why it's safe to auto-resolve"},
            },
            "required": ["txn_id", "exception_type", "confidence", "amount", "reasoning"]
        }
    },
    {
        "name": "escalate_exception",
        "description": (
            "Flag an exception for human review instead of resolving it automatically. "
            "Use this when confidence is low, the root cause is unclear, the amount is "
            "large, or the case represents a genuine error (e.g. duplicate entry, "
            "missing transaction) rather than an explainable discrepancy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string"},
                "exception_type": {
                    "type": "string",
                    "enum": ["TIMING", "ROUNDING", "DUPLICATE", "MISSING", "REFID", "FX", "OTHER", "UNKNOWN"],
                },
                "confidence": {"type": "number", "description": "Confidence 0.0-1.0"},
                "amount": {"type": "number", "description": "Transaction amount"},
                "reasoning": {"type": "string", "description": "Why this needs human review"},
            },
            "required": ["txn_id", "exception_type", "confidence", "amount", "reasoning"]
        }
    },
]


class ReconciliationToolExecutor:
    """
    Executes reconciliation tools and enforces the auto-resolve policy
    at the tool level — the agent cannot bypass the confidence/amount
    threshold by simply being persuasive in its reasoning text.
    """

    def __init__(self, ledger: list[dict], bank: list[dict],
                 audit_trail: AuditTrail, run_id: str = None):
        self.ledger = ledger
        self.bank = bank
        self.audit = audit_trail
        self.run_id = run_id or str(uuid.uuid4())[:8]

    def execute(self, tool_name: str, tool_input: dict) -> str:
        """Route a tool call by name and return a JSON string result."""
        if tool_name == "search_counterpart":
            result = self._search_counterpart(**tool_input)
        elif tool_name == "get_transaction_history":
            result = self._get_transaction_history(**tool_input)
        elif tool_name == "resolve_exception":
            result = self._resolve_exception(**tool_input)
        elif tool_name == "escalate_exception":
            result = self._escalate_exception(**tool_input)
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
        return json.dumps(result, indent=2, default=str)

    def _search_counterpart(self, search_in: str, amount: float = None,
                            description: str = None, date: str = None) -> dict:
        """Loose search across one dataset for a plausible counterpart."""
        dataset = self.ledger if search_in == "ledger" else self.bank
        candidates = []

        for txn in dataset:
            score = 0
            if amount is not None and abs(txn["amount"] - amount) / max(amount, 1) < 0.05:
                score += 2
            if description and description.lower() in txn["description"].lower():
                score += 2
            if date:
                from datetime import datetime
                try:
                    target_date = datetime.strptime(date, "%Y-%m-%d")
                    if abs((txn["date"] - target_date).days) <= 5:
                        score += 1
                except ValueError:
                    pass
            if score > 0:
                candidates.append({
                    "txn_id": txn["txn_id"],
                    "date": txn["date"].strftime("%Y-%m-%d"),
                    "amount": txn["amount"],
                    "description": txn["description"],
                    "category": txn["category"],
                    "match_score": score,
                })

        candidates.sort(key=lambda c: c["match_score"], reverse=True)
        return {"search_in": search_in, "candidates": candidates[:5]}

    def _get_transaction_history(self, txn_id: str) -> dict:
        history = self.audit.get_transaction_history(txn_id)
        return {"txn_id": txn_id, "prior_entries": history}

    def _resolve_exception(self, txn_id: str, exception_type: str,
                           confidence: float, amount: float, reasoning: str) -> dict:
        """
        Enforce the auto-resolve policy: confidence AND amount must both
        clear the threshold. This is a hard gate at the tool level —
        the agent cannot resolve a high-value or low-confidence case
        just by asserting it's fine in the reasoning text.
        """
        if confidence < AUTO_RESOLVE_CONFIDENCE:
            return {
                "status": "REJECTED",
                "reason": f"Confidence {confidence} is below the auto-resolve threshold "
                          f"({AUTO_RESOLVE_CONFIDENCE}). Use escalate_exception instead.",
            }
        if amount > AUTO_RESOLVE_MAX_AMOUNT:
            return {
                "status": "REJECTED",
                "reason": f"Amount ${amount} exceeds the auto-resolve limit "
                          f"(${AUTO_RESOLVE_MAX_AMOUNT}). Use escalate_exception instead, "
                          f"regardless of confidence.",
            }

        self.audit.log(
            run_id=self.run_id, txn_id=txn_id, action="AUTO_RESOLVED",
            actor="agent", exception_type=exception_type,
            confidence=confidence, reasoning=reasoning, amount=amount,
        )
        return {
            "status": "RESOLVED",
            "txn_id": txn_id,
            "exception_type": exception_type,
            "logged_to_audit_trail": True,
        }

    def _escalate_exception(self, txn_id: str, exception_type: str,
                            confidence: float, amount: float, reasoning: str) -> dict:
        self.audit.log(
            run_id=self.run_id, txn_id=txn_id, action="ESCALATED",
            actor="agent", exception_type=exception_type,
            confidence=confidence, reasoning=reasoning, amount=amount,
        )
        return {
            "status": "ESCALATED",
            "txn_id": txn_id,
            "exception_type": exception_type,
            "logged_to_audit_trail": True,
            "awaiting": "human_approval",
        }
