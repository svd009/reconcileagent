"""
test_mcp.py
───────────
Smoke test for Phase 3 — verifies the MCP tool executor and the durable
audit trail, including the critical policy enforcement test: the agent
CANNOT auto-resolve a high-value or low-confidence case just by calling
the tool — the tool itself rejects it.

No API calls — this tests deterministic tool logic and SQLite persistence.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from generate_data import generate_dataset, write_csv, write_ground_truth
from src.matching.loader import load_transactions
from src.mcp_server.audit_trail import AuditTrail
from src.mcp_server.reconciliation_tools import ReconciliationToolExecutor, TOOL_SCHEMAS
from config import LEDGER_FILE, BANK_FILE, DATA_DIR, AUDIT_DB, AUTO_RESOLVE_MAX_AMOUNT


def run_test():
    print("=" * 60)
    print("ReconcileAgent — Phase 3 MCP Tools & Audit Trail Test")
    print("=" * 60)

    # ── Setup ──────────────────────────────────────────────────────
    print("\n[Setup] Generating dataset and loading transactions...")
    ledger_rows, bank_rows, ground_truth = generate_dataset(num_clean=70, num_each_exception=5)
    write_csv(ledger_rows, LEDGER_FILE)
    write_csv(bank_rows, BANK_FILE)
    write_ground_truth(ground_truth, os.path.join(DATA_DIR, "ground_truth.json"))

    ledger = load_transactions(LEDGER_FILE)
    bank = load_transactions(BANK_FILE)

    # Use a fresh audit DB for this test run so results are predictable
    test_db_path = os.path.join(DATA_DIR, "test_audit_trail.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    audit = AuditTrail(test_db_path)
    executor = ReconciliationToolExecutor(ledger, bank, audit, run_id="test-run-001")

    # ── Test 1: Tool schemas ──────────────────────────────────────
    print("\n[Test 1] Verifying tool schemas...")
    assert len(TOOL_SCHEMAS) == 4
    for schema in TOOL_SCHEMAS:
        assert "name" in schema and "description" in schema and "input_schema" in schema
        print(f"  ✓ Tool: {schema['name']}")

    # ── Test 2: search_counterpart finds plausible matches ─────────
    print("\n[Test 2] Testing search_counterpart...")
    sample_txn = ledger[0]
    result_json = executor.execute("search_counterpart", {
        "search_in": "bank",
        "amount": sample_txn["amount"],
        "description": sample_txn["description"],
    })
    result = json.loads(result_json)
    assert "candidates" in result
    print(f"  Searched for '{sample_txn['description']}' ~${sample_txn['amount']}")
    print(f"  Found {len(result['candidates'])} candidates ✓")

    # ── Test 3: get_transaction_history returns empty for new txn ──
    print("\n[Test 3] Testing get_transaction_history (no prior history)...")
    result_json = executor.execute("get_transaction_history", {"txn_id": "TXN-00001"})
    result = json.loads(result_json)
    assert result["prior_entries"] == []
    print(f"  ✓ No prior history found (expected, fresh database)")

    # ── Test 4: resolve_exception SUCCEEDS for high-confidence, low-value ──
    print("\n[Test 4] Testing resolve_exception — should SUCCEED...")
    result_json = executor.execute("resolve_exception", {
        "txn_id": "TXN-00010",
        "exception_type": "TIMING",
        "confidence": 0.95,
        "amount": 1200.00,
        "reasoning": "Bank posted 2 days after ledger, consistent with TIMING pattern",
    })
    result = json.loads(result_json)
    assert result["status"] == "RESOLVED", f"Expected RESOLVED, got {result}"
    print(f"  ✓ Resolved: confidence=0.95, amount=$1200 (within policy)")

    # ── Test 5: resolve_exception REJECTED for high amount (policy enforcement) ──
    print("\n[Test 5] Testing resolve_exception — should be REJECTED (amount too high)...")
    high_amount = AUTO_RESOLVE_MAX_AMOUNT + 5000
    result_json = executor.execute("resolve_exception", {
        "txn_id": "TXN-00020",
        "exception_type": "ROUNDING",
        "confidence": 0.99,   # very high confidence — should NOT matter
        "amount": high_amount,
        "reasoning": "Small fee difference, very confident this is safe",
    })
    result = json.loads(result_json)
    assert result["status"] == "REJECTED", \
        f"Expected REJECTED due to amount policy, got {result}"
    print(f"  ✓ Correctly REJECTED despite 0.99 confidence — amount ${high_amount} exceeds policy")
    print(f"  Tool response: {result['reason']}")

    # ── Test 6: resolve_exception REJECTED for low confidence ──────
    print("\n[Test 6] Testing resolve_exception — should be REJECTED (low confidence)...")
    result_json = executor.execute("resolve_exception", {
        "txn_id": "TXN-00030",
        "exception_type": "OTHER",
        "confidence": 0.40,
        "amount": 500.00,
        "reasoning": "Not entirely sure what happened here",
    })
    result = json.loads(result_json)
    assert result["status"] == "REJECTED"
    print(f"  ✓ Correctly REJECTED — confidence 0.40 below threshold")

    # ── Test 7: escalate_exception always succeeds ─────────────────
    print("\n[Test 7] Testing escalate_exception...")
    result_json = executor.execute("escalate_exception", {
        "txn_id": "TXN-00030",
        "exception_type": "OTHER",
        "confidence": 0.40,
        "amount": 500.00,
        "reasoning": "Unclear root cause, needs human review",
    })
    result = json.loads(result_json)
    assert result["status"] == "ESCALATED"
    assert result["awaiting"] == "human_approval"
    print(f"  ✓ Escalated successfully, awaiting human approval")

    # ── Test 8: Audit trail durability — entries persist and are queryable ──
    print("\n[Test 8] Verifying audit trail durability...")
    run_history = audit.get_run_history("test-run-001")
    # Only successful actions write to the audit trail — the two REJECTED
    # resolve_exception calls (Tests 5 and 6) correctly do NOT write entries,
    # since they were never actions, just rejected attempts. Only Test 4
    # (RESOLVED) and Test 7 (ESCALATED) should appear.
    assert len(run_history) == 2, f"Expected 2 logged actions (1 resolved + 1 escalated), got {len(run_history)}"
    print(f"  ✓ {len(run_history)} entries logged for this run (rejected attempts correctly excluded)")

    escalations = audit.get_escalations("test-run-001")
    assert len(escalations) == 1
    print(f"  ✓ {len(escalations)} escalation correctly queryable")

    txn_history = audit.get_transaction_history("TXN-00030")
    assert len(txn_history) == 1, "TXN-00030 should have exactly 1 entry — the escalation (the rejected resolve attempt isn't logged)"
    print(f"  ✓ Transaction-specific history retrievable: {len(txn_history)} entry for TXN-00030")

    stats = audit.get_summary_stats()
    print(f"  ✓ Summary stats: {stats}")
    assert stats["total_entries"] == 2

    # ── Test 9: Audit trail persists across a NEW AuditTrail instance ──
    print("\n[Test 9] Verifying durability across process restarts...")
    audit2 = AuditTrail(test_db_path)  # simulate a fresh process opening the same DB
    history_after_reopen = audit2.get_run_history("test-run-001")
    assert len(history_after_reopen) == 2, "Audit trail should persist after reopening the DB file"
    print(f"  ✓ {len(history_after_reopen)} entries still present after reopening database")
    print(f"  ✓ This confirms the audit trail is durable, not in-memory only")

    print("\n" + "=" * 60)
    print("Phase 3 PASSED ✓ — MCP tools and durable audit trail working correctly")
    print("=" * 60)
    print(f"\nKey result: policy enforcement happens at the TOOL level —")
    print(f"the agent cannot auto-resolve high-value or low-confidence cases")
    print(f"by being persuasive in its reasoning text alone.")


if __name__ == "__main__":
    run_test()
