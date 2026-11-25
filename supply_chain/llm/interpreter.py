"""LLM interpretation layer, kept separate from deterministic calculation.

An interpreter only turns already-computed, validated facts into prose. Its text
never feeds back into tool arguments, evidence, feasibility checks or decisions.

    DeterministicInterpreter  -> template text, labelled DETERMINISTIC_FALLBACK.
    SemanticKernelInterpreter -> a Semantic Kernel ChatCompletionAgent. Its output is
                                 labelled SEMANTIC_KERNEL_LLM only when `live=True`
                                 (a real provider such as Gemini) AND the call returned
                                 text. Test/offline services must use live=False.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from supply_chain.schemas import ExecutionMode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Interpretation:
    text: str
    mode: ExecutionMode
    error: Optional[str] = None


class DeterministicInterpreter:
    async def interpret(self, agent_name: str, facts: Dict[str, Any]) -> Interpretation:
        summary = facts.get("summary") or json.dumps(facts, default=str)[:300]
        return Interpretation(text=f"[deterministic_fallback] {agent_name}: {summary}",
                              mode=ExecutionMode.DETERMINISTIC_FALLBACK)


class SemanticKernelInterpreter:
    """Explains facts with an SK ChatCompletionAgent; falls back (labelled) on any error."""

    INSTRUCTIONS = ("You explain supply-chain findings that were already computed by deterministic tools. "
                    "Do not change, recompute or contradict any number, status or decision. "
                    "Reply in at most three sentences.")

    def __init__(self, service: Any, live: bool):
        self.service = service
        self.live = live
        self._fallback = DeterministicInterpreter()

    async def interpret(self, agent_name: str, facts: Dict[str, Any]) -> Interpretation:
        from semantic_kernel.agents import ChatCompletionAgent
        try:
            agent = ChatCompletionAgent(service=self.service, name=agent_name, instructions=self.INSTRUCTIONS)
            response = await agent.get_response(messages=json.dumps(facts, default=str))
            text = str(response.content or "").strip()
            if not text:
                raise RuntimeError("empty LLM response")
        except Exception as exc:  # never let interpretation break the deterministic result
            logger.warning("[%s] LLM interpretation failed, using deterministic fallback: %s", agent_name, exc)
            fb = await self._fallback.interpret(agent_name, facts)
            return Interpretation(text=fb.text, mode=fb.mode, error=f"{type(exc).__name__}: {exc}")
        mode = ExecutionMode.SEMANTIC_KERNEL_LLM if self.live else ExecutionMode.DETERMINISTIC_FALLBACK
        return Interpretation(text=text, mode=mode)
