"""
report.py
───────────
Generates the before/after reconciliation report — the artifact that
makes the workflow's value legible at a glance.

"Before" state: what reconciliation looks like without this system —
  a pile of unmatched transactions, no visibility into why they didn't
  match, no record of what was investigated.

"After" state: what the system produced —
  X transactions auto-matched deterministically (zero AI cost),
  Y exceptions auto-resolved by the agent with stated reasoning,
  Z exceptions escalated with a clear explanation and a recorded
  human decision, all backed by a durable, queryable audit trail.

This report is saved as JSON (for systems integration / audit) and
rendered as a Rich console summary (for the live demo).
"""

import os
import json
from datetime import datetime


def build_report(match_summary: dict, investigations: list[dict],
                 human_decisions: list[dict], run_id: str,
                 eval_result: dict = None) -> dict:
    """
    Assemble the full before/after report as a structured dict.

    Args:
        match_summary:    Output from matcher.summarize()
        investigations:   Output from orchestrator.run()["investigations"]
        human_decisions:  Output from run_approval_gate()
        run_id:           The reconciliation run ID
        eval_result:       Optional output from ReconciliationEvaluator.evaluate()

    Returns:
        Structured report dict, ready to save as JSON or render.
    """
    total_ledger_txns = (
        match_summary["exact_matched"] +
        match_summary["fuzzy_matched"] +
        match_summary["unmatched_ledger"]
    )

    auto_resolved = [i for i in investigations if i["action_taken"] == "AUTO_RESOLVED"]
    escalated = [i for i in investigations if i["action_taken"] == "ESCALATED"]

    report = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "before": {
            "description": "State prior to reconciliation — all transactions unverified",
            "total_ledger_transactions": total_ledger_txns,
            "total_bank_transactions": (
                match_summary["exact_matched"] +
                match_summary["fuzzy_matched"] +
                match_summary["unmatched_bank"]
            ),
            "visibility_into_discrepancies": "none",
        },
        "after": {
            "description": "State following automated reconciliation",
            "exact_matched_deterministic": match_summary["exact_matched"],
            "fuzzy_matched_deterministic": match_summary["fuzzy_matched"],
            "agent_auto_resolved": len(auto_resolved),
            "agent_escalated": len(escalated),
            "human_approved": sum(1 for d in human_decisions if d["human_decision"] == "APPROVED"),
            "human_rejected": sum(1 for d in human_decisions if d["human_decision"] == "REJECTED"),
            "human_deferred": sum(1 for d in human_decisions if d["human_decision"] == "DEFERRED"),
        },
        "efficiency": {
            "pct_resolved_without_any_ai": round(
                (match_summary["exact_matched"] + match_summary["fuzzy_matched"]) /
                total_ledger_txns * 100, 1
            ) if total_ledger_txns else 0,
            "pct_resolved_without_human": round(
                (match_summary["exact_matched"] + match_summary["fuzzy_matched"] + len(auto_resolved)) /
                total_ledger_txns * 100, 1
            ) if total_ledger_txns else 0,
            "cases_requiring_human_judgment": len(escalated),
        },
        "exception_breakdown": _build_exception_breakdown(investigations),
        "auto_resolved_detail": [
            {
                "txn_id": _extract_txn_id(i["case"]),
                "exception_type": i["exception_type"],
                "confidence": i["confidence"],
                "reasoning": i["reasoning"],
            }
            for i in auto_resolved
        ],
        "escalated_detail": [
            {
                "txn_id": _extract_txn_id(i["case"]),
                "exception_type": i["exception_type"],
                "confidence": i["confidence"],
                "reasoning": i["reasoning"],
                "human_decision": next(
                    (d["human_decision"] for d in human_decisions
                     if d["txn_id"] == _extract_txn_id(i["case"])), "PENDING"
                ),
            }
            for i in escalated
        ],
    }

    if eval_result:
        report["evaluation"] = {
            "classification_accuracy": eval_result["classification_accuracy"],
            "decision_accuracy": eval_result["decision_accuracy"],
            "cases_evaluated": eval_result["cases_evaluated"],
            "passed": eval_result["passed"],
        }

    return report


def save_report(report: dict, reports_dir: str) -> str:
    """Save the report as a timestamped JSON file. Returns the file path."""
    os.makedirs(reports_dir, exist_ok=True)
    filename = f"reconciliation_{report['run_id']}.json"
    path = os.path.join(reports_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return path


def _build_exception_breakdown(investigations: list[dict]) -> dict:
    """Count investigations by exception type, for the summary table."""
    breakdown = {}
    for inv in investigations:
        t = inv["exception_type"]
        if t not in breakdown:
            breakdown[t] = {"AUTO_RESOLVED": 0, "ESCALATED": 0}
        action = inv["action_taken"]
        if action in breakdown[t]:
            breakdown[t][action] += 1
    return breakdown


def _extract_txn_id(case: dict) -> str:
    if case["type"] == "fuzzy":
        return case["ledger"]["txn_id"]
    return case["txn"]["txn_id"]
