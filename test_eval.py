"""
test_eval.py
─────────────
Smoke test for Phase 5 — verifies the evaluation framework's scoring
logic using MOCK investigation results (not real agent output).

Why mock data for this test?
  We're testing the SCORING LOGIC here, not the agent. Using mock
  investigations with known-correct and known-incorrect answers lets us
  verify the evaluator computes accuracy correctly, with zero API cost
  and fully deterministic, reproducible results.

The real agent's actual performance gets evaluated in Phase 6's full
demo run (main.py), where eval_framework scores real investigation
results against the same ground truth.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_data import generate_dataset, write_csv, write_ground_truth
from src.evaluation.eval_framework import ReconciliationEvaluator
from config import LEDGER_FILE, BANK_FILE, DATA_DIR


def build_mock_investigation(case_type, ledger_txn, exception_type, action,
                             confidence=0.9, bank_txn=None, reason="test"):
    """Build a mock investigation result shaped like InvestigationAgent's output."""
    if case_type == "fuzzy":
        case = {"type": "fuzzy", "ledger": ledger_txn, "bank": bank_txn, "reason": reason}
    else:
        case = {"type": case_type, "txn": ledger_txn}

    return {
        "case": case,
        "action_taken": action,
        "exception_type": exception_type,
        "confidence": confidence,
        "reasoning": "mock reasoning for test purposes",
        "tool_calls": [],
        "thinking": "",
    }


def run_test():
    print("=" * 60)
    print("ReconcileAgent — Phase 5 Evaluation Framework Test")
    print("=" * 60)
    print("\nNOTE: Uses MOCK investigation data — zero API cost")

    # ── Setup: generate dataset to get realistic ground truth ──────
    print("\n[Setup] Generating dataset for ground truth...")
    ledger_rows, bank_rows, ground_truth = generate_dataset(num_clean=70, num_each_exception=5)
    write_csv(ledger_rows, LEDGER_FILE)
    write_csv(bank_rows, BANK_FILE)
    write_ground_truth(ground_truth, os.path.join(DATA_DIR, "ground_truth.json"))

    # Pick 6 known ground truth entries, one per exception type
    gt_by_type = {}
    for txn_id, info in ground_truth.items():
        if info["exception_type"] not in gt_by_type:
            gt_by_type[info["exception_type"]] = txn_id
    print(f"  Selected sample transactions: {gt_by_type}")

    # ── Test 1: Perfect agent (100% accuracy) ───────────────────────
    print("\n[Test 1] Evaluating a PERFECT mock agent (should score 100%)...")
    perfect_investigations = []
    for exc_type, txn_id in gt_by_type.items():
        gt = ground_truth[txn_id]
        action = "AUTO_RESOLVED" if gt["expected_action"] == "AUTO_RESOLVE" else "ESCALATED"
        mock_ledger_txn = {"txn_id": txn_id}
        case_type = "unmatched_ledger" if gt["expected_action"] == "ESCALATE" and exc_type in ("DUPLICATE", "MISSING") else "fuzzy"
        inv = build_mock_investigation(
            "unmatched_ledger", mock_ledger_txn, exc_type, action, confidence=0.95
        )
        perfect_investigations.append(inv)

    evaluator = ReconciliationEvaluator()
    result = evaluator.evaluate(perfect_investigations, ground_truth, verbose=True)

    assert result["classification_accuracy"] == 1.0, \
        f"Expected 100% classification accuracy, got {result['classification_accuracy']}"
    assert result["decision_accuracy"] == 1.0, \
        f"Expected 100% decision accuracy, got {result['decision_accuracy']}"
    assert result["passed"] is True
    print(f"  ✓ Perfect agent correctly scored 100%/100%")

    # ── Test 2: Imperfect agent (mixed accuracy) ─────────────────────
    print("\n[Test 2] Evaluating an IMPERFECT mock agent (mixed results)...")
    imperfect_investigations = []
    items = list(gt_by_type.items())

    for i, (exc_type, txn_id) in enumerate(items):
        gt = ground_truth[txn_id]
        mock_ledger_txn = {"txn_id": txn_id}

        if i == 0:
            # Wrong classification, correct decision
            wrong_type = "OTHER" if exc_type != "OTHER" else "TIMING"
            action = "AUTO_RESOLVED" if gt["expected_action"] == "AUTO_RESOLVE" else "ESCALATED"
            inv = build_mock_investigation("unmatched_ledger", mock_ledger_txn, wrong_type, action)
        elif i == 1:
            # Correct classification, wrong decision
            wrong_action = "ESCALATED" if gt["expected_action"] == "AUTO_RESOLVE" else "AUTO_RESOLVED"
            inv = build_mock_investigation("unmatched_ledger", mock_ledger_txn, exc_type, wrong_action)
        else:
            # Correct on both
            action = "AUTO_RESOLVED" if gt["expected_action"] == "AUTO_RESOLVE" else "ESCALATED"
            inv = build_mock_investigation("unmatched_ledger", mock_ledger_txn, exc_type, action)

        imperfect_investigations.append(inv)

    result2 = evaluator.evaluate(imperfect_investigations, ground_truth, verbose=True)

    expected_classification_acc = (len(items) - 1) / len(items)  # 1 wrong out of 6
    expected_decision_acc = (len(items) - 1) / len(items)         # 1 wrong out of 6

    print(f"\n  Classification accuracy: {result2['classification_accuracy']:.1%} "
          f"(expected ~{expected_classification_acc:.1%})")
    print(f"  Decision accuracy:       {result2['decision_accuracy']:.1%} "
          f"(expected ~{expected_decision_acc:.1%})")

    assert abs(result2["classification_accuracy"] - expected_classification_acc) < 0.01
    assert abs(result2["decision_accuracy"] - expected_decision_acc) < 0.01
    print(f"  ✓ Imperfect agent scored correctly — errors detected and measured")

    # ── Test 3: Unscored cases (no ground truth) handled gracefully ──
    print("\n[Test 3] Verifying cases without ground truth are handled gracefully...")
    no_gt_investigation = build_mock_investigation(
        "unmatched_ledger", {"txn_id": "TXN-99999-NOT-SEEDED"}, "TIMING", "AUTO_RESOLVED"
    )
    result3 = evaluator.evaluate([no_gt_investigation], ground_truth, verbose=False)
    assert result3["cases_evaluated"] == 0
    assert result3["case_results"][0]["scored"] is False
    print(f"  ✓ Unlabeled case correctly excluded from scoring, not crashed or miscounted")

    # ── Test 4: Per-type breakdown is present and structured correctly ──
    print("\n[Test 4] Verifying per-type breakdown structure...")
    assert "per_type_breakdown" in result
    for exc_type, stats in result["per_type_breakdown"].items():
        assert "correct" in stats and "total" in stats and "accuracy" in stats
    print(f"  ✓ Per-type breakdown present: {list(result['per_type_breakdown'].keys())}")

    print("\n" + "=" * 60)
    print("Phase 5 PASSED ✓ — Evaluation framework scoring logic verified")
    print("=" * 60)
    print(f"\nThe evaluator correctly distinguishes classification accuracy")
    print(f"(did it diagnose the right root cause) from decision accuracy")
    print(f"(did it take the right action) — two separate, honest metrics.")


if __name__ == "__main__":
    run_test()
