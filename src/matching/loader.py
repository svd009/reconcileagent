"""
loader.py
─────────
Loads the two transaction CSVs into normalized Python dicts.

Why normalize at load time?
  CSV gives us strings for everything. We convert amount to float and
  validate date format once, here, so every downstream module (matcher,
  MCP tools, agent) can trust the data shape without re-parsing.
"""

import csv
from datetime import datetime


def load_transactions(csv_path: str) -> list[dict]:
    """
    Load a transaction CSV into a list of normalized dicts.

    Args:
        csv_path: Path to internal_ledger.csv or bank_statement.csv

    Returns:
        List of dicts: {txn_id, date (datetime), amount (float),
                         description, category}
    """
    transactions = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            transactions.append({
                "txn_id":      row["txn_id"],
                "date":        datetime.strptime(row["date"], "%Y-%m-%d"),
                "amount":      float(row["amount"]),
                "description": row["description"],
                "category":    row["category"],
            })
    return transactions


def load_ground_truth(json_path: str) -> dict:
    """Load the ground-truth exception labels (used by eval framework only)."""
    import json
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)
