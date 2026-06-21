"""
main.py
─────────
ReconcileAgent — Autonomous Transaction Reconciliation & Exception
Resolution System. Single entry point for the entire system.

Usage:
  python main.py                       # Full reconciliation run (default)
  python main.py --mode reconcile      # Full reconciliation run
  python main.py --mode eval           # Run evaluation suite only (no API cost)
  python main.py --no-interactive      # Run without pausing for human approval
  python main.py --max-cases 6         # Cap agent investigation to N cases (cost control)
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from generate_data import generate_dataset, write_csv, write_ground_truth
from src.matching.loader import load_transactions, load_ground_truth
from src.agents.orchestrator import ReconciliationOrchestrator
from src.agents.approval_gate import run_approval_gate
from src.agents.report import build_report, save_report
from src.evaluation.eval_framework import ReconciliationEvaluator
from config import LEDGER_FILE, BANK_FILE, DATA_DIR, REPORTS_DIR, AUDIT_DB

console = Console()

BANNER = """
██████╗ ███████╗ ██████╗ ██████╗ ███╗   ██╗ ██████╗██╗██╗     ███████╗ █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██╔══██╗██╔════╝██╔════╝██╔═══██╗████╗  ██║██╔════╝██║██║     ██╔════╝██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
██████╔╝█████╗  ██║     ██║   ██║██╔██╗ ██║██║     ██║██║     █████╗  ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   
██╔══██╗██╔══╝  ██║     ██║   ██║██║╚██╗██║██║     ██║██║     ██╔══╝  ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   
██║  ██║███████╗╚██████╗╚██████╔╝██║ ╚████║╚██████╗██║███████╗███████╗██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   
╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝╚═╝╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""


def initialize_data():
    """Generate the synthetic dataset and load it into memory."""
    console.print("\n[bold cyan]Initializing ReconcileAgent...[/bold cyan]")
    console.print("  [dim]→ Generating synthetic transaction datasets...[/dim]")

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    ledger_rows, bank_rows, ground_truth = generate_dataset(num_clean=70, num_each_exception=5)
    write_csv(ledger_rows, LEDGER_FILE)
    write_csv(bank_rows, BANK_FILE)
    write_ground_truth(ground_truth, os.path.join(DATA_DIR, "ground_truth.json"))

    ledger = load_transactions(LEDGER_FILE)
    bank = load_transactions(BANK_FILE)
    gt = load_ground_truth(os.path.join(DATA_DIR, "ground_truth.json"))

    console.print(f"  [bold green]✓[/bold green] {len(ledger)} ledger transactions, "
                  f"{len(bank)} bank transactions loaded\n")
    return ledger, bank, gt


def print_before_state(ledger: list, bank: list):
    """Show the 'before' picture — what reconciliation looks like with no system."""
    console.print(Panel(
        f"[bold]{len(ledger)}[/bold] internal ledger transactions\n"
        f"[bold]{len(bank)}[/bold] bank statement transactions\n"
        f"[dim]No automated visibility into which transactions match, "
        f"why they don't, or what should be done about it.[/dim]",
        title="[bold]BEFORE — Raw, Unreconciled State[/bold]",
        border_style="white"
    ))


def print_after_state(report: dict):
    """Show the 'after' picture using the structured report."""
    after = report["after"]
    eff = report["efficiency"]

    console.print(Panel(
        f"[bold]{after['exact_matched_deterministic']}[/bold] exact matched (zero AI cost)\n"
        f"[bold]{after['fuzzy_matched_deterministic']}[/bold] fuzzy matched, agent-confirmed\n"
        f"[bold]{after['agent_auto_resolved']}[/bold] auto-resolved by agent (policy-gated)\n"
        f"[bold]{after['agent_escalated']}[/bold] escalated for human review\n"
        f"  [dim]→ {after['human_approved']} approved · "
        f"{after['human_rejected']} rejected · {after['human_deferred']} deferred[/dim]\n\n"
        f"[bold green]{eff['pct_resolved_without_human']}%[/bold green] resolved without "
        f"requiring any human judgment\n"
        f"[bold]{eff['cases_requiring_human_judgment']}[/bold] cases required real human review",
        title="[bold]AFTER — Reconciled, Explained, Auditable[/bold]",
        border_style="green"
    ))


def print_exception_table(report: dict):
    """Render the exception type breakdown as a table."""
    breakdown = report["exception_breakdown"]
    if not breakdown:
        return

    table = Table(title="Exception Type Breakdown", box=box.ROUNDED)
    table.add_column("Exception Type", style="bold")
    table.add_column("Auto-Resolved", justify="center")
    table.add_column("Escalated", justify="center")

    for exc_type, counts in sorted(breakdown.items()):
        table.add_row(
            exc_type,
            f"[green]{counts['AUTO_RESOLVED']}[/green]" if counts["AUTO_RESOLVED"] else "0",
            f"[yellow]{counts['ESCALATED']}[/yellow]" if counts["ESCALATED"] else "0",
        )
    console.print(table)


def run_reconcile_mode(args):
    """Full reconciliation: match → investigate → approve → report → evaluate."""
    ledger, bank, ground_truth = initialize_data()

    print_before_state(ledger, bank)

    orchestrator = ReconciliationOrchestrator(ledger, bank, AUDIT_DB)
    result = orchestrator.run(verbose=True, max_cases=args.max_cases)

    # Human approval gate for escalated cases
    human_decisions = run_approval_gate(
        result["escalated"], orchestrator.audit, orchestrator.run_id,
        interactive=not args.no_interactive
    )

    # Evaluate against ground truth (only covers cases that were actually investigated)
    evaluator = ReconciliationEvaluator()
    eval_result = evaluator.evaluate(result["investigations"], ground_truth, verbose=False)

    # Build and save the before/after report
    report = build_report(
        result["match_summary"], result["investigations"], human_decisions,
        orchestrator.run_id, eval_result=eval_result
    )
    report_path = save_report(report, REPORTS_DIR)

    console.print("\n")
    print_after_state(report)
    console.print("")
    print_exception_table(report)

    console.print(f"\n[bold]Evaluation:[/bold] "
                  f"Classification accuracy [bold]{eval_result['classification_accuracy']:.1%}[/bold] · "
                  f"Decision accuracy [bold]{eval_result['decision_accuracy']:.1%}[/bold] "
                  f"({'[green]PASSED[/green]' if eval_result['passed'] else '[red]BELOW THRESHOLD[/red]'})")

    console.print(f"\n[bold]Report saved:[/bold] [dim]{report_path}[/dim]")
    console.print(f"[bold]Audit trail:[/bold] [dim]{AUDIT_DB}[/dim]")

    # Durable audit trail proof — show accumulated history across ALL runs ever
    stats = orchestrator.audit.get_summary_stats()
    console.print(Panel(
        f"[bold]{stats['total_runs']}[/bold] reconciliation run(s) on record\n"
        f"[bold]{stats['total_entries']}[/bold] total audit entries accumulated\n"
        f"Action breakdown: {stats['action_breakdown']}",
        title="[bold]Durable Audit Trail — All-Time History[/bold]",
        border_style="blue"
    ))


def run_eval_mode():
    """Run only the evaluation framework's self-test (mock data, zero API cost)."""
    console.print(Panel(
        "Running evaluation framework self-test with mock data.\n"
        "Verifies scoring logic — no API credits required.",
        title="[bold cyan]EVAL MODE[/bold cyan]",
        border_style="cyan"
    ))
    os.system(f'"{sys.executable}" "{os.path.join(os.path.dirname(__file__), "test_eval.py")}"')


def main():
    parser = argparse.ArgumentParser(
        description="ReconcileAgent — Autonomous Transaction Reconciliation System"
    )
    parser.add_argument("--mode", choices=["reconcile", "eval"], default="reconcile")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Run without pausing for human approval (auto-defers all escalations)")
    parser.add_argument("--max-cases", type=int, default=None,
                        help="Cap the number of cases the agent investigates (cost control)")
    args = parser.parse_args()

    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print(Panel(
        "[bold]Autonomous Transaction Reconciliation & Exception Resolution[/bold]\n"
        "Deterministic Matching · MCP Tools · Policy-Gated Agent · Durable Audit Trail",
        border_style="cyan"
    ))

    if args.mode == "eval":
        run_eval_mode()
    else:
        run_reconcile_mode(args)


if __name__ == "__main__":
    main()
