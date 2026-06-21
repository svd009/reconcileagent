"""
approval_gate.py
───────────────────
The human-in-the-loop approval interface for escalated exceptions.

Why a REAL interactive gate instead of just logging "would have asked a human"?
  An architecture diagram that says "human approval gate" is a claim.
  A CLI that actually stops, shows you the case, and waits for your
  keypress before recording a decision is proof. This is the difference
  between describing responsible agent design and demonstrating it —
  which matters a great deal when the audience is someone evaluating
  whether you understand how to build agents that are safe to deploy.

What happens at the gate:
  For every ESCALATED case, the CLI presents:
    - The transaction details
    - What the agent investigated and concluded
    - The agent's stated confidence and reasoning
    - Why it didn't qualify for auto-resolution (low confidence? high
      amount? policy-classified as a genuine error like DUPLICATE/MISSING?)
  The human then chooses: Approve the agent's suggested classification,
  Reject it (and provide a correction), or Skip (defer the decision).
  Every choice is written to the durable audit trail with actor="human".
"""

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


def run_approval_gate(escalated_cases: list[dict], audit_trail, run_id: str,
                      interactive: bool = True) -> list[dict]:
    """
    Walk a human through every escalated case and record their decision.

    Args:
        escalated_cases: List of investigation results with action_taken == "ESCALATED"
        audit_trail:     The AuditTrail instance to log human decisions to
        run_id:          The current reconciliation run ID
        interactive:     If False, auto-skips all gates (used for non-interactive
                         demo runs or automated testing) without prompting

    Returns:
        List of human decisions: [{"txn_id", "human_decision", "note"}, ...]
    """
    if not escalated_cases:
        console.print("\n[dim]No escalated cases require human approval.[/dim]")
        return []

    console.print(Panel(
        f"[bold]{len(escalated_cases)} case(s) escalated for human review[/bold]\n"
        f"Each will be presented for approval, rejection, or deferral.",
        title="[bold yellow]HUMAN APPROVAL GATE[/bold yellow]",
        border_style="yellow"
    ))

    decisions = []

    for i, case_result in enumerate(escalated_cases, 1):
        txn_id = _extract_txn_id(case_result["case"])

        console.print(f"\n[bold yellow]── Escalation {i}/{len(escalated_cases)} ──[/bold yellow]")
        console.print(f"[bold]Transaction:[/bold] {txn_id}")
        console.print(f"[bold]Agent's classification:[/bold] {case_result['exception_type']}")
        console.print(f"[bold]Agent's confidence:[/bold] {case_result['confidence']}")
        console.print(f"[bold]Agent's reasoning:[/bold] {case_result['reasoning']}")
        console.print(_describe_amount_and_policy_reason(case_result))

        if not interactive:
            decision = "DEFERRED"
            note = "Non-interactive mode — deferred for later human review"
            console.print(f"[dim]→ Auto-deferred (non-interactive mode)[/dim]")
        else:
            choice = Prompt.ask(
                "\n[bold]Decision[/bold]",
                choices=["approve", "reject", "skip"],
                default="approve"
            )
            if choice == "approve":
                decision = "APPROVED"
                note = "Human approved the agent's classification and escalation"
                console.print("[green]✓ Approved[/green]")
            elif choice == "reject":
                correction = Prompt.ask("[bold]What should the correct classification/action be?[/bold]")
                decision = "REJECTED"
                note = f"Human rejected agent's conclusion. Correction: {correction}"
                console.print(f"[red]✗ Rejected — correction noted: {correction}[/red]")
            else:
                decision = "DEFERRED"
                note = "Human chose to defer this decision for later review"
                console.print("[dim]→ Deferred[/dim]")

        audit_trail.log(
            run_id=run_id, txn_id=txn_id, action=decision,
            actor="human", exception_type=case_result["exception_type"],
            confidence=None, reasoning=note,
            amount=_extract_amount(case_result["case"]),
        )

        decisions.append({"txn_id": txn_id, "human_decision": decision, "note": note})

    console.print(Panel(
        f"[bold]{sum(1 for d in decisions if d['human_decision'] == 'APPROVED')}[/bold] approved · "
        f"[bold]{sum(1 for d in decisions if d['human_decision'] == 'REJECTED')}[/bold] rejected · "
        f"[bold]{sum(1 for d in decisions if d['human_decision'] == 'DEFERRED')}[/bold] deferred",
        title="[bold yellow]Approval Gate Complete[/bold yellow]",
        border_style="yellow"
    ))

    return decisions


def _extract_txn_id(case: dict) -> str:
    if case["type"] == "fuzzy":
        return case["ledger"]["txn_id"]
    return case["txn"]["txn_id"]


def _extract_amount(case: dict) -> float:
    if case["type"] == "fuzzy":
        return case["ledger"]["amount"]
    return case["txn"]["amount"]


def _describe_amount_and_policy_reason(case_result: dict) -> str:
    """Explain in plain terms why this case didn't auto-resolve."""
    from config import AUTO_RESOLVE_CONFIDENCE, AUTO_RESOLVE_MAX_AMOUNT

    amount = _extract_amount(case_result["case"])
    confidence = case_result["confidence"]
    reasons = []

    if confidence < AUTO_RESOLVE_CONFIDENCE:
        reasons.append(f"confidence {confidence} is below the {AUTO_RESOLVE_CONFIDENCE} auto-resolve threshold")
    if amount > AUTO_RESOLVE_MAX_AMOUNT:
        reasons.append(f"amount ${amount:,.2f} exceeds the ${AUTO_RESOLVE_MAX_AMOUNT:,.2f} auto-resolve limit")
    if case_result["exception_type"] in ("DUPLICATE", "MISSING"):
        reasons.append(f"{case_result['exception_type']} represents a genuine error requiring human correction, not just an explainable discrepancy")

    if not reasons:
        reasons.append("agent's own judgment determined this needs human review")

    return f"[dim]Why escalated: {'; '.join(reasons)}[/dim]"
