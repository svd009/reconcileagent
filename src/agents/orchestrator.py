"""
orchestrator.py
─────────────────
Coordinates the full reconciliation run: deterministic matching first,
then dispatches every case that needs investigation to the agent —
using the cheaper model for cases with a clear matcher hint, and the
more expensive reasoning model for genuinely ambiguous unmatched cases.

This is the same tiered-routing principle as FinGuard's orchestrator,
applied to a different signal: instead of routing by query complexity
keywords, we route by how much deterministic evidence already exists.

  Fuzzy-matched cases (TIMING, ROUNDING, REFID hints already known)
    → Haiku — the agent is largely confirming a strong existing signal

  Unmatched cases (DUPLICATE, MISSING, FX — no easy signal)
    → Sonnet + extended thinking — the agent has to reason from scratch
      with genuinely ambiguous evidence
"""

import uuid
from src.matching.matcher import match_transactions, summarize
from src.mcp_server.audit_trail import AuditTrail
from src.mcp_server.reconciliation_tools import ReconciliationToolExecutor
from src.agents.investigation_agent import InvestigationAgent


class ReconciliationOrchestrator:
    """
    Runs a complete reconciliation cycle: match → investigate → report.
    """

    def __init__(self, ledger: list[dict], bank: list[dict], audit_db_path: str):
        self.ledger = ledger
        self.bank = bank
        self.audit = AuditTrail(audit_db_path)
        self.run_id = str(uuid.uuid4())[:8]

    def run(self, verbose: bool = True, max_cases: int = None) -> dict:
        """
        Run the full reconciliation cycle.

        Args:
            verbose:   Print progress
            max_cases: Optional cap on how many cases the agent investigates
                       (useful for limiting API spend during testing/demo)

        Returns:
            {
              "run_id": str,
              "match_summary": dict,
              "investigations": list of investigation results,
              "auto_resolved": list,
              "escalated": list,
            }
        """
        if verbose:
            print(f"\n{'='*60}")
            print(f"  [Orchestrator] Reconciliation run: {self.run_id}")
            print(f"{'='*60}")

        # ── Step 1: Deterministic matching ────────────────────────
        if verbose:
            print(f"\n  [Orchestrator] Step 1/2: Running deterministic matcher...")

        match_results = match_transactions(self.ledger, self.bank)
        match_summary = summarize(match_results)

        if verbose:
            print(f"  [Orchestrator] Exact matched:   {match_summary['exact_matched']}")
            print(f"  [Orchestrator] Fuzzy matched:   {match_summary['fuzzy_matched']}")
            print(f"  [Orchestrator] Unmatched ledger: {match_summary['unmatched_ledger']}")
            print(f"  [Orchestrator] Unmatched bank:   {match_summary['unmatched_bank']}")
            print(f"  [Orchestrator] → {match_summary['total_needs_agent']} cases need investigation")

        # ── Step 2: Build investigation cases ──────────────────────
        cases = self._build_cases(match_results)

        if max_cases is not None:
            cases = cases[:max_cases]
            if verbose:
                print(f"  [Orchestrator] Capped to {len(cases)} cases for this run")

        # ── Step 3: Investigate each case ──────────────────────────
        if verbose:
            print(f"\n  [Orchestrator] Step 2/2: Investigating {len(cases)} cases...")

        executor = ReconciliationToolExecutor(
            self.ledger, self.bank, self.audit, run_id=self.run_id
        )
        agent = InvestigationAgent(executor)

        investigations = []
        for i, case in enumerate(cases, 1):
            if verbose:
                print(f"\n  --- Case {i}/{len(cases)} ---")

            # Tiered routing: fuzzy cases have a strong hint → Haiku
            # Unmatched cases are ambiguous → Sonnet + extended thinking
            use_extended_thinking = case["type"] in ("unmatched_ledger", "unmatched_bank")

            result = agent.investigate(
                case, use_extended_thinking=use_extended_thinking, verbose=verbose
            )
            investigations.append(result)

        auto_resolved = [r for r in investigations if r["action_taken"] == "AUTO_RESOLVED"]
        escalated = [r for r in investigations if r["action_taken"] == "ESCALATED"]

        if verbose:
            print(f"\n  [Orchestrator] Investigation complete:")
            print(f"  [Orchestrator] Auto-resolved: {len(auto_resolved)}")
            print(f"  [Orchestrator] Escalated:     {len(escalated)}")

        return {
            "run_id": self.run_id,
            "match_summary": match_summary,
            "investigations": investigations,
            "auto_resolved": auto_resolved,
            "escalated": escalated,
        }

    def _build_cases(self, match_results: dict) -> list[dict]:
        """Convert matcher output into a flat list of investigation cases."""
        cases = []

        for fm in match_results["fuzzy_matched"]:
            cases.append({"type": "fuzzy", "ledger": fm["ledger"],
                          "bank": fm["bank"], "reason": fm["reason"]})

        for txn in match_results["unmatched_ledger"]:
            cases.append({"type": "unmatched_ledger", "txn": txn})

        for txn in match_results["unmatched_bank"]:
            cases.append({"type": "unmatched_bank", "txn": txn})

        return cases
