"""
generate_data.py
─────────────────
Generates two synthetic transaction datasets — an "internal ledger" and a
"bank statement" — that mostly agree but contain deliberately seeded
mismatches representing the real exception types reconciliation teams deal
with daily.

Why synthetic data with KNOWN ground truth?
  This is what makes the evaluation framework in Phase 5 meaningful.
  Because we seed the exceptions ourselves, we know the "correct answer"
  for every mismatch — which lets us measure whether the agent's root-cause
  classification and resolution decision were actually right, not just
  plausible-sounding.

Exception types seeded (each with a unique transaction ID prefix so the
eval framework can verify the agent's classification against ground truth):

  TIMING     — same transaction, posted 1-3 days apart on each system
  ROUNDING   — same transaction, amount differs by a small fee/rounding delta
  DUPLICATE  — same transaction appears twice on the ledger side only
  MISSING    — transaction exists on one side only (genuine processing gap)
  REFID      — same transaction, reference ID has a typo/transposition
  FX         — same transaction, amount differs due to exchange rate timing

Each transaction has:
  txn_id        — unique reference ID (used for exact matching)
  date          — transaction date (YYYY-MM-DD)
  amount        — transaction amount (USD)
  description   — counterparty / memo text
  category      — internal transaction category (e.g. wire, ACH, card)
"""

import os
import csv
import random
from datetime import datetime, timedelta

random.seed(42)  # reproducible dataset across runs

CATEGORIES = ["wire_transfer", "ach_payment", "card_settlement",
              "check_deposit", "fee_adjustment"]

COUNTERPARTIES = [
    "Meridian Capital Partners", "Northbridge Holdings", "Apex Logistics LLC",
    "Sterling & Co", "Vantage Point Industries", "Crestview Mfg",
    "Harbor Trust Services", "Pinnacle Retail Group", "Atlas Freight Co",
    "Beacon Hill Advisors",
]


def _rand_date(start: datetime, days_span: int) -> datetime:
    return start + timedelta(days=random.randint(0, days_span))


def _rand_amount() -> float:
    return round(random.uniform(150.0, 48000.0), 2)


def generate_clean_transaction(txn_num: int, base_date: datetime) -> dict:
    """Generate one transaction that will match perfectly on both sides."""
    return {
        "txn_id": f"TXN-{txn_num:05d}",
        "date": _rand_date(base_date, 60).strftime("%Y-%m-%d"),
        "amount": _rand_amount(),
        "description": random.choice(COUNTERPARTIES),
        "category": random.choice(CATEGORIES),
    }


def generate_dataset(num_clean: int = 70, num_each_exception: int = 5,
                     base_date: datetime = None) -> tuple:
    """
    Generate matched ledger and bank statement datasets with seeded exceptions.

    Returns:
        (ledger_rows, bank_rows, ground_truth)
        ground_truth: dict mapping txn_id -> {"exception_type": str, "expected_action": str}
    """
    if base_date is None:
        base_date = datetime(2026, 4, 1)

    ledger_rows = []
    bank_rows = []
    ground_truth = {}

    txn_counter = 1

    # ── Clean transactions (perfect matches) ──────────────────────────────
    for _ in range(num_clean):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_rows.append(dict(txn))
        bank_rows.append(dict(txn))
        txn_counter += 1

    # ── TIMING exceptions ──────────────────────────────────────────────────
    for _ in range(num_each_exception):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_row = dict(txn)
        bank_row = dict(txn)
        # Bank posts 1-3 days after the ledger records it
        ledger_date = datetime.strptime(txn["date"], "%Y-%m-%d")
        offset = random.randint(1, 3)
        bank_row["date"] = (ledger_date + timedelta(days=offset)).strftime("%Y-%m-%d")
        ledger_rows.append(ledger_row)
        bank_rows.append(bank_row)
        ground_truth[txn["txn_id"]] = {
            "exception_type": "TIMING",
            "expected_action": "AUTO_RESOLVE",
            "explanation": f"Bank posted {offset} day(s) after ledger — timing difference, not an error"
        }
        txn_counter += 1

    # ── ROUNDING exceptions ────────────────────────────────────────────────
    for _ in range(num_each_exception):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_row = dict(txn)
        bank_row = dict(txn)
        # Bank deducts a small processing fee
        fee = round(random.uniform(0.50, 4.50), 2)
        bank_row["amount"] = round(txn["amount"] - fee, 2)
        ledger_rows.append(ledger_row)
        bank_rows.append(bank_row)
        ground_truth[txn["txn_id"]] = {
            "exception_type": "ROUNDING",
            "expected_action": "AUTO_RESOLVE",
            "explanation": f"Bank amount differs by ${fee} — processing fee deduction, not an error"
        }
        txn_counter += 1

    # ── DUPLICATE exceptions ───────────────────────────────────────────────
    for _ in range(num_each_exception):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_rows.append(dict(txn))
        # Duplicate entry on the ledger side only (different row, same txn_id won't work
        # for CSV uniqueness, so we give the duplicate a related ID)
        dup = dict(txn)
        dup["txn_id"] = f"{txn['txn_id']}-DUP"
        ledger_rows.append(dup)
        bank_rows.append(dict(txn))
        ground_truth[txn["txn_id"]] = {
            "exception_type": "DUPLICATE",
            "expected_action": "ESCALATE",
            "explanation": "Transaction recorded twice in internal ledger — duplicate entry error, needs correction"
        }
        txn_counter += 1

    # ── MISSING exceptions (genuine gap — ledger only, no bank counterpart) ─
    for _ in range(num_each_exception):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_rows.append(dict(txn))
        # Intentionally NOT added to bank_rows
        ground_truth[txn["txn_id"]] = {
            "exception_type": "MISSING",
            "expected_action": "ESCALATE",
            "explanation": "No counterpart found on bank statement — possible unprocessed or failed transaction"
        }
        txn_counter += 1

    # ── REFID exceptions (reference ID typo) ───────────────────────────────
    for _ in range(num_each_exception):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_row = dict(txn)
        bank_row = dict(txn)
        # Transpose two digits in the bank-side reference ID
        original_id = txn["txn_id"]
        digits = list(original_id.replace("TXN-", ""))
        if len(digits) >= 2:
            digits[0], digits[1] = digits[1], digits[0]
        bank_row["txn_id"] = f"TXN-{''.join(digits)}-B"  # mangled ID, unique suffix
        ledger_rows.append(ledger_row)
        bank_rows.append(bank_row)
        ground_truth[original_id] = {
            "exception_type": "REFID",
            "expected_action": "AUTO_RESOLVE",
            "explanation": "Reference ID mismatch due to transposition — same amount/date/counterparty confirms match"
        }
        txn_counter += 1

    # ── FX exceptions (amount differs due to exchange rate timing) ─────────
    for _ in range(num_each_exception):
        txn = generate_clean_transaction(txn_counter, base_date)
        ledger_row = dict(txn)
        bank_row = dict(txn)
        # FX rate drift: 1-3% difference, larger than rounding but still explainable
        fx_drift = round(txn["amount"] * random.uniform(0.01, 0.03), 2)
        bank_row["amount"] = round(txn["amount"] - fx_drift, 2)
        bank_row["category"] = "wire_transfer"
        ledger_row["category"] = "wire_transfer"
        bank_row["description"] = ledger_row["description"] + " (FX)"
        ledger_rows.append(ledger_row)
        bank_rows.append(bank_row)
        ground_truth[txn["txn_id"]] = {
            "exception_type": "FX",
            "expected_action": "ESCALATE",
            "explanation": f"Amount differs by ${fx_drift} — exceeds simple rounding, likely FX rate timing, needs review"
        }
        txn_counter += 1

    # Shuffle row order so the agent can't infer exception type from position
    random.shuffle(ledger_rows)
    random.shuffle(bank_rows)

    return ledger_rows, bank_rows, ground_truth


def write_csv(rows: list[dict], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = ["txn_id", "date", "amount", "description", "category"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"  Written: {path} ({len(rows)} rows)")


def write_ground_truth(ground_truth: dict, path: str):
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)
    print(f"  Written: {path} ({len(ground_truth)} labeled exceptions)")


if __name__ == "__main__":
    from config import LEDGER_FILE, BANK_FILE, DATA_DIR

    print("Generating synthetic reconciliation dataset...")
    ledger_rows, bank_rows, ground_truth = generate_dataset(
        num_clean=70, num_each_exception=5
    )

    write_csv(ledger_rows, LEDGER_FILE)
    write_csv(bank_rows, BANK_FILE)
    write_ground_truth(ground_truth, os.path.join(DATA_DIR, "ground_truth.json"))

    print(f"\nDataset summary:")
    print(f"  Ledger rows: {len(ledger_rows)}")
    print(f"  Bank rows:   {len(bank_rows)}")
    print(f"  Seeded exceptions: {len(ground_truth)}")
    print(f"  Clean (auto-matching) transactions: ~70")
