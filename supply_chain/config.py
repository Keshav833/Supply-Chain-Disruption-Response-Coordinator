"""
Application configuration loaded from environment variables (.env supported).

API keys are read ONLY from the environment; they are never hardcoded.
Placeholder values such as "your-openai-key-here" are treated as missing.

Execution-mode labelling:
    The default label is ExecutionMode.DETERMINISTIC_FALLBACK. A run may only be
    labelled SEMANTIC_KERNEL_LLM when a live Semantic Kernel LLM call actually
    succeeds (wired in PHASE_7). Having a key configured is NOT proof of an LLM call.
"""
import os
import logging
from pathlib import Path

from dotenv import load_dotenv

from supply_chain.schemas import ExecutionMode

try:
    load_dotenv()
except (OSError, PermissionError):
    pass  # .env file not readable in some environments

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = Path(__file__).parent / "data"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
INCIDENT_LOG_PATH: Path = BASE_DIR / os.getenv("INCIDENT_LOG_PATH", "logs/incident_runs.jsonl")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()


def _positive_float(name: str, default: float, *, allow_zero: bool = False) -> float:
    """Read a bounded numeric setting without making application import fragile."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    if value < 0 or (value == 0 and not allow_zero):
        return default
    return value


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= 1 else default


AGENT_TIMEOUT_SECONDS: float = _positive_float("AGENT_TIMEOUT_SECONDS", 60.0)
# Tool retries are deliberately short and bounded.  Only transient infrastructure
# errors are retried; validation and business-data errors fail immediately.
TOOL_RETRY_MAX_ATTEMPTS: int = _positive_int("TOOL_RETRY_MAX_ATTEMPTS", 3)
TOOL_RETRY_DELAY_SECONDS: float = _positive_float("TOOL_RETRY_DELAY_SECONDS", 0.05, allow_zero=True)

# ---------------------------------------------------------------------------
# LLM credentials (environment only)
# ---------------------------------------------------------------------------
_PLACEHOLDER_MARKERS = ("your-", "-here", "changeme", "<", "xxx")


def _read_secret(*names: str) -> str:
    """Return the first non-placeholder value among the given env vars, else ''."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value and not any(m in value.lower() for m in _PLACEHOLDER_MARKERS):
            return value
    return ""


GEMINI_API_KEY: str = _read_secret("GEMINI_API_KEY", "GOOGLE_AI_API_KEY")
OPENAI_API_KEY: str = _read_secret("OPENAI_API_KEY")
GEMINI_MODEL_ID: str = os.getenv("GEMINI_MODEL", os.getenv("GEMINI_MODEL_ID", "gemini-3.8-flash"))
OPENAI_MODEL_ID: str = os.getenv("OPENAI_API_MODEL", os.getenv("OPENAI_MODEL_ID", os.getenv("LLM_MODEL", "gpt-4o-mini")))


def resolve_llm_provider() -> str:
    """Return 'gemini', 'openai' or 'none' based on LLM_PROVIDER and available keys."""
    requested = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if requested == "none":
        return "none"
    if requested == "gemini":
        return "gemini" if GEMINI_API_KEY else "none"
    if requested == "openai":
        return "openai" if OPENAI_API_KEY else "none"
    if GEMINI_API_KEY:
        return "gemini"
    if OPENAI_API_KEY:
        return "openai"
    return "none"


LLM_PROVIDER: str = resolve_llm_provider()
LLM_CREDENTIALS_PRESENT: bool = LLM_PROVIDER != "none"
LLM_MODEL: str = GEMINI_MODEL_ID if LLM_PROVIDER == "gemini" else OPENAI_MODEL_ID

# Default execution label for every run until a live SK call is verified (PHASE_7).
DEFAULT_EXECUTION_MODE: ExecutionMode = ExecutionMode.DETERMINISTIC_FALLBACK

# Legacy names still imported by the pre-existing orchestrator/agents prototype.
# LLM_AVAILABLE gates kernel construction; LLM_MODE is the run label and stays
# "deterministic_fallback" so no run is mislabelled as an LLM execution.
LLM_AVAILABLE: bool = LLM_CREDENTIALS_PRESENT
LLM_MODE: str = DEFAULT_EXECUTION_MODE.value


def configure_logging() -> None:
    """Configure concise process logging once, without overriding host handlers."""
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level,
                            format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    logging.getLogger("supply_chain").setLevel(level)

# ---------------------------------------------------------------------------
# Business rules (single source of truth, used by tools in PHASE_4)
#   usable_stock            = max(0, available_stock - safety_stock)
#   replenishment_required  = max(0, required_quantity + safety_stock - available_stock)
#   REORDER  : supplier active, supports product, not the disrupted supplier,
#              capacity >= replenishment_required, lead_time <= deadline.
#              Not applicable to route_blockage (a reorder does not unblock delivery).
#   REROUTE  : route available, origin warehouse usable_stock >= required_quantity,
#              transit + current delay <= deadline.
#   HOLD     : neither option is supported by evidence.
#   Preference when both are feasible: REORDER for supplier_delay / warehouse_stockout,
#   REROUTE for route_blockage.
# ---------------------------------------------------------------------------
MAX_ACCEPTABLE_LEAD_DAYS: int = 10
# Legacy constants used by the pre-existing prototype tools.
SAFETY_STOCK_MULTIPLIER: float = 1.2
MIN_ROUTE_AVAILABILITY_SCORE: float = 0.5

