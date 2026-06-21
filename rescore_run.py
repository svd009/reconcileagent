"""
rescore_run.py
────────────────
Re-evaluates a completed reconciliation run using amounts pulled from the
durable audit trail, rather than re-running the agent. Demonstrates the
practical value of the durable audit trail: we can reconstruct and
re-analyze past decisions without spending any additional API credits.

Usage:
  python rescore_run.py <run_id>

Example:
  python rescore_run.py ed5bb31d
"""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.mcp_server.audit_trail import AuditTrail
from src.evaluation.eval_framework import ReconciliationEvaluator
from config import AUDIT_DB, DATA_DIR


def rescore(run_id: str):
    print(f"Re-scoring run {run_id} using the durable audit trail...\n")

    audit = AuditTrail(AUDIT_DB)
    entries = audit.get_run_history(run_id)

    if not entries:
        print(f"No audit entries found for run_id={run_id}. Check the ID and try again.")
        return

    with open(os.path.join(DATA_DIR, "ground_truth.json")) as f:
        ground_truth = json.load(f)

    # Reconstruct investigation-shaped dicts from audit entries.
    # Only AUTO_RESOLVED and ESCALATED rows represent final agent decisions —
    # RESOLVE_REJECTED rows are intermediate policy rejections, not final actions,
    # and human decisions (APPROVED/REJECTED/DEFERRED) are a separate later step.
    investigations = []
    for entry in entries:
        if entry["action"] not in ("AUTO_RESOLVED", "ESCALATED"):
            continue
        investigations.append({
            "case": {
                "type": "unmatched_ledger",
                "txn": {"txn_id": entry["txn_id"], "amount": entry["amount"]},
            },
            "action_taken": entry["action"],
            "exception_type": entry["exception_type"],
            "confidence": entry["confidence"],
            "reasoning": entry["reasoning"],
        })

    print(f"Reconstructed {len(investigations)} final agent decisions from the audit trail")
    print(f"(audit trail had {len(entries)} total entries for this run, including "
          f"policy rejections)\n")

    evaluator = ReconciliationEvaluator()
    result = evaluator.evaluate(investigations, ground_truth, verbose=True)

    print(f"\n{'='*60}")
    print(f"RESCORED RESULTS (policy-aware decision accuracy)")
    print(f"{'='*60}")
    print(f"Classification accuracy:        {result['classification_accuracy']:.1%}")
    print(f"Decision accuracy (policy-aware): {result['decision_accuracy']:.1%}")
    print(f"Decision accuracy (intrinsic):    {result['intrinsic_decision_accuracy']:.1%}  "
          f"[diagnostic only]")
    print(f"Passed: {result['passed']}")

    # Save the rescored result alongside the original report
    out_path = os.path.join("reports", f"rescored_{run_id}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nRescored evaluation saved to: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python rescore_run.py <run_id>")
        sys.exit(1)
    rescore(sys.argv[1])
