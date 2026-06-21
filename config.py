import os
from dotenv import load_dotenv

load_dotenv()

# ── API ──────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── Models ───────────────────────────────────────────────────────────────────
# Haiku  → fast, cheap → used for investigating most exceptions
# Sonnet → powerful   → reserved for the highest-value / most ambiguous cases
MODEL_FAST       = "claude-haiku-4-5"
MODEL_REASONING  = "claude-sonnet-4-6"

# ── Matching engine ────────────────────────────────────────────────────────
# Deterministic matching runs BEFORE the agent — cheap, fast, handles the
# majority of transactions. Only unmatched transactions reach the agent.
AMOUNT_TOLERANCE   = 0.02   # 2% tolerance for fuzzy amount matching
DATE_TOLERANCE_DAYS = 3     # transactions within N days are fuzzy-matchable

# ── Agent decision policy ─────────────────────────────────────────────────
# Auto-resolve only if BOTH conditions are met:
#   - confidence >= AUTO_RESOLVE_CONFIDENCE
#   - transaction amount <= AUTO_RESOLVE_MAX_AMOUNT
# Otherwise the case is escalated for human approval.
AUTO_RESOLVE_CONFIDENCE  = 0.85
AUTO_RESOLVE_MAX_AMOUNT  = 5000.00

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "data")
REPORTS_DIR  = os.path.join(BASE_DIR, "reports")

LEDGER_FILE  = os.path.join(DATA_DIR, "internal_ledger.csv")
BANK_FILE    = os.path.join(DATA_DIR, "bank_statement.csv")

# ── Evaluation ────────────────────────────────────────────────────────────────
EVAL_PASS_THRESHOLD = 0.75   # minimum accuracy to consider the agent's
                              # root-cause classification trustworthy
