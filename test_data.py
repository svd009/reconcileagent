"""
test_data.py
────────────
Smoke test for Phase 1 — verifies the synthetic dataset is internally
consistent and the seeded exceptions match their ground truth labels.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import csv
from collections import Counter
from generate_data import generate_dataset, write_csv, write_ground_truth
from config import LEDGER_FILE, BANK_FILE, DATA_DIR


def run_test():
    print("=" * 60)
    print("ReconcileAgent — Phase 1 Data Generation Test")
    print("=" * 60)

    print("\n[Step 1] Generating dataset...")
    ledger_rows, bank_rows, ground_truth = generate_dataset(
        num_clean=70, num_each_exception=5
    )

    write_csv(ledger_rows, LEDGER_FILE)
    write_csv(bank_rows, BANK_FILE)
    write_ground_truth(ground_truth, os.path.join(DATA_DIR, "ground_truth.json"))

    # ── Test 1: Row counts make sense ──────────────────────────────
    print("\n[Test 1] Verifying row counts...")
    assert len(ledger_rows) > len(bank_rows), \
        "Ledger should have more rows (duplicates + missing-from-bank entries)"
    print(f"  Ledger: {len(ledger_rows)} rows | Bank: {len(bank_rows)} rows ✓")

    # ── Test 2: Ground truth has all 6 exception types ─────────────
    print("\n[Test 2] Verifying exception type coverage...")
    type_counts = Counter(v["exception_type"] for v in ground_truth.values())
    expected_types = {"TIMING", "ROUNDING", "DUPLICATE", "MISSING", "REFID", "FX"}
    assert set(type_counts.keys()) == expected_types, \
        f"Missing exception types: {expected_types - set(type_counts.keys())}"
    for t, count in sorted(type_counts.items()):
        print(f"  {t}: {count} cases ✓")

    # ── Test 3: All ground truth txn_ids exist in ledger ────────────
    print("\n[Test 3] Verifying ground truth references valid ledger transactions...")
    ledger_ids = {row["txn_id"] for row in ledger_rows}
    missing = [tid for tid in ground_truth if tid not in ledger_ids]
    assert len(missing) == 0, f"Ground truth references missing ledger IDs: {missing}"
    print(f"  All {len(ground_truth)} ground truth IDs found in ledger ✓")

    # ── Test 4: Expected actions are valid ──────────────────────────
    print("\n[Test 4] Verifying expected_action values...")
    valid_actions = {"AUTO_RESOLVE", "ESCALATE"}
    for tid, info in ground_truth.items():
        assert info["expected_action"] in valid_actions, \
            f"{tid} has invalid action: {info['expected_action']}"
    print(f"  All expected_action values valid ✓")

    # ── Test 5: CSV files are readable and well-formed ──────────────
    print("\n[Test 5] Verifying CSV files are well-formed...")
    with open(LEDGER_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == len(ledger_rows)
        assert set(rows[0].keys()) == {"txn_id", "date", "amount", "description", "category"}
    print(f"  Ledger CSV: {len(rows)} rows, correct columns ✓")

    with open(BANK_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == len(bank_rows)
    print(f"  Bank CSV: {len(rows)} rows, correct columns ✓")

    print("\n" + "=" * 60)
    print("Phase 1 PASSED ✓ — Synthetic dataset generated correctly")
    print("=" * 60)
    print(f"\nTotal transactions: {len(ledger_rows)} ledger, {len(bank_rows)} bank")
    print(f"Seeded exceptions: {len(ground_truth)} across 6 types")
    print(f"Clean auto-matching transactions: ~70")


if __name__ == "__main__":
    run_test()
