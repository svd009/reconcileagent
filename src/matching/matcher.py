"""
matcher.py
──────────
Deterministic transaction matching engine.

Why deterministic matching BEFORE the agent runs at all?
  Running an LLM call on every transaction pair would be slow and
  expensive at scale — and unnecessary. The vast majority of transactions
  match perfectly or near-perfectly using simple rules. Real reconciliation
  systems work the same way: cheap deterministic logic handles the bulk,
  and expensive human/AI judgment is reserved for genuinely ambiguous cases.

  This mirrors the tiered model selection pattern from FinGuard (Haiku vs
  Sonnet) but applied one level earlier — at the algorithm level instead
  of the model level. It's the same underlying principle: spend compute
  proportional to difficulty.

Two-pass matching strategy:

  PASS 1 — Exact match
    Same txn_id, same amount, same date → instant match, zero ambiguity.

  PASS 2 — Fuzzy match
    For transactions that didn't exact-match, attempt a fuzzy match using:
      - Amount within AMOUNT_TOLERANCE (handles ROUNDING-type exceptions)
      - Date within DATE_TOLERANCE_DAYS (handles TIMING-type exceptions)
      - Same description (counterparty) as a sanity anchor
    Fuzzy matches are tagged as "needs investigation" — they LOOK like
    matches but the agent should verify and classify why they didn't
    exact-match in the first place.

  PASS 3 — Unmatched
    Everything left over after both passes goes to the agent for full
    investigation: DUPLICATE, MISSING, REFID, and FX cases live here,
    since none of them survive exact or simple fuzzy matching.

Output: three buckets — MATCHED (exact), FUZZY_MATCHED (needs verification),
UNMATCHED (needs full investigation) — which is what gets handed to the
agent in Phase 4.
"""

from datetime import timedelta
from config import AMOUNT_TOLERANCE, DATE_TOLERANCE_DAYS


def match_transactions(ledger: list[dict], bank: list[dict]) -> dict:
    """
    Run the full two-pass deterministic matching engine.

    Args:
        ledger: List of transaction dicts from the internal ledger
        bank:   List of transaction dicts from the bank statement

    Returns:
        {
          "matched":       list of {ledger, bank} exact match pairs,
          "fuzzy_matched": list of {ledger, bank, reason} fuzzy match pairs,
          "unmatched_ledger": list of ledger txns with no counterpart,
          "unmatched_bank":   list of bank txns with no counterpart,
        }
    """
    # Track which transactions have been claimed by a match already
    ledger_remaining = list(ledger)
    bank_remaining = list(bank)

    matched = []
    fuzzy_matched = []

    # ── PASS 1: Exact match on txn_id ──────────────────────────────────────
    bank_by_id = {t["txn_id"]: t for t in bank_remaining}
    still_unmatched_ledger = []

    for l_txn in ledger_remaining:
        b_txn = bank_by_id.get(l_txn["txn_id"])
        if b_txn and b_txn["amount"] == l_txn["amount"] and b_txn["date"] == l_txn["date"]:
            matched.append({"ledger": l_txn, "bank": b_txn})
            del bank_by_id[l_txn["txn_id"]]
        else:
            still_unmatched_ledger.append(l_txn)

    ledger_remaining = still_unmatched_ledger
    bank_remaining = list(bank_by_id.values())

    # ── PASS 2: Fuzzy match (amount tolerance + date tolerance) ────────────
    still_unmatched_ledger = []
    claimed_bank_ids = set()

    for l_txn in ledger_remaining:
        best_match = None
        best_reason = None

        for b_txn in bank_remaining:
            if b_txn["txn_id"] in claimed_bank_ids:
                continue

            amount_diff = abs(l_txn["amount"] - b_txn["amount"])
            amount_pct_diff = amount_diff / l_txn["amount"] if l_txn["amount"] else 0
            date_diff = abs((l_txn["date"] - b_txn["date"]).days)
            same_description = l_txn["description"] in b_txn["description"] or \
                                b_txn["description"] in l_txn["description"]

            # Fuzzy match condition: same/similar description AND
            # (amount within tolerance OR date within tolerance OR exact
            #  amount+date match with a DIFFERENT txn_id, which usually
            #  means a reference ID typo rather than a true timing/rounding gap)
            if same_description:
                if amount_pct_diff <= AMOUNT_TOLERANCE and date_diff == 0:
                    if amount_diff == 0:
                        best_match, best_reason = b_txn, "refid_mismatch"
                    else:
                        best_match, best_reason = b_txn, "amount_within_tolerance"
                    break
                elif amount_diff == 0 and date_diff <= DATE_TOLERANCE_DAYS:
                    if date_diff == 0:
                        best_match, best_reason = b_txn, "refid_mismatch"
                    else:
                        best_match, best_reason = b_txn, "date_within_tolerance"
                    break

        if best_match:
            fuzzy_matched.append({
                "ledger": l_txn,
                "bank": best_match,
                "reason": best_reason,
            })
            claimed_bank_ids.add(best_match["txn_id"])
        else:
            still_unmatched_ledger.append(l_txn)

    ledger_remaining = still_unmatched_ledger
    bank_remaining = [t for t in bank_remaining if t["txn_id"] not in claimed_bank_ids]

    return {
        "matched":          matched,
        "fuzzy_matched":    fuzzy_matched,
        "unmatched_ledger": ledger_remaining,
        "unmatched_bank":   bank_remaining,
    }


def summarize(results: dict) -> dict:
    """Produce a human-readable summary of matching results."""
    return {
        "exact_matched":     len(results["matched"]),
        "fuzzy_matched":     len(results["fuzzy_matched"]),
        "unmatched_ledger":  len(results["unmatched_ledger"]),
        "unmatched_bank":    len(results["unmatched_bank"]),
        "total_needs_agent": len(results["fuzzy_matched"]) +
                              len(results["unmatched_ledger"]) +
                              len(results["unmatched_bank"]),
    }
