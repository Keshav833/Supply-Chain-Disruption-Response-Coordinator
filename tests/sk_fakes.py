"""OFFLINE Semantic Kernel test service. NOT a live Gemini/OpenAI call.

A ChatCompletionClientBase subclass that reports SUPPORTS_FUNCTION_CALLING and
returns scripted FunctionCallContent items, so SK's own auto function-invocation
loop executes the real registered plugin functions. Used to verify tool
registration and invocation without network access or an API key.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, List

from pydantic import PrivateAttr
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.contents import ChatMessageContent, FunctionCallContent, FunctionResultContent
from semantic_kernel.contents.utils.author_role import AuthorRole

OFFLINE_MODEL_ID = "offline-scripted-test-service"


class ScriptedToolCallingService(ChatCompletionClientBase):
    SUPPORTS_FUNCTION_CALLING: ClassVar[bool] = True
    _script: List[List[dict]] = PrivateAttr(default_factory=list)
    _step: int = PrivateAttr(default=0)
    seen_results: List[Any] = []

    def __init__(self, script: List[List[dict]], **kw: Any):
        super().__init__(ai_model_id=OFFLINE_MODEL_ID, service_id="offline", **kw)
        self._script = script
        self.seen_results = []

    async def _inner_get_chat_message_contents(self, chat_history, settings) -> List[ChatMessageContent]:
        self.seen_results = [i for m in chat_history.messages for i in m.items
                             if isinstance(i, FunctionResultContent)]
        if self._step < len(self._script):
            batch = self._script[self._step]
            self._step += 1
            items = [FunctionCallContent(id=f"call_{self._step}_{n}", plugin_name=c["plugin"],
                                         function_name=c["function"], arguments=json.dumps(c["arguments"]))
                     for n, c in enumerate(batch)]
            return [ChatMessageContent(role=AuthorRole.ASSISTANT, items=items)]
        return [ChatMessageContent(role=AuthorRole.ASSISTANT,
                                   content=f"[offline test service] received {len(self.seen_results)} tool results")]


class ScriptedTextService(ChatCompletionClientBase):
    """Returns fixed text (or raises) — for interpreter labelling tests. Offline only."""
    _text: str = PrivateAttr(default="")
    _fail: bool = PrivateAttr(default=False)

    def __init__(self, text: str = "", fail: bool = False):
        super().__init__(ai_model_id=OFFLINE_MODEL_ID, service_id="offline-text")
        self._text, self._fail = text, fail

    async def _inner_get_chat_message_contents(self, chat_history, settings) -> List[ChatMessageContent]:
        if self._fail:
            raise ConnectionError("offline service configured to fail")
        return [ChatMessageContent(role=AuthorRole.ASSISTANT, content=self._text)]
