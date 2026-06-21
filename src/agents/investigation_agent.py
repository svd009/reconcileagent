"""
investigation_agent.py
────────────────────────
The autonomous investigation agent — the Claude-Code-style agentic loop
at the center of ReconcileAgent.

What makes this "agentic" rather than "a classification call"?
  A simple classifier would take a transaction, output a label, and stop.
  This agent instead:
    1. Receives an unmatched/fuzzy-matched transaction
    2. Decides what to investigate (search for a counterpart? check history?)
    3. Calls tools to gather evidence
    4. Forms a hypothesis about the root cause
    5. TAKES AN ACTION (resolve or escalate) — not just a suggestion
    6. The action is verified by the tool itself (policy enforcement)
  This act-verify loop, where the model uses tools to investigate before
  committing to an action, is the same fundamental pattern Claude Code
  uses when navigating a codebase: read, reason, act, verify.

Two-tier model strategy (carried over from FinGuard's cost lesson):
  - Fuzzy-matched cases (TIMING, ROUNDING, REFID) have a clear deterministic
    signal already attached by the matcher — these go to Haiku, since the
    investigation is mostly confirming what the matcher already found.
  - Fully unmatched cases (DUPLICATE, MISSING, FX) have no easy signal —
    these go to Sonnet with extended thinking, since the agent has to
    reason through ambiguous evidence from scratch.

Why does the agent see the matcher's "reason" field for fuzzy matches?
  It's a hint, not an answer. The agent is still required to call tools
  and form its own confidence-scored conclusion — we don't want it to
  blindly trust the deterministic layer's guess, since fuzzy matches can
  occasionally be coincidental (two unrelated transactions happening to
  land within tolerance).
"""

import json
import anthropic
from config import ANTHROPIC_API_KEY, MODEL_FAST, MODEL_REASONING
from src.mcp_server.reconciliation_tools import ReconciliationToolExecutor, TOOL_SCHEMAS


INVESTIGATION_SYSTEM_PROMPT = """You are a reconciliation analyst agent investigating transaction exceptions for a financial institution.

Your job is to determine WHY a transaction didn't match cleanly between the internal ledger and bank statement, then take the appropriate action.

<investigation_process>
1. Review the transaction details and any hint provided by the deterministic matcher
2. If useful, call search_counterpart to look for a plausible match using looser criteria
3. Call get_transaction_history to check if this transaction has been flagged before
4. Form a hypothesis about the root cause — one of: TIMING, ROUNDING, DUPLICATE, MISSING, REFID, FX, or OTHER
5. Assign a confidence score (0.0-1.0) based on how certain you are
6. Take ONE final action:
   - resolve_exception — if you are confident this is an explainable, low-risk discrepancy
   - escalate_exception — if the root cause is unclear, OR it represents a genuine error
     (duplicate entries and missing transactions are errors, not explainable discrepancies —
     they should generally be escalated even if you're confident about WHAT happened, because
     a human needs to decide how to correct the underlying error)
</investigation_process>

<important_policy_notes>
- The resolve_exception tool will AUTOMATICALLY REJECT your resolution if confidence is too
  low or the amount is too high, regardless of your reasoning. If this happens, call
  escalate_exception instead — do not retry resolve_exception with inflated confidence.
- Genuine errors (DUPLICATE, MISSING) should be escalated even with high confidence about
  the root cause, because resolving them automatically would hide a process failure that
  needs human correction, not just an accounting explanation.
- TIMING, ROUNDING, and REFID are typically safe to auto-resolve when confidence is high —
  they represent explainable discrepancies, not errors.
- FX discrepancies should usually be escalated unless the drift is clearly within normal
  rounding/fee territory — exchange rate timing issues often need a finance team sign-off.
</important_policy_notes>

<output_requirement>
You MUST end your investigation by calling either resolve_exception or escalate_exception.
Do not just describe your conclusion in text — take the action via the tool.
</output_requirement>"""


class InvestigationAgent:
    """
    Autonomous agent that investigates a single transaction exception
    and takes a real action via MCP tools.
    """

    def __init__(self, tool_executor: ReconciliationToolExecutor):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.executor = tool_executor
        self.tools = TOOL_SCHEMAS

    def investigate(self, case: dict, use_extended_thinking: bool = False,
                    verbose: bool = True) -> dict:
        """
        Investigate one exception case and take a final action.

        Args:
            case: A dict describing the case to investigate. Shape varies:
                  Fuzzy match: {"type": "fuzzy", "ledger": {...}, "bank": {...}, "reason": str}
                  Unmatched ledger: {"type": "unmatched_ledger", "txn": {...}}
                  Unmatched bank: {"type": "unmatched_bank", "txn": {...}}
            use_extended_thinking: Whether to use Sonnet + extended thinking
                                   (for genuinely ambiguous unmatched cases)
            verbose: Print investigation steps

        Returns:
            {
              "case": dict,           ← the original case
              "action_taken": str,    ← "AUTO_RESOLVED" or "ESCALATED"
              "exception_type": str,
              "confidence": float,
              "reasoning": str,
              "tool_calls": list,
              "thinking": str,        ← extended thinking trace, if used
            }
        """
        model = MODEL_REASONING if use_extended_thinking else MODEL_FAST
        case_description = self._describe_case(case)

        if verbose:
            print(f"\n  [InvestigationAgent] Investigating: {case_description[:80]}...")
            print(f"  [InvestigationAgent] Model: {model}"
                  f"{' (extended thinking)' if use_extended_thinking else ''}")

        user_message = f"""<case_to_investigate>
{case_description}
</case_to_investigate>

<task>
Investigate this exception and determine the root cause. Use tools as needed,
then call resolve_exception or escalate_exception to record your final decision.
</task>"""

        messages = [{"role": "user", "content": user_message}]
        tool_calls_log = []
        thinking_text = ""
        final_action = None

        max_turns = 6  # safety bound on the agentic loop

        for turn in range(max_turns):
            api_params = dict(
                model=model,
                max_tokens=2000,
                system=INVESTIGATION_SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
            )
            if use_extended_thinking:
                api_params["thinking"] = {"type": "enabled", "budget_tokens": 3000}
                api_params["max_tokens"] = 4000

            response = self.client.messages.create(**api_params)

            for block in response.content:
                if block.type == "thinking":
                    thinking_text += block.thinking

            if response.stop_reason == "tool_use":
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in tool_use_blocks:
                    if verbose:
                        print(f"  [InvestigationAgent] → {block.name}"
                              f"({json.dumps(block.input)[:70]}...)")
                    result_json = self.executor.execute(block.name, block.input)
                    result = json.loads(result_json)

                    if verbose and result.get("status") == "REJECTED":
                        print(f"  [InvestigationAgent]   ⚠ REJECTED by policy: {result['reason']}")

                    tool_calls_log.append({
                        "tool": block.name, "input": block.input, "result": result
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_json,
                    })

                    # Capture the final action if this was a resolve/escalate call
                    if block.name in ("resolve_exception", "escalate_exception") and \
                       result.get("status") in ("RESOLVED", "ESCALATED"):
                        final_action = {
                            "action_taken": result["status"] if result["status"] != "RESOLVED" else "AUTO_RESOLVED",
                            "exception_type": block.input.get("exception_type"),
                            "confidence": block.input.get("confidence"),
                            "reasoning": block.input.get("reasoning"),
                        }

                messages.append({"role": "user", "content": tool_results})

                # If a final action was taken, we can stop the loop
                if final_action:
                    break
            else:
                # Model stopped without calling a tool — shouldn't normally
                # happen given our system prompt, but guard against it
                break

        if not final_action:
            # Fallback: agent didn't take a decisive action within max_turns
            final_action = {
                "action_taken": "ESCALATED",
                "exception_type": "UNKNOWN",
                "confidence": 0.0,
                "reasoning": "Agent did not reach a final decision within the turn limit — "
                             "escalated automatically as a safety fallback.",
            }
            if verbose:
                print(f"  [InvestigationAgent] ⚠ No decisive action — auto-escalated as fallback")

        if verbose:
            print(f"  [InvestigationAgent] ✓ {final_action['action_taken']} "
                  f"({final_action['exception_type']}, confidence={final_action['confidence']})")

        return {
            "case": case,
            **final_action,
            "tool_calls": tool_calls_log,
            "thinking": thinking_text,
        }

    def _describe_case(self, case: dict) -> str:
        """Build a clear text description of the case for the agent's first message."""
        case_type = case["type"]

        if case_type == "fuzzy":
            l, b = case["ledger"], case["bank"]
            return (
                f"Type: Fuzzy match (matcher's hint: {case['reason']})\n"
                f"Ledger entry: txn_id={l['txn_id']}, date={l['date'].strftime('%Y-%m-%d')}, "
                f"amount=${l['amount']}, description={l['description']}\n"
                f"Bank entry:   txn_id={b['txn_id']}, date={b['date'].strftime('%Y-%m-%d')}, "
                f"amount=${b['amount']}, description={b['description']}\n"
                f"These two entries look similar but didn't exact-match. Determine why."
            )
        elif case_type == "unmatched_ledger":
            t = case["txn"]
            return (
                f"Type: Unmatched ledger entry (no automatic counterpart found in bank statement)\n"
                f"txn_id={t['txn_id']}, date={t['date'].strftime('%Y-%m-%d')}, "
                f"amount=${t['amount']}, description={t['description']}, category={t['category']}\n"
                f"This transaction exists in the internal ledger but no matching bank "
                f"transaction was found automatically. Investigate why."
            )
        elif case_type == "unmatched_bank":
            t = case["txn"]
            return (
                f"Type: Unmatched bank entry (no automatic counterpart found in ledger)\n"
                f"txn_id={t['txn_id']}, date={t['date'].strftime('%Y-%m-%d')}, "
                f"amount=${t['amount']}, description={t['description']}, category={t['category']}\n"
                f"This transaction exists in the bank statement but no matching ledger "
                f"entry was found automatically. Investigate why."
            )
        return "Unknown case type"
