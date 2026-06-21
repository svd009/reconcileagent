# ReconcileAgent: Autonomous Transaction Reconciliation & Exception Resolution

> **Fintech / Banking Edition** | Built with Claude API — Deterministic Matching, MCP Tools with Policy-Enforced Auto-Resolve, Durable SQLite Audit Trail, Tiered Agentic Investigation (Haiku + Sonnet Extended Thinking)

**Status: Complete, tested end-to-end with a real 33-case reconciliation run, fully evaluated against seeded ground truth.**

---

## The Problem

Financial institutions reconcile transactions across systems constantly — internal ledgers vs. bank statements, broker records vs. custodian records. Most transactions match automatically. The expensive part is the exceptions: timing differences, rounding discrepancies, duplicate entries, missing transactions, reference ID mismatches, FX conversion gaps. An analyst manually investigates each one, determines the root cause, and either resolves it or escalates it. This is exactly the kind of work transaction reconciliation tooling in fintech is built to automate.

ReconcileAgent automates the investigation: ingest two transaction datasets, deterministically match what's matchable, and let an autonomous agent investigate the rest — taking real action (resolve or escalate) under a hard-coded approval policy, with every decision durably logged.

---

## Verified Results — Real Run, Not a Simulation

A full reconciliation cycle across 105 internal ledger transactions and 95 bank statement transactions, with 30 deliberately seeded, ground-truth-labeled exceptions across 6 real-world categories:

| Metric | Result |
|---|---|
| Total ledger transactions | 105 |
| Exact matched (zero AI cost) | 75 (71.4%) |
| Fuzzy matched deterministically | 17 (16.2%) |
| **Resolved without any AI involvement** | **87.6%** |
| Cases requiring agent investigation | 33 |
| Agent auto-resolved (policy-gated) | 3 |
| Agent escalated for human review | 30 |
| **Resolved without requiring human judgment** | **90.5%** |
| Classification accuracy (root-cause diagnosis) | **100%** |
| Decision accuracy (policy-aware resolve/escalate) | **100%** |

**On the eval methodology — a genuine finding, not a footnote:** the first evaluation run scored decision accuracy at 63.6%, which looked like a real problem. Tracing it down revealed the issue was in the *evaluator*, not the agent: the ground truth labeled exception types like TIMING and ROUNDING as intrinsically safe to auto-resolve, but didn't account for the system's own $5,000 auto-resolve amount cap — and nearly every transaction in the dataset exceeded that cap. The agent was correctly escalating high-value transactions exactly as instructed by policy; the eval was scoring it against a standard the system was never supposed to follow in the first place. After fixing the evaluator to score against the *actual* policy-aware expected action, decision accuracy is 100%, with the original intrinsic-only number (67.7%) preserved as a transparent diagnostic. The fix and the re-scored run are both in the commit history.

---

## Architecture

```
┌  ──────────────────────────────────────────────────────────   ┐
│                   ReconcileAgent                              │
│                                                               │
│  [1] Synthetic Data Layer                                     │
│       Two labeled transaction datasets, 6 exception types     │
│                    ↓                                          │
│  [2] Deterministic Matching Engine                            │
│       Exact match → fuzzy match (amount/date tolerance)       │
│       Resolves 87.6% of transactions, zero AI cost            │
│                    ↓                                          │
│  [3] MCP Server — Reconciliation Tools                        │
│       search_counterpart · get_transaction_history            │
│       resolve_exception · escalate_exception                  │
│       Policy enforced AT THE TOOL LEVEL, not just prompted    │
│                    ↓                                          │
│  [4] Autonomous Investigation Agent                           │
│       Tiered routing: Haiku for matcher-hinted cases,         │
│       Sonnet + extended thinking for fully ambiguous cases    │
│       Act → verify → escalate-on-rejection loop               │
│                    ↓                                          │
│  [5] Durable SQLite Audit Trail                               │
│       Every action AND every rejected attempt logged          │
│       Persists across runs — queryable history, not console   │
│       output that vanishes when the terminal closes           │
│                    ↓                                          │
│  [6] Human Approval Gate                                      │
│       Real interactive CLI — approve / reject / defer         │
│       Every human decision also written to the audit trail    │
│                    ↓                                          │ 
│  [7] Evaluation Framework                                     │
│       Classification accuracy + policy-aware decision accuracy│
│       Scored against seeded ground truth, zero extra API cost │
│                    ↓                                          │
│  [8] Before/After Report + CLI                                │
│       Structured JSON + Rich terminal output                  │
└  ──────────────────────────────────────────────────────────   ┘
```

---

## Key Design Decisions

**Why deterministic matching before the agent runs at all?**
Running an LLM call on every transaction pair would be slow and expensive at scale. Real reconciliation systems work the same way — cheap deterministic logic handles the bulk (87.6% here), and the agent's cost is reserved for genuinely ambiguous cases. Same tiered-cost principle as model selection, applied one level earlier.

**Why is policy enforcement at the tool level, not the prompt level?**
The system prompt tells the agent to escalate high-value or low-confidence cases — but instructions in a prompt are a request, not a guarantee. The `resolve_exception` tool itself rejects any call where confidence is below threshold or amount exceeds the cap, regardless of how the agent justifies it. This was verified directly in the real run: the agent attempted `resolve_exception` on a $29,787 TIMING case at 0.95 confidence, the tool rejected it on amount alone, and the agent correctly escalated instead — visible in the logged transcript, not just claimed in documentation.

**Why log rejected attempts to the audit trail, not just successful actions?**
A compliance reviewer needs to see every time the agent *tried* to take a risky action and was blocked, not just the actions that succeeded. This was a gap in the first version of the system — caught during testing, fixed before the full demo run.

**Why two separate accuracy metrics instead of one blended score?**
An agent can correctly diagnose a problem but make the wrong decision about it, or vice versa — these are different failure modes with different consequences. Classification accuracy and decision accuracy are reported separately so neither failure mode can hide behind the other.

**Why is the durable audit trail SQLite, not a JSON log?**
SQLite persists across runs and is queryable after the fact — "show me every escalation from this run" or "has this transaction been flagged before" are real queries, not just printed lines. This was demonstrated directly: a completed run's decisions were re-evaluated under a corrected scoring methodology entirely from the audit trail, with zero additional API calls.

---

## Project Structure

```
reconcileagent/
├── data/                          ← synthetic transaction CSVs + ground truth
├── src/
│   ├── matching/
│   │   ├── loader.py              ← CSV loading and normalization
│   │   └── matcher.py             ← two-pass deterministic matching engine
│   ├── mcp_server/
│   │   ├── audit_trail.py         ← durable SQLite audit trail
│   │   └── reconciliation_tools.py ← MCP tools with policy enforcement
│   ├── agents/
│   │   ├── investigation_agent.py ← autonomous investigate-and-act loop
│   │   ├── orchestrator.py        ← tiered routing + full run coordination
│   │   ├── approval_gate.py       ← interactive human approval CLI
│   │   └── report.py              ← before/after report generation
│   └── evaluation/
│       └── eval_framework.py      ← classification + policy-aware decision accuracy
├── reports/                       ← saved reconciliation reports + audit_trail.db
├── main.py                        ← CLI entry point
├── generate_data.py               ← synthetic dataset generator
├── rescore_run.py                 ← re-evaluate a past run from the audit trail
└── config.py                      ← policy thresholds, model selection
```

---

## Quick Start

```bash
git clone https://github.com/svd009/reconcileagent.git
cd reconcileagent
pip install -r requirements.txt
cp .env.example .env   # add your Anthropic API key

python main.py                    # full reconciliation run, interactive approval gate
python main.py --max-cases 10     # capped run for quick testing
python main.py --no-interactive   # run without pausing for approval (auto-defers)
python rescore_run.py <run_id>    # re-evaluate a past run from the audit trail
```

---

## Built With

- [Anthropic Claude API](https://docs.anthropic.com) — claude-haiku-4-5, claude-sonnet-4-6 with extended thinking
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Model Context Protocol
- SQLite — durable audit trail
- [Rich](https://github.com/Textualize/rich) — interactive terminal UI

---
