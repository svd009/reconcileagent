"""
test_agent.py
──────────────
Smoke test for Phase 4 — verifies the investigation agent and orchestrator.

COST NOTE: This test makes REAL Claude API calls. To keep cost minimal
(~$0.05-0.15), we cap the run to a small number of cases using max_cases.
A full reconciliation run (33 cases) costs more — save that for the final
demo run in main.py once this test confirms everything works.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_data import generate_dataset, write_csv, write_ground_truth
from src.matching.loader import load_transactions
from src.agents.orchestrator import ReconciliationOrchestrator
from config import LEDGER_FILE, BANK_FILE, DATA_DIR


def run_test():
    print("=" * 60)
    print("ReconcileAgent — Phase 4 Investigation Agent Test")
    print("=" * 60)
    print("\nNOTE: This test makes real Claude API calls (~$0.05-0.15 for 4 cases)")

    # ── Setup ──────────────────────────────────────────────────────
    print("\n[Setup] Generating dataset...")
    ledger_rows, bank_rows, ground_truth = generate_dataset(num_clean=70, num_each_exception=5)
    write_csv(ledger_rows, LEDGER_FILE)
    write_csv(bank_rows, BANK_FILE)
    write_ground_truth(ground_truth, os.path.join(DATA_DIR, "ground_truth.json"))

    ledger = load_transactions(LEDGER_FILE)
    bank = load_transactions(BANK_FILE)

    test_db_path = os.path.join(DATA_DIR, "test_audit_trail.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    orchestrator = ReconciliationOrchestrator(ledger, bank, test_db_path)

    # ── Test: Run a capped reconciliation cycle ─────────────────────
    print("\n[Test] Running capped reconciliation cycle (4 cases)...")
    result = orchestrator.run(verbose=True, max_cases=4)

    # ── Verify structure ──────────────────────────────────────────
    print("\n[Verify] Checking result structure...")
    assert "match_summary" in result
    assert "investigations" in result
    assert len(result["investigations"]) == 4, \
        f"Expected 4 investigations, got {len(result['investigations'])}"
    print(f"  ✓ {len(result['investigations'])} investigations completed")

    for inv in result["investigations"]:
        assert inv["action_taken"] in ("AUTO_RESOLVED", "ESCALATED")
        assert "exception_type" in inv
        assert "confidence" in inv
        assert "reasoning" in inv
        print(f"  ✓ {inv['action_taken']}: {inv['exception_type']} "
              f"(confidence={inv['confidence']})")

    # ── Verify audit trail captured these decisions ──────────────────
    print("\n[Verify] Checking audit trail captured the run...")
    run_history = orchestrator.audit.get_run_history(orchestrator.run_id)
    expected_logged = len(result["auto_resolved"]) + len(result["escalated"])
    assert len(run_history) == expected_logged, \
        f"Audit trail should have {expected_logged} entries, found {len(run_history)}"
    print(f"  ✓ {len(run_history)} entries correctly logged to durable audit trail")

    print(f"\n  Auto-resolved: {len(result['auto_resolved'])}")
    print(f"  Escalated:     {len(result['escalated'])}")

    print("\n" + "=" * 60)
    print("Phase 4 PASSED ✓ — Investigation agent and orchestrator working correctly")
    print("=" * 60)
    print(f"\nThis confirms the full agentic loop: investigate → decide → act → audit log.")
    print(f"Run main.py for a full reconciliation across all ~33 cases when ready to demo.")


if __name__ == "__main__":
    run_test()
