"""Structural types for the chat model the supervisor calls (T27 F1.4).

The supervisor needs only ``bind_tools``, ``invoke`` and ``ainvoke``. A LangChain ``BaseChatModel``
satisfies these, and so do the traced and fake models the runtime bridge and the tests inject, so the
runtime depends on this protocol rather than on a LangChain base class.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.messages import AIMessage


class BoundChatModel(Protocol):
    """A chat model that can be invoked on a message list."""

    def invoke(self, messages: Sequence[Any], *args: object, **kwargs: object) -> AIMessage:
        """Return the model's reply to ``messages``."""
        ...

    async def ainvoke(self, messages: Sequence[Any], *args: object, **kwargs: object) -> AIMessage:
        """Async variant of :meth:`invoke`."""
        ...


class ChatModel(BoundChatModel, Protocol):
    """A chat model that can also bind tools."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: object) -> BoundChatModel:
        """Return a model that may call ``tools``."""
        ...
