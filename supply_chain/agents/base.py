"""Common agent machinery (PHASE_5).

Every agent:
  1. marks itself RUNNING on the shared Blackboard,
  2. builds a Semantic Kernel `Kernel` with its tool plugin registered,
  3. invokes its tools THROUGH the kernel (`Kernel.invoke`), so each call goes
     through SK's function pipeline (argument binding, filters, telemetry);
     the plugin records every call on the Blackboard,
  4. builds a typed contract output from validated tool results,
  5. optionally asks an interpreter for prose (never used for calculations),
  6. publishes to its own Blackboard section, or records FAILED with no evidence.
"""
from __future__ import annotations

import json
import logging
import asyncio
from typing import Any, Callable, ClassVar, Dict, Optional, Tuple, Type

from pydantic import BaseModel
from semantic_kernel import Kernel
from semantic_kernel.functions import KernelArguments

from supply_chain.blackboard import Blackboard
from supply_chain import config
from supply_chain.contracts import BLACKBOARD_SECTION, AgentName, AgentOutput
from supply_chain.llm.interpreter import DeterministicInterpreter
from supply_chain.schemas import AgentStatus, ExecutionMode

logger = logging.getLogger(__name__)


class AgentPrerequisiteError(RuntimeError):
    """Required Blackboard state is missing; the agent cannot proceed honestly."""


class ToolPlugin:
    """Base for SK plugins. Each @kernel_function calls a real tool via `_run`."""

    def __init__(self, agent: AgentName, blackboard: Blackboard):
        self.agent = agent
        self.blackboard = blackboard

    async def _run(self, tool: str, fn: Callable[[], BaseModel], args: Dict[str, Any]) -> str:
        for attempt in range(1, config.TOOL_RETRY_MAX_ATTEMPTS + 1):
            try:
                result = fn()
                if not isinstance(result, BaseModel):
                    raise TypeError(f"{tool} returned {type(result).__name__}, expected a validated model")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                await self.blackboard.record_tool_call(self.agent, tool, ok=False,
                                                       detail={"args": args, "attempt": attempt}, error=error)
                if not self._is_recoverable(exc) or attempt >= config.TOOL_RETRY_MAX_ATTEMPTS:
                    raise
                await self.blackboard.record_tool_retry(
                    self.agent, tool, attempt, config.TOOL_RETRY_MAX_ATTEMPTS, error,
                )
                delay = config.TOOL_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)
                continue
            break
        payload = result.model_dump(mode="json")
        await self.blackboard.record_tool_call(self.agent, tool, ok=True,
                                               detail={"args": args, "result": payload})
        return json.dumps(payload)

    @staticmethod
    def _is_recoverable(exc: Exception) -> bool:
        """Retry only transient infrastructure faults, never invalid business data."""
        return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def root_cause(exc: BaseException) -> BaseException:
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc


class BaseAgent:
    name: ClassVar[AgentName]
    plugin_name: ClassVar[str]
    output_cls: ClassVar[Type[AgentOutput]]

    def __init__(self, interpreter: Any = None):
        self.interpreter = interpreter or DeterministicInterpreter()

    # --- Semantic Kernel -------------------------------------------------------
    def make_plugin(self, blackboard: Blackboard) -> ToolPlugin:
        raise NotImplementedError

    def build_kernel(self, blackboard: Blackboard) -> Kernel:
        kernel = Kernel()
        kernel.add_plugin(self.make_plugin(blackboard), plugin_name=self.plugin_name)
        return kernel

    async def call_tool(self, kernel: Kernel, tool: str, model: Type[BaseModel], **arguments: Any) -> BaseModel:
        """Invoke a registered tool through SK and re-validate its JSON output."""
        result = await kernel.invoke(plugin_name=self.plugin_name, function_name=tool,
                                     arguments=KernelArguments(**arguments))
        if result is None or result.value is None:
            raise RuntimeError(f"{tool} returned no result")
        return model.model_validate_json(result.value)

    # --- lifecycle -------------------------------------------------------------
    async def execute(self, kernel: Kernel, blackboard: Blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Return (output payload kwargs, facts for interpretation). Raise on failure."""
        raise NotImplementedError

    async def run(self, blackboard: Blackboard) -> AgentOutput:
        await blackboard.mark_running(self.name)
        try:
            kernel = self.build_kernel(blackboard)
            payload, facts = await self.execute(kernel, blackboard)
            interp = await self.interpreter.interpret(self.name.value, facts)
            output = self.output_cls(
                incident_id=blackboard.incident_id, status=AgentStatus.COMPLETE,
                execution_mode=interp.mode, tools_invoked=list(blackboard.tools_invoked[self.name.value]),
                interpretation=interp.text, **payload,
            )
            section = BLACKBOARD_SECTION[self.name]
            await blackboard.publish(self.name, section, getattr(output, output.payload_field),
                                     execution_mode=output.execution_mode,
                                     notifications=getattr(output, "notifications", None))
            logger.info("[%s] COMPLETE (%s)", self.name.value, output.execution_mode.value)
            return output
        except Exception as exc:
            cause = root_cause(exc)
            error = f"{self.name.value} failed: {type(cause).__name__}: {cause}"
            logger.error(error)
            if blackboard.agent_status[self.name.value] != AgentStatus.COMPLETE:
                await blackboard.mark_failed(self.name, error)
            return self.output_cls(incident_id=blackboard.incident_id, status=AgentStatus.FAILED,
                                   execution_mode=ExecutionMode.DETERMINISTIC_FALLBACK,
                                   tools_invoked=list(blackboard.tools_invoked[self.name.value]), error=error)


def require_valid_assessment(blackboard: Blackboard) -> None:
    a = blackboard.incident_assessment
    if a is None:
        raise AgentPrerequisiteError("incident_assessment is UNKNOWN; Monitor has not published it")
    if not a.is_valid:
        raise AgentPrerequisiteError(f"incident failed validation: {a.validation_message}")
