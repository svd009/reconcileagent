"""
eval_framework.py
───────────────────
Evaluates the investigation agent's decisions against the ground truth
labels seeded in Phase 1 — the same principle as FinGuard's eval
framework, applied to decision quality instead of retrieval quality.

Two distinct, separately measured metrics:

  1. CLASSIFICATION ACCURACY
     Did the agent correctly identify the root cause (TIMING, ROUNDING,
     DUPLICATE, MISSING, REFID, FX)? This measures diagnostic quality —
     did the agent understand WHAT happened.

  2. DECISION ACCURACY
     Did the agent choose the policy-correct action (AUTO_RESOLVE vs
     ESCALATE)? This measures judgment quality — given what happened,
     did the agent make the right call about whether it's safe to
     auto-resolve.

IMPORTANT — decision accuracy accounts for the amount-based policy gate:
  Phase 1's ground truth labels each exception type with an "intrinsic"
  expected_action — e.g. TIMING is intrinsically safe to auto-resolve.
  But the system's actual policy (config.AUTO_RESOLVE_MAX_AMOUNT) also
  requires escalation for ANY transaction above the auto-resolve amount
  cap, regardless of exception type. A TIMING case on a $30,000 wire
  transfer is correctly escalated by policy even though TIMING alone
  would normally qualify for auto-resolution.

  Scoring against the intrinsic label alone would penalize the agent
  for correctly following policy — exactly the kind of eval design gap
  this project's earlier phases also surfaced (see FinGuard's keyword
  matching case for a similar lesson). The fix is to compute the
  POLICY-CORRECT expected action: intrinsic expectation, overridden to
  ESCALATE if amount exceeds the cap. We score against that, not the
  intrinsic label alone — and we still report the intrinsic mismatch
  rate separately, since it's a genuinely useful diagnostic.

Why separate these two numbers instead of one blended score?
  An agent can correctly diagnose a DUPLICATE (classification correct)
  but incorrectly try to auto-resolve it instead of escalating it
  (decision incorrect) — these are different failure modes with
  different real-world consequences, and a blended score would hide
  which one is happening. This mirrors how FinGuard separated
  rule-based structural checks from model-as-judge semantic checks —
  measuring different things separately is more honest than one
  composite number.

This evaluation requires NO additional API calls — it's pure comparison
logic against the ground truth and the orchestrator's recorded results.
"""

from collections import defaultdict
from config import EVAL_PASS_THRESHOLD, AUTO_RESOLVE_MAX_AMOUNT


class ReconciliationEvaluator:
    """
    Scores a completed reconciliation run against seeded ground truth.
    """

    def evaluate(self, investigations: list[dict], ground_truth: dict,
                 verbose: bool = True) -> dict:
        """
        Compare agent investigations against ground truth labels.

        Args:
            investigations: The "investigations" list from orchestrator.run()
            ground_truth:   The ground_truth.json dict from Phase 1
            verbose:        Print per-case scoring details

        Returns:
            {
              "classification_accuracy": float,
              "decision_accuracy": float,
              "intrinsic_decision_accuracy": float,  ← pre-policy-override comparison, diagnostic only
              "cases_evaluated": int,
              "cases_with_ground_truth": int,
              "per_type_breakdown": dict,
              "case_results": list,
              "passed": bool,
            }
        """
        if verbose:
            print(f"\n  [Evaluator] Scoring {len(investigations)} investigations "
                  f"against {len(ground_truth)} ground truth labels...")
            print(f"  [Evaluator] Decision accuracy accounts for the "
                  f"${AUTO_RESOLVE_MAX_AMOUNT:,.0f} amount policy override\n")

        case_results = []
        type_correct = defaultdict(int)
        type_total = defaultdict(int)
        decision_correct = 0
        intrinsic_decision_correct = 0
        classification_correct = 0
        evaluated_count = 0

        for inv in investigations:
            txn_id = self._extract_txn_id(inv["case"])
            gt_entry = self._find_ground_truth(txn_id, ground_truth)

            if gt_entry is None:
                # This case wasn't a seeded exception (e.g. a borderline FX
                # case that fuzzy-matched, or genuinely no ground truth) —
                # skip it from scored evaluation, but still record it
                case_results.append({
                    "txn_id": txn_id,
                    "scored": False,
                    "reason": "no ground truth label for this transaction",
                })
                continue

            evaluated_count += 1
            expected_type = gt_entry["exception_type"]
            intrinsic_expected_action = gt_entry["expected_action"]
            actual_type = inv["exception_type"]
            actual_action = inv["action_taken"]
            amount = self._extract_amount(inv["case"])

            # Policy-correct expected action: intrinsic expectation, but
            # forced to ESCALATE if the amount exceeds the auto-resolve cap —
            # this mirrors exactly what the resolve_exception tool itself
            # enforces, so we're scoring the agent against the SAME policy
            # it was actually required to follow.
            if amount is not None and amount > AUTO_RESOLVE_MAX_AMOUNT:
                policy_expected_action = "ESCALATE"
            else:
                policy_expected_action = intrinsic_expected_action

            # Normalize AUTO_RESOLVED -> AUTO_RESOLVE for comparison
            normalized_action = "AUTO_RESOLVE" if actual_action == "AUTO_RESOLVED" else "ESCALATE"

            type_match = (actual_type == expected_type)
            decision_match = (normalized_action == policy_expected_action)
            intrinsic_match = (normalized_action == intrinsic_expected_action)

            type_total[expected_type] += 1
            if type_match:
                type_correct[expected_type] += 1
                classification_correct += 1
            if decision_match:
                decision_correct += 1
            if intrinsic_match:
                intrinsic_decision_correct += 1

            case_results.append({
                "txn_id": txn_id,
                "scored": True,
                "expected_type": expected_type,
                "actual_type": actual_type,
                "type_correct": type_match,
                "intrinsic_expected_action": intrinsic_expected_action,
                "policy_expected_action": policy_expected_action,
                "amount": amount,
                "policy_override_applied": policy_expected_action != intrinsic_expected_action,
                "actual_action": normalized_action,
                "decision_correct": decision_match,
                "agent_confidence": inv["confidence"],
            })

            if verbose:
                type_mark = "✓" if type_match else "✗"
                decision_mark = "✓" if decision_match else "✗"
                override_note = " [policy override: amount > cap]" if policy_expected_action != intrinsic_expected_action else ""
                print(f"  {txn_id}: type {type_mark} ({actual_type} vs {expected_type}) | "
                      f"decision {decision_mark} ({normalized_action} vs {policy_expected_action}){override_note}")

        classification_accuracy = (classification_correct / evaluated_count
                                   if evaluated_count else 0.0)
        decision_accuracy = (decision_correct / evaluated_count
                             if evaluated_count else 0.0)
        intrinsic_decision_accuracy = (intrinsic_decision_correct / evaluated_count
                                       if evaluated_count else 0.0)

        per_type_breakdown = {
            t: {"correct": type_correct[t], "total": type_total[t],
                "accuracy": round(type_correct[t] / type_total[t], 2) if type_total[t] else 0.0}
            for t in type_total
        }

        passed = (classification_accuracy >= EVAL_PASS_THRESHOLD and
                  decision_accuracy >= EVAL_PASS_THRESHOLD)

        if verbose:
            print(f"\n  [Evaluator] Classification accuracy:        {classification_accuracy:.1%}")
            print(f"  [Evaluator] Decision accuracy (policy-aware): {decision_accuracy:.1%}")
            print(f"  [Evaluator] Decision accuracy (intrinsic):    {intrinsic_decision_accuracy:.1%} "
                  f"[diagnostic only — ignores amount policy]")
            print(f"  [Evaluator] {'✓ PASSED' if passed else '✗ BELOW THRESHOLD'} "
                  f"(threshold: {EVAL_PASS_THRESHOLD:.0%})")

        return {
            "classification_accuracy": round(classification_accuracy, 4),
            "decision_accuracy": round(decision_accuracy, 4),
            "intrinsic_decision_accuracy": round(intrinsic_decision_accuracy, 4),
            "cases_evaluated": evaluated_count,
            "cases_with_ground_truth": len(ground_truth),
            "per_type_breakdown": per_type_breakdown,
            "case_results": case_results,
            "passed": passed,
            "threshold": EVAL_PASS_THRESHOLD,
        }

    def _extract_txn_id(self, case: dict) -> str:
        """Get the canonical ledger-side txn_id for a case, for ground truth lookup."""
        if case["type"] == "fuzzy":
            return case["ledger"]["txn_id"]
        elif case["type"] == "unmatched_ledger":
            return case["txn"]["txn_id"]
        elif case["type"] == "unmatched_bank":
            return case["txn"]["txn_id"]
        return "UNKNOWN"

    def _extract_amount(self, case: dict) -> float:
        """Get the transaction amount for a case, used for policy-aware scoring."""
        if case["type"] == "fuzzy":
            return case["ledger"].get("amount")
        elif case["type"] in ("unmatched_ledger", "unmatched_bank"):
            return case["txn"].get("amount")
        return None

    def _find_ground_truth(self, txn_id: str, ground_truth: dict) -> dict:
        """
        Look up ground truth for a transaction ID, handling the DUPLICATE
        case where the ground truth key is the ORIGINAL id but the agent
        investigates the "-DUP" sibling entry.
        """
        if txn_id in ground_truth:
            return ground_truth[txn_id]
        # Handle "-DUP" suffix: ground truth is keyed by the original id
        if txn_id.endswith("-DUP"):
            original_id = txn_id.replace("-DUP", "")
            if original_id in ground_truth:
                return ground_truth[original_id]
        return None
