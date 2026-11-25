"""Select the interpretation layer for a run (early PHASE_7 wiring).

provider 'gemini' -> SK GoogleAIChatCompletion (google-genai), 'openai' -> SK OpenAIChatCompletion,
'none' -> DeterministicInterpreter. Building a connector makes NO network call; a run is labelled
semantic_kernel_llm only if a live call later returns text (see SemanticKernelInterpreter).
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from supply_chain import config
from supply_chain.llm.interpreter import DeterministicInterpreter, SemanticKernelInterpreter

logger = logging.getLogger(__name__)


def build_interpreter(provider: Optional[str] = None) -> Tuple[Any, str]:
    """Return (interpreter, provider_used). Falls back to deterministic on any setup error."""
    provider = (provider or config.LLM_PROVIDER).lower()
    try:
        if provider == "gemini" and config.GEMINI_API_KEY:
            from semantic_kernel.connectors.ai.google.google_ai import GoogleAIChatCompletion
            service = GoogleAIChatCompletion(gemini_model_id=config.GEMINI_MODEL_ID, api_key=config.GEMINI_API_KEY)
            return SemanticKernelInterpreter(service, live=True), "gemini"
        if provider == "openai" and config.OPENAI_API_KEY:
            from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
            service = OpenAIChatCompletion(ai_model_id=config.OPENAI_MODEL_ID, api_key=config.OPENAI_API_KEY)
            return SemanticKernelInterpreter(service, live=True), "openai"
    except Exception as exc:
        logger.warning("LLM connector setup failed (%s: %s); using deterministic fallback",
                       type(exc).__name__, exc)
    return DeterministicInterpreter(), "none"
